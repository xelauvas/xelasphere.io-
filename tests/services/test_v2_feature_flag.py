"""W-01 — SCAFFOLD_V2_ENABLED feature flag wiring.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §9
Plan: docs/superpowers/plans/2026-04-15-structure-intelligence-v2-implementation.md W-01
"""
import importlib
import os
from unittest.mock import patch


def _reload_config():
    """Reload config module so env var changes take effect."""
    from app import config as _cfg
    importlib.reload(_cfg)
    return _cfg.Config


def test_flag_defaults_false_when_unset(monkeypatch):
    """When the env var is truly absent, the parsing default is False.

    We patch dotenv.load_dotenv AT THE SOURCE (not on app.config) so
    the Config module reload doesn't re-import the original and shadow
    the 'unset' case. A live-QA .env that sets SCAFFOLD_V2_ENABLED=true
    would otherwise pollute this test.
    """
    monkeypatch.delenv("SCAFFOLD_V2_ENABLED", raising=False)
    with patch("dotenv.load_dotenv", lambda *a, **kw: None):
        Config = _reload_config()
    assert Config.SCAFFOLD_V2_ENABLED is False


def test_flag_reads_env_var_true(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    Config = _reload_config()
    assert Config.SCAFFOLD_V2_ENABLED is True


def test_flag_reads_env_var_false(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")
    Config = _reload_config()
    assert Config.SCAFFOLD_V2_ENABLED is False


def test_flag_case_insensitive_true(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "TRUE")
    Config = _reload_config()
    assert Config.SCAFFOLD_V2_ENABLED is True


def test_flag_non_truthy_string_is_false(monkeypatch):
    """Anything that isn't 'true' (case-insensitive) → False. No surprises."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "1")
    Config = _reload_config()
    assert Config.SCAFFOLD_V2_ENABLED is False
