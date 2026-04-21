"""Tests for ``mempalace_sidecar._chromadb_patch``.

Three assertions this file locks in:

1. ``test_patch_applied_on_bare_import`` — importing the package alone
   (no create_app, no lifespan) applies the patch. If this fails, some
   refactor has changed the import order so that chromadb can be
   touched before our patch runs.

2. ``test_default_ef_singleton_invariant`` — CANARY. A fresh
   ``DefaultEmbeddingFunction()(input)`` called N times constructs
   ``ONNXMiniLM_L6_V2`` exactly once. Two failure modes matter:
     * count == N  → our patch is not taking effect (chromadb is back
       to per-call instantiation). Re-verify the patch site.
     * count == 0  → cache was already populated from earlier in the
       run and the spy couldn't observe construction. Fixture reset
       failed; investigate.

3. ``test_patch_idempotent`` — re-importing ``_chromadb_patch`` does
   NOT re-patch (the idempotency guard catches the marker attribute
   and no-ops). If this fails, a fork-then-import or a test reload
   could double-wrap ``__call__`` — harmless today, but the marker
   discipline prevents future compounding weirdness.
"""

from __future__ import annotations

import importlib

from chromadb.api.types import DefaultEmbeddingFunction


def test_patch_applied_on_bare_import() -> None:
    import mempalace_sidecar  # noqa: F401  # triggers the side-effect import

    assert getattr(DefaultEmbeddingFunction.__call__, "_xela_patched", False), (
        "DefaultEmbeddingFunction.__call__ is not patched. "
        "mempalace_sidecar._chromadb_patch failed to apply at import time. "
        "Check the import order in mempalace_sidecar/__init__.py."
    )


def test_default_ef_singleton_invariant(monkeypatch) -> None:
    # Import the package so the patch is applied, then force the
    # cache back to empty so the spy can observe the (single)
    # reconstruction. Without this reset the test would see 0
    # constructions on the second run of the test session.
    import mempalace_sidecar._chromadb_patch as patch_mod  # noqa: F401
    monkeypatch.setattr(patch_mod, "_cached_onnx", None)

    from chromadb.utils.embedding_functions import onnx_mini_lm_l6_v2
    OriginalONNX = onnx_mini_lm_l6_v2.ONNXMiniLM_L6_V2

    ctor_count = 0

    class CountingONNX(OriginalONNX):  # type: ignore[valid-type, misc]
        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            nonlocal ctor_count
            ctor_count += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(onnx_mini_lm_l6_v2, "ONNXMiniLM_L6_V2", CountingONNX)

    # Five calls via fresh DefaultEmbeddingFunction instances — the
    # exact pattern chromadb uses internally when dispatching
    # collection embeddings. With our patch: one construction total.
    for _ in range(5):
        DefaultEmbeddingFunction()(["sentinel text"])

    assert ctor_count == 1, (
        f"ONNXMiniLM_L6_V2 constructed {ctor_count} times across 5 "
        f"DefaultEmbeddingFunction calls; expected 1 (singleton). "
        f"If 5: the patch is broken or chromadb reintroduced per-call "
        f"instantiation. If 0: the monkeypatch fixture didn't clear "
        f"the cached instance — investigate."
    )


def test_patch_idempotent() -> None:
    import mempalace_sidecar._chromadb_patch as patch_mod

    before = DefaultEmbeddingFunction.__call__
    importlib.reload(patch_mod)
    after = DefaultEmbeddingFunction.__call__

    assert before is after, (
        "Reloading _chromadb_patch replaced DefaultEmbeddingFunction."
        "__call__ — the idempotency guard failed to detect the existing "
        "_xela_patched marker."
    )
