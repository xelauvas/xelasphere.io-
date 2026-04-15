"""W-03 — pytest 'eval' marker is registered and opt-in by default.

Rationale: the eval harness (Phase 2) makes real LLM calls — slow and
costs money. It must not run on default `pytest` invocations.
"""
import configparser
from pathlib import Path


def _load_pytest_ini():
    cfg = configparser.ConfigParser()
    cfg.read(Path(__file__).resolve().parents[2] / "pytest.ini")
    return cfg


def test_eval_marker_is_registered():
    cfg = _load_pytest_ini()
    markers = cfg.get("pytest", "markers", fallback="")
    assert "eval:" in markers, (
        "pytest.ini must register the 'eval' marker — required before "
        "W-11 can land the LLM-backed eval runner."
    )


def test_eval_marker_opt_in_via_addopts():
    """Default pytest run must skip @pytest.mark.eval tests."""
    cfg = _load_pytest_ini()
    addopts = cfg.get("pytest", "addopts", fallback="")
    assert "not eval" in addopts, (
        "pytest.ini addopts must include `-m 'not eval'` so eval tests "
        "only run under explicit `-m eval`."
    )
