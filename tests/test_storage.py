"""Capa 1 — storage (pure I/O + validation)."""

from __future__ import annotations

import pytest

from qa_toolkit_mcp import storage
from qa_toolkit_mcp.storage import StorageError


def test_list_run_ids_returns_all_fixtures():
    ids = storage.list_run_ids()
    assert ids == [
        "run-2026-05-25-0900",
        "run-2026-05-26-0900",
        "run-2026-05-27-0900",
    ]


def test_load_run_validates_against_schema(run_25):
    assert run_25.run_id == "run-2026-05-25-0900"
    assert run_25.summary.total == 5
    assert run_25.summary.failed == 1


def test_load_run_missing_raises_with_helpful_message():
    with pytest.raises(StorageError) as exc:
        storage.load_run("does-not-exist")
    assert "not found" in str(exc.value).lower()


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "..\\escape",
        "foo/bar",
        "foo\\bar",
        ".hidden",
        "",
    ],
)
def test_load_run_rejects_traversal_attempts(bad_id):
    with pytest.raises(StorageError):
        storage.load_run(bad_id)


def test_load_summary_md_returns_companion_when_present():
    md = storage.load_summary_md("run-2026-05-25-0900")
    assert md is not None
    assert "Run run-2026-05-25-0900" in md


def test_load_summary_md_returns_none_when_absent():
    assert storage.load_summary_md("run-2026-05-26-0900") is None
