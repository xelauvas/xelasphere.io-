"""Migration runner — sibling-rename atomic pattern (M0.9, spec §5.6 dim3).

The migration flow for a single ``PalaceHandle.open`` on a stale
palace:

1. ``_recover_if_interrupted`` — promote any ``<U>.migrating`` from
   a prior crashed run whose primary is missing (spec §5.6 step 4:
   "the original U/ directory is never mutated until verification
   passes"; a crash between the two renames leaves migrating as the
   authoritative copy).
2. ``current_version(palace_dir)`` — read the schema_version.
3. Compare against ``target_version``:
   - equal: early return, zero dirs touched.
   - greater: raise ``RuntimeError`` ("sidecar too old"); route
     handlers map to 500.
   - less: run the migration chain below.
4. ``_copy_hardlink_if_possible(palace_dir, migrating)`` — create
   the ``.migrating`` sibling via hardlinks on same-fs (fast, ~ms
   for small palaces; falls back to full copy on EXDEV).
5. For each migration ``m`` in the sorted chain where
   ``m.from_version >= v and m.to_version <= target_version``:
   run ``m.apply(migrating)``.
6. Write new ``meta.json`` to ``migrating`` with
   ``schema_version=target_version``.
7. ``os.sync()``, then the two-rename dance:
   ``rename(primary, replaced_<ts>)`` followed by
   ``rename(migrating, primary)``. A crash between these two is
   recovered by step 1 on next open.
8. ``reap_stale(palaces_dir)`` runs at lifespan startup (not here),
   eventually cleans the ``.replaced_<ts>`` and any stale
   ``.migrating`` where primary exists (size-pressure class of
   garbage, NOT recovery-pending class).

**Hardlink-snapshot-integrity invariant (spec §5.6 amendment
2026-04-21).** The hardlink rename means ``.replaced_<ts>`` and
the new primary share inodes. Any post-rename in-place modification
to primary files writes through the hardlink and silently mutates
the ``.replaced_<ts>`` snapshot, breaking rollback. Mitigation:
``PalaceHandle.open`` runs dim1 (in-place chromadb format migration)
BEFORE ``apply_migrations``, so the modification hits the pre-rename
inode. Future in-place operations must check ``os.stat(f).st_nlink``
== 1 first; if > 1, use copy-on-write (write to temp, rename-over).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Iterable

from mempalace_sidecar.migrations._base import Migration


def current_version(palace_dir: Path) -> int:
    """Return ``palace_dir/meta.json``'s ``schema_version``, or ``0``
    if meta.json is missing. Does NOT raise on corrupt meta.json —
    that's ``PalaceHandle.open``'s job to handle (spec §5.6 step 2:
    corrupt meta raises ``RuntimeError`` from the caller).
    """
    meta = palace_dir / "meta.json"
    if not meta.exists():
        return 0
    return int(json.loads(meta.read_text()).get("schema_version", 0))


def _copy_hardlink_if_possible(src: Path, dst: Path) -> None:
    """Copy ``src`` → ``dst`` using hardlinks on same-filesystem
    (fast); fall back to a full copy on EXDEV or any filesystem
    that refuses links.

    The hardlink path is the hot path — a ~100 MB palace dir
    copies in milliseconds via link vs seconds via full byte-copy.
    The fallback path handles cross-filesystem cases
    (``settings.data_dir`` on one FS, tmpdir on another) and
    filesystem backends that refuse hardlinks (certain
    overlayfs/fuse combinations on some container runtimes).

    On fallback, the partial ``dst`` from the failed hardlink
    attempt is rm-rf'd first so the subsequent full copy starts
    from a clean slate.
    """
    try:
        shutil.copytree(src, dst, copy_function=os.link)
    except OSError:
        # EXDEV (cross-device), permission-denied on hardlink, or
        # backend that refuses link — fall back to a real copy.
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)


def _recover_if_interrupted(palace_dir: Path) -> bool:
    """Detect an interrupted-rename state and promote the migrated
    sibling.

    Spec §5.6 step 4 principle: the original ``U/`` directory is
    never mutated until verification passes, and a crash between
    the two renames (primary → replaced_<ts>, migrating → primary)
    must not lose the migrated data. The filesystem state after
    such a crash: primary is gone, ``.migrating`` still exists,
    ``.replaced_<ts>`` is the old state. Call this on every open
    before touching ``palace_dir``.

    Returns True if a recovery happened.
    """
    migrating = palace_dir.with_name(palace_dir.name + ".migrating")
    if not palace_dir.exists() and migrating.exists():
        # Crashed after the first rename but before the second.
        # The migrated version is in .migrating; promote it to
        # primary. os.sync to flush the rename to disk — we don't
        # want a second crash here to re-orphan the migrated state.
        os.rename(migrating, palace_dir)
        os.sync()
        return True
    return False


def apply_migrations(
    palace_dir: Path,
    target_version: int,
    migrations: Iterable[Migration],
) -> None:
    """Apply the migration chain from ``current_version(palace_dir)``
    up to ``target_version`` via the sibling-rename atomic pattern.

    Early-return on ``v == target``. Raises ``RuntimeError`` with
    "sidecar too old" substring on ``v > target`` (route handlers
    map to HTTP 500 via FastAPI's default exception handling).

    Each migration in the chain runs against the ``.migrating``
    sibling — modifications there are safe because either
    (a) the full chain succeeds and ``.migrating`` becomes the new
    primary via the two-rename sequence, or (b) any migration
    raises, in which case ``.migrating`` is rm-rf'd before the
    exception propagates. The primary directory is never mutated
    until verification passes.
    """
    _recover_if_interrupted(palace_dir)
    v = current_version(palace_dir)
    if v == target_version:
        return
    if v > target_version:
        raise RuntimeError(
            f"palace schema_version {v} > sidecar "
            f"CURRENT_SCHEMA_VERSION {target_version}; sidecar too old"
        )

    migrating = palace_dir.with_name(palace_dir.name + ".migrating")
    if migrating.exists():
        # Stale ``.migrating`` from a previous aborted run with primary
        # still present — safe to rm since primary is the authoritative
        # state. (The "primary missing + migrating present" case was
        # handled above by _recover_if_interrupted.)
        shutil.rmtree(migrating)
    _copy_hardlink_if_possible(palace_dir, migrating)
    try:
        for m in sorted(migrations, key=lambda x: x.from_version):
            if m.from_version >= v and m.to_version <= target_version:
                m.apply(migrating)
                v = m.to_version
        # Finalise meta.json in the migrating sibling.
        #
        # **HARDLINK-BREAK before write (spec §5.6 amendment
        # 2026-04-21, hardlink-snapshot-integrity invariant
        # applied to the runner's own writes).** Linux does NOT
        # copy-on-write hardlinked files; ``write_text`` uses
        # ``open(..., 'w')`` which truncates the shared inode and
        # writes, propagating the new content through EVERY
        # hardlink — including primary's meta.json (which becomes
        # ``.replaced_<ts>/meta.json`` after the first rename).
        # Without the unlink here, the snapshot silently shows
        # target_version instead of the pre-migration version,
        # defeating rollback integrity. Discovered empirically via
        # ``test_atomic_rename_happy_path`` — M0.9 Stage 3 finding.
        # Plan code at line 2784 had the bug.
        meta_path_migrating = migrating / "meta.json"
        if (
            meta_path_migrating.exists()
            and meta_path_migrating.stat().st_nlink > 1
        ):
            # Hardlink from the copytree. Unlinking here only removes
            # the migrating-side link; primary's copy survives with
            # its own st_nlink count decremented by one (still >= 1).
            meta_path_migrating.unlink()
        meta_path_migrating.write_text(
            json.dumps({
                "schema_version": target_version,
                "migrated_at": time.time(),
            })
        )
        os.sync()
        replaced = palace_dir.with_name(
            palace_dir.name + f".replaced_{int(time.time())}"
        )
        # Two renames. A crash here leaves palace_dir gone and
        # migrating still present; _recover_if_interrupted picks
        # that up on next open.
        os.rename(palace_dir, replaced)
        os.rename(migrating, palace_dir)
    except Exception:
        if migrating.exists():
            # Pre-rename-sequence failure (migration apply raised,
            # meta.json write failed, etc.) — primary is still
            # authoritative; safe to rm migrating. Post-first-rename
            # failure would leave primary missing, which
            # _recover_if_interrupted handles on next open.
            shutil.rmtree(migrating, ignore_errors=True)
        raise


def reap_stale(palaces_dir: Path, max_age_sec: int = 24 * 3600) -> int:
    """Reap ``.replaced_*`` dirs and orphan ``.migrating`` dirs
    older than ``max_age_sec``.

    **CAUTION: never reap a ``.migrating`` that is the only copy of
    a user's data.** The "primary missing + migrating present"
    state is a recovery-in-progress shape;
    ``_recover_if_interrupted`` will promote migrating on the next
    palace open. Reaping here would silently delete the migrated
    data before recovery ran. Rule encoded: refuse to delete
    ``<U>.migrating`` if ``<U>`` does not exist.

    Returns the count of directories reaped.
    """
    n = 0
    now = time.time()
    for child in palaces_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name.endswith(".migrating"):
            sibling = palaces_dir / name[: -len(".migrating")]
            if not sibling.exists():
                # Recovery-in-progress. Leave it alone.
                continue
        elif ".replaced_" not in name:
            continue
        try:
            age = now - child.stat().st_mtime
            if age > max_age_sec:
                shutil.rmtree(child, ignore_errors=True)
                n += 1
        except FileNotFoundError:
            continue
    return n


__all__ = [
    "current_version",
    "_copy_hardlink_if_possible",
    "_recover_if_interrupted",
    "apply_migrations",
    "reap_stale",
]
