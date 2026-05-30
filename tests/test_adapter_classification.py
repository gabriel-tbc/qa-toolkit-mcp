"""Tests for the classification.json → RunReport adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qa_toolkit_mcp import adapter_classification, storage
from qa_toolkit_mcp.models import Classification, RunReport, TestStatus


# ─── Format detection ────────────────────────────────────────────────────────


def test_is_classification_format_true_for_classification_report(classification_runs_dir):
    raw = json.loads((classification_runs_dir / "search-25_classification.json").read_text())
    assert adapter_classification.is_classification_format(raw) is True


def test_is_classification_format_false_for_native_v1_report(native_runs_dir):
    raw = json.loads((native_runs_dir / "run-2026-05-25-0900.json").read_text())
    assert adapter_classification.is_classification_format(raw) is False


def test_is_classification_format_false_for_random_dict():
    assert adapter_classification.is_classification_format({"foo": "bar"}) is False
    assert adapter_classification.is_classification_format([]) is False
    assert adapter_classification.is_classification_format(None) is False


# ─── Case-id extraction ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("test_search_insurances_positive[SI-POS-001]", "SI-POS-001"),
        ("test_search_insurances_negative[SI-NEG-004]", "SI-NEG-004"),
        ("test_no_parameters", "test_no_parameters"),
        ("nested[outer[inner]]", "outer[inner"),  # greedy-stop at first ]
    ],
)
def test_extract_case_id(name, expected):
    assert adapter_classification._extract_case_id(name) == expected


# ─── Fingerprint stability ───────────────────────────────────────────────────


def test_fingerprint_is_stable_for_same_normalized_input():
    fp1 = adapter_classification._compute_fingerprint(
        "SI-POS-001", "AssertionError", "Expected 200, got 500 at 2026-05-25T12:00:00Z"
    )
    fp2 = adapter_classification._compute_fingerprint(
        "SI-POS-001", "AssertionError", "Expected 200, got 500 at 2026-05-26T09:30:00Z"
    )
    # Different timestamps → same fingerprint (timestamps are normalized).
    assert fp1 == fp2


def test_fingerprint_differs_for_different_status_codes():
    fp_500 = adapter_classification._compute_fingerprint(
        "SI-POS-001", "AssertionError", "Expected 200, got 500"
    )
    fp_404 = adapter_classification._compute_fingerprint(
        "SI-POS-001", "AssertionError", "Expected 200, got 404"
    )
    # Numbers are NOT normalized — different errors are different fingerprints.
    assert fp_500 != fp_404


def test_fingerprint_differs_for_different_test_ids():
    fp_a = adapter_classification._compute_fingerprint("SI-POS-001", "Err", "boom")
    fp_b = adapter_classification._compute_fingerprint("SI-POS-002", "Err", "boom")
    assert fp_a != fp_b


# ─── End-to-end conversion ───────────────────────────────────────────────────


def test_load_run_with_xml_is_exhaustive(use_classification_runs):
    run = storage.load_run("search-25_classification")
    assert isinstance(run, RunReport)
    assert run.is_exhaustive is True
    assert run.schema_version == "1.1"
    assert run.suite == "search-25"
    assert run.summary.total == 8
    assert run.summary.passed == 5
    assert run.summary.failed == 3
    # XML provided 8 testcases — they should all be present.
    assert len(run.tests) == 8


def test_load_run_with_xml_attaches_classification_to_failures(use_classification_runs):
    run = storage.load_run("search-25_classification")
    by_id = {t.id: t for t in run.tests}
    assert by_id["SI-POS-006"].classification == Classification.BUG_REAL
    assert by_id["SI-POS-007"].classification == Classification.UNCLASSIFIED
    assert by_id["SI-POS-005"].classification == Classification.VALIDATION_EXPECTED
    # Passed tests should have no classification.
    assert by_id["SI-POS-001"].classification is None


def test_load_run_with_xml_failures_have_fingerprints(use_classification_runs):
    run = storage.load_run("search-25_classification")
    failures = [t for t in run.tests if t.status == TestStatus.FAILED]
    assert len(failures) == 3
    for t in failures:
        assert t.error is not None
        assert len(t.error.fingerprint) >= 8


def test_load_run_without_xml_is_not_exhaustive(use_classification_runs):
    run = storage.load_run("partial-only_classification")
    assert run.is_exhaustive is False
    # Only failures listed when no XML is found.
    assert len(run.tests) == 1
    assert run.tests[0].id == "SI-POS-001"
    assert run.tests[0].classification == Classification.BUG_REAL


def test_load_run_error_type_inference_for_backend_exception(use_classification_runs):
    run = storage.load_run("search-25_classification")
    by_id = {t.id: t for t in run.tests}
    # The raw_result mentions SQLGrammarException — adapter should detect it.
    assert by_id["SI-POS-006"].error is not None
    assert by_id["SI-POS-006"].error.type == "SQLGrammarException"


def test_load_run_unknown_classification_value_becomes_none(tmp_path, monkeypatch):
    """If a classification report uses a value not in the catalog, the test
    still loads but the classification field becomes None (safe fallback)."""
    raw = {
        "fecha": "2026-05-27 09:00:00",
        "tests": 1, "passed": 0, "failed": 1, "errors": 0,
        "classifications": [
            {
                "case_id": "X1", "description": "x", "test": "test_x",
                "status": "failed",
                "classification": "totally-unknown-value",
                "message": "boom",
                "payload": {}, "expected": {},
                "result_summary": {}, "raw_result": "",
            }
        ],
    }
    p = tmp_path / "weird_classification.json"
    p.write_text(json.dumps(raw))
    monkeypatch.setenv("QA_TOOLKIT_RUNS_DIR", str(tmp_path))

    run = storage.load_run("weird_classification")
    assert run.tests[0].classification is None
