"""Integration test for M0.5.1 Chroma embedding pre-warm.

``prime_embedding_function`` must force-load the sentence-transformers
model at sidecar startup so the first user /wake-up doesn't pay the
~2-5s cold-model load.

Spec §5.3 ("Embedding-model scope — process-global, warmed once") is
the contract: after ``prime_embedding_function`` returns, a fresh
Chroma collection in a different palace runs its first query in a
budget dominated by PersistentClient init (~50-200 ms), NOT by model
load.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_warmup_primes_chroma_embedding(tmp_path: Path) -> None:
    """After ``prime_embedding_function`` runs, a fresh-collection query
    in a DIFFERENT palace completes quickly — the model is cached in
    the process.

    We deliberately use a SECOND palace (not the warmup one) for the
    post-warmup timing: if we reused the warmup palace, Chroma's own
    in-memory PersistentClient cache would mask the model-level caching
    we're actually testing.
    """
    from mempalace_sidecar.warmup import prime_embedding_function

    # Prime the model via the throwaway ``_warmup`` palace.
    prime_embedding_function(tmp_path / "_warmup")

    # Open a DIFFERENT palace. If the warmup worked, the model weights
    # are already resident, and the first query on this palace pays
    # only PersistentClient init + Chroma query cost.
    from mempalace.palace import get_collection

    fresh_path = tmp_path / "fresh_palace"
    fresh_path.mkdir(parents=True, exist_ok=True)
    col = get_collection(str(fresh_path), create=True)

    t0 = time.monotonic()
    _ = col.query(query_texts=["probe"], n_results=1)
    elapsed = time.monotonic() - t0

    # Budget: sentence-transformers cold load is 2-5s. Chroma client
    # init + a single empty-collection query without model load is well
    # under 1.5s even on modest CI hardware. If this fails, the model
    # re-loaded — prime_embedding_function is not working.
    assert elapsed < 1.5, (
        f"post-warmup query took {elapsed:.2f}s — suggests model "
        f"re-loaded. Spec §5.3 requires process-global weight caching."
    )


def test_warmup_palace_directory_created(tmp_path: Path) -> None:
    """prime_embedding_function creates its palace directory if absent.

    Wired into the lifespan with ``settings.data_dir / "_warmup"``. On
    a fresh install that directory doesn't exist yet — the helper MUST
    mkdir it, else startup crashes with FileNotFoundError.
    """
    from mempalace_sidecar.warmup import prime_embedding_function

    warmup_path = tmp_path / "nested" / "path" / "_warmup"
    assert not warmup_path.exists()
    prime_embedding_function(warmup_path)
    assert warmup_path.exists()
    assert warmup_path.is_dir()


def test_lifespan_sets_embedding_ready(monkeypatch, tmp_path: Path) -> None:
    """The lifespan MUST set ``app.state.embedding_ready = True`` after
    ``prime_embedding_function`` completes — that's what the future
    /readyz (M0.11) will observe to gate traffic.

    Drives the app lifespan via ``TestClient`` (sync), which triggers
    startup synchronously, then reads the flag off ``app.state``.
    """
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)

    from fastapi.testclient import TestClient

    from mempalace_sidecar.main import create_app

    app = create_app()
    with TestClient(app):
        assert getattr(app.state, "embedding_ready", False) is True
