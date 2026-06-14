# qa-toolkit-mcp

An [MCP](https://modelcontextprotocol.io) server that turns test-run reports into **regression analysis** an LLM agent can reason about.

It does not run your tests. It **reads the reports your test runs already produce** (manual, CI, or cron) and answers questions like *"what regressed between Monday and Friday, and which failures are just known issues?"* — categorizing every change instead of dumping a raw diff.

The split is deliberate: the producer of reports (your pytest job, your pipeline) stays completely decoupled from the consumer (this server). Anything that emits the report schema can be analyzed.

## What it exposes

| Kind | Name | Purpose |
|---|---|---|
| Tool | `qa_list_runs` | List available runs with summary counts (filter by suite / date, paginated). |
| Tool | `qa_get_run` | Return one run; failures only by default to keep agent context small. |
| Tool | `qa_compare_runs` | Categorized regression analysis between two runs (the core). |
| Resource | `run://{run_id}/summary.md` | Markdown summary of a run for the host to load into context. |
| Prompt | `weekly_regression_review` | Orchestrates list → compare consecutive pairs → real-regression report. |

All tool parameters are exposed as **flat, top-level arguments** (`run_a`,
`run_b`, …), not nested under a `params` wrapper — a model-callability decision
documented in [`docs/adr/0001-flatten-tool-parameters.md`](docs/adr/0001-flatten-tool-parameters.md).

## The core idea: categorize, don't diff

`qa_compare_runs` sorts every test transition into the buckets a QA engineer actually acts on:

| Bucket | Meaning |
|---|---|
| **Regression** | passed in A, failed in B — the expensive signal |
| **Fix** | failed in A, passed in B — validates a change |
| **Persistent failure** | failed in both; `same_error` flag from a fingerprint match |
| **New / Removed test** | appeared / disappeared between runs |
| **Classification change** | the QA-assigned label changed (the human oracle changed its mind) |

Example output (Markdown mode):

```markdown
# Compare `search-25` → `search-26`

**1 regression(s) · 1 fix(es) · 2 persistent · 1 new · 0 removed · 1 reclassified**

## Regressions (passed → failed)
- `SI-POS-008` — AssertionError: Expected success, got 'error' - timeout

## Fixes (failed → passed)
- `SI-POS-005`

## Persistent failures
- `SI-POS-006` (same error) — SQLGrammarException
- `SI-POS-007` (same error) — SQLGrammarException

## Classification changes (QA oracle changed its mind)
- `SI-POS-007`: unclassified → bug real
```

### Why a fingerprint, not the error message

Comparing by raw error message is fragile — timestamps, IDs and line numbers make "the same bug" look different every run. Instead each failure carries a `fingerprint`: a hash of `(test_id, error_type, normalized_message)`. Two failures with the same fingerprint are treated as the same root cause. The **producer** computes it (only the producer knows which parts of its messages are volatile), which keeps this server framework-agnostic.

## Supported report formats (auto-detected)

Drop either kind into the runs directory; the format is detected per file.

**Native** (`run-report.v1.json`, schema 1.0 / 1.1) — has a `schema_version` field. See [`schemas/run-report.v1.json`](schemas/run-report.v1.json).

**Classification reports** — a CI/QA pipeline that lists *failures* with human-assigned labels, plus an optional JUnit XML sibling that supplies the passed tests:

```
runs/
  search_suite_classification.json   ← failures + labels
  search_suite.xml                   ← JUnit XML (passed + failed)
```

When the XML sibling is found, the run is `is_exhaustive=true` and every test is included. Without it, only failures load and `is_exhaustive=false`; `compare_runs` adapts — an absent test in B is treated as passed-implicit, so failing→absent becomes a **fix**, not a removed test.

## Configuration

Copy `.env.example` to `.env` and point `QA_TOOLKIT_RUNS_DIR` at your reports directory. `.env` is gitignored — one per machine.

```powershell
copy .env.example .env
notepad .env
```

Resolution order (highest priority first):

1. Real OS environment variables (e.g. set in your MCP client config or shell).
2. `.env` in the project root.
3. `.env` in the current working directory.
4. Default: `./runs/` relative to the server's working directory.

Keys: `QA_TOOLKIT_RUNS_DIR`, `QA_TOOLKIT_LOG_LEVEL`.

## Install & run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The server speaks **stdio**, so you don't launch it by hand for normal use — your MCP client starts it as a subprocess. Register it:

**Claude Code**

```powershell
claude mcp add qa-toolkit -s user -- `
  "<repo>\.venv\Scripts\python.exe" -m qa_toolkit_mcp.server
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "qa-toolkit": {
      "command": "<repo>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "qa_toolkit_mcp.server"]
    }
  }
}
```

Explore it interactively with the MCP Inspector:

```powershell
npx @modelcontextprotocol/inspector .\.venv\Scripts\python.exe -m qa_toolkit_mcp.server
```

## Testing

```powershell
pytest
```

The suite mirrors a layered test strategy:

- **Pure-function layer** — `compare`, `storage`, the adapter and formatters tested in isolation.
- **Tool layer** — the registered MCP tools exercised through their entry points.
- **Metamorphic relations** over `compare_runs` — invariants that any correct implementation must satisfy, independent of specific examples:
  - *Identity*: `compare(A, A)` reports no changes.
  - *Symmetry*: regressions in `compare(A, B)` equal fixes in `compare(B, A)`; new ↔ removed swap.
  - *Coverage*: every test lands in at most one bucket per direction.

## Project layout

```
qa_toolkit_mcp/
  server.py                 FastMCP server: tools, resource, prompt, entry point
  models.py                 Pydantic models mirroring the report schema
  storage.py                Read + path-safety + format auto-detection
  adapter_classification.py classification.json (+ JUnit XML) → canonical model
  compare.py                Pure regression-analysis logic
  formatters.py             Models → Markdown / JSON
  config.py                 .env + env-var resolution
schemas/run-report.v1.json  The report contract (source of truth)
docs/adr/                   Architecture Decision Records (e.g. 0001 — flatten tool parameters)
evaluations/                LLM evaluation questions for the server
tests/                      Layered + metamorphic test suite
```

## Status

v0.1 — stdio transport, three tools (flat-parameter schema, see ADR 0001),
one resource, one prompt. Not yet published to PyPI.

## License

MIT — see [LICENSE](LICENSE).
