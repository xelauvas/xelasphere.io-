"""Integration tests for migration framework wire-ups (plan M0.9).

Three mandatory integration tests covering the wire-ups that land
in Stage 3 GREEN:

- ``test_palace_handle_open_runs_schema_migration_when_stale``
  verifies deviation #1 (PalaceHandle.open calls apply_migrations
  when meta.json.schema_version < CURRENT_SCHEMA_VERSION).

- ``test_palace_handle_open_refuses_newer_schema`` verifies
  deviation #5 (schema_version > CURRENT raises RuntimeError).

- ``test_lifespan_reaps_stale_dirs`` verifies deviation #4
  (reap_stale called at lifespan startup).

All three go through the real app lifespan and PalaceHandle paths,
so they're integration-flavored even when the assertions are
against filesystem state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


async def test_palace_handle_open_runs_schema_migration_when_stale(
    app, tmp_path: Path, monkeypatch
) -> None:
    """Palace at ``schema_version=0`` + a registered v0→v1 migration
    that writes a marker file → after opening via registry, the
    palace is at v1 and the marker file exists.

    Exercises the full wire-up: PalaceHandle.open reads meta.json,
    sees 0 < CURRENT_SCHEMA_VERSION, calls apply_migrations with
    ALL_MIGRATIONS, runner creates .migrating, applies each
    migration, renames, re-reads meta.json.
    """
    from mempalace_sidecar import migrations as mig_pkg
    from mempalace_sidecar.migrations._base import Migration
    from mempalace_sidecar.palace_handle import CURRENT_SCHEMA_VERSION

    assert CURRENT_SCHEMA_VERSION == 1, (
        "test's v0→v1 migration shape assumes CURRENT_SCHEMA_VERSION==1; "
        "update the test when the sidecar bumps to v2+"
    )

    class MarkerMigration(Migration):
        from_version = 0
        to_version = 1

        def apply(self, palace_dir: Path) -> None:
            (palace_dir / "migration_marker.txt").write_text("v0->v1 ran")

    # Monkeypatch ALL_MIGRATIONS so the test's marker migration fires
    # rather than V0ToV1NoOp.
    monkeypatch.setattr(
        mig_pkg, "ALL_MIGRATIONS", [MarkerMigration()],
    )

    # Seed palace at v0 BEFORE opening — PalaceHandle.open would
    # otherwise create it at current schema.
    palace_dir = tmp_path / "palaces" / "alice"
    palace_dir.mkdir(parents=True)
    (palace_dir / "meta.json").write_text('{"schema_version": 0}')

    async with app.router.lifespan_context(app):
        registry = app.state.registry
        entry = await registry.open("alice")
        try:
            # Post-open: meta.json at v1, marker file from migration.
            assert json.loads(
                (palace_dir / "meta.json").read_text()
            )["schema_version"] == 1
            assert (palace_dir / "migration_marker.txt").exists()
            assert (
                palace_dir / "migration_marker.txt"
            ).read_text() == "v0->v1 ran"
        finally:
            # Close the handle so Chroma releases the sqlite lock
            # before tmp_path rmtree at test teardown.
            entry.handle.close()


async def test_palace_handle_open_refuses_newer_schema(
    app, tmp_path: Path
) -> None:
    """Palace at schema_version=999, CURRENT_SCHEMA_VERSION=1 →
    PalaceHandle.open raises RuntimeError matching "sidecar too
    old". Routes map RuntimeError → 500 via FastAPI's default
    handler; this test stops at the PalaceHandle level.
    """
    palace_dir = tmp_path / "palaces" / "alice"
    palace_dir.mkdir(parents=True)
    (palace_dir / "meta.json").write_text('{"schema_version": 999}')

    async with app.router.lifespan_context(app):
        registry = app.state.registry
        with pytest.raises(RuntimeError, match="sidecar too old"):
            await registry.open("alice")


async def test_lifespan_reaps_stale_dirs(app, tmp_path: Path) -> None:
    """Lifespan startup calls reap_stale (deviation #4). Prepopulate
    palaces_dir with (a) an aged .replaced_<ts> that MUST be reaped
    and (b) an aged .migrating with primary missing that MUST NOT
    be reaped (recovery-in-progress state, spec §5.6).
    """
    palaces_dir = tmp_path / "palaces"
    palaces_dir.mkdir()

    # Case A: aged .replaced_<ts> — should be reaped.
    old_replaced = palaces_dir / "alice.replaced_1000000000"
    old_replaced.mkdir()
    (old_replaced / "marker.txt").write_text("old-state")
    past = time.time() - 48 * 3600
    os.utime(old_replaced, (past, past))

    # Case B: orphan .migrating with primary missing — recovery
    # state, must survive even aged.
    orphan_migrating = palaces_dir / "bob.migrating"
    orphan_migrating.mkdir()
    (orphan_migrating / "meta.json").write_text('{"schema_version": 1}')
    os.utime(orphan_migrating, (past, past))

    async with app.router.lifespan_context(app):
        # Lifespan startup completes — reap_stale has run by this point.
        pass

    # Case A reaped.
    assert not old_replaced.exists(), (
        "aged .replaced_<ts> should have been reaped at lifespan startup"
    )
    # Case B survives.
    assert orphan_migrating.exists(), (
        "orphan .migrating with primary missing MUST survive reap — "
        "recovery-in-progress state"
    )
