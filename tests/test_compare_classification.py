"""Compare with classification reports — end-to-end story.

Fixture story (see tests/fixtures/runs_classification/):

  search-25:
    SI-POS-001..004 passed
    SI-POS-005 failed (validation-expected)
    SI-POS-006 failed (bug real, SQLGrammarException)
    SI-POS-007 failed (unclassified, SQLGrammarException)
    SI-POS-008 passed

  search-26:
    SI-POS-001..005 passed (SI-POS-005 was a FIX)
    SI-POS-006 failed (bug real, SQLGrammarException)        — persistent same_error
    SI-POS-007 failed (bug real, SQLGrammarException)        — persistent + classification_change
    SI-POS-008 failed (unclassified, timeout)                — REGRESSION
    SI-POS-009 failed (schema-strictness-candidate, encoding) — NEW TEST

Expected from compare(25, 26):
  - 1 regression  (SI-POS-008)
  - 1 fix         (SI-POS-005)
  - 2 persistent  (SI-POS-006, SI-POS-007), both same_error
  - 1 new_test    (SI-POS-009)
  - 0 removed     (both exhaustive)
  - 1 classification_change (SI-POS-007: unclassified → bug real)
"""

from __future__ import annotations

import pytest

from qa_toolkit_mcp import storage
from qa_toolkit_mcp.compare import compare_runs
from qa_toolkit_mcp.models import Classification


@pytest.fixture
def run_search_25(use_classification_runs):
    return storage.load_run("search-25_classification")


@pytest.fixture
def run_search_26(use_classification_runs):
    return storage.load_run("search-26_classification")


def test_both_runs_are_exhaustive(run_search_25, run_search_26):
    assert run_search_25.is_exhaustive
    assert run_search_26.is_exhaustive


def test_regression_detected(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    assert cmp.counts.regressions == 1
    assert cmp.regressions[0].test_id == "SI-POS-008"


def test_fix_detected(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    assert cmp.counts.fixes == 1
    assert cmp.fixes[0].test_id == "SI-POS-005"


def test_persistent_failures_same_error(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    assert cmp.counts.persistent_failures == 2
    assert cmp.counts.persistent_same_error == 2
    ids = {p.test_id for p in cmp.persistent_failures}
    assert ids == {"SI-POS-006", "SI-POS-007"}


def test_new_test(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    assert cmp.counts.new_tests == 1
    assert cmp.new_tests[0].test_id == "SI-POS-009"


def test_no_removed_when_both_exhaustive(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    assert cmp.counts.removed_tests == 0


def test_classification_change_for_si_pos_007(run_search_25, run_search_26):
    cmp = compare_runs(run_search_25, run_search_26)
    changes = {cc.test_id: cc for cc in cmp.classification_changes}
    assert "SI-POS-007" in changes
    assert changes["SI-POS-007"].classification_a == Classification.UNCLASSIFIED
    assert changes["SI-POS-007"].classification_b == Classification.BUG_REAL


def test_classification_change_is_orthogonal_to_persistent(run_search_25, run_search_26):
    """SI-POS-007 appears in BOTH persistent_failures AND classification_changes.
    That's the whole point: status didn't change, but we changed our mind about why."""
    cmp = compare_runs(run_search_25, run_search_26)
    persistent_ids = {p.test_id for p in cmp.persistent_failures}
    change_ids = {cc.test_id for cc in cmp.classification_changes}
    assert "SI-POS-007" in persistent_ids
    assert "SI-POS-007" in change_ids


# ─── Non-exhaustive (no XML) ─────────────────────────────────────────────────


def test_compare_with_non_exhaustive_b_treats_absent_as_passed(
    use_classification_runs,
):
    """When B is non-exhaustive: tests that were failing in A but absent in B
    are interpreted as FIX, not REMOVED."""
    a = storage.load_run("search-25_classification")     # exhaustive (has XML)
    b = storage.load_run("partial-only_classification")  # not exhaustive

    cmp = compare_runs(a, b)
    # A's failures (005, 006, 007) are absent in B's tests list — should
    # become fixes (because B is non-exhaustive, ausencia = passed-implícito).
    assert cmp.counts.removed_tests == 0
    fix_ids = {f.test_id for f in cmp.fixes}
    assert {"SI-POS-005", "SI-POS-006", "SI-POS-007"}.issubset(fix_ids)
