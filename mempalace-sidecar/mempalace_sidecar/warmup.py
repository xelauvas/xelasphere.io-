"""Chroma embedding pre-warm (M0.5.1).

ChromaDB loads the sentence-transformers model lazily on first query.
A cold first /wake-up would pay that ~2-5s one-time load synchronously.
``prime_embedding_function`` forces the load at sidecar startup by
issuing a throwaway query against a dedicated ``_warmup`` palace.

Spec §5.3 "Embedding-model scope": the model is PROCESS-global —
warming it once in the lifespan makes every subsequent user palace
open cheap.

The warmup palace is never used for real user data; it exists only to
host a Chroma collection we can issue ``col.query(query_texts=...)``
against. Its directory is created if absent.
"""

from __future__ import annotations

from pathlib import Path


def prime_embedding_function(warmup_palace_path: Path) -> None:
    """Force ChromaDB's default embedding function to load eagerly.

    Args:
        warmup_palace_path: Dedicated directory for the throwaway
            ``_warmup`` palace. Created on first call, reused after.

    Side-effect: resolves the sentence-transformers model and holds it
    in the process-global cache for the lifetime of the process.

    Library-isolation note: this module imports ``mempalace.palace``
    directly. That's allowed because the warmup palace is not a user
    palace — there's no ``PalaceHandle`` around it and no user-scoped
    state to protect. The alternative (``PalaceHandle.open`` + query)
    would build + open a kg.sqlite, a turns.sqlite, and a config dir
    we never touch.
    """
    from mempalace.palace import get_collection

    warmup_palace_path = Path(warmup_palace_path)
    warmup_palace_path.mkdir(parents=True, exist_ok=True)
    col = get_collection(str(warmup_palace_path), create=True)
    # query_texts forces the embedding function to resolve and load.
    # n_results=1 keeps the query cheap; an empty palace returns no rows.
    col.query(query_texts=["warmup"], n_results=1)


__all__ = ["prime_embedding_function"]
