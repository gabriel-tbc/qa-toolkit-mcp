"""Capa 2 — MCP tools tested through their Python entrypoints.

We don't spin up a real MCP client here. We call the decorated tool functions
directly to validate that input parsing (Pydantic) + business logic + output
formatting all wire correctly.
"""

from __future__ import annotations

import json

import pytest

from qa_toolkit_mcp.formatters import ResponseFormat
from qa_toolkit_mcp.server import (
    CompareRunsInput,
    GetRunInput,
    ListRunsInput,
    qa_compare_runs,
    qa_get_run,
    qa_list_runs,
    run_summary,
)


# ─── qa_list_runs ────────────────────────────────────────────────────────────


async def test_list_runs_json_returns_all_fixtures():
    out = await qa_list_runs(ListRunsInput(response_format=ResponseFormat.JSON))
    data = json.loads(out)
    assert data["total"] == 3
    assert data["count"] == 3
    assert {item["run_id"] for item in data["items"]} == {
        "run-2026-05-25-0900",
        "run-2026-05-26-0900",
        "run-2026-05-27-0900",
    }


async def test_list_runs_filters_by_suite():
    out = await qa_list_runs(
        ListRunsInput(suite="nonexistent-suite", response_format=ResponseFormat.JSON)
    )
    data = json.loads(out)
    assert data["total"] == 0


async def test_list_runs_paginates():
    out = await qa_list_runs(
        ListRunsInput(limit=2, offset=0, response_format=ResponseFormat.JSON)
    )
    data = json.loads(out)
    assert data["count"] == 2
    assert data["has_more"] is True
    assert data["next_offset"] == 2


async def test_list_runs_markdown_contains_table():
    out = await qa_list_runs(ListRunsInput(response_format=ResponseFormat.MARKDOWN))
    assert "| run_id |" in out
    assert "run-2026-05-25-0900" in out


# ─── qa_get_run ──────────────────────────────────────────────────────────────


async def test_get_run_markdown_omits_passed_by_default():
    out = await qa_get_run(GetRunInput(run_id="run-2026-05-25-0900"))
    assert "## Failures" in out
    assert "test_delete_insurance" in out
    # The 4 passed tests should NOT appear by default.
    assert "test_create_insurance" not in out


async def test_get_run_markdown_includes_passed_when_requested():
    out = await qa_get_run(
        GetRunInput(run_id="run-2026-05-25-0900", include_passed=True)
    )
    assert "test_create_insurance" in out


async def test_get_run_unknown_returns_actionable_error():
    out = await qa_get_run(GetRunInput(run_id="nope"))
    assert out.startswith("Error:")
    assert "qa_list_runs" in out  # actionable: tells the agent what to do next


# ─── qa_compare_runs ─────────────────────────────────────────────────────────


async def test_compare_runs_markdown_reports_regression():
    out = await qa_compare_runs(
        CompareRunsInput(
            run_a="run-2026-05-25-0900",
            run_b="run-2026-05-26-0900",
        )
    )
    assert "1 regression" in out
    assert "test_search_insurances" in out


async def test_compare_runs_json_has_full_structure():
    out = await qa_compare_runs(
        CompareRunsInput(
            run_a="run-2026-05-25-0900",
            run_b="run-2026-05-27-0900",
            response_format=ResponseFormat.JSON,
        )
    )
    data = json.loads(out)
    assert "counts" in data
    assert "regressions" in data
    assert "persistent_failures" in data


async def test_compare_runs_unknown_run_id():
    out = await qa_compare_runs(
        CompareRunsInput(run_a="nope", run_b="run-2026-05-26-0900")
    )
    assert out.startswith("Error:")


# ─── Resource ────────────────────────────────────────────────────────────────


async def test_resource_returns_companion_md_when_present():
    md = await run_summary("run-2026-05-25-0900")
    assert "Run run-2026-05-25-0900" in md


async def test_resource_synthesizes_md_when_companion_missing():
    md = await run_summary("run-2026-05-26-0900")
    assert "run-2026-05-26-0900" in md
    assert "Failures" in md


async def test_resource_error_for_unknown_run():
    md = await run_summary("nope")
    assert "Error" in md
