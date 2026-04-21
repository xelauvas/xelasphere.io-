"""Migration registry (M0.9, spec §5.6 dim3).

Exports ``ALL_MIGRATIONS: list[Migration]`` — the explicit chain of
single-step schema migrations from ``from_version=0`` up through
``CURRENT_SCHEMA_VERSION``. Explicit registration (not
``pkgutil.iter_modules``-style auto-discovery) is a feature: every
new migration requires a deliberate edit here, which forces reviewer
attention on the migration chain's shape.

``_validate`` runs at module import time. If ``ALL_MIGRATIONS`` has
any of the following, import raises ``RuntimeError`` and the sidecar
fails to start:

- A migration with ``from_version < 0``
- A migration with ``to_version != from_version + 1`` (multi-step)
- Two migrations with the same ``from_version`` (duplicate)
- A gap in the chain (e.g., v0→v1 present but v1→v2 missing when
  v2→v3 exists)

Catching these at sidecar-startup time beats catching them at first
palace open, which beats catching them at runtime inside
``apply_migrations``.
"""

from __future__ import annotations

from mempalace_sidecar.migrations._base import Migration
from mempalace_sidecar.migrations.v0_to_v1 import V0ToV1NoOp


ALL_MIGRATIONS: list[Migration] = [V0ToV1NoOp()]


def _validate(migrations: list[Migration]) -> None:
    """Raise ``RuntimeError`` on any malformed migration chain.

    Called at module import against ``ALL_MIGRATIONS``. Parameterized
    to be unit-testable against synthetic chains without monkey-
    patching the module.
    """
    if not migrations:
        # Empty chain is legal — there's nothing to upgrade through.
        # CURRENT_SCHEMA_VERSION will either be 0 (fresh project, never
        # migrated) or higher (but then _validate should have been
        # populated).
        return

    sorted_migrations = sorted(migrations, key=lambda m: m.from_version)
    for i, m in enumerate(sorted_migrations):
        if m.from_version < 0:
            raise RuntimeError(
                f"migration {m.__class__.__name__} has "
                f"from_version={m.from_version} < 0; invalid"
            )
        if m.to_version != m.from_version + 1:
            raise RuntimeError(
                f"migration {m.__class__.__name__} is not a single-step "
                f"upgrade ({m.from_version} -> {m.to_version}); "
                f"multi-step migrations are not supported"
            )
        if i > 0:
            prev = sorted_migrations[i - 1]
            if prev.from_version == m.from_version:
                raise RuntimeError(
                    f"duplicate from_version {m.from_version} in "
                    f"{prev.__class__.__name__} and "
                    f"{m.__class__.__name__}"
                )
            if prev.to_version != m.from_version:
                raise RuntimeError(
                    f"gap in migration chain: "
                    f"{prev.__class__.__name__} ends at "
                    f"v{prev.to_version}, next migration "
                    f"{m.__class__.__name__} starts at "
                    f"v{m.from_version}"
                )


_validate(ALL_MIGRATIONS)


__all__ = ["ALL_MIGRATIONS", "Migration", "_validate"]
