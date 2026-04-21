"""Abstract Migration base for schema-version migrations (M0.9, spec §5.6 dim3).

Each concrete migration lives in its own module under
``mempalace_sidecar/migrations/`` and registers itself in
``migrations/__init__.py``'s ``ALL_MIGRATIONS`` list. Discovery is
explicit (not via ``pkgutil.iter_modules``) so every new migration
forces a deliberate edit — the kind of attention schema migrations
deserve.

**Single-step only.** Each migration goes from version ``N`` to
``N+1``. Multi-step migrations (e.g., v0 → v3 in one apply) are not
supported in v1: the runner chains single-step migrations in order.
See ``migrations/__init__.py::_validate`` for the load-time check
that enforces this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Migration(ABC):
    """Base class for a single-step schema migration.

    Subclasses define:

    - ``from_version: int`` — the schema_version that this migration
      upgrades FROM.
    - ``to_version: int`` — must equal ``from_version + 1``.
    - ``apply(palace_dir)`` — mutate the palace directory in place.
      Called by ``apply_migrations`` on the ``.migrating`` sibling
      (never on the primary), so the apply is free to modify files
      destructively: rollback is "rm -rf .migrating" if any
      migration in the chain raises.
    """

    from_version: int
    to_version: int

    @abstractmethod
    def apply(self, palace_dir: Path) -> None:
        """Apply this migration's transformations to ``palace_dir``.

        ``palace_dir`` is the ``.migrating`` sibling directory at call
        time. Modifying files here is safe: the runner either renames
        ``.migrating`` to primary on success, or ``rm -rf``'s it on
        failure.

        The migration MUST NOT update ``meta.json``'s
        ``schema_version`` field itself — the runner writes the final
        meta.json once all migrations in the chain have applied.
        """
        ...


__all__ = ["Migration"]
