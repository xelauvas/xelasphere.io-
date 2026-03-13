"""
Tests for app/file_processor.py - File processing and embedding pipeline.

Tests cover chunking, embedding, and file processing logic.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestTextChunker:
    """Tests for TextChunker class."""

    def test_chunk_simple_text(self):
        """Chunk simple text into pieces."""
        from app.text_chunker import TextChunker

        chunker = TextChunker(chunk_size=50, overlap=10)
        text = "This is a simple test. " * 10  # ~240 chars

        chunks = chunker.chunk_text(text)
        assert len(chunks) > 0
        assert all(hasattr(c, 'text') for c in chunks)

    def test_chunk_empty_text(self):
        """Empty text should return empty list."""
        from app.text_chunker import TextChunker

        chunker = TextChunker(chunk_size=100, overlap=10)
        chunks = chunker.chunk_text("")
        assert len(chunks) == 0

    def test_chunk_short_text(self):
        """Short text should return single chunk."""
        from app.text_chunker import TextChunker

        chunker = TextChunker(chunk_size=1000, overlap=100)
        text = "Short text."

        chunks = chunker.chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].text == text

    def test_chunks_have_indices(self):
        """Chunks should have proper indices."""
        from app.text_chunker import TextChunker

        chunker = TextChunker(chunk_size=50, overlap=10)
        text = "Test content. " * 20

        chunks = chunker.chunk_text(text)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestTextChunk:
    """Tests for TextChunk dataclass."""

    def test_text_chunk_creation(self):
        """TextChunk should be creatable with required fields."""
        from app.text_chunker import TextChunk

        chunk = TextChunk(
            text="Test content",
            char_start=0,
            char_end=12,
            chunk_index=0
        )
        assert chunk.text == "Test content"
        assert chunk.char_start == 0
        assert chunk.char_end == 12
        assert chunk.chunk_index == 0


class TestFileProcessor:
    """Tests for FileProcessor class."""

    def test_init_creates_store_and_chunker(self, mock_firestore):
        """FileProcessor should initialize with store and chunker."""
        from app.file_processor import FileProcessor

        processor = FileProcessor(mock_firestore)
        assert processor.db is not None
        assert processor.store is not None
        assert processor.chunker is not None


class TestProcessingLimits:
    """Tests for processing constants and limits."""

    def test_max_text_length_reasonable(self):
        """MAX_TEXT_LENGTH should be reasonable."""
        from app.file_processor import MAX_TEXT_LENGTH

        # Should handle reasonably large files but not unlimited
        assert MAX_TEXT_LENGTH > 10000
        assert MAX_TEXT_LENGTH < 10_000_000

    def test_chunk_size_reasonable(self):
        """CHUNK_SIZE should be in reasonable range."""
        from app.file_processor import CHUNK_SIZE

        assert 100 < CHUNK_SIZE < 10000

    def test_chunk_overlap_smaller_than_size(self):
        """CHUNK_OVERLAP should be smaller than CHUNK_SIZE."""
        from app.file_processor import CHUNK_SIZE, CHUNK_OVERLAP

        assert CHUNK_OVERLAP < CHUNK_SIZE
        assert CHUNK_OVERLAP > 0

    def test_batch_size_reasonable(self):
        """EMBEDDING_BATCH_SIZE should be reasonable."""
        from app.file_processor import EMBEDDING_BATCH_SIZE

        assert 1 <= EMBEDDING_BATCH_SIZE <= 100


class TestFileChunksModule:
    """Tests for file_chunks module."""

    def test_file_status_enum(self):
        """FileStatus enum should have expected values."""
        from app.file_chunks import FileStatus

        assert FileStatus.PROCESSING is not None
        assert FileStatus.READY is not None
        assert FileStatus.FAILED is not None

    def test_max_chunks_constant(self):
        """MAX_CHUNKS_PER_FILE should be defined."""
        from app.file_chunks import MAX_CHUNKS_PER_FILE

        assert MAX_CHUNKS_PER_FILE > 0
        assert MAX_CHUNKS_PER_FILE < 100000

    def test_max_chunk_text_length(self):
        """MAX_CHUNK_TEXT_LENGTH should be defined."""
        from app.file_chunks import MAX_CHUNK_TEXT_LENGTH

        assert MAX_CHUNK_TEXT_LENGTH > 0


class TestEmbeddingUtils:
    """Tests for embedding utilities."""

    def test_embedding_to_storage_function_exists(self):
        """embedding_to_storage should be importable."""
        from app.embedding_utils import embedding_to_storage

        assert callable(embedding_to_storage)
