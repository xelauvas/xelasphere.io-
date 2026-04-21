"""Unit tests for the migration framework (plan M0.9, spec §5.6 dim3).

Eleven mandatory unit tests covering the migration runner, the
sibling-rename atomic pattern, the reap_stale invariants (both
directions), the hardlink-snapshot-integrity invariant added in
the spec §5.6 amendment, and the PalaceHandle-open guard that
prevents wasted work when current schema matches target.

The crash-simulation test uses bare ``os.fork()`` + ``os._exit(0)``
in a ``finally`` + ``os.WIFEXITED``/``WEXITSTATUS`` parent-side
assertions. Pattern rationale: ``@pytest.mark.forked`` runs the
WHOLE test in a subprocess for isolation, not for crash simulation.
Bare ``os.fork()`` lets the child do partial work + hard-exit while
the parent inspects the resulting disk state.

Tests run pure against tmp_path; no registry or FastAPI involvement.
``test_palace_handle_open_at_current_schema_does_not_touch_disk``
calls ``PalaceHandle.open`` directly — still a unit test.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# apply_migrations — core sibling-rename mechanism
# ---------------------------------------------------------------------------


def test_no_op_when_at_target(tmp_path: Path) -> None:
    """Current schema == target → no dirs touched, no file mutations.

    Runner must early-return when ``current_version(palace_dir) ==
    target_version``. A naive implementation that unconditionally
    runs the copytree+rename dance creates churn and burns fs ops
    for nothing.
    """
    from mempalace_sidecar.migrations.runner import apply_migrations

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 1}')
    (palace_dir / "marker.txt").write_text("do not touch")
    original_mtime = (palace_dir / "meta.json").stat().st_mtime

    apply_migrations(palace_dir, target_version=1, migrations=[])

    # No sibling dirs created.
    assert not (tmp_path / "alice.migrating").exists()
    assert not list(tmp_path.glob("alice.replaced_*"))
    # meta.json untouched.
    assert (palace_dir / "meta.json").stat().st_mtime == original_mtime
    # marker.txt untouched.
    assert (palace_dir / "marker.txt").read_text() == "do not touch"


def test_atomic_rename_happy_path(tmp_path: Path) -> None:
    """v0 → v1 with the no-op migration. Post-run: .migrating gone,
    exactly one .replaced_<ts>/ exists with pre-migration content,
    primary at v1.
    """
    from mempalace_sidecar.migrations.runner import apply_migrations
    from mempalace_sidecar.migrations.v0_to_v1 import V0ToV1NoOp

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')
    (palace_dir / "marker.txt").write_text("pre-migration")

    apply_migrations(palace_dir, target_version=1, migrations=[V0ToV1NoOp()])

    # No .migrating left (second rename completed).
    assert not (tmp_path / "alice.migrating").exists()
    # Exactly one .replaced_<ts> with the pre-migration state.
    replaced = list(tmp_path.glob("alice.replaced_*"))
    assert len(replaced) == 1, f"expected 1 .replaced_<ts>, got {replaced}"
    assert (replaced[0] / "marker.txt").read_text() == "pre-migration"
    assert json.loads((replaced[0] / "meta.json").read_text())["schema_version"] == 0
    # Primary at v1.
    assert palace_dir.exists()
    assert json.loads((palace_dir / "meta.json").read_text())["schema_version"] == 1


def test_refuses_newer_schema(tmp_path: Path) -> None:
    """Palace at v999, target v1 → raise RuntimeError with "sidecar
    too old" substring. Spec §5.6 step 2 + deviation #5.
    """
    from mempalace_sidecar.migrations.runner import apply_migrations
    from mempalace_sidecar.migrations.v0_to_v1 import V0ToV1NoOp

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 999}')

    with pytest.raises(RuntimeError, match="sidecar too old"):
        apply_migrations(
            palace_dir, target_version=1, migrations=[V0ToV1NoOp()],
        )


# ---------------------------------------------------------------------------
# Crash-mid-migration — uses bare os.fork(), NOT @pytest.mark.forked
# ---------------------------------------------------------------------------


def test_crash_mid_migration_leaves_migrating_dir(tmp_path: Path) -> None:
    """Child process fork, partial migration work in the child,
    hard-exit mid-apply. Parent inspects: .migrating dir exists,
    primary unchanged. The reaper contract depends on this shape
    (primary present + .migrating alongside = recoverable crash
    from a failed migration, NOT a post-first-rename state).

    Non-negotiable details:
      - ``os._exit(0)`` in the child's finally — NOT ``sys.exit()``
        (raises SystemExit, pytest catches) and NOT bare return
        (child continues running pytest collector).
      - ``os.WIFEXITED(status)`` asserted BEFORE ``os.WEXITSTATUS``
        so a child segfault is a loud failure, not a silent "clean
        crash" false-positive.
    """
    from mempalace_sidecar.migrations._base import Migration
    from mempalace_sidecar.migrations.runner import apply_migrations

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')
    pre_migration_meta = (palace_dir / "meta.json").read_text()

    class CrashingMigration(Migration):
        from_version = 0
        to_version = 1

        def apply(self, dir_: Path) -> None:
            (dir_ / "partial_marker.txt").write_text("crash point")
            os._exit(42)  # hard-exit mid-apply

    pid = os.fork()
    if pid == 0:
        # Child: do partial work, then hard-exit.
        try:
            apply_migrations(
                palace_dir,
                target_version=1,
                migrations=[CrashingMigration()],
            )
        finally:
            # MUST be os._exit — sys.exit raises SystemExit which
            # pytest would catch; a bare return would let the child
            # continue running pytest's collector.
            os._exit(0)

    # Parent.
    _, status = os.waitpid(pid, 0)
    # Clean exit check BEFORE exit-status read — catches segfaults
    # as test failures rather than silent "crash simulation worked"
    # false-positives.
    assert os.WIFEXITED(status), (
        f"child died via signal, not os._exit; WTERMSIG="
        f"{os.WTERMSIG(status) if os.WIFSIGNALED(status) else 'unknown'}"
    )
    assert os.WEXITSTATUS(status) == 42, (
        f"expected child to exit 42 (crash in apply), got "
        f"{os.WEXITSTATUS(status)}"
    )

    # State after crash: .migrating exists with partial marker;
    # primary unchanged.
    migrating_dir = palace_dir.parent / f"{palace_dir.name}.migrating"
    assert migrating_dir.exists(), (
        "apply_migrations must copy-to-migrating BEFORE any migration "
        "apply runs; the child crashed during apply, so .migrating "
        "should exist with the partial marker"
    )
    assert (migrating_dir / "partial_marker.txt").exists()
    assert palace_dir.exists()
    assert (palace_dir / "meta.json").read_text() == pre_migration_meta


def test_crash_between_renames_is_recoverable(tmp_path: Path) -> None:
    """Hand-place the state that exists immediately after the first
    rename (``os.rename(palace_dir, replaced_path)``) but BEFORE the
    second rename (``os.rename(migrating_path, palace_dir)``) would
    have fired: primary is GONE, .migrating has the migrated state,
    .replaced_<ts> has the pre-migration state. The next
    ``PalaceHandle.open`` must call ``_recover_if_interrupted``
    which promotes .migrating to primary.

    Regression test for plan-review fix #2. Without this test, the
    reaper could delete an orphan .migrating BEFORE recovery ran,
    losing the migrated state entirely.
    """
    from mempalace_sidecar.migrations.runner import _recover_if_interrupted

    palace_dir = tmp_path / "alice"
    migrating_dir = palace_dir.parent / f"{palace_dir.name}.migrating"
    replaced_dir = palace_dir.parent / f"{palace_dir.name}.replaced_1234567890"

    # Migrated sibling — has post-migration state.
    migrating_dir.mkdir()
    (migrating_dir / "meta.json").write_text(
        json.dumps({"schema_version": 1, "migrated_at": 1234567890.0})
    )
    (migrating_dir / "marker.txt").write_text("migrated content")

    # Replaced dir — has pre-migration state.
    replaced_dir.mkdir()
    (replaced_dir / "meta.json").write_text('{"schema_version": 0}')
    (replaced_dir / "marker.txt").write_text("pre-migration content")

    # Primary absent (the first rename moved it to .replaced_<ts>;
    # the second rename that would have created primary never ran).
    assert not palace_dir.exists()

    recovered = _recover_if_interrupted(palace_dir)

    assert recovered is True
    assert palace_dir.exists(), "recovery must restore primary"
    assert not migrating_dir.exists(), "migrating must be gone post-recovery"
    assert (palace_dir / "marker.txt").read_text() == "migrated content"
    assert json.loads(
        (palace_dir / "meta.json").read_text()
    )["schema_version"] == 1
    # .replaced_<ts> untouched by recovery — it's the reaper's job,
    # on its own schedule.
    assert replaced_dir.exists()


# ---------------------------------------------------------------------------
# reap_stale — both directions of the skip-when-primary-missing invariant
# ---------------------------------------------------------------------------


def _age_dir(dir_: Path, seconds: int) -> None:
    """Set a dir's mtime to N seconds ago."""
    past = time.time() - seconds
    os.utime(dir_, (past, past))


def test_reap_stale_never_deletes_orphan_migrating_when_primary_missing(
    tmp_path: Path,
) -> None:
    """.migrating/ with primary absent == recovery-in-progress
    state. Even aged beyond the 24h threshold, it MUST survive —
    it's the only copy of the migrated data, and the next
    PalaceHandle.open for that user will call
    _recover_if_interrupted to promote it.

    A "fix" that always reaped .migrating would silently lose
    migrated palaces for users who don't reconnect within 24h.
    """
    from mempalace_sidecar.migrations.runner import reap_stale

    palaces_dir = tmp_path / "palaces"
    palaces_dir.mkdir()

    migrating = palaces_dir / "alice.migrating"
    migrating.mkdir()
    (migrating / "meta.json").write_text('{"schema_version": 1}')
    _age_dir(migrating, 48 * 3600)  # 48h > 24h threshold

    # Primary absent — this is the recovery-in-progress state.
    assert not (palaces_dir / "alice").exists()

    reaped = reap_stale(palaces_dir, max_age_sec=24 * 3600)

    assert migrating.exists(), (
        "orphan .migrating with primary absent MUST NOT be reaped; "
        "it's the only copy of migrated data pending recovery"
    )
    assert reaped == 0


def test_reap_stale_reaps_orphan_migrating_when_primary_exists(
    tmp_path: Path,
) -> None:
    """Other direction of the invariant: .migrating/ with primary
    PRESENT + aged > threshold == stale-from-crashed-migration,
    safely reapable. The migrated state was lost (process crashed
    before the first rename fired), primary has the original
    pre-migration state, .migrating is garbage.

    Without this direction tested, a "fix" that always skipped
    .migrating (interpreting invariant as "never touch
    .migrating") would accumulate orphans forever.
    """
    from mempalace_sidecar.migrations.runner import reap_stale

    palaces_dir = tmp_path / "palaces"
    palaces_dir.mkdir()

    primary = palaces_dir / "alice"
    primary.mkdir()
    (primary / "meta.json").write_text('{"schema_version": 0}')

    migrating = palaces_dir / "alice.migrating"
    migrating.mkdir()
    (migrating / "meta.json").write_text('{"schema_version": 1}')
    _age_dir(migrating, 48 * 3600)

    reaped = reap_stale(palaces_dir, max_age_sec=24 * 3600)

    assert not migrating.exists(), (
        "aged .migrating with primary present should be reaped "
        "(crashed-migration garbage)"
    )
    assert reaped == 1
    # Primary preserved.
    assert primary.exists()


def test_reap_stale_cleans_old_replaced_dirs(tmp_path: Path) -> None:
    """.replaced_<ts> aged beyond threshold → reaped. Under
    threshold → kept (might still be load-bearing as a rollback
    target)."""
    from mempalace_sidecar.migrations.runner import reap_stale

    palaces_dir = tmp_path / "palaces"
    palaces_dir.mkdir()

    old_replaced = palaces_dir / "alice.replaced_old"
    old_replaced.mkdir()
    (old_replaced / "marker.txt").write_text("old")
    _age_dir(old_replaced, 48 * 3600)

    fresh_replaced = palaces_dir / "alice.replaced_fresh"
    fresh_replaced.mkdir()
    (fresh_replaced / "marker.txt").write_text("fresh")
    _age_dir(fresh_replaced, 1 * 3600)  # 1h << 24h threshold

    reaped = reap_stale(palaces_dir, max_age_sec=24 * 3600)

    assert not old_replaced.exists()
    assert fresh_replaced.exists()
    assert reaped == 1


# ---------------------------------------------------------------------------
# Hardlink-snapshot-integrity invariant (spec §5.6 amendment 2026-04-21)
# ---------------------------------------------------------------------------


def test_hardlink_rename_shares_inodes(tmp_path: Path) -> None:
    """Direct invariant: after ``apply_migrations`` runs with
    ``copy_function=os.link``, primary and .replaced_<ts> share
    inodes for files the runner didn't overwrite. This is the
    mechanical foundation for spec §5.6's hardlink-snapshot-
    integrity amendment — without it, the ``st_nlink > 1`` guard
    prescribed in the amendment wouldn't fire.

    ``meta.json`` is excluded: the runner writes a fresh meta.json
    to ``.migrating`` during apply, which breaks the hardlink.
    Other files (chroma.sqlite3, turns.sqlite) aren't touched by
    the no-op V0ToV1NoOp migration, so they remain hardlinked.
    """
    from mempalace_sidecar.migrations.runner import apply_migrations
    from mempalace_sidecar.migrations.v0_to_v1 import V0ToV1NoOp

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')
    (palace_dir / "chroma.sqlite3").write_bytes(b"chroma content")
    (palace_dir / "turns.sqlite").write_bytes(b"turns content")

    apply_migrations(palace_dir, target_version=1, migrations=[V0ToV1NoOp()])

    replaced_dirs = list(tmp_path.glob("alice.replaced_*"))
    assert len(replaced_dirs) == 1
    snapshot = replaced_dirs[0]

    for filename in ("chroma.sqlite3", "turns.sqlite"):
        primary_stat = os.stat(palace_dir / filename)
        snapshot_stat = os.stat(snapshot / filename)
        assert primary_stat.st_ino == snapshot_stat.st_ino, (
            f"{filename}: primary and snapshot should share inode "
            f"(copy_function=os.link); got primary="
            f"{primary_stat.st_ino} snapshot={snapshot_stat.st_ino}"
        )
        assert primary_stat.st_nlink >= 2, (
            f"{filename} should have st_nlink >= 2 after hardlink "
            f"rename; got {primary_stat.st_nlink}"
        )


def test_dim1_does_not_corrupt_replaced_snapshot_via_hardlink(
    tmp_path: Path,
) -> None:
    """Teeth-bearing regression catch for the spec §5.6 amendment's
    ordering rule. In the PRODUCTION order (dim1 in-place modify
    BEFORE apply_migrations' hardlink rename), the snapshot is safe
    because the modification happens on the pre-rename inode.

    This test exercises the REVERSE order to document the
    corruption that correct ordering prevents: call
    apply_migrations FIRST (creates hardlinked .replaced_<ts>),
    then simulate dim1 with an in-place write to primary's
    chroma.sqlite3, inspect .replaced_<ts>/chroma.sqlite3 — assert
    it was MUTATED via the hardlink. If this assertion ever stops
    being true (snapshot unchanged despite primary's in-place
    write), copytree/hardlink/filesystem semantics changed and
    spec §5.6's amendment needs revisit.
    """
    from mempalace_sidecar.migrations.runner import apply_migrations
    from mempalace_sidecar.migrations.v0_to_v1 import V0ToV1NoOp

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')
    pre_state = b"pre-modification chroma content"
    (palace_dir / "chroma.sqlite3").write_bytes(pre_state)

    # dim3 FIRST (violates production order — deliberate for this test).
    apply_migrations(palace_dir, target_version=1, migrations=[V0ToV1NoOp()])

    replaced_dirs = list(tmp_path.glob("alice.replaced_*"))
    assert len(replaced_dirs) == 1
    snapshot_chroma = replaced_dirs[0] / "chroma.sqlite3"
    # Precondition: snapshot has pre_state.
    assert snapshot_chroma.read_bytes() == pre_state

    # Now simulate dim1 running AFTER dim3 (the order the spec
    # forbids): in-place write to primary's chroma.sqlite3.
    post_state = b"POST-dim1-content-very-different-from-pre"
    (palace_dir / "chroma.sqlite3").write_bytes(post_state)

    # Teeth: hardlink propagated the write; snapshot corrupted.
    assert snapshot_chroma.read_bytes() == post_state, (
        "expected hardlink to propagate in-place write from primary "
        "to .replaced_<ts>; if this fails, copytree's os.link no "
        "longer shares inodes — revisit spec §5.6 amendment"
    )
    assert snapshot_chroma.read_bytes() != pre_state


# ---------------------------------------------------------------------------
# PalaceHandle.open guard against wasted work at current schema
# ---------------------------------------------------------------------------


def test_multi_step_chain_runs_in_order(tmp_path: Path) -> None:
    """M0.9 quality-review I-3: the migration runner's
    ``for m in sorted(migrations, key=...)`` iteration is exercised
    with exactly one registered migration (V0ToV1NoOp). Multi-step
    chains (v0 → v1 → v2, from any starting point) are untested.

    This test uses TWO ad-hoc Migration subclasses (NOT from
    ALL_MIGRATIONS) to exercise:
      - Iteration order (v0→v1 runs before v1→v2)
      - The ``v = m.to_version`` update
      - The filter ``m.from_version >= v and m.to_version <= target``
      - Final meta.json at target_version

    Each ad-hoc migration writes a unique marker file. Post-run
    assertions: both markers exist AND their content indicates the
    ordered execution.
    """
    from mempalace_sidecar.migrations._base import Migration
    from mempalace_sidecar.migrations.runner import apply_migrations

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')

    # Mutable state both migrations touch — lets us verify ordering.
    order_log: list[str] = []

    class V0ToV1(Migration):
        from_version = 0
        to_version = 1

        def apply(self, dir_: Path) -> None:
            order_log.append("v0_to_v1")
            (dir_ / "m1_marker.txt").write_text(
                f"v0_to_v1 ran at order_log length={len(order_log)}"
            )

    class V1ToV2(Migration):
        from_version = 1
        to_version = 2

        def apply(self, dir_: Path) -> None:
            order_log.append("v1_to_v2")
            (dir_ / "m2_marker.txt").write_text(
                f"v1_to_v2 ran at order_log length={len(order_log)}"
            )

    # Pass migrations in REVERSE declaration order to verify the
    # runner's sorted-by-from_version discipline. If the runner
    # iterated without sorting, v1_to_v2 would run first on a
    # v=0 palace and the filter would reject it.
    apply_migrations(
        palace_dir,
        target_version=2,
        migrations=[V1ToV2(), V0ToV1()],
    )

    # Both ran, in correct order.
    assert order_log == ["v0_to_v1", "v1_to_v2"], (
        f"expected [v0_to_v1, v1_to_v2] (sorted by from_version); "
        f"got {order_log}"
    )
    # Both markers present in the new primary.
    assert (palace_dir / "m1_marker.txt").exists()
    assert (palace_dir / "m2_marker.txt").exists()
    assert "length=1" in (palace_dir / "m1_marker.txt").read_text()
    assert "length=2" in (palace_dir / "m2_marker.txt").read_text()
    # Final meta.json at v2.
    assert json.loads(
        (palace_dir / "meta.json").read_text()
    )["schema_version"] == 2


def test_copy_hardlink_falls_back_on_exdev(
    tmp_path: Path, monkeypatch,
) -> None:
    """M0.9 quality-review M-2: the EXDEV fallback path in
    ``_copy_hardlink_if_possible`` (cross-filesystem or
    hardlink-refusing backend) is untested by default because
    cross-fs setup is hard in CI. Monkeypatch-based verification:
    make shutil.copytree raise OSError on the FIRST call (simulating
    hardlink rejection) and pass through on subsequent calls.

    Expected behavior: partial dst rm-rf'd, second copytree produces
    a full byte-copy. Post-assert: dst exists with the same content
    as src.
    """
    from mempalace_sidecar.migrations.runner import (
        _copy_hardlink_if_possible,
    )
    import shutil

    src = tmp_path / "src"
    src.mkdir()
    (src / "marker.txt").write_text("source content")
    (src / "nested").mkdir()
    (src / "nested" / "data.bin").write_bytes(b"nested data")

    dst = tmp_path / "dst"

    original_copytree = shutil.copytree
    call_count = {"n": 0}

    def _copytree_with_first_call_fail(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate EXDEV on the first call (the os.link attempt).
            # A partial dst may exist; caller rm-rfs before retrying.
            if dst.exists():
                pass  # leave partial dst to test the rm-rf path
            else:
                # Create a partial dst to verify rm-rf happens.
                dst.mkdir()
                (dst / "partial").write_text("leftover")
            raise OSError(18, "EXDEV simulated (Invalid cross-device link)")
        # Subsequent calls pass through (no copy_function arg now).
        return original_copytree(*args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", _copytree_with_first_call_fail)

    _copy_hardlink_if_possible(src, dst)

    # Second copytree produced a full copy.
    assert dst.exists()
    assert (dst / "marker.txt").read_text() == "source content"
    assert (dst / "nested" / "data.bin").read_bytes() == b"nested data"
    # Partial-dst leftover was rm-rf'd (no "partial" file from the
    # failed first attempt).
    assert not (dst / "partial").exists()
    # At least 2 calls: one EXDEV-fail + one fallback. More are OK
    # because shutil.copytree recurses via the module-level copytree
    # attribute which the monkeypatch intercepts on every recursion
    # into subdirs (src/nested/data.bin makes the recursion visible).
    # Behavior is what matters, not the count.
    assert call_count["n"] >= 2, (
        f"expected >=2 copytree calls (1 EXDEV fail + 1+ fallback); "
        f"got {call_count['n']}"
    )


def test_palace_handle_open_at_current_schema_does_not_call_apply_migrations(
    tmp_path: Path, monkeypatch,
) -> None:
    """Strong version of the guard-regression check (originally
    drafted as ``does_not_touch_disk``; strengthened per M0.9
    Stage 3 to verify INTENT rather than absence-of-symptoms).

    The ``< CURRENT_SCHEMA_VERSION`` guard in ``PalaceHandle.open``
    (deviation #1) must skip ``apply_migrations`` entirely when the
    palace is already at the current schema. A refactor that
    ran ``apply_migrations`` unconditionally would create
    .migrating/ + .replaced_<ts>/ dirs + rewrite meta.json on every
    open for a current-schema palace — churn + snapshot pollution.

    Monkeypatches ``apply_migrations`` to raise if called, then
    opens a v1 palace (current schema) via ``PalaceHandle.open``
    directly. The open MUST NOT raise — which is only possible
    when the guard correctly skipped ``apply_migrations``.

    Strictly stronger than the original ``does_not_touch_disk``
    formulation: that version passed vacuously at RED (no
    wire-up = no disk-touching); this version verifies the intent
    (guard is present AND skips the call) rather than the absence
    of symptoms.
    """
    from mempalace_sidecar.migrations import runner
    from mempalace_sidecar.palace_handle import (
        CURRENT_SCHEMA_VERSION,
        PalaceHandle,
    )

    palace_dir = tmp_path / "alice"
    palace_dir.mkdir()
    (palace_dir / "meta.json").write_text(
        json.dumps({"schema_version": CURRENT_SCHEMA_VERSION})
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError(
            "apply_migrations was called for a palace already at "
            "CURRENT_SCHEMA_VERSION; the < guard in PalaceHandle.open "
            "must skip it to avoid churn + snapshot pollution"
        )

    monkeypatch.setattr(runner, "apply_migrations", _fail_if_called)

    handle = PalaceHandle.open(palace_dir)
    handle.close()
