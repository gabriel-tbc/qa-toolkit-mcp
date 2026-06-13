"""qa_toolkit_mcp — MCP server entry point.

Exposes three tools, one resource, one prompt. Transport: stdio.

Tools (all read-only):
    qa_list_runs       List runs available in the configured directory.
    qa_get_run         Return a single run report.
    qa_compare_runs    Regression analysis between two runs.

Resource:
    run://{run_id}/summary.md   Markdown summary of a run.

Prompt:
    weekly_regression_review   Orchestrates a multi-tool weekly analysis.

Logging note: stdio servers must NOT write to stdout. All logging goes to stderr.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StringConstraints

from . import config, storage
from .compare import compare_runs
from .formatters import (
    ResponseFormat,
    format_comparison_markdown,
    format_run_list_markdown,
    format_run_markdown,
    synth_summary_md_from_run,
    to_json,
)
from .storage import StorageError

# Configure logging to stderr only (stdio requirement).
_settings = config.get_settings()
logging.basicConfig(
    stream=sys.stderr, # this means all logs go to stderr, preserving stdout for MCP communication
    level=getattr(logging, _settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("qa_toolkit_mcp")


mcp = FastMCP("qa_toolkit_mcp")  
# FastMCP instance for registering tools, resources, prompts.
# FastMCP is chosen for its performance and ease of use, but the code is structured to allow 
# swapping out the MCP framework if needed in the future without major refactoring. 
# The mcp instance is used as a decorator to register tools, resources, 
# and prompts, and it handles the underlying communication protocol.


# ─── Shared parameter types ──────────────────────────────────────────────────
#
# Tools expose their parameters as flat, top-level arguments (run_a, run_b, …)
# rather than a single nested `params` object. FastMCP derives each tool's
# inputSchema from the function signature: one parameter becomes one top-level
# JSON property. A single Pydantic-model parameter would instead nest every
# field under a *required* `params` wrapper — a shape LLM callers routinely
# flatten and so fail to provide. See docs/adr/0001-flatten-tool-parameters.md.
#
# `_NonEmptyStr` reproduces the former input models' `str_strip_whitespace=True`
# + `min_length=1` on run-id arguments, so validation is unchanged.

_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_OptStrippedStr = Annotated[Optional[str], StringConstraints(strip_whitespace=True)]


# ─── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool(
    name="qa_list_runs",
    annotations={
        "title": "List test runs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qa_list_runs(
    suite: Annotated[
        _OptStrippedStr,
        Field(description="Filter by suite name (exact match). Omit to include all suites."),
    ] = None,
    since: Annotated[
        Optional[datetime],
        Field(description="Inclusive lower bound on started_at (ISO 8601, e.g. '2026-05-20T00:00:00Z')."),
    ] = None,
    until: Annotated[
        Optional[datetime],
        Field(description="Inclusive upper bound on started_at (ISO 8601)."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max runs to return.")] = 50,
    offset: Annotated[int, Field(ge=0, description="Number of matching runs to skip.")] = 0,
    response_format: Annotated[
        ResponseFormat,
        Field(description="'markdown' for human-readable, 'json' for programmatic."),
    ] = ResponseFormat.MARKDOWN,
) -> str:
    """List available test runs from the configured runs directory.

    Returns metadata only (run_id, suite, timestamps, counts) — not the full
    list of test cases. Use `qa_get_run` for that.

    Filters are applied in this order: suite (exact match), since, until.
    Results are sorted by `started_at` ascending. Pagination via limit/offset.

    Returns:
        Markdown table or JSON depending on response_format. JSON shape:
        {
            "total": int,
            "count": int,
            "offset": int,
            "has_more": bool,
            "next_offset": int | null,
            "items": [
                {"run_id": str, "suite": str, "started_at": iso8601,
                 "summary": {"total","passed","failed","skipped","errors"}}
            ]
        }

    Error response: string starting with "Error:".
    """
    try:
        run_ids = storage.list_run_ids()
        matched: list[dict] = []
        for rid in run_ids:
            try:
                run = storage.load_run(rid)
            except StorageError as exc:
                logger.warning("Skipping malformed run %s: %s", rid, exc)
                continue
            if suite and run.suite != suite:
                continue
            if since and run.started_at < since:
                continue
            if until and run.started_at > until:
                continue
            matched.append(
                {
                    "run_id": run.run_id,
                    "suite": run.suite,
                    "started_at": run.started_at.isoformat(),
                    "summary": run.summary.model_dump(),
                }
            )

        matched.sort(key=lambda r: r["started_at"])
        total = len(matched)
        page = matched[offset : offset + limit]
        has_more = offset + len(page) < total
        next_offset = offset + len(page) if has_more else None

        if response_format == ResponseFormat.JSON:
            import json as _json

            return _json.dumps(
                {
                    "total": total,
                    "count": len(page),
                    "offset": offset,
                    "has_more": has_more,
                    "next_offset": next_offset,
                    "items": page,
                },
                indent=2,
            )
        return format_run_list_markdown(page, total, offset, has_more)

    except StorageError as exc:
        return f"Error: {exc}"


@mcp.tool(
    name="qa_get_run",
    annotations={
        "title": "Get test run",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qa_get_run(
    run_id: Annotated[
        _NonEmptyStr,
        Field(description="Exact run_id (file stem, no extension)."),
    ],
    include_passed: Annotated[
        bool,
        Field(description="If true, include passed tests in the output. Default false to keep context small."),
    ] = False,
    response_format: Annotated[
        ResponseFormat,
        Field(description="'markdown' for human-readable, 'json' for programmatic."),
    ] = ResponseFormat.MARKDOWN,
) -> str:
    """Return a single test run by id.

    By default, only failed/error tests are listed in the body (to keep context
    small). Set `include_passed=true` for the full inventory.

    Returns:
        Markdown or JSON depending on response_format. JSON returns the full
        RunReport model conforming to schemas/run-report.v1.json.

    Error response: string starting with "Error: ..." (e.g., "Error: Run not found").
    """
    try:
        run = storage.load_run(run_id)
        if response_format == ResponseFormat.JSON:
            return to_json(run)
        return format_run_markdown(run, include_passed=include_passed)
    except StorageError as exc:
        return f"Error: {exc}. Use qa_list_runs to see available run_ids."


@mcp.tool(
    name="qa_compare_runs",
    annotations={
        "title": "Compare two test runs (regression analysis)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qa_compare_runs(
    run_a: Annotated[
        _NonEmptyStr,
        Field(description="Baseline run_id (treated as 'before')."),
    ],
    run_b: Annotated[
        _NonEmptyStr,
        Field(description="Newer run_id (treated as 'after')."),
    ],
    response_format: Annotated[
        ResponseFormat,
        Field(description="'markdown' for human-readable, 'json' for programmatic."),
    ] = ResponseFormat.MARKDOWN,
) -> str:
    """Compare two test runs and categorize the differences.

    `run_a` is treated as baseline (older), `run_b` as newer.

    Categories returned:
        regressions          passed in A, failed/error in B (highest priority)
        fixes                failed/error in A, passed in B
        persistent_failures  failed in both
            same_error       fingerprints match → same root cause
            different_error  fingerprints differ → root cause changed
        new_tests            in B but not A
        removed_tests        in A but not B
        other_changes        transitions involving skipped (low priority)

    Flakiness detection requires N>2 runs and is not in this tool. Use the
    weekly_regression_review prompt to orchestrate multi-run analysis.

    Returns:
        Markdown summary or JSON of the full ComparisonResult model.

    Error response: string starting with "Error: ...".
    """
    try:
        report_a = storage.load_run(run_a)
        report_b = storage.load_run(run_b)
        result = compare_runs(report_a, report_b)
        if response_format == ResponseFormat.JSON:
            return to_json(result)
        return format_comparison_markdown(result)
    except StorageError as exc:
        return f"Error: {exc}. Use qa_list_runs to see available run_ids."


# ─── Resource ────────────────────────────────────────────────────────────────


@mcp.resource("run://{run_id}/summary.md")
async def run_summary(run_id: str) -> str:
    """Markdown summary of a run.

    Returns the companion `{run_id}.md` file if present, otherwise synthesizes
    one from the JSON report.
    """
    try:
        existing = storage.load_summary_md(run_id)
        if existing is not None:
            return existing
        run = storage.load_run(run_id)
        return synth_summary_md_from_run(run)
    except StorageError as exc:
        return f"# Error\n\n{exc}"


# ─── Prompt ──────────────────────────────────────────────────────────────────


@mcp.prompt() # Fábrica de decoradores de prompts. Sin argumentos porque este prompt no necesita parámetros de configuración.
def weekly_regression_review(suite: str = "", days: int = 7) -> str:
    """Slash command: end-of-week regression review.

    The host injects this as a user message. The model is expected to use
    qa_list_runs / qa_get_run / qa_compare_runs to fulfill it.

    Args:
        suite: Optional suite filter (e.g., "api-regression"). Empty = all suites.
        days: How many days back to consider. Default 7.
    """
    suite_clause = f"for suite `{suite}`" if suite else "across all suites"
    return (
        f"You are reviewing test regressions {suite_clause} over the last {days} days.\n\n"
        "Use the qa-toolkit-mcp tools to do the following:\n"
        "1. Call `qa_list_runs` with a `since` filter set to N days ago. Use response_format='json' "
        "so you can iterate programmatically.\n"
        "2. Sort the returned runs by started_at ascending and pair each consecutive pair.\n"
        "3. For each pair, call `qa_compare_runs` and collect the regressions, fixes, and "
        "persistent_failures.\n"
        "4. Across all pairs, identify:\n"
        "   - REAL regressions: tests that newly failed in some pair AND have NOT been seen "
        "passing again by the end of the window. List each with its error message.\n"
        "   - FIXES that landed during the week.\n"
        "   - PERSISTENT failures with same_error=true the entire week (known issues).\n"
        "   - FLAKY SUSPECTS: tests whose status flipped more than once during the window. "
        "List them separately and DO NOT count them as real regressions.\n"
        "5. Produce a short Markdown report with sections in this order: Real regressions, "
        "Fixes, Known persistent failures, Flaky suspects, Summary counts.\n\n"
        "Do not invent test names. Only report what the tools return."
    )


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    """Run the server over stdio."""
    settings = config.get_settings()
    logger.info(
        "qa_toolkit_mcp starting. runs_dir=%s (project_root=%s, log_level=%s)",
        settings.runs_dir,
        settings.project_root,
        settings.log_level,
    )
    mcp.run()


if __name__ == "__main__":
    main()
