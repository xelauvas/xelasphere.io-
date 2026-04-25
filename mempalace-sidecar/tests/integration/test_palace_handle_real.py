"""Integration tests for PalaceHandle against real Chroma + sentence-transformers.

Marked ``integration`` because they exercise the full library stack —
ChromaDB PersistentClient, the default sentence-transformers embedding
function, and real on-disk sqlite. Skipped by default only if the marker
filters are applied at pytest time.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mempalace_sidecar.palace_handle import PalaceHandle

pytestmark = pytest.mark.integration


def test_cold_palace_open_reuses_embedding_model(tmp_path: Path):
    """Spec §5.3 "Embedding-model scope — process-global, warmed once":
    opening a fresh palace after the process has already loaded the
    default embedding function (via an earlier palace open + query)
    MUST NOT re-pay the model-load cost. The second palace's query
    completes comfortably within a tight budget because the weights are
    already resident.

    We don't call the explicit M0.5.1 ``prime_embedding_function`` helper
    here — M0.3.0 doesn't ship it yet. Instead we open a warmup palace
    and issue one query, which forces ChromaDB's default embedding
    function to resolve and load. Then we open a fresh palace and time
    the first query — it should be dominated by Chroma PersistentClient
    init (~50-200 ms), not by model load (~2-5 s).
    """
    # Warmup palace: force the library to resolve and cache the embedding
    # function in the process.
    warmup_path = tmp_path / "warmup"
    warm = PalaceHandle.open(warmup_path)
    try:
        warm.add_drawer(
            turn_id="warm",
            session_id="s",
            pair_id="p",
            role="user",
            text="seed drawer for warmup",
            ts=1.0,
        )
        # Trigger an embedding call so the ONNX/sentence-transformers
        # model loads into process memory.
        _ = warm.semantic_search("anything", k=1)
    finally:
        warm.close()

    # Cold palace open: fresh directory, but the process-global embedding
    # weights are already resident. A full open + query must fit inside a
    # generous budget that would FAIL if the model re-loaded from disk.
    cold_path = tmp_path / "cold"
    t0 = time.perf_counter()
    cold = PalaceHandle.open(cold_path)
    try:
        cold.add_drawer(
            turn_id="cold",
            session_id="s",
            pair_id="p",
            role="user",
            text="cold palace first drawer",
            ts=1.0,
        )
        _ = cold.semantic_search("anything", k=1)
    finally:
        cold.close()
    elapsed = time.perf_counter() - t0

    # Budget: spec says cold opens pay only ~50-200 ms for Chroma init;
    # we add generous headroom (CI overhead, first-query distance math,
    # closet-collection open) but stay well under the ~2-5 s model load
    # that would indicate the weights aren't process-cached. 2.0 s is the
    # ceiling the spec intends.
    assert elapsed < 2.0, (
        f"cold palace open + query took {elapsed:.2f}s — suggests the "
        f"embedding model re-loaded. Spec §5.3 requires process-global "
        f"weight caching (budget: < 2.0s)."
    )
