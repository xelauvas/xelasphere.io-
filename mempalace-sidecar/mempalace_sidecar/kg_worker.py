"""KG extraction worker — batched, cap-aware, cancellable (plan M0.7).

Single ``asyncio`` task running every ``interval_sec`` (default 30s,
per spec §5.5). Each tick: check cap → claim up to 4 pending rows →
group drawer_ids by user, chunk to ``N=8 drawers`` per LLM call
(deviation #1 from dispatch: one LLM call per batch of 8, even if
those 8 span multiple ``kg_pending`` rows) → invoke extractor → write
triples to each user's palace KG → mark rows done or failed.

**Extractor is a Protocol stub in M0.7.** See deviation #2: the real
LLM integration (ollama/groq/anthropic per
``settings.kg_llm_provider``) is deferred. Lifespan wires
``NullExtractor()`` now; real provider selection lands in a later
milestone. Tests inject ``FailingExtractor`` / ``NullExtractor`` /
``CancellingExtractor`` to exercise the worker body without any LLM
dependency.

**CancelledError handling.** The INNER ``except Exception`` around
the extractor call MUST NOT catch ``asyncio.CancelledError`` — in
Py3.8+ ``CancelledError`` inherits from ``BaseException``, not
``Exception``, so the natural ``except Exception:`` structure
propagates cancellation cleanly up to the OUTER loop's
``except asyncio.CancelledError: break``. The outer-loop catch is
what terminates ``run_kg_worker`` when lifespan teardown calls
``task.cancel()``. See
``tests/unit/test_kg_queue.py::test_worker_inner_handler_propagates_cancelled_error``
for the guardrail — it fails if a refactor broadens the handler to
``except BaseException:`` (which would make the worker
uncancellable and hang shutdown).

**Shutdown ordering (deviation #3).** ``main.py``'s lifespan
finally-block cancels the worker task and awaits it BEFORE calling
``registry.close_all()``. Reversing that order would yank palace
handles out from under an in-flight triple write. The integration
test ``test_worker_lifecycle_cancels_cleanly_on_shutdown`` asserts
the ordering directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Protocol

from mempalace_sidecar.kg_queue import KGQueue


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Triple:
    """KG triple returned by an extractor and written to a palace KG.

    Matches the ``KnowledgeGraph.add_triple`` parameter shape
    (subject / predicate / obj / valid_from / confidence). ``obj``
    is spelled out (not ``object``) to avoid shadowing the builtin
    in the dataclass field namespace.
    """

    subject: str
    predicate: str
    obj: str
    valid_from: str | None = None
    confidence: float = 1.0


class KGExtractor(Protocol):
    """Duck-typed extractor interface. ``extract`` takes a list of
    drawer texts (for the real LLM caller) or drawer ids (for the
    M0.7 stub; real impl fetches texts via
    ``registry.open(user).handle`` before calling the LLM) and
    returns a list of ``Triple``.

    Protocol (not abstract class) per dispatch deviation #2: duck
    typing is harder to mess up than an inheritance chain and the
    tests don't care about a type hierarchy.
    """

    async def extract(self, drawer_texts: list[str]) -> list[Triple]: ...


class NullExtractor:
    """Stub that returns no triples. Used in lifespan wiring until a
    real LLM provider lands (see TODO in main.py lifespan).
    """

    async def extract(self, drawer_texts: list[str]) -> list[Triple]:
        return []


# Callable signature for the per-user triple write. Factored out so
# _run_one_tick can be unit-tested without a live registry (tests
# pass ``kg_writer=None`` and ``registry=None``; production wires it
# via ``_make_registry_writer``).
KGWriter = Callable[[str, list[Triple]], Awaitable[None]]


async def _noop_writer(user_id: str, triples: list[Triple]) -> None:
    return None


def _make_registry_writer(registry) -> KGWriter:
    """Build a ``KGWriter`` backed by a live registry.

    Opens the user's palace (may trigger cold-open + LRU eviction
    via ``registry.open``) and writes each triple through
    ``entry.handle.kg.add_triple``. Runs under the user's per-user
    writer_lock because KG writes mutate per-palace sqlite state
    that the handle owns (spec §5.3 "caller holds per-user write
    lock for mutating methods").
    """

    async def _write(user_id: str, triples: list[Triple]) -> None:
        if not triples:
            return
        entry = await registry.open(user_id)
        lock = registry.lock_for(user_id)
        async with lock.writer_lock:
            for t in triples:
                # handle.kg.add_triple is synchronous; wrap in
                # to_thread to keep the loop responsive. The
                # writer_lock is held across all triples to keep
                # the batch atomic from a reader's perspective.
                await asyncio.to_thread(
                    entry.handle.kg.add_triple,
                    t.subject,
                    t.predicate,
                    t.obj,
                    valid_from=t.valid_from,
                    confidence=t.confidence,
                )

    return _write


def _batch_by_user(
    rows: list[dict], max_drawers: int = 8
) -> list[tuple[str, list[str]]]:
    """Group claimed ``kg_pending`` rows by ``user_id``, concatenate
    each user's drawer_ids, chunk into groups of at most
    ``max_drawers``.

    Spec §5.5 prescribes ``N=8 drawers per LLM call``. This is the
    batching deviation from the plan sketch (deviation #1): the
    plan handled each row individually, but the spec's "one LLM call
    per batch of 8" requires cross-row aggregation within a user.

    Returns a list of ``(user_id, drawer_ids)`` tuples where every
    ``drawer_ids`` list satisfies ``len(drawer_ids) <= max_drawers``
    and every batch is single-user.
    """
    per_user: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        ids = json.loads(row["drawer_ids"])
        per_user[row["user_id"]].extend(ids)

    batches: list[tuple[str, list[str]]] = []
    for user_id, ids in per_user.items():
        for i in range(0, len(ids), max_drawers):
            batches.append((user_id, ids[i : i + max_drawers]))
    return batches


async def _run_one_tick(
    queue: KGQueue,
    registry,
    extractor: KGExtractor,
    cap_usd: float,
    kg_writer: KGWriter | None = None,
) -> None:
    """One iteration of the worker.

    Steps:
      1. Cap check — if today's spend >= cap_usd, short-circuit
         (no claim call; rows stay pending for the next tick).
      2. Claim up to 4 pending rows.
      3. Group drawer_ids by user into batches of <= 8 drawers.
      4. For each batch: call extractor. INNER try/except catches
         ``Exception`` only (NOT ``CancelledError`` — see module
         docstring). Failures record a per-user error string;
         successes write triples via ``kg_writer`` (or the
         registry-backed writer).
      5. After the batch loop: for each user, if any batch
         failed, mark all that user's rows ``mark_failed``; else
         mark all ``mark_done``.

    Arguments:
      - ``queue``: the ``KGQueue`` instance.
      - ``registry``: PalaceRegistry, or ``None`` for tests.
      - ``extractor``: a ``KGExtractor`` implementation.
      - ``cap_usd``: today's USD spend cap.
      - ``kg_writer``: optional explicit writer (overrides the
        registry-backed default, used in tests).

    Error paths:
      - ``CancelledError`` from extractor: propagates up and OUT of
        ``_run_one_tick`` (the mark_done/mark_failed block never
        runs; rows stay in 'running' — next worker start after
        recovery will NOT re-claim them because claim filters on
        'pending', so a future milestone should add a startup
        sweep that resets 'running' → 'pending' for crash recovery).
      - ``Exception`` from extractor: caught, row marked with
        ``mark_failed``. ``attempts`` increments; terminal after 5.
      - ``Exception`` from ``kg_writer``: same path — treated as a
        batch failure for that user.
    """
    if await queue.today_spend() >= cap_usd:
        log.info(
            "kg_worker: cap hit, pausing",
            extra={"cap_usd": cap_usd},
        )
        return

    rows = await queue.claim(limit=4)
    if not rows:
        return

    # Map user_id -> list of row_ids that contributed to this user's
    # batches. Needed for mark_done / mark_failed after the batch
    # loop completes.
    rows_by_user: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        rows_by_user[row["user_id"]].append(row["id"])

    batches = _batch_by_user(rows, max_drawers=8)

    # Writer resolution — explicit kg_writer wins; registry-backed
    # default next; else no-op (tests without a registry).
    writer: KGWriter
    if kg_writer is not None:
        writer = kg_writer
    elif registry is not None:
        writer = _make_registry_writer(registry)
    else:
        writer = _noop_writer

    user_failures: dict[str, list[str]] = defaultdict(list)

    for user_id, drawer_ids in batches:
        try:
            triples = await extractor.extract(drawer_ids)
            await writer(user_id, triples)
        except Exception as exc:
            # CancelledError is NOT caught here — it inherits from
            # BaseException, not Exception, and must propagate up
            # to run_kg_worker's outer handler which breaks the
            # while-True loop. See module docstring for the
            # guardrail test.
            log.warning(
                "kg_worker: batch failed",
                extra={
                    "user_id_hash_prefix": user_id[:4],  # no salt at this layer
                    "err": str(exc)[:200],
                    "drawer_count": len(drawer_ids),
                },
            )
            user_failures[user_id].append(str(exc))

    # Post-batch: mark each user's rows done or failed. A user with
    # any failed batch has ALL their rows marked failed — approximate
    # but acceptable given drawer counts per row are small (usually
    # 1-2); the failure granularity is per-row-group, not per-row.
    for user_id, row_ids in rows_by_user.items():
        if user_id in user_failures:
            err = "; ".join(user_failures[user_id])
            for row_id in row_ids:
                await queue.mark_failed(row_id, err)
        else:
            for row_id in row_ids:
                await queue.mark_done(row_id)


async def run_kg_worker(
    queue: KGQueue,
    registry,
    extractor: KGExtractor,
    cap_usd: float,
    interval_sec: float = 30.0,
) -> None:
    """Long-running worker task. One ``_run_one_tick`` per
    ``interval_sec``, exits cleanly on ``task.cancel()`` via the
    outer ``except asyncio.CancelledError: break``.

    Lifespan attaches this via ``asyncio.create_task(
    run_kg_worker(...))`` and cancels + awaits on shutdown BEFORE
    calling ``registry.close_all()``. The cancel-before-close_all
    ordering is deviation #3; see the integration test for the
    enforcement.

    Non-CancelledError exceptions from ``_run_one_tick`` are logged
    and the loop sleeps + retries on the next interval. A truly
    unrecoverable error (e.g., sqlite db corrupt) will log-spam at
    30s cadence but won't kill the worker task — that's better than
    silent task death, because a dead task is invisible to
    lifespan teardown and would hang shutdown forever.
    """
    log.info(
        "kg_worker: started",
        extra={"interval_sec": interval_sec, "cap_usd": cap_usd},
    )
    try:
        while True:
            try:
                await _run_one_tick(queue, registry, extractor, cap_usd)
            except asyncio.CancelledError:
                # Propagate to the outer handler; DO NOT catch here.
                # (Kept explicit for reviewer clarity; `except
                # Exception` below wouldn't catch it anyway.)
                raise
            except Exception:
                log.exception("kg_worker: tick error, will retry")
            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        log.info("kg_worker: cancelled, exiting")
        # Return cleanly — callers can `await task` without seeing
        # CancelledError propagate IF they expect clean shutdown.
        # (Tests that want to observe the cancellation as a raised
        # exception call _run_one_tick directly, where the catch
        # here doesn't apply.)
        return


__all__ = [
    "Triple",
    "KGExtractor",
    "NullExtractor",
    "KGQueue",
    "_batch_by_user",
    "_run_one_tick",
    "run_kg_worker",
]
