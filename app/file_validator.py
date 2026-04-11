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


def _detect_with_libmagic(header: bytes) -> str:
    """Use libmagic for robust content detection. Returns MIME type or empty string."""
    try:
        import magic
        return magic.from_buffer(header, mime=True) or ""
    except Exception:
        return ""


def get_file_type(filename: str, file) -> str:
    """
    Determines the MIME type of a file using libmagic + magic bytes + extension.
    Priority: libmagic > custom signatures > extension-based guess.
    Returns content-based MIME type for security.
    """
    ext_mime_type, _ = mimetypes.guess_type(filename)
    ext = os.path.splitext(filename)[1].lower() if filename else ''

    header = file.read(4096)
    file.seek(0)

    # ── Step 1: libmagic (most reliable) ──
    content_mime_type = _detect_with_libmagic(header)

    # ── Step 2: Custom signatures for formats libmagic may miss or misclassify ──
    if not content_mime_type or content_mime_type in ('application/octet-stream',):
        content_signatures = [
            (b'\x89PNG\r\n\x1a\n', 'image/png'),
            (b'\xFF\xD8\xFF', 'image/jpeg'),
            (b'%PDF', 'application/pdf'),
            (b'GIF87a', 'image/gif'),
            (b'GIF89a', 'image/gif'),
            (b'PK\x03\x04', 'application/zip'),
            (b'BM', 'image/bmp'),
            (b'II\x2a\x00', 'image/tiff'),  # Little-endian TIFF
            (b'MM\x00\x2a', 'image/tiff'),  # Big-endian TIFF
            (b'ID3', 'audio/mpeg'),          # MP3 with ID3 tag
            (b'\xff\xfb', 'audio/mpeg'),     # MP3 frame sync
            (b'\xff\xf3', 'audio/mpeg'),     # MP3 frame sync
            (b'\xff\xf2', 'audio/mpeg'),     # MP3 frame sync
            (b'fLaC', 'audio/flac'),
            (b'OggS', 'audio/ogg'),
            (b'\x1aE\xdf\xa3', 'audio/webm'),  # WebM/Matroska
        ]
        for signature, mime in content_signatures:
            if header.startswith(signature):
                content_mime_type = mime
                break

    # ── Step 2b: Override libmagic text/plain if binary signature matches ──
    # libmagic sometimes misclassifies audio/image headers as text/plain
    if content_mime_type == 'text/plain':
        binary_overrides = [
            (b'ID3', 'audio/mpeg'), (b'\xff\xfb', 'audio/mpeg'),
            (b'\xff\xf3', 'audio/mpeg'), (b'\xff\xf2', 'audio/mpeg'),
            (b'fLaC', 'audio/flac'), (b'OggS', 'audio/ogg'),
            (b'\x89PNG', 'image/png'), (b'\xFF\xD8\xFF', 'image/jpeg'),
            (b'BM', 'image/bmp'),
        ]
        for sig, mime in binary_overrides:
            if header.startswith(sig):
                content_mime_type = mime
                break

    # ── Step 3: RIFF container disambiguation (WebP vs WAV vs AVI) ──
    if (not content_mime_type or content_mime_type == 'application/octet-stream') and header[:4] == b'RIFF':
        if header[8:12] == b'WEBP':
            content_mime_type = 'image/webp'
        elif header[8:12] == b'WAVE':
            content_mime_type = 'audio/wav'
        else:
            content_mime_type = 'application/octet-stream'

    # ── Step 4: HEIC/HEIF detection (ftyp box) ──
    if not content_mime_type or content_mime_type == 'application/octet-stream':
        if len(header) >= 12 and header[4:8] == b'ftyp':
            ftyp_brand = header[8:12]
            heic_brands = {b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'}
            if ftyp_brand in heic_brands:
                content_mime_type = 'image/heic'
            elif ftyp_brand in {b'M4A ', b'M4B ', b'mp41', b'mp42', b'isom'}:
                content_mime_type = 'audio/mp4'

    # ── Step 5: ZIP-based format disambiguation ──
    if content_mime_type == 'application/zip' and ext_mime_type:
        zip_extensions = {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/epub+zip',
            'application/vnd.oasis.opendocument.text',
            'application/vnd.oasis.opendocument.spreadsheet',
            'application/vnd.oasis.opendocument.presentation',
        }
        if ext_mime_type in zip_extensions:
            content_mime_type = ext_mime_type

    # ── Step 6: Text file safety check ──
    if not content_mime_type or content_mime_type == 'application/octet-stream':
        if ext_mime_type and ('text/' in (ext_mime_type or '') or ext in _CODE_EXTENSIONS):
            try:
                header.decode('utf-8')
                dangerous_patterns = [b'<script', b'<?php', b'<%', b'#!/']
                if not any(pattern in header.lower() for pattern in dangerous_patterns):
                    content_mime_type = ext_mime_type or 'text/plain'
            except UnicodeDecodeError:
                pass

    if not content_mime_type:
        content_mime_type = 'application/octet-stream'

    # Log mismatches for security monitoring
    if ext_mime_type and content_mime_type and ext_mime_type != content_mime_type:
        if content_mime_type != 'application/octet-stream':
            logger.warning(
                f"File type mismatch: extension={ext_mime_type}, content={content_mime_type}, "
                f"filename={filename}"
            )

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
        'image/webp': ['.webp'],
        'image/bmp': ['.bmp'],
        'image/tiff': ['.tiff', '.tif'],
        'image/heic': ['.heic'],
        'image/heif': ['.heif'],
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
        # EPUB
        'application/epub+zip': ['.epub'],
        # Audio
        'audio/mpeg': ['.mp3'],
        'audio/mp4': ['.m4a', '.mp4'],
        'audio/wav': ['.wav'],
        'audio/x-wav': ['.wav'],
        'audio/ogg': ['.ogg'],
        'audio/flac': ['.flac'],
        'audio/webm': ['.webm'],
        # ODF (LibreOffice)
        'application/vnd.oasis.opendocument.text': ['.odt'],
        'application/vnd.oasis.opendocument.spreadsheet': ['.ods'],
        'application/vnd.oasis.opendocument.presentation': ['.odp'],
        # Office Open XML formats are ZIP archives
        'application/zip': ['.docx', '.xlsx', '.pptx', '.epub', '.odt', '.ods', '.odp'],
        # Catch-all for unrecognized but allowed text files
        'application/octet-stream': _CODE_EXTENSIONS + ['.heic', '.heif'],
    }
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    # Blocked extensions — executables, installers, macro-enabled Office
    # Code files (.py, .js etc) are allowed for text analysis, never executed
    BLOCKED_EXTENSIONS = {
        # Executables & libraries
        '.exe', '.dll', '.so', '.dylib', '.scr', '.com', '.pif',
        # Windows scripts
        '.bat', '.cmd', '.vbs', '.vbe', '.wsf', '.wsh', '.ps1', '.psm1',
        # Installers & packages
        '.msi', '.msp', '.deb', '.rpm', '.dmg', '.app',
        # HTML applications
        '.hta', '.mht', '.mhtml',
        # Java
        '.jar', '.class',
        # Server-side scripts
        '.asp', '.aspx', '.jsp', '.cgi',
        # Macro-enabled Office (can execute code)
        '.xlsm', '.docm', '.pptm', '.xltm', '.dotm', '.potm',
        # Archives (zip bomb risk — allow only via explicit archive handler)
        '.tar', '.gz', '.bz2', '.7z', '.rar',
    }

    # MIME types that indicate executable content — reject regardless of extension
    DANGEROUS_MIMES = {
        'application/x-executable', 'application/x-mach-binary',
        'application/x-elf', 'application/x-dosexec',
        'application/x-sharedlib', 'application/x-pie-executable',
        'application/x-msdos-program',
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

        # Deep content check with libmagic — catches exe renamed to .pdf
        try:
            import magic
            header = file.read(4096)
            file.seek(0)
            detected = magic.from_buffer(header, mime=True)
            if detected in cls.DANGEROUS_MIMES:
                logger.warning(
                    f"🛡️ BLOCKED: executable content detected as {detected} "
                    f"in file claiming to be {ext} ({filename})"
                )
                return False, "File contains executable content and cannot be uploaded"
        except Exception:
            pass  # libmagic unavailable — fall through to standard checks

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
