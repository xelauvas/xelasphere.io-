"""Regenerate the committed OpenAPI snapshot (M0.12, spec §5.2).

Usage::

    python -m mempalace_sidecar.tools.regen_openapi

Emits the current FastAPI app's OpenAPI schema to
``mempalace-sidecar/tests/contract/openapi_schema.json`` with
sorted keys + 2-space indent for diffable git output.

**Env handling.** The tool sets ``MEMPALACE_TOKEN`` / ``_DATA_DIR`` /
``_USER_ID_HASH_SALT`` to dev-safe defaults via ``setdefault`` (so
an operator's real prod-ish env doesn't get overwritten) and uses
a ``tempfile.mkdtemp``-provided data_dir so regen runs don't leave
``/tmp/mp`` litter or collide with concurrent invocations. The
snapshot is pure-route-introspection output; data_dir / token /
salt values don't leak into the rendered schema.

**Determinism contract.** ``app.openapi()`` is stable across
invocations given the same FastAPI version + Python version +
Pydantic version + route set. Drift sources the snapshot test is
designed to CATCH:

  1. Route add / remove / path change.
  2. Pydantic model field add / remove / rename.
  3. Response model or status_code change.
  4. Route docstring change (surfaces in ``description``).
  5. FastAPI minor-version bump that shifts docstring / error
     response / openapi-version handling.

If the snapshot test reds after a dependency upgrade, the fail
message surfaces the current FastAPI / Pydantic / Python versions
so the operator can check ``uv.lock`` before blindly regenerating.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _snapshot_path() -> Path:
    """Resolve ``tests/contract/openapi_schema.json`` relative to this module.

    ``__file__`` lives at
    ``mempalace-sidecar/mempalace_sidecar/tools/regen_openapi.py``;
    ``parents[2]`` climbs to the sidecar repo root.
    """
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "contract"
        / "openapi_schema.json"
    )


_ENV_DEFAULTS = {
    "MEMPALACE_TOKEN": "t" * 32,
    "MEMPALACE_DATA_DIR": None,  # filled in from TemporaryDirectory
    "MEMPALACE_USER_ID_HASH_SALT": "x" * 16,
}


def regenerate() -> Path:
    """Write the current OpenAPI snapshot and return its path.

    Env-var restoration: the three MEMPALACE_* vars are
    save/restored around the setdefault block so importing
    ``regenerate()`` from a long-lived process (tests, notebooks)
    doesn't leak a dev-token / dev-salt / now-cleaned-up-tempdir
    ``MEMPALACE_DATA_DIR`` into the caller's environment. CLI
    invocation via ``__main__`` drops the env on process exit
    regardless; the restoration matters only for the import path.
    """
    saved: dict[str, str | None] = {
        key: os.environ.get(key) for key in _ENV_DEFAULTS
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.setdefault("MEMPALACE_TOKEN", _ENV_DEFAULTS["MEMPALACE_TOKEN"])
            os.environ.setdefault("MEMPALACE_DATA_DIR", tmp)
            os.environ.setdefault("MEMPALACE_USER_ID_HASH_SALT", _ENV_DEFAULTS["MEMPALACE_USER_ID_HASH_SALT"])
            # Deferred import so env vars land before Settings parse.
            from mempalace_sidecar.main import create_app

            app = create_app()
            schema = app.openapi()
    finally:
        # Restore: if the key wasn't set before, remove it; if it
        # was set to a prior value, restore it. Preserves
        # operator-env transparency.
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline so the file plays nicely with editors that
    # auto-add one on save (no spurious diff noise).
    try:
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        # Disk full / permissions / read-only FS — surface a clean
        # message so the operator doesn't have to parse a raw
        # traceback to understand the failure.
        raise RuntimeError(
            f"failed to write OpenAPI snapshot to {path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return path


def main() -> None:
    path = regenerate()
    # Print a relative-ish path so the operator can grep for it in
    # the repo without the full absolute path cluttering output.
    try:
        rel = path.relative_to(Path.cwd())
    except ValueError:
        rel = path
    print(f"wrote {rel}")


if __name__ == "__main__":
    main()
