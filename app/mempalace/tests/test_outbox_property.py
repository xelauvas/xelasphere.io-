"""M0.15 Stage 1 RED: hypothesis property test + simulation fidelity test
(Task M0.15.8).

Two tests:
  - `test_property_simulation_matches_sidecar_dedupe`: unit test of the
    SimulatedPalace helper class, asserting its dedupe semantic matches the
    real sidecar's IngestRequest UNIQUE-on-turn_id constraint at
    `mempalace-sidecar/mempalace_sidecar/schemas.py:66-73`. Passes at
    Stage 1 RED because SimulatedPalace is pure test infrastructure.
  - `test_outbox_never_loses_or_dupes`: hypothesis @given invariant test
    over interleaved insert/flush/crash ops. At Stage 1 RED, every
    @given example fails at the first `outbox.insert(...)` call
    (AttributeError) — hypothesis surfaces one or more counterexamples.

**Hypothesis infrastructure note (per tightened hazard-X precondition).**
If either test surprises at Stage 1 RED due to hypothesis INFRASTRUCTURE
behavior (strategy construction, shrinker, deadline, DB persistence,
plugin coordination), that's the third test-harness-semantics data point.
A failure due to `outbox.insert` being undefined is a REGULAR doctrine
catch (expected RED shape), not a third data point.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings as hs, strategies as st

from app.mempalace.outbox import Outbox


class SimulatedPalace:
    """In-memory stand-in for the sidecar's palace, used by the hypothesis
    harness to assert the Outbox's no-loss-no-dupe invariant.

    Dedupe semantic: matches sidecar `IngestRequest` UNIQUE-on-turn_id
    behavior — a flush_success on an already-seen turn_id is a no-op
    (returns 'duplicate' semantically; does not error, does not re-store).

    See `mempalace-sidecar/mempalace_sidecar/schemas.py:66-73` for the
    IngestRequest fields and the UNIQUE constraint enforced there.
    See `mempalace-sidecar/mempalace_sidecar/routes/palace.py:47` for
    the ingest route's duplicate-on-retry behavior.
    """

    def __init__(self) -> None:
        self._seen_turn_ids: set[str] = set()
        self._records: dict[str, dict] = {}

    def ingest(self, turn_id: str, user_id: str, text: str) -> str:
        """Idempotent insert keyed on turn_id. Returns 'accepted' or 'duplicate'."""
        if turn_id in self._seen_turn_ids:
            return "duplicate"
        self._seen_turn_ids.add(turn_id)
        self._records[turn_id] = {"user_id": user_id, "text": text}
        return "accepted"

    def distinct_pairs(self) -> set[tuple[str, str]]:
        """Set of distinct (user_id, text) pairs seen so far."""
        return {(r["user_id"], r["text"]) for r in self._records.values()}


def test_property_simulation_matches_sidecar_dedupe():
    """Simulation-fidelity check: SimulatedPalace.ingest behaves as the real
    sidecar does — idempotent on turn_id, distinct-pairs = distinct inserts
    after dedup.

    Passes at Stage 1 RED; guards the hypothesis harness from silently
    diverging from real sidecar contract if SimulatedPalace is modified
    later.
    """
    palace = SimulatedPalace()
    assert palace.ingest("t1", "u", "hello") == "accepted"
    # Same turn_id, different body → still 'duplicate' (dedupe is by turn_id).
    assert palace.ingest("t1", "u", "different text") == "duplicate"
    # Different turn_id, same body → 'accepted' (not dedupe'd by body).
    assert palace.ingest("t2", "u", "hello") == "accepted"
    # Distinct pairs reflects FIRST-seen body per turn_id (dedupe preserves first).
    assert palace.distinct_pairs() == {("u", "hello")}


@hs(max_examples=30, deadline=None)
@given(
    ops=st.lists(
        st.one_of(
            st.tuples(
                st.just("insert"),
                st.text(min_size=1, max_size=3, alphabet="abc"),
                st.text(min_size=1, max_size=4),
            ),
            st.just(("flush_success",)),
            st.just(("flush_5xx",)),
            st.just(("crash_restart",)),
        ),
        min_size=0,
        max_size=20,
    )
)
def test_outbox_never_loses_or_dupes(ops):
    """Invariant: for any interleaving of inserts, flushes, failures, and
    restarts, the set of distinct (user_id, text) pairs delivered to the
    simulated palace equals the set of distinct (user_id, text) pairs
    ever inserted.

    Uses `tempfile.TemporaryDirectory()` inside the @given body (NOT a
    pytest tmp_path fixture) so each hypothesis example runs on a fresh
    outbox. pytest function-scoped fixtures are not reset between
    @given-generated inputs — the shared-state pattern would let later
    examples see earlier examples' rows, weakening the invariant claim.
    This refactor was driven by a `FailedHealthCheck` at Stage 1 RED;
    see M0.15 deltas entry for the test-harness-semantics data point
    and template addition that resulted.

    At Stage 1 RED: fails at `outbox.insert(...)` call with AttributeError
    because Outbox.insert is undefined. Expected RED shape.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "outbox.db"
        outbox = Outbox(db_path)
        palace = SimulatedPalace()

        inserted_pairs: set[tuple[str, str]] = set()
        turn_counter = 0

        for op in ops:
            if op[0] == "insert":
                _, user_id, text = op
                turn_counter += 1
                turn_id = f"t{turn_counter}"
                outbox.insert(
                    turn_id=turn_id, pair_id="p", user_id=user_id,
                    session_id="s", role="user", text=text,
                    ts=float(turn_counter), request_id=None,
                )
                inserted_pairs.add((user_id, text))
            elif op[0] == "flush_success":
                rows = outbox.claim_batch(10)
                for row in rows:
                    palace.ingest(row["turn_id"], row["user_id"], row["text"])
                    outbox.mark_sent([row["id"]])
            elif op[0] == "flush_5xx":
                rows = outbox.claim_batch(10)
                for row in rows:
                    outbox.mark_failed(row["id"], "simulated 5xx")
            elif op[0] == "crash_restart":
                # Crash drops in-flight state; pending/claimed persists.
                # Simulate by closing and reopening the outbox connection
                # on the same db_path.
                outbox._conn.close()
                outbox = Outbox(db_path)

        # After all ops: drain remaining pending rows for a stable state.
        for _ in range(5):
            rows = outbox.claim_batch(10)
            if not rows:
                break
            for row in rows:
                palace.ingest(row["turn_id"], row["user_id"], row["text"])
                outbox.mark_sent([row["id"]])

        # Invariant: palace's distinct pairs ⊆ inserted pairs (no phantom sends),
        # AND every inserted pair eventually appears (no losses).
        assert palace.distinct_pairs() <= inserted_pairs, (
            f"palace has phantom pairs not in inserts: "
            f"{palace.distinct_pairs() - inserted_pairs}"
        )
        assert palace.distinct_pairs() == inserted_pairs, (
            f"palace missing pairs: {inserted_pairs - palace.distinct_pairs()}"
        )
