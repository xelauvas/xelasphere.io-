"""
Hybrid Retrieval - Lexical + Semantic + Structure
==================================================
Combines multiple retrieval strategies for better recall:

    1. BM25/Keyword: Exact matches for error codes, filenames, variables
    2. Semantic: Embedding similarity for conceptual matches
    3. Structured: Direct bucket lookups for known categories
    4. Recency: Time-based boosting

Scoring: 0.6 * semantic + 0.3 * lexical + 0.1 * recency

Why hybrid?
    - Embeddings miss: EADDRINUSE, 401, stack traces, variable names
    - Keywords miss: paraphrases, synonyms, conceptual similarity
    - Together: best of both worlds

Usage:
    from app.hybrid_retrieval import HybridRetriever

    retriever = HybridRetriever()
    retriever.index_messages(messages)

    results = retriever.search(
        query="EADDRINUSE error in server.py",
        top_k=5
    )
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Scoring weights
SEMANTIC_WEIGHT = 0.6
LEXICAL_WEIGHT = 0.3
RECENCY_WEIGHT = 0.1

# BM25 parameters
BM25_K1 = 1.5  # Term frequency saturation
BM25_B = 0.75  # Length normalization

# Recency decay (days)
RECENCY_HALF_LIFE = 7  # Score halves every 7 days

# Minimum scores to consider
MIN_LEXICAL_SCORE = 0.1
MIN_SEMANTIC_SCORE = 0.2

# =============================================================================
# Two-Stage Retrieval Configuration
# =============================================================================
# Stage 1: Limit candidates by recency/conversation before expensive cosine sim
# This dramatically reduces compute for users with large history

# Maximum messages to consider per user (prevents unbounded growth)
MAX_MESSAGES_PER_USER = 2000

# Maximum age of messages to consider (days)
MAX_MESSAGE_AGE_DAYS = 90

# Stage 1 candidate limit (BM25 pre-filter before semantic scoring)
STAGE1_CANDIDATE_LIMIT = 100

# Whether to search only current conversation by default
SEARCH_CURRENT_CONVERSATION_ONLY = True


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class RetrievalResult:
    """Single retrieval result with scores."""
    content: Dict[str, Any]  # Original message/entry
    text: str  # Searchable text
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    recency_score: float = 0.0
    combined_score: float = 0.0
    source: str = ""  # "message", "bucket", "summary"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "text": self.text[:200],
            "scores": {
                "semantic": round(self.semantic_score, 3),
                "lexical": round(self.lexical_score, 3),
                "recency": round(self.recency_score, 3),
                "combined": round(self.combined_score, 3)
            },
            "source": self.source
        }


@dataclass
class Document:
    """Indexed document for retrieval."""
    id: str
    text: str
    tokens: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    timestamp: Optional[datetime] = None
    source: str = "message"
    original: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Tokenization
# =============================================================================

# Patterns to preserve (don't split these)
PRESERVE_PATTERNS = [
    r'[A-Z][A-Z0-9_]{2,}',  # ERROR_CODES, CONSTANTS
    r'\b\d{3}\b',  # HTTP codes: 401, 500
    r'[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z]+',  # filenames: server.py
    r'[a-zA-Z_][a-zA-Z0-9_]*(?:Error|Exception)',  # ErrorTypes
    r'0x[0-9a-fA-F]+',  # Hex values
    r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b',  # CamelCase
]

STOPWORDS = {
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
    'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
    'during', 'before', 'after', 'above', 'below', 'between', 'under',
    'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
    'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
    'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until',
    'while', 'although', 'though', 'after', 'before', 'i', 'me', 'my',
    'myself', 'we', 'our', 'ours', 'you', 'your', 'yours', 'he', 'him',
    'his', 'she', 'her', 'hers', 'it', 'its', 'they', 'them', 'their',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'am', 'been', 'being', 'having', 'doing', 'would', 'could', 'should'
}


def tokenize(text: str, preserve_special: bool = True) -> List[str]:
    """
    Tokenize text while preserving important patterns.

    Args:
        text: Input text
        preserve_special: Whether to preserve error codes, filenames, etc.

    Returns:
        List of tokens (lowercase, no stopwords)
    """
    if not text:
        return []

    tokens = []
    preserved = set()

    # First, extract and preserve special patterns
    if preserve_special:
        for pattern in PRESERVE_PATTERNS:
            for match in re.finditer(pattern, text):
                preserved.add(match.group().lower())
                tokens.append(match.group().lower())

    # Standard tokenization
    words = re.findall(r'\b[a-zA-Z0-9_]+\b', text.lower())

    for word in words:
        if word in preserved:
            continue  # Already added
        if word in STOPWORDS:
            continue
        if len(word) < 2:
            continue
        tokens.append(word)

    return tokens


# =============================================================================
# BM25 Implementation
# =============================================================================

class BM25Index:
    """
    BM25 (Best Match 25) index for lexical retrieval.

    BM25 scoring considers:
        - Term frequency (with saturation)
        - Inverse document frequency
        - Document length normalization
    """

    def __init__(self, k1: float = BM25_K1, b: float = BM25_B):
        self.k1 = k1
        self.b = b
        self.documents: List[Document] = []
        self.doc_freqs: Dict[str, int] = {}  # Term -> doc count
        self.avg_doc_len: float = 0.0
        self.doc_lengths: List[int] = []

    def add_documents(self, documents: List[Document]) -> None:
        """Add documents to the index."""
        self.documents = documents
        self._build_index()

    def _build_index(self) -> None:
        """Build BM25 index structures."""
        self.doc_freqs = {}
        self.doc_lengths = []

        for doc in self.documents:
            self.doc_lengths.append(len(doc.tokens))

            # Count document frequencies
            unique_terms = set(doc.tokens)
            for term in unique_terms:
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1

        # Average document length
        if self.doc_lengths:
            self.avg_doc_len = sum(self.doc_lengths) / len(self.doc_lengths)
        else:
            self.avg_doc_len = 1.0

    def score(self, query_tokens: List[str], doc_idx: int) -> float:
        """
        Calculate BM25 score for a document given query tokens.

        BM25 formula:
            score = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D|/avgdl))
        """
        if doc_idx >= len(self.documents):
            return 0.0

        doc = self.documents[doc_idx]
        doc_len = self.doc_lengths[doc_idx]
        n_docs = len(self.documents)

        # Term frequencies in document
        tf = Counter(doc.tokens)

        score = 0.0
        for term in query_tokens:
            if term not in tf:
                continue

            # IDF: log((N - n + 0.5) / (n + 0.5) + 1)
            doc_freq = self.doc_freqs.get(term, 0)
            idf = math.log((n_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1)

            # TF component with saturation and length normalization
            term_freq = tf[term]
            length_norm = 1 - self.b + self.b * (doc_len / self.avg_doc_len)
            tf_component = (term_freq * (self.k1 + 1)) / (term_freq + self.k1 * length_norm)

            score += idf * tf_component

        return score

    def search(self, query: str, top_k: int = 10) -> List[Tuple[int, float]]:
        """
        Search index and return top-k (doc_idx, score) pairs.
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = []
        for idx in range(len(self.documents)):
            score = self.score(query_tokens, idx)
            if score > 0:
                scores.append((idx, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        # Normalize scores to [0, 1]
        if scores:
            max_score = scores[0][1]
            if max_score > 0:
                scores = [(idx, score / max_score) for idx, score in scores]

        return scores[:top_k]


# =============================================================================
# Semantic Search (uses existing embeddings)
# =============================================================================

def get_embedding(text: str) -> Optional[List[float]]:
    """Get embedding for text using Jina API (via memory_v2 embeddings)."""
    try:
        from app.memory_v2.embeddings import embed_text
        result = embed_text(text)
        if result is not None:
            return result.tolist()
        return None
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
        return None


def normalize_stored_embedding(embedding_data) -> Optional[List[float]]:
    """
    Normalize embedding data from storage, handling both formats:
    - Quantized format: {'data': base64, 'min': float, 'max': float}
    - Raw format: [float, float, ...]

    Args:
        embedding_data: Stored embedding (quantized dict or raw list)

    Returns:
        List of float32 values, or None if invalid
    """
    if not embedding_data:
        return None

    # Quantized format (new)
    if isinstance(embedding_data, dict) and 'data' in embedding_data:
        try:
            from .embedding_utils import embedding_from_storage
            result = embedding_from_storage(embedding_data)
            return result if result else None
        except ImportError:
            logger.warning("embedding_utils not available for quantized embedding")
            return None

    # Raw float list format (legacy)
    if isinstance(embedding_data, list) and len(embedding_data) > 0:
        return embedding_data

    return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


# =============================================================================
# Recency Scoring
# =============================================================================

def recency_score(timestamp: Optional[datetime], half_life_days: float = RECENCY_HALF_LIFE) -> float:
    """
    Calculate recency score with exponential decay.

    Score = 0.5 ^ (age_days / half_life)

    Examples (half_life=7):
        - Today: 1.0
        - 7 days ago: 0.5
        - 14 days ago: 0.25
        - 30 days ago: ~0.06
    """
    if not timestamp:
        return 0.5  # Default for unknown timestamps

    now = datetime.utcnow()

    # Handle string timestamps
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            # Strip timezone to make naive (for comparison with utcnow)
            timestamp = parsed.replace(tzinfo=None)
        except:
            return 0.5
    elif hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
        # Strip timezone from aware datetime
        timestamp = timestamp.replace(tzinfo=None)

    try:
        age = now - timestamp
        age_days = max(0, age.total_seconds() / 86400)
    except TypeError:
        # Fallback if datetime comparison still fails
        return 0.5

    # Exponential decay
    return 0.5 ** (age_days / half_life_days)


# =============================================================================
# Two-Stage Candidate Filtering
# =============================================================================

def filter_messages_by_recency(
    messages: List[Dict[str, Any]],
    max_messages: int = MAX_MESSAGES_PER_USER,
    max_age_days: int = MAX_MESSAGE_AGE_DAYS,
    session_id: Optional[str] = None,
    search_all: bool = False
) -> List[Dict[str, Any]]:
    """
    Stage 1: Filter messages before expensive embedding operations.

    Limits candidates by:
    1. Current conversation only (unless search_all=True)
    2. Recency window (last N days)
    3. Maximum count (last N messages)

    Args:
        messages: All messages to filter
        max_messages: Maximum number of messages to keep
        max_age_days: Maximum age in days
        session_id: Current session ID (for conversation filter)
        search_all: If True, search across all conversations

    Returns:
        Filtered list of messages (most recent first)
    """
    if not messages:
        return []

    now = datetime.utcnow()
    cutoff = now - timedelta(days=max_age_days)
    filtered = []

    for msg in messages:
        # Filter by session if not searching all
        if not search_all and session_id:
            msg_session = msg.get('session_id')
            if msg_session and msg_session != session_id:
                continue

        # Filter by age
        timestamp = msg.get('timestamp') or msg.get('created_at')
        if timestamp:
            try:
                if isinstance(timestamp, str):
                    ts = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    ts = ts.replace(tzinfo=None)
                else:
                    ts = timestamp.replace(tzinfo=None) if hasattr(timestamp, 'tzinfo') else timestamp

                if ts < cutoff:
                    continue
            except:
                pass  # Include messages with unparseable timestamps

        filtered.append(msg)

    # Sort by timestamp descending (most recent first)
    def get_timestamp(m):
        ts = m.get('timestamp') or m.get('created_at') or ''
        if isinstance(ts, str):
            return ts
        return ts.isoformat() if hasattr(ts, 'isoformat') else ''

    filtered.sort(key=get_timestamp, reverse=True)

    # Limit to max_messages
    return filtered[:max_messages]


def detect_search_all_intent(query: str) -> bool:
    """
    Detect if user wants to search across all conversations.

    Looks for explicit signals like:
    - "search everything"
    - "in all chats"
    - "across conversations"
    - "find in history"

    Args:
        query: User's search query

    Returns:
        True if user wants global search
    """
    if not query:
        return False

    lower = query.lower()

    search_all_patterns = [
        'search everything',
        'search all',
        'in all chats',
        'in all conversations',
        'across conversations',
        'across all',
        'find in history',
        'search history',
        'in my history',
        'everywhere',
        'all messages',
        'all my chats',
    ]

    return any(pattern in lower for pattern in search_all_patterns)


# =============================================================================
# Hybrid Retriever
# =============================================================================

class HybridRetriever:
    """
    Hybrid retrieval combining lexical, semantic, and recency signals.

    Usage:
        retriever = HybridRetriever()
        retriever.index_messages(messages)
        results = retriever.search("EADDRINUSE error", top_k=5)
    """

    def __init__(
        self,
        semantic_weight: float = SEMANTIC_WEIGHT,
        lexical_weight: float = LEXICAL_WEIGHT,
        recency_weight: float = RECENCY_WEIGHT
    ):
        self.semantic_weight = semantic_weight
        self.lexical_weight = lexical_weight
        self.recency_weight = recency_weight

        self.documents: List[Document] = []
        self.bm25_index = BM25Index()
        self._indexed = False

    def index_messages(
        self,
        messages: List[Dict[str, Any]],
        compute_embeddings: bool = True
    ) -> int:
        """
        Index messages for hybrid retrieval.

        Args:
            messages: List of message dicts with 'prompt', 'response', 'timestamp'
            compute_embeddings: Whether to compute semantic embeddings

        Returns:
            Number of documents indexed
        """
        self.documents = []

        for i, msg in enumerate(messages):
            # Extract searchable text
            prompt = msg.get('prompt', '')
            response = msg.get('response', '')
            text = f"{prompt} {response}".strip()

            if not text or len(text) < 10:
                continue

            # Parse timestamp
            timestamp = None
            ts_str = msg.get('timestamp') or msg.get('created_at')
            if ts_str:
                try:
                    timestamp = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except:
                    pass

            doc = Document(
                id=f"msg_{i}",
                text=text,
                tokens=tokenize(text),
                timestamp=timestamp,
                source="message",
                original=msg
            )

            # Try to use pre-existing embedding from storage first
            # Handles both quantized format (new) and raw float list (legacy)
            stored_embedding = msg.get('embedding')
            if stored_embedding:
                doc.embedding = normalize_stored_embedding(stored_embedding)

            # Compute embedding if not found and requested
            if compute_embeddings and not doc.embedding:
                # Use truncated text for embedding (more efficient)
                embed_text = text[:512]
                doc.embedding = get_embedding(embed_text)

            self.documents.append(doc)

        # Build BM25 index
        self.bm25_index.add_documents(self.documents)
        self._indexed = True

        logger.debug(f"Indexed {len(self.documents)} documents for hybrid retrieval")
        return len(self.documents)

    def index_buckets(
        self,
        buckets: Dict[str, List[Dict[str, Any]]],
        compute_embeddings: bool = True
    ) -> int:
        """
        Add memory bucket entries to the index.

        Args:
            buckets: Dict of bucket_name -> list of entries
            compute_embeddings: Whether to compute embeddings

        Returns:
            Number of entries added
        """
        count = 0

        for bucket_name, entries in buckets.items():
            for i, entry in enumerate(entries):
                text = entry.get('text', '')
                if not text:
                    continue

                timestamp = None
                ts_str = entry.get('created_at') or entry.get('updated_at')
                if ts_str:
                    try:
                        timestamp = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    except:
                        pass

                doc = Document(
                    id=f"bucket_{bucket_name}_{i}",
                    text=text,
                    tokens=tokenize(text),
                    timestamp=timestamp,
                    source=f"bucket:{bucket_name}",
                    original=entry
                )

                if compute_embeddings:
                    doc.embedding = get_embedding(text)

                self.documents.append(doc)
                count += 1

        # Rebuild BM25 index
        self.bm25_index.add_documents(self.documents)

        logger.debug(f"Added {count} bucket entries to index")
        return count

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.15,
        source_filter: Optional[str] = None,
        stage1_limit: int = STAGE1_CANDIDATE_LIMIT
    ) -> List[RetrievalResult]:
        """
        Two-stage hybrid search combining lexical, semantic, and recency.

        Stage 1: BM25 pre-filter to get top candidates (fast, no embeddings)
        Stage 2: Semantic scoring only on candidates (expensive cosine sim)

        This dramatically reduces compute for large message histories.

        Args:
            query: Search query
            top_k: Number of results to return
            min_score: Minimum combined score threshold
            source_filter: Optional filter like "message" or "bucket:preferences"
            stage1_limit: Max candidates from Stage 1 BM25 (default 100)

        Returns:
            List of RetrievalResult sorted by combined score
        """
        if not self._indexed or not self.documents:
            return []

        # =====================================================================
        # STAGE 1: BM25 pre-filter (fast lexical matching)
        # =====================================================================
        # Only compute embeddings on top BM25 candidates, not all documents

        # Get BM25 scores for all documents
        lexical_scores = {}
        bm25_results = list(self.bm25_index.search(query, top_k=len(self.documents)))

        for idx, score in bm25_results:
            # Apply source filter early
            if source_filter and not self.documents[idx].source.startswith(source_filter):
                continue
            lexical_scores[idx] = score

        # Sort by BM25 score and take top candidates
        stage1_candidates = sorted(
            lexical_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:stage1_limit]

        candidate_indices = {idx for idx, _ in stage1_candidates}

        # Also include recent documents that might have low BM25 but high recency
        # (handles cases where user asks about "what we just discussed")
        recent_docs = []
        for idx, doc in enumerate(self.documents):
            if idx not in candidate_indices:
                if source_filter and not doc.source.startswith(source_filter):
                    continue
                rec_score = recency_score(doc.timestamp)
                if rec_score > 0.7:  # Very recent (< 3 days with default half-life)
                    recent_docs.append((idx, rec_score))

        # Add top 20 recent docs to candidates
        recent_docs.sort(key=lambda x: x[1], reverse=True)
        for idx, _ in recent_docs[:20]:
            candidate_indices.add(idx)
            if idx not in lexical_scores:
                lexical_scores[idx] = 0.0

        logger.debug(
            f"Stage 1: {len(candidate_indices)} candidates from "
            f"{len(self.documents)} documents"
        )

        # =====================================================================
        # STAGE 2: Semantic scoring (only on candidates)
        # =====================================================================

        # Only compute query embedding once
        query_embedding = get_embedding(query)

        results = []

        for idx in candidate_indices:
            doc = self.documents[idx]

            # Lexical score (from Stage 1)
            lexical = lexical_scores.get(idx, 0.0)

            # Semantic score (expensive - only on candidates!)
            semantic = 0.0
            if query_embedding and doc.embedding:
                semantic = max(0, cosine_similarity(query_embedding, doc.embedding))

            # Recency score
            recency = recency_score(doc.timestamp)

            # Combined score
            combined = (
                self.semantic_weight * semantic +
                self.lexical_weight * lexical +
                self.recency_weight * recency
            )

            if combined < min_score:
                continue

            results.append(RetrievalResult(
                content=doc.original,
                text=doc.text,
                semantic_score=semantic,
                lexical_score=lexical,
                recency_score=recency,
                combined_score=combined,
                source=doc.source
            ))

        # Sort by combined score
        results.sort(key=lambda x: x.combined_score, reverse=True)

        return results[:top_k]

    def search_lexical_only(self, query: str, top_k: int = 5) -> List[RetrievalResult]:
        """Search using only BM25 (for exact matches)."""
        results = []

        for idx, score in self.bm25_index.search(query, top_k):
            doc = self.documents[idx]
            results.append(RetrievalResult(
                content=doc.original,
                text=doc.text,
                lexical_score=score,
                combined_score=score,
                source=doc.source
            ))

        return results

    def search_semantic_only(self, query: str, top_k: int = 5) -> List[RetrievalResult]:
        """Search using only semantic similarity."""
        query_embedding = get_embedding(query)
        if not query_embedding:
            return []

        results = []

        for doc in self.documents:
            if not doc.embedding:
                continue

            score = max(0, cosine_similarity(query_embedding, doc.embedding))
            if score < MIN_SEMANTIC_SCORE:
                continue

            results.append(RetrievalResult(
                content=doc.original,
                text=doc.text,
                semantic_score=score,
                combined_score=score,
                source=doc.source
            ))

        results.sort(key=lambda x: x.combined_score, reverse=True)
        return results[:top_k]


# =============================================================================
# Convenience Functions
# =============================================================================

_retriever_cache: Dict[str, HybridRetriever] = {}


def get_retriever(user_id: str) -> HybridRetriever:
    """Get or create a retriever for a user."""
    if user_id not in _retriever_cache:
        _retriever_cache[user_id] = HybridRetriever()
    return _retriever_cache[user_id]


def clear_retriever(user_id: str) -> None:
    """Clear cached retriever for a user."""
    if user_id in _retriever_cache:
        del _retriever_cache[user_id]


def hybrid_search(
    query: str,
    messages: List[Dict[str, Any]],
    buckets: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    top_k: int = 5
) -> List[RetrievalResult]:
    """
    One-shot hybrid search (indexes and searches).

    For repeated searches on same data, use HybridRetriever class directly.

    Args:
        query: Search query
        messages: List of messages to search
        buckets: Optional memory buckets to include
        top_k: Number of results

    Returns:
        List of RetrievalResult
    """
    retriever = HybridRetriever()
    retriever.index_messages(messages)

    if buckets:
        retriever.index_buckets(buckets)

    return retriever.search(query, top_k=top_k)


# =============================================================================
# File Chunk Retrieval
# =============================================================================

class FileChunkRetriever:
    """
    Retriever for file chunks stored in Firestore.

    Integrates with the main HybridRetriever for unified search.

    Usage:
        retriever = FileChunkRetriever(db)
        chunks = retriever.search(
            user_id=user_id,
            query="explain the architecture",
            conversation_id=conv_id,
            top_k=5
        )
    """

    def __init__(self, db):
        """Initialize with Firestore client."""
        self.db = db

    def get_chunks_for_retrieval(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Get file chunks from Firestore for indexing.

        Args:
            user_id: Owner user ID
            conversation_id: Filter to specific conversation
            file_ids: Filter to specific files
            limit: Maximum chunks to return

        Returns:
            List of chunk dicts
        """
        from .file_chunks import FileChunkStore, MAX_FILE_CHUNKS_STAGE1

        store = FileChunkStore(self.db)
        chunks = store.get_chunks_for_retrieval(
            user_id=user_id,
            conversation_id=conversation_id,
            file_ids=file_ids,
            limit=min(limit, MAX_FILE_CHUNKS_STAGE1)
        )

        return [chunk.to_dict() for chunk in chunks]

    def search(
        self,
        user_id: str,
        query: str,
        conversation_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
        top_k: int = 5,
        stage1_limit: int = 100
    ) -> List[RetrievalResult]:
        """
        Search file chunks using hybrid retrieval.

        Args:
            user_id: Owner user ID
            query: Search query
            conversation_id: Filter to conversation
            file_ids: Filter to specific files
            top_k: Number of results
            stage1_limit: BM25 pre-filter limit

        Returns:
            List of RetrievalResult for file chunks
        """
        # First try to get chunks for this conversation
        chunks = self.get_chunks_for_retrieval(
            user_id=user_id,
            conversation_id=conversation_id,
            file_ids=file_ids
        )

        # If no chunks found for this conversation, search ALL user's recent files
        # This handles cases where user uploads file, deletes session, asks in new session
        # DISABLED: This fallback was causing wrong chunks to be returned when user
        # uploads a new file (extracted_text in session) but RAG hasn't processed it yet.
        # The old chunks from previous files would pollute the context.
        # Only enable fallback if explicitly needed for cross-session retrieval.
        # if not chunks and conversation_id:
        #     logger.info(f"No chunks in conversation {conversation_id}, searching all user files")
        #     chunks = self.get_chunks_for_retrieval(
        #         user_id=user_id,
        #         conversation_id=None,  # Search all
        #         file_ids=file_ids,
        #         limit=200  # Limit for cross-conversation search
        #     )

        if not chunks:
            return []

        # Create retriever and index chunks
        retriever = HybridRetriever()
        count = self._index_chunks(retriever, chunks)

        if count == 0:
            return []

        # Search
        results = retriever.search(
            query=query,
            top_k=top_k,
            source_filter="file_chunk",
            stage1_limit=stage1_limit
        )

        return results

    def _index_chunks(
        self,
        retriever: HybridRetriever,
        chunks: List[Dict[str, Any]]
    ) -> int:
        """
        Index file chunks into the retriever.
        """
        count = 0

        for chunk in chunks:
            text = chunk.get('text', '')
            if not text:
                continue

            # Parse timestamp
            created_at = chunk.get('created_at', '')
            try:
                timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                timestamp = datetime.utcnow()

            # Create document
            chunk_id = chunk.get('chunk_id', f"chunk_{count}")
            doc = Document(
                id=chunk_id,
                text=text,
                tokens=tokenize(text),
                timestamp=timestamp,
                source=f"file_chunk:{chunk.get('file_id', '')}",
                original=chunk
            )

            # Use stored embedding if available
            stored_embedding = chunk.get('embedding')
            if stored_embedding:
                doc.embedding = normalize_stored_embedding(stored_embedding)
            else:
                doc.embedding = get_embedding(text[:512])

            retriever.documents.append(doc)
            count += 1

        # Rebuild BM25 index
        if count > 0:
            retriever.bm25_index.add_documents(retriever.documents)
            retriever._indexed = True

        logger.debug(f"Indexed {count} file chunks")
        return count


def hybrid_search_with_files(
    query: str,
    user_id: str,
    db,
    messages: List[Dict[str, Any]],
    conversation_id: Optional[str] = None,
    buckets: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    file_ids: Optional[List[str]] = None,
    top_k: int = 5,
    file_chunk_weight: float = 1.0
) -> Tuple[List[RetrievalResult], List[RetrievalResult]]:
    """
    Unified hybrid search across messages, buckets, AND file chunks.

    Returns separate lists for messages and file chunks to allow
    different context formatting.

    Args:
        query: Search query
        user_id: Owner user ID
        db: Firestore client
        messages: Chat messages to search
        conversation_id: Filter files to conversation
        buckets: Memory buckets to include
        file_ids: Specific file IDs to search
        top_k: Results per source type
        file_chunk_weight: Boost for file chunk scores

    Returns:
        Tuple of (message_results, file_chunk_results)
    """
    # Search messages
    retriever = HybridRetriever()
    retriever.index_messages(messages)

    if buckets:
        retriever.index_buckets(buckets)

    message_results = retriever.search(query, top_k=top_k)

    # Search file chunks
    file_retriever = FileChunkRetriever(db)
    file_results = file_retriever.search(
        user_id=user_id,
        query=query,
        conversation_id=conversation_id,
        file_ids=file_ids,
        top_k=top_k
    )

    # Apply weight boost to file chunks
    if file_chunk_weight != 1.0:
        for result in file_results:
            result.combined_score *= file_chunk_weight

    return message_results, file_results


def format_file_chunk_context(results: List[RetrievalResult]) -> str:
    """
    Format file chunk results for LLM context.

    Groups chunks by file and includes page references.

    Args:
        results: List of file chunk RetrievalResult

    Returns:
        Formatted context string
    """
    if not results:
        return ""

    # Group by file
    by_file: Dict[str, List[Dict[str, Any]]] = {}

    for result in results:
        chunk = result.content
        file_id = chunk.get('file_id', 'unknown')
        filename = chunk.get('filename', 'Unknown file')

        key = f"{file_id}:{filename}"
        if key not in by_file:
            by_file[key] = []
        by_file[key].append(chunk)

    # Format output
    lines = ["## Relevant Document Excerpts"]

    for key, chunks in by_file.items():
        file_id, filename = key.split(':', 1)
        lines.append(f"\n### From: {filename}")

        # Sort chunks by index
        chunks.sort(key=lambda c: c.get('chunk_index', 0))

        for chunk in chunks:
            text = chunk.get('text', '')
            page_start = chunk.get('page_start')
            page_end = chunk.get('page_end')

            # Page reference
            if page_start:
                if page_end and page_end != page_start:
                    lines.append(f"[Pages {page_start}-{page_end}]")
                else:
                    lines.append(f"[Page {page_start}]")

            # Truncate very long chunks
            if len(text) > 1500:
                text = text[:1500] + "..."

            lines.append(text)
            lines.append("")  # Blank line between chunks

    return "\n".join(lines)
