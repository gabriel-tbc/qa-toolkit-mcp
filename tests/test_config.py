"""Tests for the configuration loader.

Validates the precedence rules:
    real env var > .env in project root > .env in CWD > hardcoded default
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_toolkit_mcp import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test with a clean slate (no QA_TOOLKIT_* vars, fresh dotenv flag).
    Conftest's autouse sets QA_TOOLKIT_RUNS_DIR to the native fixtures dir; we
    undo that here so config tests can probe precedence rules from a blank state.
    """
    config.reset_dotenv_loaded_flag()
    monkeypatch.delenv(config.ENV_RUNS_DIR, raising=False)
    monkeypatch.delenv(config.ENV_LOG_LEVEL, raising=False)
    yield
    config.reset_dotenv_loaded_flag()


@pytest.fixture
def isolated_project_root(monkeypatch, tmp_path):
    """Redirect config._PROJECT_ROOT to tmp_path so the real project's .env
    cannot leak into tests. Returns the redirected path."""
    monkeypatch.setattr(config, "_PROJECT_ROOT", tmp_path)
    return tmp_path


# ─── Precedence rules ────────────────────────────────────────────────────────


def test_real_env_var_wins_over_dotenv_file(
    isolated_project_root, monkeypatch, tmp_path
):
    # .env in (redirected) project root says one thing.
    (isolated_project_root / ".env").write_text(
        f"{config.ENV_RUNS_DIR}=C:\\from\\dotenv\n"
    )
    # Real env var says another.
    target = tmp_path / "from_real_env"
    monkeypatch.setenv(config.ENV_RUNS_DIR, str(target))

    settings = config.get_settings()
    assert settings.runs_dir == target.resolve()


def test_dotenv_in_project_root_is_used_when_no_real_env_var(
    isolated_project_root,
):
    target = isolated_project_root / "from_project_root_dotenv"
    target.mkdir()
    (isolated_project_root / ".env").write_text(f"{config.ENV_RUNS_DIR}={target}\n")

    settings = config.get_settings()
    assert settings.runs_dir == target.resolve()


def test_dotenv_in_cwd_used_when_no_project_root_dotenv(
    isolated_project_root, monkeypatch, tmp_path
):
    cwd_with_env = tmp_path / "elsewhere"
    cwd_with_env.mkdir()
    target = cwd_with_env / "from_cwd_dotenv"
    target.mkdir()
    (cwd_with_env / ".env").write_text(f"{config.ENV_RUNS_DIR}={target}\n")
    monkeypatch.chdir(cwd_with_env)

    settings = config.get_settings()
    assert settings.runs_dir == target.resolve()


def test_default_runs_dir_when_nothing_set(
    isolated_project_root, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    settings = config.get_settings()
    assert settings.runs_dir == (tmp_path / "runs").resolve()


# ─── Log level ───────────────────────────────────────────────────────────────


def test_log_level_defaults_to_info(isolated_project_root, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = config.get_settings()
    assert settings.log_level == "INFO"


def test_log_level_from_env_is_uppercased(
    isolated_project_root, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.ENV_LOG_LEVEL, "debug")
    settings = config.get_settings()
    assert settings.log_level == "DEBUG"


# ─── Project root reflects the real package layout ───────────────────────────


def test_project_root_is_the_real_package_parent():
    """No isolation here — we want to verify the unmocked value points at the
    actual project (which has pyproject.toml and the qa_toolkit_mcp module)."""
    settings = config.get_settings()
    assert (settings.project_root / "pyproject.toml").is_file()
    assert (settings.project_root / "qa_toolkit_mcp").is_dir()


# ─── Storage delegates to config ─────────────────────────────────────────────


def test_storage_get_runs_dir_consults_config(
    isolated_project_root, monkeypatch, tmp_path
):
    """The storage module must call config.get_settings(), not read os.environ directly."""
    from qa_toolkit_mcp import storage

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "consult_via_config"
    monkeypatch.setenv(config.ENV_RUNS_DIR, str(target))

    assert storage.get_runs_dir() == target.resolve()
