# qa-toolkit-mcp

An [MCP](https://modelcontextprotocol.io) server that reads test reports and turns them into a regression analysis a model can work with. It does not run your tests. It reads the reports your test runs already write, and answers questions like "what regressed between Monday and Friday, and which of these failures are just the same known issues?"

I built it so an agent can do the boring part of a weekly regression review for me: line up the runs, compare them, and tell me what actually broke instead of handing me a raw diff.

## What it does

You point it at a folder of run reports (the JSON your CI, your cron job, or your manual runs already produce) and it gives a model three tools, one resource, and one prompt:

- `qa_list_runs` - lists the runs in the folder. You can filter by suite or by date, and it pages.
- `qa_get_run` - returns one run. By default it only lists the failures, to keep the model's context small. Pass `include_passed` when you want everything.
- `qa_compare_runs` - takes two runs and sorts what changed between them. This is the main one.
- `run://{run_id}/summary.md` (resource) - a Markdown summary of a single run the host can load.
- `weekly_regression_review` (prompt) - walks the model through a week: list the runs, compare each consecutive pair, and write a short report.

`qa_compare_runs` does not hand you a raw diff. It sorts every test into the buckets a QA person actually acts on:

- **regression** - passed in A, failed in B. The one you care about most.
- **fix** - failed in A, passed in B.
- **persistent failure** - failed in both. It also tells you whether it is the same error as before or a new one (see fingerprints below).
- **new test / removed test** - showed up or disappeared between the two runs.
- **classification change** - the QA label changed, even when the pass/fail did not. "Still failing here, but we changed our mind about why."

Here is what the Markdown output looks like:

```markdown
# Compare `search-25` → `search-26`

Suite: search → search
Started: 2026-05-25T09:00:00 → 2026-05-26T09:00:00

**1 regression(s) · 1 fix(es) · 2 persistent · 0 new · 0 removed · 1 reclassified**

## Regressions (passed → failed)
- `SI-POS-008` — **AssertionError**: Expected success, got 'error' - timeout

## Fixes (failed → passed)
- `SI-POS-005`

## Persistent failures
- `SI-POS-006` (same error) — SQLGrammarException: unexpected token
- `SI-POS-007` (same error) — SQLGrammarException: unexpected token

## Classification changes (QA oracle changed its mind)
- `SI-POS-007`: unclassified → bug real
```

Every tool can return JSON instead of Markdown, for when the agent needs to read the numbers and not the prose.

## What it doesn't do

- It does not run your tests. It only reads reports. Whatever writes the report (a pytest job, a pipeline, a person) stays separate from this server.
- It does not compute the fingerprint that groups "the same error". The producer of the report does that (more on why below).
- No HTTP yet. It speaks stdio only, which is what local MCP clients use.
- No dashboard and no UI. That is a separate thing that will read the same JSON one day.
- It is not on PyPI yet. You install it from the repo.

## How it works

**Two report formats, detected per file.** Drop either kind into the runs folder and the server works out which one it is:

- Native reports have a `schema_version` field and are checked against `schemas/run-report.v1.json`.
- Classification reports are what a QA pipeline tends to write: a list of the failures, each with a human-assigned label. If there is a JUnit XML file sitting next to it with the same name, the server reads that too, so now it also knows about the tests that passed.

**`is_exhaustive`.** A native report, or a classification report with its XML sibling, knows every test that ran. A bare classification report only knows the failures. The compare keeps track of this: when one side only lists failures, a test that is missing is treated as passed. So a test that failed in A and is gone in B counts as a fix, not as a removed test.

**Fingerprints.** Each failure in a report carries a `fingerprint`: a hash of the test id, the error type, and the normalized message. Two failures with the same fingerprint are treated as the same root cause. That is how `qa_compare_runs` tells "still the same bug" apart from "now it fails for a different reason".

**Flat parameters.** The tools take plain top-level arguments (`run_a`, `run_b`, and so on), not one nested `params` object. There is a story behind that, in the next section.

**Configuration.** Copy `.env.example` to `.env` and set `QA_TOOLKIT_RUNS_DIR` to your reports folder. A real env var wins over `.env`, which wins over the default of `./runs/`. `.env` is gitignored, so it stays one per machine.

## Why it works this way

**It reads reports instead of running tests** because the thing that runs the tests and the thing that reads them should not be glued together. CI runs the tests on its own schedule and writes a report. This server reads that report whenever an agent asks. Anything that writes the schema can be read, and a dashboard could read the same files later without sharing a line of code with this server.

**It categorizes instead of diffing** because a raw diff makes you read everything again. A QA engineer does not treat all changes the same: a regression is urgent, a known persistent failure is something you already triaged. So the tool does that sorting up front.

**The fingerprint lives in the producer, not here.** Error messages change every run (timestamps, ids, line numbers), so comparing raw messages makes the same bug look new every time. A fingerprint stays stable. I put it in the producer because only the producer knows which parts of its own messages are the volatile bits, and that keeps this server framework-agnostic.

**The parameters are flat** because models are bad at the nested version. I found this with my own test harness: a local model could only call `qa_compare_runs` about one time in ten, because FastMCP was wrapping every argument under a required `params` object and the model kept sending the arguments flat. So I flattened the schema to match what models actually send. The whole investigation is written up in [ADR 0001](docs/adr/0001-flatten-tool-parameters.md).

## Running it

Install it:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The server speaks stdio, so you do not start it by hand. Your MCP client launches it as a subprocess. Register it and point it at your reports.

Claude Code:

```powershell
claude mcp add qa-toolkit -s user -- `
  "<repo>\.venv\Scripts\python.exe" -m qa_toolkit_mcp.server
```

Claude Desktop, in `claude_desktop_config.json`:

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

Or poke at it by hand with the MCP Inspector:

```powershell
npx @modelcontextprotocol/inspector .\.venv\Scripts\python.exe -m qa_toolkit_mcp.server
```

Run the tests with `pytest`. The suite is layered the way I test things: the pure functions (compare, storage, the adapter, the formatters) on their own, then the tools through their real entry points, and then a set of metamorphic checks on `qa_compare_runs` - properties that have to hold whatever the input. For example, comparing a run against itself reports nothing changed, and the regressions going from A to B are exactly the fixes going from B to A.

## Project structure

```
qa_toolkit_mcp/
  server.py                  the MCP server: the tools, the resource, the prompt, the entry point
  models.py                  Pydantic models for the report schema
  storage.py                 reads files, keeps paths safe, detects the format
  adapter_classification.py  turns a classification report (+ JUnit XML) into the canonical model
  compare.py                 the regression analysis, pure functions, no I/O
  formatters.py              models to Markdown or JSON
  config.py                  .env and env-var handling
schemas/
  run-report.v1.json         the report contract, the source of truth
docs/adr/                    decision records (0001 - why the tool parameters are flat)
evaluations/                 eval questions for the server
tests/                       the layered + metamorphic suite
```

## License

MIT, see [LICENSE](LICENSE).
