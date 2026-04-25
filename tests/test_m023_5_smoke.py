"""M0.23.5 — chat-flow integration coverage (Phase 1 + 2: infrastructure smoke).

Phase 1: firebase_admin mock fix in conftest.py enables Flask test_client.
Phase 2: exercise the /admin/mempalace-mode endpoint and confirm
         create_app() boots with the M0.18 mempalace wrapping-template
         imports in place.

Phase 3 (chat-endpoint behavior-preservation coverage under mode=off +
legacy=on) requires additional LLM / memory_v2 / memory_v3 mocking;
scoped as follow-on work within M0.23.5 once Phase 1-2 land cleanly.
"""

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# Phase 1 — infrastructure smoke
# ----------------------------------------------------------------------


def test_flask_test_client_fixture_works(client):
    """The client fixture initializes without error.

    Proves that the conftest firebase_admin.exceptions mock addition
    (M0.23.5 Phase 1) actually unblocked the Flask test infrastructure.
    """
    assert client is not None


def test_create_app_boots_with_mempalace_wiring(app):
    """create_app() boots with M0.18 mempalace adapter imports present.

    Backward-compat smoke: verifies that adding
    `from app.mempalace import get_adapter as _mempalace_get_adapter`
    and
    `from app.mempalace.feature_flag import mode as _mempalace_mode, ...`
    at the top of chat_routes.py doesn't break app startup. Default
    mode=off state should be the shipped reality for pre-P0.
    """
    assert app is not None
    assert "chat" in app.blueprints


# ----------------------------------------------------------------------
# Phase 2 — /admin/mempalace-mode probe endpoint
# ----------------------------------------------------------------------
# Reactivates the M0.19.3 test dropped due to broken firebase mock.


def test_mempalace_mode_probe_defaults_to_off(client, monkeypatch):
    """With MEMPALACE_MODE unset, probe returns 'off' per M0.13 default."""
    monkeypatch.delenv("MEMPALACE_MODE", raising=False)
    resp = client.get("/admin/mempalace-mode")
    assert resp.status_code == 200
    assert resp.get_json() == {"mode": "off"}


@pytest.mark.parametrize("target", ["off", "shadow", "active"])
def test_mempalace_mode_probe_reflects_env_var(client, monkeypatch, target):
    """Env var changes reflected on next call (M0.13 contract: mode()
    reads env every call, not cached at module-import). Core of why
    the mempalace-mode rotation script can restart xelasphere and
    poll this endpoint to verify the change.
    """
    monkeypatch.setenv("MEMPALACE_MODE", target)
    resp = client.get("/admin/mempalace-mode")
    assert resp.status_code == 200
    assert resp.get_json() == {"mode": target}


def test_mempalace_mode_probe_permissive_parsing(client, monkeypatch):
    """M0.13 parsing: whitespace-trim + case-insensitive. Operator-paste
    patterns like 'MEMPALACE_MODE= SHADOW ' normalize, don't raise.
    """
    monkeypatch.setenv("MEMPALACE_MODE", " SHADOW ")
    resp = client.get("/admin/mempalace-mode")
    assert resp.status_code == 200
    assert resp.get_json() == {"mode": "shadow"}


def test_mempalace_mode_probe_requires_no_auth(client):
    """Endpoint is not @login_required — local-root probe from the
    /usr/local/bin/mempalace-mode rotation script runs without a
    session cookie. Exposes only which mode is set, already public
    info via /etc/xelasphere/mempalace.env.

    If this starts failing with 302/401, the endpoint got
    @login_required by accident and the rotation script breaks.
    """
    resp = client.get("/admin/mempalace-mode")
    # 200 OK, not a 302 redirect to login or 401 unauthorized.
    assert resp.status_code == 200
