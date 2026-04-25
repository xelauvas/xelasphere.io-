"""Unit test for the M0.11 structured-logging middleware (spec §8.4).

One teeth-bearing test — the doctrine-trained surface for this
milestone. Exercises the middleware directly (no app, no lifespan)
so the schema-compliance assertion is isolated from route
instrumentation.

Schema (spec §8.4):

    {ts, service, level, event, user_id, turn_id, route,
     latency_ms, err, request_id}

``user_id`` is always ``hash_uid(user_id, salt)`` — 12-char hex
digest — never raw.

Stage 1 RED: middleware is a pass-through stub. No record is
emitted. Test fails at "expected 1 record, got 0." Stage 3 GREEN
lands the emission + schema compliance.
"""

from __future__ import annotations

import json
import logging

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from mempalace_sidecar.logging_middleware import (
    StructuredLoggingMiddleware,
)


_EXPECTED_FIELDS = {
    "ts",
    "service",
    "level",
    "event",
    "user_id",
    "turn_id",
    "route",
    "latency_ms",
    "err",
    "request_id",
}


async def test_structured_log_schema(caplog) -> None:
    """Middleware emits one JSON log record with every §8.4 field.
    ``user_id`` is hashed (12-char hex), raw user_id never appears.

    Nastiest reachable context: a raw user_id getting through to
    journald because a code path missed the hash step. The test
    seeds a real raw user_id into the request path, then asserts
    the emitted record's ``user_id`` is a 12-char hex string AND
    the raw value does NOT appear anywhere in the record's JSON.
    """
    salt = "s" * 16
    raw_user = "alice_raw_user_id_should_never_leak"

    async def inner(_request) -> JSONResponse:
        return JSONResponse({"ok": True})

    middleware = StructuredLoggingMiddleware(inner, salt=salt)

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/palace/{raw_user}/ingest",
        "raw_path": f"/palace/{raw_user}/ingest".encode(),
        "headers": [(b"x-request-id", b"req-abc-123")],
        "query_string": b"",
        "client": ("127.0.0.1", 0),
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive=_receive)

    with caplog.at_level(
        logging.INFO, logger="mempalace_sidecar.logging_middleware"
    ):
        resp = await middleware.dispatch(request, inner)
        assert resp.status_code == 200

    # Expect exactly one record from the middleware for this request.
    log_records = [
        rec for rec in caplog.records
        if rec.name == "mempalace_sidecar.logging_middleware"
    ]
    assert len(log_records) == 1, (
        f"expected 1 structured-log record, got {len(log_records)}"
    )

    payload = log_records[0].getMessage()
    record: dict = json.loads(payload)

    missing = _EXPECTED_FIELDS - record.keys()
    assert not missing, f"log record missing required fields: {missing}"

    # Hashed user_id is 12-char lowercase hex.
    uid = record["user_id"]
    assert isinstance(uid, str) and len(uid) == 12, (
        f"user_id should be 12-char hashed hex, got {uid!r}"
    )
    assert all(c in "0123456789abcdef" for c in uid), (
        f"user_id is not lowercase hex: {uid!r}"
    )

    # Raw user_id must never appear anywhere in the record JSON.
    assert raw_user not in payload, (
        "raw user_id leaked into structured log record"
    )

    # request_id propagated from the X-Request-ID header.
    assert record["request_id"] == "req-abc-123", (
        f"request_id should propagate from header, got {record['request_id']!r}"
    )
