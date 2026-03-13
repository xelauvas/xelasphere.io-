"""FileValidator class for validating uploaded files.

This module handles file validation including MIME type checking,
extension validation, and security measures against malicious uploads.
"""

import hashlib
import logging
import mimetypes
import os
from datetime import datetime
from typing import Tuple

from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


# All code/text extensions for text/plain MIME type
_CODE_EXTENSIONS = [
    '.py', '.pyw', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx',
    '.java', '.kt', '.kts', '.scala', '.c', '.h', '.cpp', '.hpp', '.cc',
    '.cs', '.go', '.rs', '.rb', '.erb', '.php', '.swift', '.dart', '.lua',
    '.pl', '.pm', '.r', '.sql', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.psm1',
    '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
    '.hs', '.lhs', '.ex', '.exs', '.clj', '.cljs', '.cljc', '.ml', '.mli',
    '.fs', '.fsx', '.erl', '.hrl', '.elm', '.v', '.sv', '.vhd', '.vhdl',
    '.asm', '.s', '.dockerfile', '.makefile', '.mk', '.gradle', '.cmake',
    '.tf', '.tfvars', '.proto', '.graphql', '.gql', '.sol', '.zig', '.nim',
    '.cr', '.jl', '.txt', '.text', '.md', '.ini', '.cfg', '.conf', '.env',
    '.yaml', '.yml', '.toml', '.log',
    # Markup files (no magic bytes, detected as octet-stream)
    '.html', '.htm', '.xml', '.svg', '.xhtml',
]


def get_file_type(filename: str, file) -> str:
    """
    Determines the MIME type of a file using both extension and content inspection.
    This helps prevent mime-type spoofing attacks.
    Returns content-based MIME type for security.
    """
    # First check the extension
    ext_mime_type, _ = mimetypes.guess_type(filename)

    # Then check the actual content
    header = file.read(2048)
    file.seek(0)

    # Map of magic bytes to mime types (ordered by specificity)
    content_signatures = [
        # More specific signatures first
        (b'\x89PNG\r\n\x1a\n', 'image/png'),  # Complete PNG signature
        (b'\xFF\xD8\xFF\xE0', 'image/jpeg'),  # JPEG with JFIF
        (b'\xFF\xD8\xFF\xE1', 'image/jpeg'),  # JPEG with EXIF
        (b'\xFF\xD8\xFF\xDB', 'image/jpeg'),  # JPEG with DQT
        (b'\xFF\xD8\xFF', 'image/jpeg'),  # Generic JPEG
        (b'%PDF-', 'application/pdf'),  # PDF with version
        (b'%PDF', 'application/pdf'),  # PDF fallback
        (b'GIF87a', 'image/gif'),
        (b'GIF89a', 'image/gif'),
        (b'PK\x03\x04', 'application/zip'),  # ZIP files including docx/xlsx
        (b'RIFF', 'application/octet-stream'),  # RIFF container (could be WebP, AVI, etc.)
    ]

    # Determine content-based mime type
    content_mime_type = None
    for signature, mime in content_signatures:
        if header.startswith(signature):
            content_mime_type = mime
            break

    # For ZIP-based formats (docx, xlsx, etc.), check internal structure
    if content_mime_type == 'application/zip' and ext_mime_type:
        office_extensions = {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        }
        if ext_mime_type in office_extensions:
            content_mime_type = ext_mime_type

    # For text files, require extension match and validate content is safe
    if not content_mime_type:
        if ext_mime_type == 'text/plain':
            # Verify it's actually text by checking for non-printable chars
            try:
                decoded = header.decode('utf-8')
                # Check for dangerous patterns in "text" files
                dangerous_patterns = [b'<script', b'<?php', b'<%', b'#!/']
                if not any(pattern in header.lower() for pattern in dangerous_patterns):
                    content_mime_type = 'text/plain'
            except UnicodeDecodeError:
                pass

    # If we still couldn't determine, mark as unknown
    if not content_mime_type:
        content_mime_type = 'application/octet-stream'

    # Log mismatches for security monitoring
    if ext_mime_type and content_mime_type and ext_mime_type != content_mime_type:
        if content_mime_type != 'application/octet-stream':
            logger.warning(
                f"File type mismatch detected: extension={ext_mime_type}, content={content_mime_type}, "
                f"filename={filename}"
            )

    # Always trust content-based detection over extension
    return content_mime_type


def generate_unique_filename(original_filename: str, user_id: str) -> str:
    """Generate a unique filename with timestamp and hash."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_hash = hashlib.md5(f"{user_id}_{str(datetime.now())}".encode()).hexdigest()[:8]
    _, ext = os.path.splitext(original_filename)
    secure_name = secure_filename(original_filename)
    base_name = os.path.splitext(secure_name)[0]
    return f"{user_id}_{base_name}_{timestamp}_{file_hash}{ext}"


class FileValidator:
    """Validates uploaded files for security and supported types."""

    # Reference the module-level code extensions
    _CODE_EXTENSIONS = _CODE_EXTENSIONS

    ALLOWED_MIMETYPES = {
        # PDF
        'application/pdf': ['.pdf'],
        # Images
        'image/jpeg': ['.jpg', '.jpeg'],
        'image/png': ['.png'],
        'image/gif': ['.gif'],
        'image/svg+xml': ['.svg'],
        # Word
        'application/msword': ['.doc'],
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
        # Excel
        'application/vnd.ms-excel': ['.xls'],
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
        # PowerPoint
        'application/vnd.ms-powerpoint': ['.ppt'],
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
        # Text/Code - most code files are detected as text/plain
        'text/plain': _CODE_EXTENSIONS,
        'text/x-python': ['.py', '.pyw'],
        'text/javascript': ['.js', '.mjs', '.cjs'],
        'application/javascript': ['.js', '.mjs', '.cjs'],
        'text/typescript': ['.ts', '.tsx'],
        'text/x-java': ['.java'],
        'text/x-c': ['.c', '.h'],
        'text/x-c++': ['.cpp', '.hpp', '.cc'],
        'text/x-csharp': ['.cs'],
        'text/x-go': ['.go'],
        'text/x-rust': ['.rs'],
        'text/x-ruby': ['.rb', '.erb'],
        'text/x-php': ['.php'],
        'application/x-php': ['.php'],
        'text/x-swift': ['.swift'],
        'text/x-sql': ['.sql'],
        'text/x-shellscript': ['.sh', '.bash', '.zsh', '.fish'],
        'application/x-sh': ['.sh', '.bash'],
        'text/css': ['.css'],
        'text/x-scss': ['.scss'],
        'text/x-sass': ['.sass'],
        'text/x-less': ['.less'],
        # Data formats
        'text/csv': ['.csv'],
        'text/markdown': ['.md'],
        'application/json': ['.json'],
        'application/rtf': ['.rtf'],
        'text/rtf': ['.rtf'],
        'text/xml': ['.xml'],
        'application/xml': ['.xml'],
        'text/html': ['.html', '.htm'],
        'application/x-yaml': ['.yaml', '.yml'],
        'text/yaml': ['.yaml', '.yml'],
        'application/toml': ['.toml'],
        'text/x-toml': ['.toml'],
        # Office Open XML formats are ZIP archives
        'application/zip': ['.docx', '.xlsx', '.pptx'],
        # Catch-all for unrecognized but allowed text files
        'application/octet-stream': _CODE_EXTENSIONS,
    }
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    # Blocked extensions - only truly dangerous executables
    # Code files are allowed for analysis (read-only, never executed)
    BLOCKED_EXTENSIONS = {
        '.exe', '.dll', '.scr', '.com', '.pif',  # Windows executables
        '.bat', '.cmd', '.vbs', '.wsf', '.wsh',   # Windows scripts (dangerous)
        '.msi', '.msp',                            # Windows installers
        '.hta', '.mht', '.mhtml',                  # HTML applications
        '.jar',                                     # Java archives (executable)
        '.asp', '.aspx', '.jsp', '.cgi',           # Server-side scripts
    }

    @classmethod
    def validate(cls, file) -> Tuple[bool, str]:
        if not file:
            return False, "No file provided"

        # Check filename for blocked extensions
        filename = file.filename.lower() if file.filename else ''
        _, ext = os.path.splitext(filename)
        if ext in cls.BLOCKED_EXTENSIONS:
            logger.warning(f"Blocked file extension attempted: {ext}")
            return False, f"File type {ext} is not allowed"

        # Check file size
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > cls.MAX_FILE_SIZE:
            return False, f"File size exceeds {cls.MAX_FILE_SIZE // (1024*1024)}MB"

        # Validate using magic bytes (content-based detection)
        mime_type = get_file_type(file.filename, file)

        if mime_type not in cls.ALLOWED_MIMETYPES:
            logger.warning(f"Disallowed MIME type: {mime_type} for file {file.filename}")
            return False, f"File type {mime_type} not allowed"

        # Verify extension matches the detected content type
        allowed_extensions = cls.ALLOWED_MIMETYPES.get(mime_type, [])
        if ext and ext not in allowed_extensions:
            logger.warning(
                f"Extension mismatch: {ext} doesn't match detected type {mime_type} "
                f"(allowed: {allowed_extensions})"
            )
            return False, f"File extension {ext} doesn't match content type"

        return True, "File is valid"
