"""Configuration loader.

Loads settings for the MCP server. Single source of truth that the rest of the
package consults via `get_settings()`.

Resolution order (highest priority first):

    1. Real OS environment variables (e.g., set by claude_desktop_config.json,
       by the user's shell, or by the Inspector UI).
    2. `.env` file in the project root (next to pyproject.toml). One per
       checkout / machine — gitignored so each PC can have its own.
    3. `.env` file in the current working directory (fallback for ad-hoc use).
    4. Hardcoded defaults defined here.

`python-dotenv` is invoked with `override=False`, so real env vars always win
over file-based ones. This means a user can override any setting on the fly
(`$env:QA_TOOLKIT_RUNS_DIR = "..."`) without editing the `.env`.

Only the `.env` loading is cached (once per process — they don't change
mid-run). The Settings object is recomputed on each `get_settings()` call so
that test fixtures using `monkeypatch.setenv` work naturally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Where this file lives → its parent is the package dir → its parent is project root.
_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent

ENV_RUNS_DIR = "QA_TOOLKIT_RUNS_DIR"
ENV_LOG_LEVEL = "QA_TOOLKIT_LOG_LEVEL"

DEFAULT_RUNS_SUBDIR = "runs"
DEFAULT_LOG_LEVEL = "INFO"

_dotenv_loaded = False


@dataclass(frozen=True)
class Settings:
    """Effective configuration snapshot."""

    runs_dir: Path
    log_level: str
    project_root: Path


def _ensure_dotenv_loaded() -> None:
    """Apply `.env` files in priority order, exactly once per process."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    candidates: list[Path] = [
        _PROJECT_ROOT / ".env",
        Path.cwd() / ".env",
    ]
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        load_dotenv(dotenv_path=resolved, override=False)
    _dotenv_loaded = True


def reset_dotenv_loaded_flag() -> None:
    """For tests that need to re-trigger .env loading after fixture setup."""
    global _dotenv_loaded
    _dotenv_loaded = False


def _resolve_runs_dir() -> Path:
    raw = os.environ.get(ENV_RUNS_DIR)
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / DEFAULT_RUNS_SUBDIR).resolve()


def get_settings() -> Settings:
    """Return the effective settings. Reads env vars fresh on every call."""
    _ensure_dotenv_loaded()
    return Settings(
        runs_dir=_resolve_runs_dir(),
        log_level=os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL).upper(),
        project_root=_PROJECT_ROOT,
    )
