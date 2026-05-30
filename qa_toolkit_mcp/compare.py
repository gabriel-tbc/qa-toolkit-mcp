"""Regression analysis between two run reports.

This is the heart of the MCP. It does NOT compute a raw diff — it categorizes
test transitions into the buckets a QA engineer actually cares about:

    REGRESSION         passed in A, failed/error in B         ← the expensive signal
    FIX                failed/error in A, passed in B         ← validates changes
    PERSISTENT_FAILURE failed in both                          ← known/ongoing
        same_error     fingerprints match → same root cause
        different_error fingerprints differ → root cause changed
    NEW_TEST           absent in A, present in B (only when A.is_exhaustive)
    REMOVED_TEST       present in A, absent in B (only when B.is_exhaustive)
    OTHER_CHANGE       transitions involving skipped (low priority surface)

And, orthogonal to status:

    CLASSIFICATION_CHANGE   QA-assigned classification differs between runs

`is_exhaustive` semantics: when a report is *not* exhaustive (e.g., a
classification report without a JUnit XML hermano), absent tests are NOT
interpreted as removed/new. Instead they are treated as passed-implicit (so
failing-in-A → absent-in-B becomes a FIX, not a REMOVED).

All functions here are pure: input = two RunReport, output = ComparisonResult.
No I/O. This is what makes them trivial to test and to exercise with
metamorphic relations.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .models import Classification, RunReport, TestCase, TestError, TestStatus


# ─── Output models ───────────────────────────────────────────────────────────


class RunRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite: str
    started_at: str
    is_exhaustive: bool = True


class TestTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str
    status_a: TestStatus
    status_b: TestStatus
    error_b: Optional[TestError] = None


class PersistentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str
    error_a: Optional[TestError]
    error_b: Optional[TestError]
    same_error: bool


class TestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str
    status: TestStatus
    error: Optional[TestError] = None


class ClassificationChange(BaseModel):
    """A test whose QA-assigned classification differs between runs.

    Orthogonal to status: a test can be in `persistent_failures` AND
    `classification_changes` simultaneously — that means "still failing AND
    we changed our mind about why".
    """

    model_config = ConfigDict(extra="forbid")

    test_id: str
    classification_a: Optional[Classification]
    classification_b: Optional[Classification]


class ComparisonCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regressions: int
    fixes: int
    persistent_failures: int
    persistent_same_error: int
    persistent_different_error: int
    new_tests: int
    removed_tests: int
    other_changes: int
    classification_changes: int = 0


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_a: RunRef
    run_b: RunRef
    counts: ComparisonCounts
    regressions: list[TestTransition] = Field(default_factory=list)
    fixes: list[TestTransition] = Field(default_factory=list)
    persistent_failures: list[PersistentFailure] = Field(default_factory=list)
    new_tests: list[TestSnapshot] = Field(default_factory=list)
    removed_tests: list[TestSnapshot] = Field(default_factory=list)
    other_changes: list[TestTransition] = Field(default_factory=list)
    classification_changes: list[ClassificationChange] = Field(default_factory=list)


# ─── Status classification ───────────────────────────────────────────────────


_FAILING = {TestStatus.FAILED, TestStatus.ERROR}


def _is_failing(s: TestStatus) -> bool:
    return s in _FAILING


def _is_passing(s: TestStatus) -> bool:
    return s == TestStatus.PASSED


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ref(run: RunReport) -> RunRef:
    return RunRef(
        run_id=run.run_id,
        suite=run.suite,
        started_at=run.started_at.isoformat(),
        is_exhaustive=run.is_exhaustive,
    )


def _index(run: RunReport) -> dict[str, TestCase]:
    return {t.id: t for t in run.tests}


def _snapshot(t: TestCase) -> TestSnapshot:
    return TestSnapshot(test_id=t.id, status=t.status, error=t.error)


# ─── Core comparison ─────────────────────────────────────────────────────────


def compare_runs(run_a: RunReport, run_b: RunReport) -> ComparisonResult:
    """Categorize the differences between two runs.

    Conventions:
    - A is the baseline / older.
    - B is the newer.
    - When `is_exhaustive=False` on either side, absent tests are treated as
      passed-implicit (rather than removed/new), reflecting the convention of
      classification reports that only list failures.
    """
    a_by_id = _index(run_a)
    b_by_id = _index(run_b)

    regressions: list[TestTransition] = []
    fixes: list[TestTransition] = []
    persistent: list[PersistentFailure] = []
    new_tests: list[TestSnapshot] = []
    removed_tests: list[TestSnapshot] = []
    other_changes: list[TestTransition] = []
    classification_changes: list[ClassificationChange] = []

    all_ids = sorted(set(a_by_id) | set(b_by_id))

    for tid in all_ids:
        a = a_by_id.get(tid)
        b = b_by_id.get(tid)

        # ─── only in A ───────────────────────────────────────────────────────
        if b is None:
            assert a is not None
            if run_b.is_exhaustive:
                removed_tests.append(_snapshot(a))
            else:
                # Treat absent-in-B as passed-implicit.
                if _is_failing(a.status):
                    fixes.append(
                        TestTransition(
                            test_id=tid,
                            status_a=a.status,
                            status_b=TestStatus.PASSED,
                            error_b=None,
                        )
                    )
                # passed→passed and skipped→passed are not interesting enough to report.
            continue

        # ─── only in B ───────────────────────────────────────────────────────
        if a is None:
            assert b is not None
            if run_a.is_exhaustive:
                new_tests.append(_snapshot(b))
            else:
                # Treat absent-in-A as passed-implicit.
                if _is_failing(b.status):
                    regressions.append(
                        TestTransition(
                            test_id=tid,
                            status_a=TestStatus.PASSED,
                            status_b=b.status,
                            error_b=b.error,
                        )
                    )
            # Note: if both runs are non-exhaustive and a test is "only in A",
            # we can't know if it disappeared or just stopped failing. We
            # default above to assuming passed-implicit in B; symmetry holds.
            continue

        # ─── both present — status comparison ────────────────────────────────
        if a.status == b.status:
            if _is_failing(a.status):
                same = (
                    a.error is not None
                    and b.error is not None
                    and a.error.fingerprint == b.error.fingerprint
                )
                persistent.append(
                    PersistentFailure(
                        test_id=tid,
                        error_a=a.error,
                        error_b=b.error,
                        same_error=same,
                    )
                )
        else:
            if _is_passing(a.status) and _is_failing(b.status):
                regressions.append(
                    TestTransition(
                        test_id=tid,
                        status_a=a.status,
                        status_b=b.status,
                        error_b=b.error,
                    )
                )
            elif _is_failing(a.status) and _is_passing(b.status):
                fixes.append(
                    TestTransition(
                        test_id=tid,
                        status_a=a.status,
                        status_b=b.status,
                        error_b=None,
                    )
                )
            else:
                other_changes.append(
                    TestTransition(
                        test_id=tid,
                        status_a=a.status,
                        status_b=b.status,
                        error_b=b.error if _is_failing(b.status) else None,
                    )
                )

        # ─── both present — classification comparison ────────────────────────
        # Orthogonal to status. Reported even when status is unchanged.
        if a.classification != b.classification:
            classification_changes.append(
                ClassificationChange(
                    test_id=tid,
                    classification_a=a.classification,
                    classification_b=b.classification,
                )
            )

    persistent_same = sum(1 for p in persistent if p.same_error)
    counts = ComparisonCounts(
        regressions=len(regressions),
        fixes=len(fixes),
        persistent_failures=len(persistent),
        persistent_same_error=persistent_same,
        persistent_different_error=len(persistent) - persistent_same,
        new_tests=len(new_tests),
        removed_tests=len(removed_tests),
        other_changes=len(other_changes),
        classification_changes=len(classification_changes),
    )

    return ComparisonResult(
        run_a=_ref(run_a),
        run_b=_ref(run_b),
        counts=counts,
        regressions=regressions,
        fixes=fixes,
        persistent_failures=persistent,
        new_tests=new_tests,
        removed_tests=removed_tests,
        other_changes=other_changes,
        classification_changes=classification_changes,
    )
