"""Metamorphic relations over compare_runs.

These are the invariants any correct implementation must satisfy. They survive
refactors and catch subtle bugs that example-based tests miss.

Relations checked:
    M1. Identity:    compare(A, A) reports zero changes.
    M2. Symmetry:    compare(A, B).regressions ↔ compare(B, A).fixes.
                     compare(A, B).new_tests   ↔ compare(B, A).removed_tests.
                     persistent_failures are stable under swap.
    M3. Bounded:     |regressions| + |fixes| ≤ |tests_in_either|.
    M4. Coverage:    every test_id appears in at most one bucket per direction.
"""

from __future__ import annotations

import pytest

from qa_toolkit_mcp.compare import compare_runs


# ─── M1. Identity ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("run_fixture", ["run_25", "run_26", "run_27"])
def test_identity_no_changes(request, run_fixture):
    run = request.getfixturevalue(run_fixture)
    cmp = compare_runs(run, run)
    assert cmp.counts.regressions == 0
    assert cmp.counts.fixes == 0
    assert cmp.counts.new_tests == 0
    assert cmp.counts.removed_tests == 0
    assert cmp.counts.other_changes == 0
    # Persistent failures may still show — a test that failed in this run is
    # still failing in itself. That's expected: persistent = "failed in both".
    for p in cmp.persistent_failures:
        assert p.same_error is True


# ─── M2. Symmetry ────────────────────────────────────────────────────────────


def _ids(items, attr="test_id"):
    return {getattr(t, attr) for t in items}


def test_symmetry_regressions_become_fixes(run_25, run_26):
    forward = compare_runs(run_25, run_26)
    reverse = compare_runs(run_26, run_25)
    assert _ids(forward.regressions) == _ids(reverse.fixes)
    assert _ids(forward.fixes) == _ids(reverse.regressions)


def test_symmetry_new_becomes_removed(run_26, run_27):
    forward = compare_runs(run_26, run_27)
    reverse = compare_runs(run_27, run_26)
    assert _ids(forward.new_tests) == _ids(reverse.removed_tests)
    assert _ids(forward.removed_tests) == _ids(reverse.new_tests)


def test_symmetry_persistent_failures_stable(run_25, run_26):
    forward = compare_runs(run_25, run_26)
    reverse = compare_runs(run_26, run_25)
    assert _ids(forward.persistent_failures) == _ids(reverse.persistent_failures)
    # same_error categorization is fingerprint-based and direction-independent.
    f_same = {p.test_id for p in forward.persistent_failures if p.same_error}
    r_same = {p.test_id for p in reverse.persistent_failures if p.same_error}
    assert f_same == r_same


# ─── M3. Bounded ─────────────────────────────────────────────────────────────


def test_bounded_by_universe(run_25, run_27):
    cmp = compare_runs(run_25, run_27)
    universe = {t.id for t in run_25.tests} | {t.id for t in run_27.tests}
    bucket_total = (
        cmp.counts.regressions
        + cmp.counts.fixes
        + cmp.counts.persistent_failures
        + cmp.counts.new_tests
        + cmp.counts.removed_tests
        + cmp.counts.other_changes
    )
    assert bucket_total <= len(universe)


# ─── M4. Coverage — each test_id in at most one bucket ───────────────────────


def test_each_test_appears_in_at_most_one_bucket(run_25, run_27):
    cmp = compare_runs(run_25, run_27)
    buckets = [
        _ids(cmp.regressions),
        _ids(cmp.fixes),
        _ids(cmp.persistent_failures),
        _ids(cmp.new_tests),
        _ids(cmp.removed_tests),
        _ids(cmp.other_changes),
    ]
    # Pairwise disjoint.
    for i, a in enumerate(buckets):
        for b in buckets[i + 1 :]:
            assert a.isdisjoint(b), f"Overlap between bucket {i} and another: {a & b}"
