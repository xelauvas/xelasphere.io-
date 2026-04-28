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


# ==================== File utility helpers (migrated from views.py) ====================

import hashlib
import mimetypes
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

from .text_extractor import TextExtractorUtility
from .file_validator import FileValidator

_fp_logger = logging.getLogger(__name__)

# Pattern list for detecting file-related intent in user prompts
_FILE_INTENT_PATTERNS = [
    r"\b(?:in|from|about)\s+(?:the|my|this|that)\s+(?:document|file|pdf|upload|attachment)\b",
    r"\b(?:according|based)\s+(?:on|to)\s+(?:the|my|this|that)\s+(?:document|file|pdf)\b",
    r"\b(?:summarize|analyze|explain|extract|read|review)\s+(?:the|my|this|that)?\s*(?:document|file|pdf|upload|text)\b",
    r"\bwhat\s+(?:does|is|are)\s+(?:the|my|this)\s+(?:document|file|pdf)\b",
    r"\b(?:uploaded|attached)\s+(?:file|document|pdf)\b",
]


def get_file_type(filename: str, file) -> str:
    """
    Determines the MIME type of a file using both extension and content inspection.
    """
    ext_mime_type, _ = mimetypes.guess_type(filename)
    header = file.read(2048)
    file.seek(0)

    content_signatures = [
        (b'\\x89PNG\\r\\n\\x1a\\n', 'image/png'),
        (b'\\xFF\\xD8\\xFF\\xE0', 'image/jpeg'),
        (b'\\xFF\\xD8\\xFF\\xE1', 'image/jpeg'),
        (b'\\xFF\\xD8\\xFF\\xDB', 'image/jpeg'),
        (b'\\xFF\\xD8\\xFF', 'image/jpeg'),
        (b'%PDF-', 'application/pdf'),
        (b'%PDF', 'application/pdf'),
        (b'GIF87a', 'image/gif'),
        (b'GIF89a', 'image/gif'),
        (b'PK\\x03\\x04', 'application/zip'),
        (b'RIFF', 'application/octet-stream'),
    ]

    content_mime_type = None
    for signature, mime in content_signatures:
        if header.startswith(signature):
            content_mime_type = mime
            break

    if content_mime_type == 'application/zip' and ext_mime_type:
        office_extensions = {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        }
        if ext_mime_type in office_extensions:
            content_mime_type = ext_mime_type

    if not content_mime_type:
        if ext_mime_type == 'text/plain':
            try:
                decoded = header.decode('utf-8')
                dangerous_patterns = [b'<script', b'<?php', b'<%', b'#!/']
                if not any(pattern in header.lower() for pattern in dangerous_patterns):
                    content_mime_type = 'text/plain'
            except UnicodeDecodeError:
                pass

    if not content_mime_type:
        content_mime_type = 'application/octet-stream'

    if ext_mime_type and content_mime_type and ext_mime_type != content_mime_type:
        if content_mime_type != 'application/octet-stream':
            _fp_logger.warning(
                f"File type mismatch detected: extension={ext_mime_type}, content={content_mime_type}, "
                f"filename={filename}"
            )

    return content_mime_type


def generate_unique_filename(original_filename: str, user_id: str) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_hash = hashlib.md5(f"{user_id}_{str(datetime.now())}".encode()).hexdigest()[:8]
    _, ext = os.path.splitext(original_filename)
    secure_name = secure_filename(original_filename)
    base_name = os.path.splitext(secure_name)[0]
    return f"{user_id}_{base_name}_{timestamp}_{file_hash}{ext}"


def extract_text_concurrently(file_path: str) -> Tuple[str, Dict[str, Any]]:
    text, debug_info = TextExtractorUtility.extract_text(file_path)
    if debug_info.get('error'):
        _fp_logger.error(f"Text extraction error: {debug_info['error']}")
    return text, debug_info


def _safe_upload_relative_path(candidate: Optional[str]) -> Optional[str]:
    """Normalize a user-provided upload path so it stays within UPLOAD_FOLDER."""
    if not candidate or not isinstance(candidate, str):
        return None
    path = candidate.strip()
    if not path:
        return None
    prefixes = ['/uploads/', 'uploads/']
    for prefix in prefixes:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    path = path.lstrip('/').replace('\\\\', '/')
    if not path:
        return None
    normalized = os.path.normpath(path)
    if normalized.startswith('..'):
        return None
    return normalized.replace('\\\\', '/')


def _build_metadata_lookup() -> Dict[str, List[Dict[str, Any]]]:
    from flask import session
    metadata_list = session.get('file_metadata_list') or []
    if not isinstance(metadata_list, list):
        metadata_list = [metadata_list]
    lookup: Dict[str, List[Dict[str, Any]]] = {}
    for meta in metadata_list:
        if not isinstance(meta, dict):
            continue
        original = meta.get('original_filename')
        if not original:
            continue
        lookup.setdefault(original, []).append(meta)
    return lookup


def _resolve_user_attachment_source(
    attachment: Dict[str, Any],
    metadata_lookup: Dict[str, List[Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    from flask import url_for
    name = attachment.get('name') or attachment.get('original_filename')
    matched_meta = None
    if name:
        meta_bucket = metadata_lookup.get(name)
        if meta_bucket:
            matched_meta = meta_bucket.pop(0)

    if matched_meta:
        rel_path = matched_meta.get('relative_path')
        rel_path = _safe_upload_relative_path(rel_path)
        if rel_path:
            return {
                'url': url_for('upload.serve_uploaded_file', requested_path=rel_path, _external=False),
                'local_path': rel_path,
                'original_filename': matched_meta.get('original_filename') or name
            }

    preview_url = attachment.get('previewUrl')
    rel_path = _safe_upload_relative_path(preview_url)
    if rel_path:
        from flask import url_for as _url_for
        return {
            'url': _url_for('upload.serve_uploaded_file', requested_path=rel_path, _external=False),
            'local_path': rel_path,
            'original_filename': name
        }

    return None


def build_user_attachment_records(user_prompt: str, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform raw attachment payloads so they can be persisted in chat history."""
    from app.services.chat_engine import build_prompt_fragment
    if not attachments:
        return []

    metadata_lookup = _build_metadata_lookup()
    prompt_fragment = build_prompt_fragment(user_prompt)
    records: List[Dict[str, Any]] = []

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        resolved = _resolve_user_attachment_source(attachment, metadata_lookup)
        if not resolved:
            continue
        record = {
            'name': attachment.get('name') or attachment.get('original_filename') or 'Attachment',
            'original_filename': resolved.get('original_filename'),
            'type': attachment.get('type'),
            'isImage': bool(attachment.get('isImage')),
            'url': resolved['url'],
            'local_path': resolved.get('local_path'),
            'prompt_fragment': prompt_fragment,
            'provider': 'user-upload',
            'uploaded_at': datetime.utcnow().isoformat()
        }
        cleaned = {k: v for k, v in record.items() if v is not None}
        records.append(cleaned)

    return records


def _get_file_chunk_context(
    user_id: str,
    query: str,
    conversation_id: Optional[str] = None,
    top_k: int = 5
) -> str:
    """Retrieve relevant file chunks for the query."""
    try:
        from app.hybrid_retrieval import FileChunkRetriever, format_file_chunk_context
        from app.config import db as _db

        _fp_logger.info(f"RAG retrieval: query_len={len(query)}, conversation_id={conversation_id}")

        retriever = FileChunkRetriever(_db)
        results = retriever.search(
            user_id=user_id,
            query=query,
            conversation_id=conversation_id,
            top_k=top_k
        )

        if not results:
            _fp_logger.info("RAG retrieval: No chunks found")
            return ""

        _fp_logger.info(f"RAG retrieval: Found {len(results)} relevant chunks")
        return format_file_chunk_context(results)

    except Exception as e:
        _fp_logger.warning(f"Failed to retrieve file chunks: {e}")
        return ""


def _detect_file_intent(prompt: str, has_files: bool = False) -> bool:
    """Detect if the user's prompt references uploaded files/documents."""
    prompt_lower = prompt.lower()

    if has_files and any(word in prompt_lower for word in ['document', 'file', 'pdf', 'uploaded', 'attachment']):
        return True

    for pattern in _FILE_INTENT_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True

    return False
