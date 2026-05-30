"""Shared test fixtures.

Layout:
    tests/fixtures/runs_native/         — native v1.0 run reports (3 runs)
    tests/fixtures/runs_classification/ — classification.json + JUnit XML pairs

The autouse `_point_storage_at_native_runs` fixture defaults all tests to the
native dir. Tests that exercise the classification adapter override the
QA_TOOLKIT_RUNS_DIR env var explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_toolkit_mcp import storage
from qa_toolkit_mcp.models import RunReport

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
NATIVE_DIR = FIXTURES_ROOT / "runs_native"
CLASSIFICATION_DIR = FIXTURES_ROOT / "runs_classification"


@pytest.fixture
def native_runs_dir() -> Path:
    return NATIVE_DIR


@pytest.fixture
def classification_runs_dir() -> Path:
    return CLASSIFICATION_DIR


@pytest.fixture(autouse=True)
def _point_storage_at_native_runs(monkeypatch):
    """Default: every test sees the native v1.0 fixtures."""
    monkeypatch.setenv("QA_TOOLKIT_RUNS_DIR", str(NATIVE_DIR))


@pytest.fixture
def use_classification_runs(monkeypatch, classification_runs_dir):
    """Opt-in: redirect storage to the classification fixtures for this test."""
    monkeypatch.setenv("QA_TOOLKIT_RUNS_DIR", str(classification_runs_dir))
    return classification_runs_dir


# Native run fixtures (preserved from v1.0 era for the metamorphic suite).
@pytest.fixture
def run_25() -> RunReport:
    return storage.load_run("run-2026-05-25-0900")


@pytest.fixture
def run_26() -> RunReport:
    return storage.load_run("run-2026-05-26-0900")


@pytest.fixture
def run_27() -> RunReport:
    return storage.load_run("run-2026-05-27-0900")
