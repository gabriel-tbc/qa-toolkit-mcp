"""Output formatters: Pydantic models → Markdown or JSON.

Two output modes are supported across all tools (per MCP best practices):
- JSON: full structured payload, for programmatic consumption.
- Markdown: condensed, human-readable, optimized for agent context efficiency.

The Markdown formatters omit verbose metadata and noise (e.g., unchanged
passing tests in compare_runs) by default.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Iterable

from .compare import (
    ClassificationChange,
    ComparisonResult,
    PersistentFailure,
    TestSnapshot,
    TestTransition,
)
from .models import RunReport, TestCase, TestStatus


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


# ─── JSON helpers ────────────────────────────────────────────────────────────


def to_json(model) -> str:
    """Compact JSON serialization for any Pydantic model."""
    return model.model_dump_json(indent=2, exclude_none=True)


# ─── Markdown: run listings ──────────────────────────────────────────────────


def format_run_list_markdown(
    items: list[dict], total: int, offset: int, has_more: bool
) -> str:
    """Format the output of qa_list_runs."""
    if not items:
        return "No runs found."

    lines = [
        f"# Runs ({len(items)} of {total})",
        "",
        "| run_id | suite | started_at | total | passed | failed | skipped | errors |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in items:
        lines.append(
            f"| `{r['run_id']}` "
            f"| {r['suite']} "
            f"| {r['started_at']} "
            f"| {r['summary']['total']} "
            f"| {r['summary']['passed']} "
            f"| {r['summary']['failed']} "
            f"| {r['summary']['skipped']} "
            f"| {r['summary']['errors']} |"
        )
    if has_more:
        lines.append("")
        lines.append(f"_More runs available. Use offset={offset + len(items)} to continue._")
    return "\n".join(lines)


# ─── Markdown: single run ────────────────────────────────────────────────────


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def format_run_markdown(run: RunReport, include_passed: bool = False) -> str:
    """Format the output of qa_get_run."""
    s = run.summary
    lines = [
        f"# Run `{run.run_id}` — {run.suite}",
        "",
        f"- Started: {_fmt_dt(run.started_at)}",
        f"- Finished: {_fmt_dt(run.finished_at)}",
    ]
    if run.git and (run.git.commit or run.git.branch):
        commit = run.git.commit or "—"
        branch = run.git.branch or "—"
        lines.append(f"- Commit: `{commit}` ({branch})")
    lines.append("")
    lines.append(
        f"**{s.total} tests · {s.passed} passed · {s.failed} failed · "
        f"{s.skipped} skipped · {s.errors} errors**"
    )

    failures = [t for t in run.tests if t.status in (TestStatus.FAILED, TestStatus.ERROR)]
    if failures:
        lines += ["", "## Failures", ""]
        for t in failures:
            lines.append(_fmt_test_line(t))

    if include_passed:
        passed = [t for t in run.tests if t.status == TestStatus.PASSED]
        if passed:
            lines += ["", "## Passed", ""]
            for t in passed:
                lines.append(f"- `{t.id}`")

    return "\n".join(lines)


def _fmt_test_line(t: TestCase) -> str:
    base = f"- `{t.id}`"
    if t.error:
        return f"{base} — **{t.error.type}**: {t.error.message}"
    return f"{base} — {t.status.value}"


# ─── Markdown: comparison ────────────────────────────────────────────────────


def format_comparison_markdown(cmp: ComparisonResult) -> str:
    """Format the output of qa_compare_runs.

    Sections appear only when they have content, in order of priority:
    regressions first (the expensive signal), then fixes, then persistent
    failures, then new/removed, then other changes.
    """
    c = cmp.counts
    headline = (
        f"**{c.regressions} regression(s) · "
        f"{c.fixes} fix(es) · "
        f"{c.persistent_failures} persistent · "
        f"{c.new_tests} new · "
        f"{c.removed_tests} removed · "
        f"{c.classification_changes} reclassified**"
    )
    lines = [
        f"# Compare `{cmp.run_a.run_id}` → `{cmp.run_b.run_id}`",
        "",
        f"Suite: {cmp.run_a.suite} → {cmp.run_b.suite}",
        f"Started: {cmp.run_a.started_at} → {cmp.run_b.started_at}",
    ]
    if not cmp.run_a.is_exhaustive or not cmp.run_b.is_exhaustive:
        lines.append(
            "_Note: at least one run is non-exhaustive (failures-only). "
            "Absent tests are treated as passed-implicit; REMOVED detection is degraded._"
        )
    lines += ["", headline]

    if cmp.regressions:
        lines += ["", "## Regressions (passed → failed)", ""]
        for t in cmp.regressions:
            lines.append(_fmt_transition_line(t))

    if cmp.fixes:
        lines += ["", "## Fixes (failed → passed)", ""]
        for t in cmp.fixes:
            lines.append(f"- `{t.test_id}`")

    if cmp.persistent_failures:
        lines += ["", "## Persistent failures", ""]
        for p in cmp.persistent_failures:
            lines.append(_fmt_persistent_line(p))

    if cmp.new_tests:
        lines += ["", "## New tests", ""]
        for t in cmp.new_tests:
            lines.append(_fmt_snapshot_line(t))

    if cmp.removed_tests:
        lines += ["", "## Removed tests", ""]
        for t in cmp.removed_tests:
            lines.append(f"- `{t.test_id}` (was {t.status.value})")

    if cmp.other_changes:
        lines += ["", "## Other status changes", ""]
        for t in cmp.other_changes:
            lines.append(f"- `{t.test_id}`: {t.status_a.value} → {t.status_b.value}")

    if cmp.classification_changes:
        lines += ["", "## Classification changes (QA oracle changed its mind)", ""]
        for cc in cmp.classification_changes:
            lines.append(_fmt_classification_change_line(cc))

    return "\n".join(lines)


def _fmt_classification_change_line(cc: ClassificationChange) -> str:
    a = cc.classification_a.value if cc.classification_a else "—"
    b = cc.classification_b.value if cc.classification_b else "—"
    return f"- `{cc.test_id}`: {a} → {b}"


def _fmt_transition_line(t: TestTransition) -> str:
    base = f"- `{t.test_id}`"
    if t.error_b:
        return f"{base} — **{t.error_b.type}**: {t.error_b.message}"
    return f"{base} — {t.status_a.value} → {t.status_b.value}"


def _fmt_persistent_line(p: PersistentFailure) -> str:
    marker = "same error" if p.same_error else "**error changed**"
    if p.error_b:
        return (
            f"- `{p.test_id}` ({marker}) — "
            f"{p.error_b.type}: {p.error_b.message}"
        )
    return f"- `{p.test_id}` ({marker})"


def _fmt_snapshot_line(t: TestSnapshot) -> str:
    base = f"- `{t.test_id}` ({t.status.value})"
    if t.error:
        return f"{base} — {t.error.type}: {t.error.message}"
    return base


def synth_summary_md_from_run(run: RunReport) -> str:
    """Fallback Markdown summary when no `.md` companion file exists."""
    return format_run_markdown(run, include_passed=False)
