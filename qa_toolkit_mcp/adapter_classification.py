"""Adapter: classification.json (+ optional JUnit XML hermano) → RunReport.

A classification pipeline (CI or manual QA) emits per-suite reports of two kinds:

    <suite>_classification.json   — list of FAILURES with QA-assigned classification
    <suite>.xml                   — JUnit XML with every test that ran (passed + failed)

This adapter converts that pair into the canonical RunReport model used
internally by the MCP, so that compare_runs / list_runs / get_run work
uniformly across both formats.

Detection happens by structure (presence of `classifications` key + no
`schema_version`), so storage.load_run can dispatch without the caller having
to know which format a given file is in.

If the JUnit XML hermano is found, the resulting RunReport is `is_exhaustive=True`
and lists every test (passed + failed) with correct status. If not found, only
the failures are emitted and `is_exhaustive=False` — compare_runs adjusts its
semantics accordingly.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    Classification,
    RunReport,
    RunSummary,
    TestCase,
    TestError,
    TestStatus,
)

# ─── Format detection ────────────────────────────────────────────────────────


def is_classification_format(raw: Any) -> bool:
    """True iff `raw` looks like a classification report (failures + labels)."""
    return (
        isinstance(raw, dict)
        and "schema_version" not in raw
        and "classifications" in raw
        and isinstance(raw["classifications"], list)
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────

_CASE_ID_RE = re.compile(r"\[([^\]]+)\]")
_FECHA_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def _extract_case_id(testcase_name: str) -> str:
    """`test_search_insurances_positive[SI-POS-001]` → `SI-POS-001`.

    Falls back to the full name if no bracketed parameter is present.
    """
    m = _CASE_ID_RE.search(testcase_name)
    return m.group(1) if m else testcase_name


def _parse_fecha(value: str) -> datetime:
    """Parse '2026-05-25 12:36:37' (assumed UTC since the producer is in Spain
    and the field has no tz info; we make a documented choice instead of an
    invisible one)."""
    m = _FECHA_RE.match(value.strip())
    if not m:
        # Defensive fallback — datetime.fromisoformat handles many shapes.
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=timezone.utc)


def _normalize_message(msg: str) -> str:
    """Strip volatile substrings so fingerprints are stable across runs.

    Conservative: only timestamps, hex addresses, and UUIDs. Numbers (status
    codes, counts) are KEPT — those usually distinguish real different errors.
    """
    s = msg.lower()
    s = re.sub(
        r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}(\.\d+)?(z|[+-]\d{2}:?\d{2})?",
        "<ts>",
        s,
    )
    s = re.sub(r"0x[0-9a-f]+", "<hex>", s)
    s = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<uuid>",
        s,
    )
    return " ".join(s.split())  # collapse whitespace


def _compute_fingerprint(case_id: str, error_type: str, message: str) -> str:
    payload = f"{case_id}|{error_type}|{_normalize_message(message)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _infer_error_type(entry: dict) -> str:
    """Best-effort error type extraction from a classification entry."""
    msg = (entry.get("message") or "")
    raw = (entry.get("raw_result") or "")
    rsum = entry.get("result_summary") or {}

    if isinstance(rsum, dict) and rsum.get("_kind") == "error":
        # Backend-reported error.
        # Look for an exception name in the raw_result, like "SQLGrammarException".
        m = re.search(r"([A-Z][A-Za-z0-9_]+(?:Exception|Error))", raw)
        if m:
            return m.group(1)
        return "BackendError"

    if msg.startswith("Expected error, got"):
        # Test predicted error but got success — that's an oracle mismatch.
        return "ExpectationMismatch"
    if msg.startswith("Expected success, got"):
        # Test predicted success but got error.
        m = re.search(r"([A-Z][A-Za-z0-9_]+(?:Exception|Error))", raw)
        if m:
            return m.group(1)
        return "ExpectationMismatch"

    return "AssertionError"


def _safe_classification(value: Optional[str]) -> Optional[Classification]:
    """Map a string to Classification, returning None for unknown values."""
    if not value:
        return None
    try:
        return Classification(value)
    except ValueError:
        return None


# ─── JUnit XML reader ────────────────────────────────────────────────────────


def _find_junit_xml(json_path: Path, raw: dict) -> Optional[Path]:
    """Search for a JUnit XML hermano. Candidates:
    1. Same stem, .xml extension.
    2. If stem ends with `_classification`, the version without that suffix.
    3. The `xml_report` field in the header, resolved relative to the JSON's directory.
    """
    candidates: list[Path] = []

    candidates.append(json_path.with_suffix(".xml"))

    if json_path.stem.endswith("_classification"):
        base = json_path.stem[: -len("_classification")]
        candidates.append(json_path.parent / f"{base}.xml")

    hint = raw.get("xml_report")
    if hint:
        hint_path = Path(hint.replace("\\", "/"))
        if hint_path.is_absolute() and hint_path.is_file():
            candidates.append(hint_path)
        else:
            # Resolve relative to the JSON file's directory.
            candidates.append((json_path.parent / hint_path.name).resolve())

    for c in candidates:
        if c.is_file():
            return c
    return None


def _parse_junit_xml(xml_path: Path) -> list[tuple[str, str, Optional[float]]]:
    """Return `[(case_id, status, duration_ms), ...]` for every testcase.

    status is one of TestStatus values.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    if root.tag == "testsuites":
        testsuites = list(root.findall("testsuite"))
    elif root.tag == "testsuite":
        testsuites = [root]
    else:
        # Be permissive; some emitters wrap differently.
        testsuites = list(root.findall(".//testsuite"))

    out: list[tuple[str, str, Optional[float]]] = []
    for ts in testsuites:
        for tc in ts.findall("testcase"):
            name = tc.get("name", "")
            case_id = _extract_case_id(name)
            time_s = tc.get("time")
            duration_ms = float(time_s) * 1000 if time_s else None

            if tc.find("failure") is not None:
                status = TestStatus.FAILED.value
            elif tc.find("error") is not None:
                status = TestStatus.ERROR.value
            elif tc.find("skipped") is not None:
                status = TestStatus.SKIPPED.value
            else:
                status = TestStatus.PASSED.value

            out.append((case_id, status, duration_ms))
    return out


# ─── Main entry point ────────────────────────────────────────────────────────


def to_run_report(raw: dict, source_path: Path) -> RunReport:
    """Convert a parsed classification.json into a RunReport.

    Args:
        raw: The decoded JSON contents.
        source_path: Path to the classification.json on disk. Used to locate
            the JUnit XML hermano and to derive run_id/suite.
    """
    # Identity ----------------------------------------------------------------
    stem = source_path.stem
    run_id = stem
    suite = stem[: -len("_classification")] if stem.endswith("_classification") else stem

    # Timestamp ---------------------------------------------------------------
    started_at = _parse_fecha(raw.get("fecha", ""))

    # Summary -----------------------------------------------------------------
    summary = RunSummary(
        total=int(raw.get("tests", 0)),
        passed=int(raw.get("passed", 0)),
        failed=int(raw.get("failed", 0) or raw.get("failures", 0)),
        skipped=0,  # Not present in this format; default to 0.
        errors=int(raw.get("errors", 0)),
    )

    # Failures (the heart of the classification report) ----------------------
    classifications: list[dict] = raw.get("classifications", []) or []

    failure_cases: dict[str, TestCase] = {}
    for entry in classifications:
        case_id = entry.get("case_id") or _extract_case_id(entry.get("test", ""))
        if not case_id:
            continue

        status_str = (entry.get("status") or "failed").lower()
        try:
            status = TestStatus(status_str)
        except ValueError:
            status = TestStatus.FAILED

        error: Optional[TestError] = None
        if status in (TestStatus.FAILED, TestStatus.ERROR):
            error_type = _infer_error_type(entry)
            message = entry.get("message") or ""
            error = TestError(
                type=error_type,
                message=message,
                fingerprint=_compute_fingerprint(case_id, error_type, message),
            )

        failure_cases[case_id] = TestCase(
            id=case_id,
            name=entry.get("description"),
            status=status,
            duration_ms=None,
            error=error,
            tags=[],
            classification=_safe_classification(entry.get("classification")),
        )

    # JUnit XML — gives us the passed tests too -------------------------------
    junit_path = _find_junit_xml(source_path, raw)
    if junit_path is not None:
        is_exhaustive = True
        tests: list[TestCase] = []
        for case_id, status_str, duration_ms in _parse_junit_xml(junit_path):
            existing = failure_cases.get(case_id)
            if existing is not None:
                # Failure already constructed from classification.json — keep
                # the rich classification metadata; just attach the duration.
                tests.append(existing.model_copy(update={"duration_ms": duration_ms}))
            else:
                tests.append(
                    TestCase(
                        id=case_id,
                        status=TestStatus(status_str),
                        duration_ms=duration_ms,
                    )
                )
    else:
        is_exhaustive = False
        tests = list(failure_cases.values())

    return RunReport(
        schema_version="1.1",
        run_id=run_id,
        suite=suite,
        started_at=started_at,
        finished_at=None,
        is_exhaustive=is_exhaustive,
        summary=summary,
        tests=tests,
    )
