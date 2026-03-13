"""
File Chunks - Document RAG Storage Layer
=========================================

Firestore schema for chunked document storage:

Collection: users/{user_id}/files
    - file_id (doc id)
    - user_id
    - conversation_id (optional - links to chat session)
    - filename
    - original_filename
    - mime_type
    - file_size
    - created_at
    - status: 'processing' | 'ready' | 'failed'
    - error_message (if failed)
    - text_length (total extracted text length)
    - num_chunks
    - num_pages (if PDF)

Collection: users/{user_id}/file_chunks
    - chunk_id (doc id)
    - file_id
    - user_id
    - conversation_id (optional)
    - chunk_index (0-based)
    - page_start (1-based, if PDF)
    - page_end (1-based, if PDF)
    - text
    - embedding (quantized: {data: base64, min: float, max: float})
    - char_start (position in original text)
    - char_end
    - created_at

This mirrors the messages collection but for document chunks.
"""

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Safety limits
MAX_CHUNKS_PER_FILE = 300          # Prevent runaway chunking
MAX_FILE_CHUNKS_STAGE1 = 1500      # Global retrieval cap
MAX_CHUNK_TEXT_LENGTH = 4000       # ~1000 tokens

# Chunking parameters
DEFAULT_CHUNK_SIZE = 2000          # chars (~500 tokens)
DEFAULT_CHUNK_OVERLAP = 200        # 10% overlap
MIN_CHUNK_SIZE = 100               # Don't create tiny chunks


class FileStatus(str, Enum):
    PROCESSING = 'processing'
    READY = 'ready'
    FAILED = 'failed'


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class FileMetadata:
    """Metadata for an uploaded file."""
    file_id: str
    user_id: str
    filename: str
    original_filename: str
    mime_type: str
    file_size: int
    created_at: str
    status: str = FileStatus.PROCESSING.value
    conversation_id: Optional[str] = None
    error_message: Optional[str] = None
    text_length: int = 0
    num_chunks: int = 0
    num_pages: Optional[int] = None
    file_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = asdict(self)
        # Remove None values for cleaner storage
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FileMetadata':
        """Create from Firestore document."""
        return cls(
            file_id=data.get('file_id', ''),
            user_id=data.get('user_id', ''),
            filename=data.get('filename', ''),
            original_filename=data.get('original_filename', ''),
            mime_type=data.get('mime_type', ''),
            file_size=data.get('file_size', 0),
            created_at=data.get('created_at', ''),
            status=data.get('status', FileStatus.PROCESSING.value),
            conversation_id=data.get('conversation_id'),
            error_message=data.get('error_message'),
            text_length=data.get('text_length', 0),
            num_chunks=data.get('num_chunks', 0),
            num_pages=data.get('num_pages'),
            file_path=data.get('file_path')
        )


@dataclass
class FileChunk:
    """A single chunk from a document."""
    chunk_id: str
    file_id: str
    user_id: str
    chunk_index: int
    text: str
    created_at: str
    conversation_id: Optional[str] = None
    page_start: Optional[int] = None  # 1-based
    page_end: Optional[int] = None    # 1-based
    char_start: int = 0
    char_end: int = 0
    embedding: Optional[Dict[str, Any]] = None  # Quantized format
    filename: Optional[str] = None  # Denormalized for retrieval display

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FileChunk':
        """Create from Firestore document."""
        return cls(
            chunk_id=data.get('chunk_id', ''),
            file_id=data.get('file_id', ''),
            user_id=data.get('user_id', ''),
            chunk_index=data.get('chunk_index', 0),
            text=data.get('text', ''),
            created_at=data.get('created_at', ''),
            conversation_id=data.get('conversation_id'),
            page_start=data.get('page_start'),
            page_end=data.get('page_end'),
            char_start=data.get('char_start', 0),
            char_end=data.get('char_end', 0),
            embedding=data.get('embedding'),
            filename=data.get('filename')
        )


# =============================================================================
# Firestore Operations
# =============================================================================

class FileChunkStore:
    """
    Firestore storage for file metadata and chunks.

    Usage:
        store = FileChunkStore(db)

        # Create file record
        file_meta = store.create_file(user_id, filename, ...)

        # Store chunks
        store.store_chunks(user_id, file_id, chunks)

        # Mark ready
        store.update_file_status(user_id, file_id, 'ready', num_chunks=10)

        # Retrieve for RAG
        chunks = store.get_chunks_for_retrieval(user_id, conversation_id, limit=100)
    """

    def __init__(self, db):
        """
        Initialize with Firestore client.

        Args:
            db: Firestore client instance
        """
        self.db = db

    def _get_files_ref(self, user_id: str):
        """Get reference to user's files collection."""
        return self.db.collection('users').document(user_id).collection('files')

    def _get_chunks_ref(self, user_id: str):
        """Get reference to user's file_chunks collection."""
        return self.db.collection('users').document(user_id).collection('file_chunks')

    # -------------------------------------------------------------------------
    # File Metadata Operations
    # -------------------------------------------------------------------------

    def create_file(
        self,
        user_id: str,
        filename: str,
        original_filename: str,
        mime_type: str,
        file_size: int,
        file_path: Optional[str] = None,
        conversation_id: Optional[str] = None,
        num_pages: Optional[int] = None
    ) -> FileMetadata:
        """
        Create a new file record in processing state.

        Args:
            user_id: Owner user ID
            filename: Stored filename (unique)
            original_filename: Original upload name
            mime_type: MIME type
            file_size: Size in bytes
            file_path: Storage path (optional)
            conversation_id: Associated chat session (optional)
            num_pages: Number of pages if PDF

        Returns:
            FileMetadata with generated file_id
        """
        file_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        file_meta = FileMetadata(
            file_id=file_id,
            user_id=user_id,
            filename=filename,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=file_size,
            created_at=now,
            status=FileStatus.PROCESSING.value,
            conversation_id=conversation_id,
            num_pages=num_pages,
            file_path=file_path
        )

        # Store in Firestore
        self._get_files_ref(user_id).document(file_id).set(file_meta.to_dict())

        logger.info(f"Created file record: {file_id} for user {user_id}")
        return file_meta

    def update_file_status(
        self,
        user_id: str,
        file_id: str,
        status: str,
        text_length: int = 0,
        num_chunks: int = 0,
        error_message: Optional[str] = None
    ) -> None:
        """
        Update file processing status.

        Args:
            user_id: Owner user ID
            file_id: File ID
            status: New status ('processing', 'ready', 'failed')
            text_length: Total extracted text length
            num_chunks: Number of chunks created
            error_message: Error message if failed
        """
        update_data = {
            'status': status,
            'text_length': text_length,
            'num_chunks': num_chunks
        }

        if error_message:
            update_data['error_message'] = error_message

        self._get_files_ref(user_id).document(file_id).update(update_data)

        logger.info(f"Updated file {file_id} status to {status}")

    def get_file(self, user_id: str, file_id: str) -> Optional[FileMetadata]:
        """Get file metadata by ID."""
        doc = self._get_files_ref(user_id).document(file_id).get()
        if doc.exists:
            return FileMetadata.from_dict(doc.to_dict())
        return None

    def get_files_for_conversation(
        self,
        user_id: str,
        conversation_id: str,
        status: Optional[str] = None
    ) -> List[FileMetadata]:
        """Get all files for a conversation."""
        query = self._get_files_ref(user_id).where(
            filter=FieldFilter('conversation_id', '==', conversation_id)
        )

        if status:
            query = query.where(filter=FieldFilter('status', '==', status))

        files = []
        for doc in query.stream():
            files.append(FileMetadata.from_dict(doc.to_dict()))

        return files

    def get_recent_files(
        self,
        user_id: str,
        limit: int = 50,
        status: str = FileStatus.READY.value
    ) -> List[FileMetadata]:
        """Get recent files for a user."""
        query = self._get_files_ref(user_id).where(
            filter=FieldFilter('status', '==', status)
        ).order_by('created_at', direction='DESCENDING').limit(limit)

        files = []
        for doc in query.stream():
            files.append(FileMetadata.from_dict(doc.to_dict()))

        return files

    # -------------------------------------------------------------------------
    # Chunk Operations
    # -------------------------------------------------------------------------

    def store_chunks(
        self,
        user_id: str,
        file_id: str,
        chunks: List[FileChunk]
    ) -> int:
        """
        Store multiple chunks for a file.

        Args:
            user_id: Owner user ID
            file_id: Parent file ID
            chunks: List of FileChunk objects

        Returns:
            Number of chunks stored
        """
        if not chunks:
            return 0

        # Safety limit
        if len(chunks) > MAX_CHUNKS_PER_FILE:
            logger.warning(
                f"Truncating chunks from {len(chunks)} to {MAX_CHUNKS_PER_FILE}"
            )
            chunks = chunks[:MAX_CHUNKS_PER_FILE]

        # Batch write for efficiency
        batch = self.db.batch()
        chunks_ref = self._get_chunks_ref(user_id)

        for chunk in chunks:
            doc_ref = chunks_ref.document(chunk.chunk_id)
            batch.set(doc_ref, chunk.to_dict())

        batch.commit()

        logger.info(f"Stored {len(chunks)} chunks for file {file_id}")
        return len(chunks)

    def get_chunks_for_file(
        self,
        user_id: str,
        file_id: str
    ) -> List[FileChunk]:
        """Get all chunks for a specific file."""
        query = self._get_chunks_ref(user_id).where(
            filter=FieldFilter('file_id', '==', file_id)
        ).order_by('chunk_index')

        chunks = []
        for doc in query.stream():
            chunks.append(FileChunk.from_dict(doc.to_dict()))

        return chunks

    def get_chunks_for_conversation(
        self,
        user_id: str,
        conversation_id: str,
        limit: int = MAX_FILE_CHUNKS_STAGE1
    ) -> List[FileChunk]:
        """
        Get chunks for all files in a conversation.
        Used for Stage 1 candidate retrieval.
        """
        query = self._get_chunks_ref(user_id).where(
            filter=FieldFilter('conversation_id', '==', conversation_id)
        ).limit(limit)

        chunks = []
        for doc in query.stream():
            chunks.append(FileChunk.from_dict(doc.to_dict()))

        return chunks

    def get_chunks_for_retrieval(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
        max_age_days: int = 90,
        limit: int = MAX_FILE_CHUNKS_STAGE1
    ) -> List[FileChunk]:
        """
        Get chunks for hybrid retrieval.

        Args:
            user_id: Owner user ID
            conversation_id: Filter to specific conversation (optional)
            file_ids: Filter to specific files (optional)
            max_age_days: Maximum age in days
            limit: Maximum chunks to return

        Returns:
            List of FileChunk objects
        """
        from datetime import timedelta

        chunks_ref = self._get_chunks_ref(user_id)

        # Calculate cutoff date
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()

        # Build query - use single field queries to avoid composite index requirements
        if conversation_id:
            # Query by conversation_id only, filter by date in Python
            query = chunks_ref.where(
                filter=FieldFilter('conversation_id', '==', conversation_id)
            ).limit(limit * 2)  # Fetch extra to account for date filtering

            chunks = []
            for doc in query.stream():
                chunk = FileChunk.from_dict(doc.to_dict())
                # Filter by date in Python
                if chunk.created_at >= cutoff:
                    chunks.append(chunk)
                    if len(chunks) >= limit:
                        break
            return chunks
        elif file_ids:
            # Firestore doesn't support 'in' with other conditions well,
            # so we fetch and filter
            chunks = []
            for file_id in file_ids[:10]:  # Limit file IDs
                file_query = chunks_ref.where(
                    filter=FieldFilter('file_id', '==', file_id)
                ).limit(limit // len(file_ids))

                for doc in file_query.stream():
                    chunk = FileChunk.from_dict(doc.to_dict())
                    if chunk.created_at >= cutoff:
                        chunks.append(chunk)

            return chunks[:limit]
        else:
            # All user chunks within recency window
            query = chunks_ref.where(
                filter=FieldFilter('created_at', '>=', cutoff)
            ).limit(limit)

        chunks = []
        for doc in query.stream():
            chunks.append(FileChunk.from_dict(doc.to_dict()))

        return chunks

    def delete_file_and_chunks(self, user_id: str, file_id: str) -> int:
        """
        Delete a file and all its chunks.

        Returns:
            Number of chunks deleted
        """
        # Delete chunks first
        chunks_ref = self._get_chunks_ref(user_id)
        query = chunks_ref.where(filter=FieldFilter('file_id', '==', file_id))

        batch = self.db.batch()
        count = 0

        for doc in query.stream():
            batch.delete(doc.reference)
            count += 1
            # Firestore batch limit is 500
            if count % 450 == 0:
                batch.commit()
                batch = self.db.batch()

        if count % 450 != 0:
            batch.commit()

        # Delete file metadata
        self._get_files_ref(user_id).document(file_id).delete()

        logger.info(f"Deleted file {file_id} and {count} chunks")
        return count


# =============================================================================
# Utility Functions
# =============================================================================

def generate_chunk_id() -> str:
    """Generate a unique chunk ID."""
    return f"chunk_{uuid.uuid4().hex[:16]}"


def create_chunk(
    file_id: str,
    user_id: str,
    chunk_index: int,
    text: str,
    char_start: int,
    char_end: int,
    embedding: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    filename: Optional[str] = None
) -> FileChunk:
    """
    Create a FileChunk with generated ID and timestamp.

    Args:
        file_id: Parent file ID
        user_id: Owner user ID
        chunk_index: 0-based index
        text: Chunk text content
        char_start: Start position in original text
        char_end: End position in original text
        embedding: Quantized embedding dict (optional)
        conversation_id: Associated conversation (optional)
        page_start: Start page (1-based, optional)
        page_end: End page (1-based, optional)
        filename: Original filename for display

    Returns:
        FileChunk object
    """
    return FileChunk(
        chunk_id=generate_chunk_id(),
        file_id=file_id,
        user_id=user_id,
        chunk_index=chunk_index,
        text=text,
        created_at=datetime.utcnow().isoformat(),
        conversation_id=conversation_id,
        page_start=page_start,
        page_end=page_end,
        char_start=char_start,
        char_end=char_end,
        embedding=embedding,
        filename=filename
    )
