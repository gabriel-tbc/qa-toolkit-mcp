"""Capa 1 — compare_runs as a pure function.

Anchored on the fixture story (see tests/fixtures/runs/):
    run-25: baseline. test_delete fails (fingerprint: del-204-vs-200).
    run-26: REGRESSION on test_search_insurances (passed → failed).
            test_delete keeps failing (same fingerprint).
    run-27: FIX on test_search_insurances (failed → passed).
            test_delete still failing (same fingerprint).
            NEW TEST test_list_tools_count (and it fails).
"""

from __future__ import annotations

from qa_toolkit_mcp.compare import compare_runs


# ─── 25 → 26: regression scenario ────────────────────────────────────────────


def test_25_to_26_detects_search_regression(run_25, run_26):
    cmp = compare_runs(run_25, run_26)

    regression_ids = [t.test_id for t in cmp.regressions]
    assert "tests/test_api.py::test_search_insurances" in regression_ids
    assert cmp.counts.regressions == 1


def test_25_to_26_no_fixes(run_25, run_26):
    cmp = compare_runs(run_25, run_26)
    assert cmp.counts.fixes == 0
    assert cmp.fixes == []


def test_25_to_26_delete_is_persistent_same_error(run_25, run_26):
    cmp = compare_runs(run_25, run_26)
    persistent_ids = {p.test_id: p for p in cmp.persistent_failures}
    assert "tests/test_api.py::test_delete_insurance" in persistent_ids
    p = persistent_ids["tests/test_api.py::test_delete_insurance"]
    assert p.same_error is True
    assert cmp.counts.persistent_same_error == 1


def test_25_to_26_no_new_or_removed_tests(run_25, run_26):
    cmp = compare_runs(run_25, run_26)
    assert cmp.counts.new_tests == 0
    assert cmp.counts.removed_tests == 0


# ─── 26 → 27: fix + new test scenario ────────────────────────────────────────


def test_26_to_27_search_is_a_fix(run_26, run_27):
    cmp = compare_runs(run_26, run_27)
    fix_ids = [t.test_id for t in cmp.fixes]
    assert "tests/test_api.py::test_search_insurances" in fix_ids
    assert cmp.counts.fixes == 1


def test_26_to_27_introduces_new_test(run_26, run_27):
    cmp = compare_runs(run_26, run_27)
    new_ids = [t.test_id for t in cmp.new_tests]
    assert "tests/test_mcp.py::test_list_tools_count" in new_ids
    assert cmp.counts.new_tests == 1


def test_26_to_27_no_regressions(run_26, run_27):
    """New tests that fail count as new_tests, not regressions (they had no prior baseline)."""
    cmp = compare_runs(run_26, run_27)
    assert cmp.counts.regressions == 0


def test_26_to_27_delete_still_persistent(run_26, run_27):
    cmp = compare_runs(run_26, run_27)
    persistent_ids = {p.test_id for p in cmp.persistent_failures}
    assert "tests/test_api.py::test_delete_insurance" in persistent_ids
