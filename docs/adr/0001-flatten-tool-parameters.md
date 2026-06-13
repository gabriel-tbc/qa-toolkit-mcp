# ADR 0001 — Flatten tool parameters (drop the `params` wrapper)

- **Status:** Accepted
- **Date:** 2026-06-13
- **Affects:** `qa_toolkit_mcp/server.py` (`qa_list_runs`, `qa_get_run`, `qa_compare_runs`), `tests/test_tools.py`
- **Downstream (coordinated separately):** `qa-mcp-harness` (external repo — not modified by this change)

## Context

A Layer-4 tool-calling eval in the `qa-mcp-harness`, run against a local model
(qwen2.5 via Ollama), found that `qa_compare_runs` was almost impossible for the
model to call. The model produced **flat** arguments:

```json
{"run_a": "search-25_classification", "run_b": "search-26_classification", "response_format": "json"}
```

and the server returned `isError: true` on 9 of 10 attempts. The only successful
call used the **wrapped** form:

```json
{"params": {"run_a": "...", "run_b": "...", "response_format": "markdown"}}
```

### Root cause (confirmed)

All three tools were defined with a single Pydantic-model parameter:

```python
async def qa_compare_runs(params: CompareRunsInput) -> str: ...
```

FastMCP derives a tool's `inputSchema` from the function signature, creating one
JSON property per parameter (`mcp/server/fastmcp/utilities/func_metadata.py`,
`func_metadata`). One model-typed parameter named `params` therefore produces a
schema whose only top-level property is a **required** `params` object, with the
real fields nested one level down. The ground-truth schema (dumped from the
running FastMCP instance, mcp 1.27.1):

```json
{
  "properties": { "params": { "$ref": "#/$defs/CompareRunsInput" } },
  "required": ["params"],
  "title": "qa_compare_runsArguments",
  "type": "object"
}
```

Reproduced locally via `mcp.call_tool(...)`:

| Arguments sent | Result |
|---|---|
| `{"run_a": ..., "run_b": ...}` (flat) | `isError: true` — `params Field required [type=missing]` |
| `{"params": {"run_a": ..., "run_b": ...}}` (wrapped) | `isError: false` |

The failure is exactly a missing required `params` key. The wrapper is an
artifact of the Pydantic-model-as-single-arg pattern, not an intentional API
contract. Models — especially smaller local ones — are strongly biased toward
emitting tool arguments flat, so the wrapper makes the tool effectively
uncallable.

## Decision

**Flatten the schema.** Each tool exposes its parameters as flat, top-level
arguments, matching what models naturally produce:

```python
async def qa_compare_runs(
    run_a: Annotated[_NonEmptyStr, Field(description="Baseline run_id (treated as 'before').")],
    run_b: Annotated[_NonEmptyStr, Field(description="Newer run_id (treated as 'after').")],
    response_format: Annotated[ResponseFormat, Field(description="...")] = ResponseFormat.MARKDOWN,
) -> str: ...
```

The `CompareRunsInput` / `GetRunInput` / `ListRunsInput` models are removed; the
tool signatures are now the single source of truth for the input schema.

### Validation is preserved, not dropped

The former models carried `str_strip_whitespace=True` and per-field constraints
via `ConfigDict` + `Field`. These are reproduced on the flat parameters with
`Annotated` + `pydantic.StringConstraints`, verified by test:

- run-id args use `_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]` — whitespace stripped, empty/whitespace-only rejected.
- `suite` uses `Annotated[Optional[str], StringConstraints(strip_whitespace=True)]`.
- `limit` (`ge=1, le=200`), `offset` (`ge=0`), defaults, and the `ResponseFormat` enum are unchanged.

### Two intentional, documented side effects

1. **Unknown top-level keys are now ignored instead of rejected.** The former
   inner model used `extra="forbid"`; the FastMCP-generated argument model does
   not. This is *more* tolerant of model output (a stray extra key no longer
   fails the call) and does not affect the happy path. Acceptable — leniency
   helps callability.
2. **`response_format` gained a description** on `qa_get_run` / `qa_compare_runs`
   (it previously had none). Pure documentation improvement; no behavior change.

The regression analysis itself (`compare.py`, `storage.py`, `formatters.py`,
the report models in `models.py`) is untouched. The full suite — including the
pure-function and metamorphic layers — stays green (74 passed).

## Alternatives considered

1. **Keep the wrapper, improve the description.** Rejected. No prose in a
   `params` description reliably overcomes a model's bias to flatten; the eval
   shows ~10% success even when the model is told the shape exists. The wrapper
   carries no semantic value here — the tools have 3, 3, and 6 simple scalar
   params.
2. **Keep the models, re-wrap inside a flat signature** (construct
   `CompareRunsInput(...)` from flat params in the body). Achieves the same wire
   schema but duplicates field definitions (description + constraints in both the
   signature and the model). More code, no benefit; rejected in favor of the
   signature being the single source of truth.

## Consequences

- **Positive:** tools are callable with the flat arguments models naturally
  emit; the inputSchema is self-documenting; less code (three model classes
  removed); a new test (`test_tools_expose_flat_top_level_params`) fails if a
  `params` wrapper is ever reintroduced.
- **Breaking — wire contract changed.** Callers that sent `{"params": {...}}`
  now get `isError: true` (`run_a Field required`). This is the intended
  inversion of the bug.
- **`qa-mcp-harness` must be updated** to send flat arguments before/with the
  rollout of this change. That repo is **out of scope here** and must be
  coordinated separately. Any other client pinned to the wrapped form needs the
  same update.
- **In-process callers** (e.g. `tests/test_tools.py`) now call the tool
  functions with keyword arguments instead of a model instance.

## Follow-up (not part of this change)

Re-run the same Layer-4 eval against a stronger model (e.g. Claude) on the
*pre-flatten* schema. If Claude also flattened the wrapper, that corroborates
"MCP design defect" over "weak local model" — useful signal, but the decision
above stands regardless: flat top-level parameters are the correct shape for an
LLM-facing tool either way.

## Verification

```text
$ pytest -q
74 passed

# Flattened schema (dumped from the FastMCP instance)
qa_compare_runs  required=['run_a','run_b']  props=['run_a','run_b','response_format']
qa_get_run       required=['run_id']         props=['run_id','include_passed','response_format']
qa_list_runs     required=None               props=['suite','since','until','limit','offset','response_format']

# call_tool behavior after the change
FLAT    -> OK (isError=false)
WRAPPED -> ERROR (isError=true): run_a Field required
```
