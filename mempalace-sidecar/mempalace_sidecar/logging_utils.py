"""Shared logging utilities (M0.11).

Lifts ``hash_uid`` out of ``routes/_common.py`` so every module that
emits a log record mentioning ``user_id`` — the structured-logging
middleware, route handlers, the KG worker, the migration runner —
shares one canonical hash function.

Spec §8.4: ``user_id`` in logs is always ``sha256(user_id +
salt)[:12]``. Raw user_id must never reach journald. The
pseudonymous-logging contract is load-bearing for GDPR compliance —
operators correlate turns across Flask-outbox → sidecar-ingest →
kg_worker-extract by matching the hash across streams, and the hash
is the unit the correlation is allowed to operate on.

Pre-M0.11 history: ``routes/_common._hash_uid`` was the original
home at the M0.8 route split; ``kg_worker.py`` kept a local copy
to avoid cross-module coupling. M0.11 consolidates into this
module; ``_common._hash_uid`` is retained as a re-export so M0.12's
audit of "what imports from where" sees a single source of truth.
"""

from __future__ import annotations

import hashlib


def hash_uid(user_id: str, salt: str) -> str:
    """Spec §8.4 pseudonymous-logging contract.

    Returns 12-char hex digest of ``sha256(user_id + salt)``. The
    12-char truncation balances collision risk (~1-in-a-trillion for
    10k users) against log-line width.
    """
    return hashlib.sha256(f"{user_id}{salt}".encode()).hexdigest()[:12]


__all__ = ["hash_uid"]
