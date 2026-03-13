"""
Text Chunker - Smart Document Splitting
========================================

Implements intelligent text chunking for RAG:
- Respects page boundaries (PDF)
- Splits on natural breaks (headings, paragraphs, sentences)
- Maintains overlap for context continuity
- Tracks character positions for citations

Chunking strategy:
- Target size: 500-1000 tokens (~2000-4000 chars)
- Overlap: 10-20% (~200-400 chars)
- Priority: page breaks > headings > paragraphs > sentences
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Chunking parameters (in characters, ~4 chars per token)
DEFAULT_CHUNK_SIZE = 2000       # ~500 tokens
MAX_CHUNK_SIZE = 4000           # ~1000 tokens
MIN_CHUNK_SIZE = 200            # Don't create tiny chunks
DEFAULT_OVERLAP = 200           # ~50 tokens, 10% of default

# Regex patterns for split points
HEADING_PATTERN = re.compile(
    r'^(?:'
    r'#{1,6}\s+.+|'                    # Markdown headings
    r'[A-Z][A-Za-z\s]{2,50}:\s*$|'     # Title with colon
    r'[A-Z][A-Z\s]{2,50}$|'            # ALL CAPS heading
    r'\d+\.\s+[A-Z].{2,50}$'           # Numbered section
    r')',
    re.MULTILINE
)

PARAGRAPH_BREAK = re.compile(r'\n\s*\n')
SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
PAGE_MARKER = re.compile(r'\[PAGE\s*(\d+)\]|\f|---PAGE\s*(\d+)---')


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class TextChunk:
    """A chunk of text with position metadata."""
    text: str
    char_start: int
    char_end: int
    chunk_index: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    @property
    def length(self) -> int:
        return len(self.text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'char_start': self.char_start,
            'char_end': self.char_end,
            'chunk_index': self.chunk_index,
            'page_start': self.page_start,
            'page_end': self.page_end
        }


@dataclass
class PagedText:
    """Text with page boundary information."""
    text: str
    page_boundaries: List[Tuple[int, int, int]]  # (page_num, start_char, end_char)

    @classmethod
    def from_pages(cls, pages: List[str]) -> 'PagedText':
        """Create from list of page texts."""
        boundaries = []
        current_pos = 0
        full_text_parts = []

        for page_num, page_text in enumerate(pages, start=1):
            start = current_pos
            full_text_parts.append(page_text)
            current_pos += len(page_text)
            end = current_pos
            boundaries.append((page_num, start, end))

            # Add page separator if not last page
            if page_num < len(pages):
                full_text_parts.append('\n')
                current_pos += 1

        return cls(
            text=''.join(full_text_parts),
            page_boundaries=boundaries
        )

    def get_page_at_position(self, char_pos: int) -> Optional[int]:
        """Get page number for a character position."""
        for page_num, start, end in self.page_boundaries:
            if start <= char_pos < end:
                return page_num
        return None


# =============================================================================
# Main Chunker
# =============================================================================

class TextChunker:
    """
    Smart text chunker with configurable strategy.

    Usage:
        chunker = TextChunker(chunk_size=2000, overlap=200)

        # For plain text
        chunks = chunker.chunk_text(text)

        # For PDF with pages
        chunks = chunker.chunk_pages(list_of_page_texts)
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_chunk_size: int = MAX_CHUNK_SIZE,
        min_chunk_size: int = MIN_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP
    ):
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str) -> List[TextChunk]:
        """
        Chunk plain text without page information.

        Args:
            text: Input text to chunk

        Returns:
            List of TextChunk objects
        """
        if not text or len(text) < self.min_chunk_size:
            if text:
                return [TextChunk(
                    text=text,
                    char_start=0,
                    char_end=len(text),
                    chunk_index=0
                )]
            return []

        # Find split points
        split_points = self._find_split_points(text)

        # Create chunks
        chunks = self._create_chunks(text, split_points)

        return chunks

    def chunk_pages(self, pages: List[str]) -> List[TextChunk]:
        """
        Chunk text from multiple pages, preserving page boundaries.

        Args:
            pages: List of text for each page

        Returns:
            List of TextChunk objects with page metadata
        """
        if not pages:
            return []

        # Create paged text
        paged = PagedText.from_pages(pages)

        if len(paged.text) < self.min_chunk_size:
            if paged.text:
                return [TextChunk(
                    text=paged.text,
                    char_start=0,
                    char_end=len(paged.text),
                    chunk_index=0,
                    page_start=1,
                    page_end=len(pages)
                )]
            return []

        # Find split points, preferring page boundaries
        split_points = self._find_split_points_with_pages(paged)

        # Create chunks
        chunks = self._create_chunks_with_pages(paged, split_points)

        return chunks

    def _find_split_points(self, text: str) -> List[Tuple[int, int]]:
        """
        Find potential split points with priority scores.

        Returns:
            List of (position, priority) tuples
            Priority: 0=page, 1=heading, 2=paragraph, 3=sentence
        """
        points = []

        # Headings (priority 1)
        for match in HEADING_PATTERN.finditer(text):
            # Split before heading
            points.append((match.start(), 1))

        # Paragraph breaks (priority 2)
        for match in PARAGRAPH_BREAK.finditer(text):
            points.append((match.end(), 2))

        # Sentence endings (priority 3)
        for match in SENTENCE_END.finditer(text):
            points.append((match.start() + 1, 3))

        # Sort by position, then by priority (lower is better)
        points.sort(key=lambda x: (x[0], x[1]))

        return points

    def _find_split_points_with_pages(
        self,
        paged: PagedText
    ) -> List[Tuple[int, int]]:
        """
        Find split points including page boundaries.
        """
        points = self._find_split_points(paged.text)

        # Add page boundaries (priority 0 - highest)
        for page_num, start, end in paged.page_boundaries:
            if start > 0:  # Don't add at position 0
                points.append((start, 0))

        # Re-sort
        points.sort(key=lambda x: (x[0], x[1]))

        return points

    def _create_chunks(
        self,
        text: str,
        split_points: List[Tuple[int, int]]
    ) -> List[TextChunk]:
        """
        Create chunks using split points.
        """
        chunks = []
        chunk_start = 0
        chunk_index = 0
        text_len = len(text)

        while chunk_start < text_len:
            # Target end position
            target_end = min(chunk_start + self.chunk_size, text_len)
            max_end = min(chunk_start + self.max_chunk_size, text_len)

            # Find best split point
            chunk_end = self._find_best_split(
                split_points,
                chunk_start,
                target_end,
                max_end,
                text_len
            )

            # Extract chunk text
            chunk_text = text[chunk_start:chunk_end].strip()

            if len(chunk_text) >= self.min_chunk_size or chunk_end >= text_len:
                chunks.append(TextChunk(
                    text=chunk_text,
                    char_start=chunk_start,
                    char_end=chunk_end,
                    chunk_index=chunk_index
                ))
                chunk_index += 1

            # Move start with overlap
            if chunk_end >= text_len:
                break

            # Calculate next start with overlap
            chunk_start = max(chunk_end - self.overlap, chunk_start + 1)

        return chunks

    def _create_chunks_with_pages(
        self,
        paged: PagedText,
        split_points: List[Tuple[int, int]]
    ) -> List[TextChunk]:
        """
        Create chunks with page metadata.
        """
        chunks = []
        chunk_start = 0
        chunk_index = 0
        text_len = len(paged.text)

        while chunk_start < text_len:
            target_end = min(chunk_start + self.chunk_size, text_len)
            max_end = min(chunk_start + self.max_chunk_size, text_len)

            chunk_end = self._find_best_split(
                split_points,
                chunk_start,
                target_end,
                max_end,
                text_len
            )

            chunk_text = paged.text[chunk_start:chunk_end].strip()

            if len(chunk_text) >= self.min_chunk_size or chunk_end >= text_len:
                # Find page range
                page_start = paged.get_page_at_position(chunk_start)
                page_end = paged.get_page_at_position(chunk_end - 1)

                chunks.append(TextChunk(
                    text=chunk_text,
                    char_start=chunk_start,
                    char_end=chunk_end,
                    chunk_index=chunk_index,
                    page_start=page_start,
                    page_end=page_end
                ))
                chunk_index += 1

            if chunk_end >= text_len:
                break

            chunk_start = max(chunk_end - self.overlap, chunk_start + 1)

        return chunks

    def _find_best_split(
        self,
        split_points: List[Tuple[int, int]],
        chunk_start: int,
        target_end: int,
        max_end: int,
        text_len: int
    ) -> int:
        """
        Find the best split point near target_end.

        Strategy:
        1. Look for high-priority splits near target_end
        2. Prefer splits slightly before target over slightly after
        3. Never exceed max_end
        4. Fall back to target_end if no good splits
        """
        if target_end >= text_len:
            return text_len

        # Find candidates in range [chunk_start + min_size, max_end]
        min_pos = chunk_start + self.min_chunk_size

        candidates = [
            (pos, priority)
            for pos, priority in split_points
            if min_pos <= pos <= max_end
        ]

        if not candidates:
            # No split points, use target end
            return target_end

        # Score candidates: prefer near target_end and low priority
        def score(pos, priority):
            # Distance penalty (prefer close to target)
            distance = abs(pos - target_end)
            distance_penalty = distance / self.chunk_size

            # Priority bonus (0 is best)
            priority_score = priority * 0.1

            # Slight preference for positions before target
            before_bonus = 0 if pos <= target_end else 0.05

            return distance_penalty + priority_score + before_bonus

        best = min(candidates, key=lambda x: score(x[0], x[1]))
        return best[0]


# =============================================================================
# Utility Functions
# =============================================================================

def chunk_text_simple(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP
) -> List[TextChunk]:
    """
    Simple chunking function for quick use.

    Args:
        text: Input text
        chunk_size: Target chunk size in characters
        overlap: Overlap between chunks

    Returns:
        List of TextChunk objects
    """
    chunker = TextChunker(chunk_size=chunk_size, overlap=overlap)
    return chunker.chunk_text(text)


def chunk_pdf_pages(
    pages: List[str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP
) -> List[TextChunk]:
    """
    Chunk PDF pages with page boundary awareness.

    Args:
        pages: List of text for each page
        chunk_size: Target chunk size in characters
        overlap: Overlap between chunks

    Returns:
        List of TextChunk objects with page metadata
    """
    chunker = TextChunker(chunk_size=chunk_size, overlap=overlap)
    return chunker.chunk_pages(pages)


def estimate_token_count(text: str) -> int:
    """
    Rough estimate of token count.

    Rule of thumb: ~4 characters per token for English.
    """
    return len(text) // 4


def split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences for fine-grained chunking.
    """
    # Simple sentence splitter
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


# =============================================================================
# PDF Page Extractor Helper
# =============================================================================

def extract_pages_from_text(text: str) -> List[str]:
    """
    Extract pages from text with page markers.

    Supports markers like:
    - [PAGE 1], [PAGE 2], etc.
    - ---PAGE 1---, ---PAGE 2---, etc.
    - Form feed character (\f)

    Args:
        text: Text with page markers

    Returns:
        List of page texts
    """
    # Try to find page markers
    matches = list(PAGE_MARKER.finditer(text))

    if not matches:
        # No markers, return as single page
        return [text]

    pages = []
    prev_end = 0

    for match in matches:
        # Text before this marker
        page_text = text[prev_end:match.start()].strip()
        if page_text:
            pages.append(page_text)
        prev_end = match.end()

    # Text after last marker
    remaining = text[prev_end:].strip()
    if remaining:
        pages.append(remaining)

    return pages if pages else [text]
