"""M0.17 Stage 1 RED — MempalaceAdapter tests.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4178-4292 (Task M0.17.4).

At Stage 1 RED: tests fail at `MempalaceAdapter(...)` or its method
calls (NotImplementedError). Expected RED shape.

Spec §6 line 565: "test_adapter.py — all six public methods with fake
client; documented fallback on each failure mode."

**Deviations under test (pinned at pre-resolution):**
  - D2: record_turn duplicate_turn_id labeling (distinct from outbox_db_down).
  - D3: record_turn mode-off metric fires at adapter site.
  - D4: wake_up passes session_id through in the body.
  - H5: wake_up returns ctx unmodified in SHADOW mode (caller-contract test).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.mempalace.adapter import DeletionReceipt, MempalaceAdapter
from app.mempalace.feature_flag import MempalaceMode


# --- Fake collaborators ---


class FakeClient:
    """Async client double. Records every call; returns programmable results."""

    def __init__(self):
        self.calls: list[dict] = []
        self.response: Any = None
        self.raise_exc: Exception | None = None

    async def post_json(self, path, body, headers=None):
        self.calls.append({"method": "POST", "path": path, "body": body})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    async def get_json(self, path, headers=None):
        self.calls.append({"method": "GET", "path": path, "body": None})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    async def delete_json(self, path, body=None, headers=None):
        self.calls.append({"method": "DELETE", "path": path, "body": body})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class FakeOutbox:
    """In-memory outbox double. Records inserts; abandon_user tallies."""

    def __init__(self):
        self.inserts: list[dict] = []
        self.abandons: list[str] = []
        self.insert_raise: Exception | None = None

    def insert(self, **kwargs):
        if self.insert_raise is not None:
            raise self.insert_raise
        self.inserts.append(kwargs)

    def abandon_user(self, user_id):
        self.abandons.append(user_id)
        return 0


class FakeShadowSink:
    def __init__(self):
        self.enqueued: list[dict] = []

    def enqueue(self, record):
        self.enqueued.append(record)


class FakeMetrics:
    """Records every metric call so tests can assert label values."""

    def __init__(self):
        self.inserts_dropped_calls: list[str] = []
        self.redaction_hit_calls: list[str] = []
        self.wakeup_skipped_calls: list[str] = []

    def inserts_dropped(self, reason):
        self.inserts_dropped_calls.append(reason)

    def redaction_hit(self, rule):
        self.redaction_hit_calls.append(rule)

    def wakeup_skipped(self, reason):
        self.wakeup_skipped_calls.append(reason)


# --- fixtures ---


@pytest.fixture
def adapter_factory(monkeypatch):
    """Build a MempalaceAdapter with fake collaborators. Test sets mode
    via `MEMPALACE_MODE` env var (reads env every call per M0.13).
    """

    def _build(mode: str = "active", rules: list[dict] | None = None):
        monkeypatch.setenv("MEMPALACE_MODE", mode)
        client = FakeClient()
        outbox = FakeOutbox()
        shadow = FakeShadowSink()
        metrics = FakeMetrics()
        adapter = MempalaceAdapter(
            client=client,
            outbox=outbox,
            shadow_sink=shadow,
            redaction_rules=rules if rules is not None else [],
            metrics=metrics,
        )
        return adapter, client, outbox, shadow, metrics

    return _build


# --- wake_up ---


@pytest.mark.asyncio
async def test_wake_up_off_mode_returns_none(adapter_factory):
    adapter, client, _, _, _ = adapter_factory(mode="off")
    result = await adapter.wake_up("u-1", "hello")
    assert result is None
    assert client.calls == []  # no sidecar call in OFF


@pytest.mark.asyncio
async def test_wake_up_shadow_mode_returns_ctx_unmodified(adapter_factory):
    """H5 caller-contract: SHADOW mode does NOT suppress the fetch.
    Orchestrator is responsible for not injecting (spec §4.2).
    """
    adapter, client, _, _, _ = adapter_factory(mode="shadow")
    client.response = {"user_id": "u-1", "l0_recent": [], "meta": {"hot": True}}
    result = await adapter.wake_up("u-1", "hello")
    assert result is not None
    assert result["user_id"] == "u-1"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_wake_up_returns_none_on_sidecar_failure(adapter_factory):
    """Spec §4.5: sidecar crash / UDS drift / circuit open → None."""
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.raise_exc = RuntimeError("sidecar circuit open")
    result = await adapter.wake_up("u-1", "hello")
    assert result is None


@pytest.mark.asyncio
async def test_wake_up_passes_session_id_in_body(adapter_factory):
    """D4 contract: session_id threads through to sidecar body so
    structured logging at routes/palace.py:243 can correlate turns.
    """
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = {"user_id": "u-1", "l0_recent": [], "meta": {"hot": True}}
    await adapter.wake_up("u-1", "hello", session_id="sess-abc")
    assert len(client.calls) == 1
    body = client.calls[0]["body"]
    assert body.get("session_id") == "sess-abc"
    assert body.get("query") == "hello"


@pytest.mark.asyncio
async def test_wake_up_omits_session_id_when_none(adapter_factory):
    """When caller doesn't pass session_id, adapter omits it (not sends
    null) so sidecar log shows absence-of-session, not None-as-session.
    Sidecar schema has Optional[str] = None default so either works;
    'omit' is the cleaner choice.
    """
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = {"user_id": "u-1", "l0_recent": [], "meta": {"hot": True}}
    await adapter.wake_up("u-1", "hello")
    body = client.calls[0]["body"]
    assert "session_id" not in body or body["session_id"] is None


@pytest.mark.asyncio
async def test_wake_up_posts_to_correct_path(adapter_factory):
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = {"user_id": "u-1", "l0_recent": [], "meta": {"hot": True}}
    await adapter.wake_up("u-42", "hello")
    assert client.calls[0]["path"] == "/palace/u-42/wake-up"


# --- search ---


@pytest.mark.asyncio
async def test_search_off_mode_returns_empty_list(adapter_factory):
    adapter, client, _, _, _ = adapter_factory(mode="off")
    result = await adapter.search("u-1", "query")
    assert result == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_search_returns_empty_on_failure(adapter_factory):
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.raise_exc = RuntimeError("sidecar circuit open")
    result = await adapter.search("u-1", "query")
    assert result == []


@pytest.mark.asyncio
async def test_search_posts_full_body(adapter_factory):
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = []
    await adapter.search("u-1", "hello", wing="conversations", k=3)
    body = client.calls[0]["body"]
    assert body == {"query": "hello", "wing": "conversations", "k": 3}


# --- record_turn (D2 + D3 + general) ---


def _record_turn_args(**overrides):
    base = dict(
        user_id="u-1",
        session_id="sess-1",
        turn_id="turn-1",
        pair_id="pair-1",
        role="user",
        text="hello",
        ts=100.0,
    )
    base.update(overrides)
    return base


def test_record_turn_off_mode_fires_mode_off_metric(adapter_factory):
    """D3 contract: OFF-mode record_turn increments inserts_dropped
    {reason="mode_off"}. Adapter is the single funnel; orchestrator
    call-sites don't have to remember this counter.
    """
    adapter, _, outbox, _, metrics = adapter_factory(mode="off")
    adapter.record_turn(**_record_turn_args())
    assert metrics.inserts_dropped_calls == ["mode_off"]
    assert outbox.inserts == []  # no outbox write in OFF


def test_record_turn_active_mode_writes_to_outbox(adapter_factory):
    adapter, _, outbox, _, _ = adapter_factory(mode="active")
    adapter.record_turn(**_record_turn_args())
    assert len(outbox.inserts) == 1
    row = outbox.inserts[0]
    assert row["turn_id"] == "turn-1"
    assert row["pair_id"] == "pair-1"
    assert row["user_id"] == "u-1"
    assert row["session_id"] == "sess-1"
    assert row["role"] == "user"
    assert row["text"] == "hello"
    assert row["ts"] == 100.0


def test_record_turn_generates_ts_when_omitted(adapter_factory):
    import time as _time

    adapter, _, outbox, _, _ = adapter_factory(mode="active")
    before = _time.time()
    adapter.record_turn(**_record_turn_args(ts=None))
    after = _time.time()
    row = outbox.inserts[0]
    assert before <= row["ts"] <= after


def test_record_turn_shadow_mode_writes_to_outbox(adapter_factory):
    """Spec §3 line 138: shadow mode — writes happen (outbox + sidecar).
    Only read-injection is suppressed.
    """
    adapter, _, outbox, _, _ = adapter_factory(mode="shadow")
    adapter.record_turn(**_record_turn_args())
    assert len(outbox.inserts) == 1


def test_record_turn_integrity_error_labels_duplicate_turn_id(adapter_factory):
    """D2 contract: sqlite3.IntegrityError on duplicate turn_id is
    labeled 'duplicate_turn_id', NOT 'outbox_db_down'. Idempotency-
    semantic pin: record_turn is not idempotent by contract; spec §4.1
    assigns idempotency authority to sidecar turns.sqlite.
    """
    adapter, _, outbox, _, metrics = adapter_factory(mode="active")
    outbox.insert_raise = sqlite3.IntegrityError(
        "UNIQUE constraint failed: outbox.turn_id"
    )
    adapter.record_turn(**_record_turn_args())
    assert metrics.inserts_dropped_calls == ["duplicate_turn_id"]


def test_record_turn_generic_exception_labels_outbox_db_down(adapter_factory):
    """D2 contract: Exception that is NOT IntegrityError labels
    'outbox_db_down'. Label disambiguation matters for ops alerting
    per pre-resolution (alerts like 'DB down rate > threshold' must
    not misfire on caller duplicate-turn_id bugs).
    """
    adapter, _, outbox, _, metrics = adapter_factory(mode="active")
    outbox.insert_raise = RuntimeError("disk gone")
    adapter.record_turn(**_record_turn_args())
    assert metrics.inserts_dropped_calls == ["outbox_db_down"]


def test_record_turn_scrubs_text_via_rules(adapter_factory):
    """Redaction runs before outbox insert per spec §4.1 line 189.
    Rules wire through to scrub with metrics for hit counting.
    """
    rules = [
        {"name": "test_secret", "pattern": "SECRET-[0-9]+", "replace": "[REDACTED]"},
    ]
    adapter, _, outbox, _, metrics = adapter_factory(mode="active", rules=rules)
    adapter.record_turn(**_record_turn_args(text="my SECRET-12345 leak"))
    row = outbox.inserts[0]
    assert "[REDACTED]" in row["text"]
    assert "SECRET-12345" not in row["text"]
    assert metrics.redaction_hit_calls == ["test_secret"]


# --- log_shadow ---


def test_log_shadow_shadow_mode_enqueues(adapter_factory):
    adapter, _, _, shadow, _ = adapter_factory(mode="shadow")
    adapter.log_shadow({"query": "hello"})
    assert shadow.enqueued == [{"query": "hello"}]


def test_log_shadow_active_mode_skips(adapter_factory):
    """log_shadow ONLY enqueues in SHADOW mode; active mode ignores."""
    adapter, _, _, shadow, _ = adapter_factory(mode="active")
    adapter.log_shadow({"query": "hello"})
    assert shadow.enqueued == []


def test_log_shadow_off_mode_skips(adapter_factory):
    adapter, _, _, shadow, _ = adapter_factory(mode="off")
    adapter.log_shadow({"query": "hello"})
    assert shadow.enqueued == []


# --- delete_palace ---


@pytest.mark.asyncio
async def test_delete_palace_abandons_user_outbox_first(adapter_factory):
    """Spec §4.4 two-phase delete: abandon outbox rows BEFORE the
    sidecar DELETE so the flusher doesn't resurrect a palace we're
    about to delete.
    """
    adapter, client, outbox, _, _ = adapter_factory(mode="active")
    client.response = {"status": "deleted", "bytes_removed": 1024}
    receipt = await adapter.delete_palace("u-1", "admin", "gdpr")
    assert outbox.abandons == ["u-1"]
    assert isinstance(receipt, DeletionReceipt)
    assert receipt.status == "deleted"
    assert receipt.bytes_removed == 1024
    # Last call is the DELETE request; body carries requester + reason.
    delete_call = next(c for c in client.calls if c["method"] == "DELETE")
    assert delete_call["path"] == "/palace/u-1"
    assert delete_call["body"] == {"requested_by": "admin", "reason": "gdpr"}


@pytest.mark.asyncio
async def test_delete_palace_proceeds_even_if_abandon_fails(adapter_factory):
    """If abandon_user raises (e.g., DB down), the sidecar DELETE still
    fires. The user-intent is 'delete my data'; a Flask-side outbox
    failure must not block the sidecar-side delete. Flusher retry on
    DB recovery would then try to flush rows against a deleted palace
    and be a 404 — acceptable because the user-data-deletion already
    landed.
    """
    class ExplodingOutbox(FakeOutbox):
        def abandon_user(self, user_id):
            raise RuntimeError("db gone")

    import os
    os.environ["MEMPALACE_MODE"] = "active"
    client = FakeClient()
    client.response = {"status": "deleted", "bytes_removed": 0}
    adapter = MempalaceAdapter(
        client=client,
        outbox=ExplodingOutbox(),
        shadow_sink=FakeShadowSink(),
        redaction_rules=[],
        metrics=FakeMetrics(),
    )
    receipt = await adapter.delete_palace("u-1", "admin", "gdpr")
    assert receipt.status == "deleted"


# --- list_drawers ---


@pytest.mark.asyncio
async def test_list_drawers_uses_get(adapter_factory):
    """Plan review fix #5: GET not POST for list_drawers."""
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = {"drawers": [], "total": 0, "next_offset": None}
    await adapter.list_drawers("u-1", wing="conversations", limit=50, offset=10)
    call = client.calls[0]
    assert call["method"] == "GET"
    assert "/palace/u-1/drawers" in call["path"]
    assert "wing=conversations" in call["path"]
    assert "limit=50" in call["path"]
    assert "offset=10" in call["path"]


@pytest.mark.asyncio
async def test_list_drawers_omits_wing_when_none(adapter_factory):
    """wing=None must not render as `wing=None` in the query string."""
    adapter, client, _, _, _ = adapter_factory(mode="active")
    client.response = {"drawers": [], "total": 0, "next_offset": None}
    await adapter.list_drawers("u-1", limit=10)
    qs = client.calls[0]["path"]
    assert "wing=" not in qs, f"unexpected wing param in: {qs}"
    assert "limit=10" in qs
