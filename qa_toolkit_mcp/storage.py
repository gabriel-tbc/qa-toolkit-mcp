"""Storage layer: read run reports from disk.

Reports are discovered by globbing `*.json` in the configured runs directory.
The storage layer auto-detects the report format:

- **Native** (with `schema_version`): validated directly against run-report.v1.json.
- **Classification** (CI/QA pipeline output, with `classifications[]`):
  converted via `adapter_classification.to_run_report`, which also looks for a
  JUnit XML hermano to populate the passed tests.

Companion `.md` files (same stem as the JSON) are read on demand by the
`run://{run_id}/summary.md` resource. They are optional — when missing, the
resource returns a minimal Markdown rendering generated from the JSON itself.

Path safety: all lookups go through `_resolve_run_path` which enforces that
the resolved file lives inside the configured runs directory. This prevents
directory traversal via crafted run_ids (`../../etc/passwd`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from . import adapter_classification, config
from .models import RunReport


class StorageError(Exception):
    """Raised for any storage-layer problem (not-found, malformed, path violation)."""


def get_runs_dir() -> Path:
    """Resolve the active runs directory.

    Delegates to `config.get_settings()` so the resolution rules (env vars,
    `.env` files, defaults) live in a single module. See `config.py`.
    """
    return config.get_settings().runs_dir


def _resolve_run_path(run_id: str, runs_dir: Path, suffix: str = ".json") -> Path:
    """Resolve `{run_id}{suffix}` inside runs_dir, refusing traversal."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise StorageError(f"Invalid run_id: {run_id!r}")

    candidate = (runs_dir / f"{run_id}{suffix}").resolve()
    try:
        candidate.relative_to(runs_dir)
    except ValueError as exc:
        raise StorageError(f"run_id {run_id!r} resolves outside the runs directory") from exc
    return candidate


def list_run_ids(runs_dir: Optional[Path] = None) -> list[str]:
    """Return all run_ids available (stem of every *.json in the runs directory).

    Sorted lexicographically.
    """
    dir_ = runs_dir or get_runs_dir()
    if not dir_.is_dir():
        return []
    return sorted(p.stem for p in dir_.glob("*.json") if p.is_file())


def _load_raw(run_id: str, runs_dir: Path) -> tuple[Path, Any]:
    """Read and JSON-decode a run file. Returns (path, decoded)."""
    path = _resolve_run_path(run_id, runs_dir, suffix=".json")
    if not path.is_file():
        raise StorageError(f"Run not found: {run_id!r} (looked at {path})")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageError(f"Run {run_id!r} is not valid JSON: {exc.msg}") from exc


def load_run(run_id: str, runs_dir: Optional[Path] = None) -> RunReport:
    """Load and validate a single run report by id. Auto-detects format."""
    dir_ = runs_dir or get_runs_dir()
    path, raw = _load_raw(run_id, dir_)

    if adapter_classification.is_classification_format(raw):
        try:
            return adapter_classification.to_run_report(raw, path)
        except (KeyError, ValueError, TypeError) as exc:
            raise StorageError(
                f"Run {run_id!r} looks like a classification report but failed to adapt: {exc}"
            ) from exc

    # Native format.
    try:
        return RunReport.model_validate(raw)
    except ValidationError as exc:
        raise StorageError(
            f"Run {run_id!r} does not conform to run-report.v1: {exc.error_count()} error(s)"
        ) from exc


def load_summary_md(run_id: str, runs_dir: Optional[Path] = None) -> Optional[str]:
    """Return the contents of `{run_id}.md` if present, else None."""
    dir_ = runs_dir or get_runs_dir()
    path = _resolve_run_path(run_id, dir_, suffix=".md")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
