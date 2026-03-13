"""
Memory V2 Embeddings
====================
API-based embedding support for semantic memory retrieval.

Uses Jina Embeddings API for high-quality multilingual embeddings.
Zero local model loading — no PyTorch/transformers in RAM.
"""

import logging
import os
import requests
import numpy as np
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# API configuration
JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_API_KEY = os.getenv('JINA_API_KEY', '')
JINA_MODEL = "jina-embeddings-v3"
EMBEDDING_DIM = 1024  # jina-embeddings-v3 dimension

# Request timeout
API_TIMEOUT = 10  # seconds


def _call_jina_api(texts: List[str], task: str = "retrieval.passage") -> Optional[List[List[float]]]:
    """
    Call Jina Embeddings API.

    Args:
        texts: List of texts to embed
        task: Task type for LoRA adapter ('retrieval.query', 'retrieval.passage', 'text-matching')

    Returns:
        List of embedding vectors, or None if failed
    """
    if not JINA_API_KEY:
        logger.debug("Jina API key not configured; embeddings disabled")
        return None

    try:
        response = requests.post(
            JINA_API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {JINA_API_KEY}",
            },
            json={
                "model": JINA_MODEL,
                "input": texts,
                "task": task,
                "normalized": True,
                "embedding_type": "float",
            },
            timeout=API_TIMEOUT,
        )

        if response.status_code != 200:
            logger.warning(f"Jina API error {response.status_code}: {response.text[:200]}")
            return None

        data = response.json()
        # Sort by index to maintain order
        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in embeddings]

    except requests.exceptions.Timeout:
        logger.warning("Jina API timeout")
        return None
    except Exception as e:
        logger.error(f"Jina API call failed: {e}")
        return None


def get_embedding_dim() -> int:
    """Get the embedding dimension."""
    return EMBEDDING_DIM


def embed_text(text: str) -> Optional[np.ndarray]:
    """
    Generate embedding for a single text (passage/document).

    Args:
        text: Text to embed

    Returns:
        Numpy array of embedding, or None if failed
    """
    result = _call_jina_api([text], task="retrieval.passage")
    if result and len(result) > 0:
        return np.array(result[0], dtype=np.float32)
    return None


def embed_query(query: str) -> Optional[np.ndarray]:
    """
    Generate embedding for a query.

    Args:
        query: Query text to embed

    Returns:
        Numpy array of embedding, or None if failed
    """
    result = _call_jina_api([query], task="retrieval.query")
    if result and len(result) > 0:
        return np.array(result[0], dtype=np.float32)
    return None


def embed_batch(texts: List[str], is_query: bool = False) -> Optional[np.ndarray]:
    """
    Generate embeddings for multiple texts.

    Args:
        texts: List of texts to embed
        is_query: If True, use query task

    Returns:
        Numpy array of embeddings (N x dim), or None if failed
    """
    if not texts:
        return None

    task = "retrieval.query" if is_query else "retrieval.passage"
    result = _call_jina_api(texts, task=task)
    if result:
        return np.array(result, dtype=np.float32)
    return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cosine_similarity_batch(query_embedding: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between query and multiple embeddings.

    Args:
        query_embedding: Query vector (dim,)
        embeddings: Matrix of embeddings (N x dim)

    Returns:
        Array of similarities (N,)
    """
    query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-9)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-9
    embeddings_norm = embeddings / norms
    similarities = np.dot(embeddings_norm, query_norm)
    return similarities


def find_similar(
    query_embedding: np.ndarray,
    embeddings: List[Tuple[str, np.ndarray]],
    top_k: int = 10,
    threshold: float = 0.3
) -> List[Tuple[str, float]]:
    """
    Find most similar items to query.

    Args:
        query_embedding: Query vector
        embeddings: List of (id, embedding) tuples
        top_k: Number of results to return
        threshold: Minimum similarity threshold

    Returns:
        List of (id, similarity) tuples, sorted by similarity descending
    """
    if not embeddings or query_embedding is None:
        return []

    results = []
    for item_id, item_embedding in embeddings:
        if item_embedding is None:
            continue
        sim = cosine_similarity(query_embedding, item_embedding)
        if sim >= threshold:
            results.append((item_id, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def embedding_to_list(embedding: np.ndarray) -> List[float]:
    """Convert numpy embedding to list for JSON/Firestore storage."""
    if embedding is None:
        return []
    return embedding.tolist()


def list_to_embedding(data: List[float]) -> Optional[np.ndarray]:
    """Convert list back to numpy embedding."""
    if not data:
        return None
    return np.array(data, dtype=np.float32)


def embeddings_available() -> bool:
    """Check if embedding API is configured."""
    return bool(JINA_API_KEY)
