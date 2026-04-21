"""OpenAPI snapshot contract test (M0.12).

Freezes the FastAPI app's OpenAPI schema as a committed JSON file
and asserts the current app produces byte-for-byte the same shape.
Any route add / remove, Pydantic model field change, response
model change, or docstring change reds this test.

**Remediation protocol** (embedded in the fail message too, for
the contributor who doesn't open the test file):

  - **Intentional schema change** (you added a route, renamed a
    field, bumped a response model): run
    ``python -m mempalace_sidecar.tools.regen_openapi`` and commit
    the updated ``tests/contract/openapi_schema.json``. If the
    change is specifically in the ``schema_version``-tracked
    surface (``/healthz`` or ``/readyz`` response contract), ALSO
    bump ``SCHEMA_VERSION`` in ``routes/ops.py``. The snapshot
    file itself is not versioned by SCHEMA_VERSION — git history
    is the historical record.

  - **Unexpected red after a dependency bump** (e.g., ``uv sync``
    moved FastAPI or Pydantic): the fail message emits the
    current fastapi / pydantic / python versions. Check
    ``uv.lock`` diff BEFORE regenerating — blind regen silently
    absorbs a library's output changes into the committed
    snapshot, which defeats the contract.

**Nastiest reachable context** (B4 checklist): environment-
dependent fields sneaking into ``app.openapi()`` output. The
test's fixture env (fixed token + salt, tmp_path as data_dir)
matches the regen tool's env so the two outputs agree. If a
future route adds a docstring containing a settings-derived
value, the snapshot would drift per-environment — this test
would fail on any machine with a different data_dir, surfacing
the leak.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

import pytest


def _env_diagnostic() -> str:
    """Versions whose upgrades are the most likely source of
    unexpected schema drift. Rendered at the top of the failure
    message so the contributor sees them BEFORE the diff.
    """
    import fastapi
    import pydantic

    return (
        "=== environment (check these first if drift is unexpected) ===\n"
        f"  fastapi={fastapi.__version__}\n"
        f"  pydantic={pydantic.__version__}\n"
        f"  python={sys.version.split()[0]}\n"
        "================================================================\n"
    )


_REMEDIATION = (
    "If the drift is INTENTIONAL (route / field / model / docstring "
    "change you made): run "
    "`python -m mempalace_sidecar.tools.regen_openapi` and commit "
    "the updated snapshot. If the change was specifically in the "
    "/healthz or /readyz response contract, ALSO bump SCHEMA_VERSION "
    "in routes/ops.py.\n\n"
    "If the drift is UNEXPECTED (you didn't change any route / "
    "model): the environment diagnostic above names the fastapi / "
    "pydantic / python versions. Check uv.lock for a dependency "
    "bump BEFORE regenerating — blind regen absorbs library output "
    "changes into the committed snapshot and defeats the contract."
)


def test_openapi_schema_matches_snapshot(monkeypatch, tmp_path: Path) -> None:
    """Committed ``tests/contract/openapi_schema.json`` MUST match
    ``create_app().openapi()`` byte-for-byte after normalization.
    """
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)

    from mempalace_sidecar.main import create_app

    app = create_app()
    current = app.openapi()

    snap_path = Path(__file__).parent / "openapi_schema.json"
    if not snap_path.exists():
        pytest.fail(
            "OpenAPI snapshot file is missing at "
            f"{snap_path}.\n\n"
            "This is expected at M0.12 Stage 1 RED. Stage 3 GREEN "
            "generates the snapshot via "
            "`python -m mempalace_sidecar.tools.regen_openapi` and "
            "commits the output."
        )

    # Corrupted / unreadable snapshot (merge-conflict markers,
    # truncated file, encoding mishap) surfaces with the same
    # "run the regen tool" remediation as any other drift — future
    # contributor shouldn't have to infer the fix from a raw
    # JSONDecodeError.
    try:
        expected = json.loads(snap_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        pytest.fail(
            f"OpenAPI snapshot at {snap_path} is unreadable "
            f"({type(exc).__name__}: {exc}). If the file has merge "
            "markers, truncation, or other corruption, run "
            "`python -m mempalace_sidecar.tools.regen_openapi` "
            "to regenerate it cleanly from the current app, then "
            "commit the result."
        )

    if current == expected:
        return

    # Structured diff with env diagnostic header.
    expected_str = json.dumps(expected, indent=2, sort_keys=True).splitlines()
    current_str = json.dumps(current, indent=2, sort_keys=True).splitlines()
    diff = "\n".join(
        difflib.unified_diff(
            expected_str,
            current_str,
            fromfile="openapi_schema.json (committed snapshot)",
            tofile="current (from create_app().openapi())",
            lineterm="",
        )
    )
    pytest.fail(
        "OpenAPI schema drifted from the committed snapshot.\n\n"
        f"{_env_diagnostic()}"
        f"\n{diff}\n\n"
        f"{_REMEDIATION}"
    )
