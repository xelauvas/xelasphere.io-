"""
File Processor - Document RAG Pipeline
=======================================

Handles the complete file processing pipeline:
1. Chunk text with page awareness
2. Embed each chunk
3. Store in Firestore for retrieval

This runs asynchronously after file upload to avoid blocking
the user. Files are marked 'processing' -> 'ready' | 'failed'.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from .file_chunks import (
    FileChunkStore,
    FileMetadata,
    FileChunk,
    FileStatus,
    create_chunk,
    MAX_CHUNKS_PER_FILE,
    MAX_CHUNK_TEXT_LENGTH
)
from .text_chunker import (
    TextChunker,
    TextChunk,
    chunk_pdf_pages,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP
)
from .embedding_utils import embedding_to_storage

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Processing limits
MAX_TEXT_LENGTH = 500_000      # ~125k tokens max per file
EMBEDDING_BATCH_SIZE = 16      # Process embeddings in batches
EMBEDDING_WORKERS = 4          # Parallel embedding threads

# Chunk parameters
CHUNK_SIZE = 2000              # ~500 tokens
CHUNK_OVERLAP = 200            # 10% overlap


# =============================================================================
# Embedding Helper
# =============================================================================

def get_embedding(text: str) -> Optional[List[float]]:
    """Get embedding for text using Jina API."""
    try:
        from app.memory_v2.embeddings import embed_text as _embed_text
        embed_input = text[:512] if len(text) > 512 else text
        result = _embed_text(embed_input)
        if result is not None:
            return result.tolist()
        return None
    except Exception as e:
        logger.warning(f"Failed to get embedding: {e}")
        return None


def embed_chunks_batch(chunks: List[TextChunk]) -> List[Optional[Dict[str, Any]]]:
    """
    Embed multiple chunks in batch for efficiency.

    Returns:
        List of quantized embedding dicts (or None for failures)
    """
    try:
        from app.memory_v2.embeddings import embed_batch

        # Prepare texts (truncate for efficiency)
        texts = [c.text[:512] for c in chunks]

        # Batch encode via Jina API
        embeddings = embed_batch(texts)
        if embeddings is None:
            return [None] * len(chunks)

        # Quantize and return
        results = []
        for emb in embeddings:
            try:
                quantized = embedding_to_storage(emb.tolist())
                results.append(quantized)
            except Exception as e:
                logger.warning(f"Failed to quantize embedding: {e}")
                results.append(None)

        return results

    except Exception as e:
        logger.warning(f"Batch embedding failed: {e}")
        return [None] * len(chunks)


# =============================================================================
# File Processor
# =============================================================================

class FileProcessor:
    """
    Processes uploaded files for RAG.

    Usage:
        processor = FileProcessor(db)

        # Process a new file (async-friendly)
        file_meta = processor.process_file(
            user_id=user_id,
            filename=unique_filename,
            original_filename=original_name,
            mime_type=mime_type,
            file_size=size,
            extracted_text=text,
            file_path=path,
            conversation_id=conv_id
        )

        # Check status
        status = processor.get_file_status(user_id, file_meta.file_id)
    """

    def __init__(self, db):
        """Initialize with Firestore client."""
        self.db = db
        self.store = FileChunkStore(db)
        self.chunker = TextChunker(
            chunk_size=CHUNK_SIZE,
            overlap=CHUNK_OVERLAP
        )

    def process_file(
        self,
        user_id: str,
        filename: str,
        original_filename: str,
        mime_type: str,
        file_size: int,
        extracted_text: str,
        file_path: Optional[str] = None,
        conversation_id: Optional[str] = None,
        pages: Optional[List[str]] = None,
        num_pages: Optional[int] = None
    ) -> FileMetadata:
        """
        Process a file: chunk, embed, and store.

        Args:
            user_id: Owner user ID
            filename: Stored filename (unique)
            original_filename: Original upload name
            mime_type: MIME type
            file_size: Size in bytes
            extracted_text: Full extracted text
            file_path: Storage path (optional)
            conversation_id: Associated chat session (optional)
            pages: List of per-page texts (for PDFs)
            num_pages: Number of pages if PDF

        Returns:
            FileMetadata with status
        """
        # Create file record in 'processing' state
        file_meta = self.store.create_file(
            user_id=user_id,
            filename=filename,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=file_size,
            file_path=file_path,
            conversation_id=conversation_id,
            num_pages=num_pages or (len(pages) if pages else None)
        )

        try:
            # Check if this is an image file (for visual analysis, not text RAG)
            is_image = mime_type and mime_type.startswith('image/')

            # Validate text - images without text are OK (visual analysis)
            if not extracted_text or len(extracted_text.strip()) < 10:
                if is_image:
                    # Images without text are ready for visual analysis, not text RAG
                    self.store.update_file_status(
                        user_id=user_id,
                        file_id=file_meta.file_id,
                        status=FileStatus.READY.value,
                        text_length=0,
                        num_chunks=0
                    )
                    file_meta.status = FileStatus.READY.value
                    file_meta.num_chunks = 0
                    logger.info(f"Image file {file_meta.file_id} ready for visual analysis (no text chunks)")
                    return file_meta
                else:
                    # Non-image files need text for RAG
                    self.store.update_file_status(
                        user_id=user_id,
                        file_id=file_meta.file_id,
                        status=FileStatus.FAILED.value,
                        error_message="No text content extracted"
                    )
                    file_meta.status = FileStatus.FAILED.value
                    file_meta.error_message = "No text content extracted"
                    return file_meta

            # Truncate if too long
            text = extracted_text[:MAX_TEXT_LENGTH]
            if len(extracted_text) > MAX_TEXT_LENGTH:
                logger.warning(
                    f"Truncated text from {len(extracted_text)} to {MAX_TEXT_LENGTH} chars"
                )

            # Chunk the text
            if pages and len(pages) > 1:
                # Use page-aware chunking for PDFs
                text_chunks = self.chunker.chunk_pages(pages)
            else:
                # Plain text chunking
                text_chunks = self.chunker.chunk_text(text)

            if not text_chunks:
                self.store.update_file_status(
                    user_id=user_id,
                    file_id=file_meta.file_id,
                    status=FileStatus.FAILED.value,
                    error_message="Failed to create text chunks"
                )
                file_meta.status = FileStatus.FAILED.value
                file_meta.error_message = "Failed to create text chunks"
                return file_meta

            # Limit chunks
            if len(text_chunks) > MAX_CHUNKS_PER_FILE:
                logger.warning(
                    f"Limiting chunks from {len(text_chunks)} to {MAX_CHUNKS_PER_FILE}"
                )
                text_chunks = text_chunks[:MAX_CHUNKS_PER_FILE]

            # Embed chunks in batches
            file_chunks = self._embed_and_create_chunks(
                text_chunks=text_chunks,
                file_id=file_meta.file_id,
                user_id=user_id,
                conversation_id=conversation_id,
                filename=original_filename
            )

            # Store chunks
            num_stored = self.store.store_chunks(user_id, file_meta.file_id, file_chunks)

            # Mark as ready
            self.store.update_file_status(
                user_id=user_id,
                file_id=file_meta.file_id,
                status=FileStatus.READY.value,
                text_length=len(text),
                num_chunks=num_stored
            )

            file_meta.status = FileStatus.READY.value
            file_meta.text_length = len(text)
            file_meta.num_chunks = num_stored

            logger.info(
                f"Processed file {file_meta.file_id}: {num_stored} chunks, "
                f"{len(text)} chars"
            )

            return file_meta

        except Exception as e:
            logger.error(f"Failed to process file {file_meta.file_id}: {e}")

            self.store.update_file_status(
                user_id=user_id,
                file_id=file_meta.file_id,
                status=FileStatus.FAILED.value,
                error_message=str(e)[:500]
            )

            file_meta.status = FileStatus.FAILED.value
            file_meta.error_message = str(e)[:500]
            return file_meta

    def _embed_and_create_chunks(
        self,
        text_chunks: List[TextChunk],
        file_id: str,
        user_id: str,
        conversation_id: Optional[str],
        filename: Optional[str]
    ) -> List[FileChunk]:
        """
        Embed text chunks and create FileChunk objects.
        """
        file_chunks = []

        # Process in batches for efficiency
        for i in range(0, len(text_chunks), EMBEDDING_BATCH_SIZE):
            batch = text_chunks[i:i + EMBEDDING_BATCH_SIZE]

            # Get embeddings for batch
            embeddings = embed_chunks_batch(batch)

            # Create FileChunk objects
            for text_chunk, embedding in zip(batch, embeddings):
                # Truncate text if needed
                chunk_text = text_chunk.text
                if len(chunk_text) > MAX_CHUNK_TEXT_LENGTH:
                    chunk_text = chunk_text[:MAX_CHUNK_TEXT_LENGTH]

                file_chunk = create_chunk(
                    file_id=file_id,
                    user_id=user_id,
                    chunk_index=text_chunk.chunk_index,
                    text=chunk_text,
                    char_start=text_chunk.char_start,
                    char_end=text_chunk.char_end,
                    embedding=embedding,
                    conversation_id=conversation_id,
                    page_start=text_chunk.page_start,
                    page_end=text_chunk.page_end,
                    filename=filename
                )
                file_chunks.append(file_chunk)

        return file_chunks

    def get_file_status(self, user_id: str, file_id: str) -> Optional[str]:
        """Get processing status of a file."""
        file_meta = self.store.get_file(user_id, file_id)
        return file_meta.status if file_meta else None

    def get_file(self, user_id: str, file_id: str) -> Optional[FileMetadata]:
        """Get file metadata."""
        return self.store.get_file(user_id, file_id)

    def get_files_for_conversation(
        self,
        user_id: str,
        conversation_id: str
    ) -> List[FileMetadata]:
        """Get all processed files for a conversation."""
        return self.store.get_files_for_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            status=FileStatus.READY.value
        )

    def delete_file(self, user_id: str, file_id: str) -> bool:
        """Delete a file and its chunks."""
        try:
            self.store.delete_file_and_chunks(user_id, file_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return False


# =============================================================================
# Background Processing
# =============================================================================

# Thread pool for background processing
_processing_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="file_proc_")


def process_file_async(
    db,
    user_id: str,
    filename: str,
    original_filename: str,
    mime_type: str,
    file_size: int,
    extracted_text: str,
    file_path: Optional[str] = None,
    conversation_id: Optional[str] = None,
    pages: Optional[List[str]] = None,
    num_pages: Optional[int] = None
) -> str:
    """
    Process a file asynchronously in background thread.

    Returns:
        file_id for status tracking
    """
    processor = FileProcessor(db)

    # Create file record immediately (returns file_id)
    file_meta = processor.store.create_file(
        user_id=user_id,
        filename=filename,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=file_size,
        file_path=file_path,
        conversation_id=conversation_id,
        num_pages=num_pages or (len(pages) if pages else None)
    )

    file_id = file_meta.file_id

    # Submit for background processing
    def _process():
        try:
            proc = FileProcessor(db)
            # Skip create_file since we already did it
            proc._process_file_internal(
                file_meta=file_meta,
                extracted_text=extracted_text,
                pages=pages
            )
        except Exception as e:
            logger.error(f"Background processing failed for {file_id}: {e}")

    _processing_executor.submit(_process)

    return file_id


# Add internal processing method for async use
def _process_file_internal(self, file_meta: FileMetadata, extracted_text: str, pages: Optional[List[str]] = None):
    """Internal processing after file record created."""
    user_id = file_meta.user_id
    file_id = file_meta.file_id

    try:
        if not extracted_text or len(extracted_text.strip()) < 10:
            self.store.update_file_status(
                user_id=user_id,
                file_id=file_id,
                status=FileStatus.FAILED.value,
                error_message="No text content extracted"
            )
            return

        text = extracted_text[:MAX_TEXT_LENGTH]

        if pages and len(pages) > 1:
            text_chunks = self.chunker.chunk_pages(pages)
        else:
            text_chunks = self.chunker.chunk_text(text)

        if not text_chunks:
            self.store.update_file_status(
                user_id=user_id,
                file_id=file_id,
                status=FileStatus.FAILED.value,
                error_message="Failed to create text chunks"
            )
            return

        if len(text_chunks) > MAX_CHUNKS_PER_FILE:
            text_chunks = text_chunks[:MAX_CHUNKS_PER_FILE]

        file_chunks = self._embed_and_create_chunks(
            text_chunks=text_chunks,
            file_id=file_id,
            user_id=user_id,
            conversation_id=file_meta.conversation_id,
            filename=file_meta.original_filename
        )

        num_stored = self.store.store_chunks(user_id, file_id, file_chunks)

        self.store.update_file_status(
            user_id=user_id,
            file_id=file_id,
            status=FileStatus.READY.value,
            text_length=len(text),
            num_chunks=num_stored
        )

        logger.info(f"Processed file {file_id}: {num_stored} chunks")

    except Exception as e:
        logger.error(f"Failed to process file {file_id}: {e}")
        self.store.update_file_status(
            user_id=user_id,
            file_id=file_id,
            status=FileStatus.FAILED.value,
            error_message=str(e)[:500]
        )


# Attach method to class
FileProcessor._process_file_internal = _process_file_internal
