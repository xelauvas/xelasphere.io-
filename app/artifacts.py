"""Utilities for Claude-style artifact routing and persistence."""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from firebase_admin import firestore as admin_firestore
from google.cloud import firestore as gc_firestore


logger = logging.getLogger(__name__)


META_PATTERN = re.compile(r"<META>(.*?)</META>", re.DOTALL | re.IGNORECASE)
CHAT_PATTERN = re.compile(r"<CHAT>(.*?)</CHAT>", re.DOTALL | re.IGNORECASE)
ARTIFACT_PATTERN = re.compile(r"<ARTIFACT>(.*?)</ARTIFACT>", re.DOTALL | re.IGNORECASE)
CODE_FENCE_PATTERN = re.compile(r"(```|~~~).+?\1", re.DOTALL)
CODE_KEYWORD_PATTERN = re.compile(
    r"\b(def|class|function|fn|var|let|const|import|from|public|private|return|SELECT|INSERT|UPDATE|DELETE|BEGIN|END)\b",
    re.IGNORECASE,
)
CODE_FENCE_LANG_PATTERN = re.compile(r"```([\w.+-]+)")
SYMBOL_PATTERN = re.compile(r"[^A-Za-z0-9\s]")
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"(?i)(api|access|secret)[_\-]?key[=:\s]*[A-Za-z0-9\-]{16,}"),
]
# Pattern to find artifact fence blocks - captures everything between the fences
ARTIFACT_FENCE_PATTERN = re.compile(r"```artifact\s*([\s\S]*?)\s*```", re.IGNORECASE)
# Fallback pattern for unclosed artifact blocks (some models don't close with ```)
ARTIFACT_UNCLOSED_PATTERN = re.compile(r"```artifact\s*([\s\S]+)", re.IGNORECASE)
ARTIFACT_PATCH_FENCE_PATTERN = re.compile(r"```artifact_patch\s*([\s\S]*?)\s*```", re.IGNORECASE)
# New fenced format patterns (artifact-meta + artifact-file blocks)
FENCED_META_RE = re.compile(r"```artifact-meta\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
FENCED_FILE_RE = re.compile(r"```artifact-file:([^:]+):(\w+)\s*\n([\s\S]*?)```", re.IGNORECASE)
MAX_ARTIFACT_FILES = 40
MAX_ARTIFACT_TOTAL_BYTES = 800_000
MAX_ARTIFACT_FILE_BYTES = 200_000
DEFAULT_PRIMARY_LANGUAGE = "code"
SAFE_TEXT_LANGUAGES = {"text", "plain", "markdown"}

# Curly quote normalization for patch matching
_CURLY_TO_STRAIGHT = str.maketrans({
    '\u2018': "'",  # LEFT SINGLE QUOTATION MARK
    '\u2019': "'",  # RIGHT SINGLE QUOTATION MARK
    '\u201C': '"',  # LEFT DOUBLE QUOTATION MARK
    '\u201D': '"',  # RIGHT DOUBLE QUOTATION MARK
})


class ArtifactParseError(ValueError):
    """Raised when an artifact block cannot be parsed safely."""

    def __init__(self, message: str, technical_details: Optional[str] = None):
        """
        Initialize ArtifactParseError with user-friendly and technical messages.

        Args:
            message: Technical error message for logging
            technical_details: Additional technical context (optional)
        """
        super().__init__(message)
        self.technical_message = message
        self.technical_details = technical_details
        self.user_message = self._build_user_friendly_message(message)

    def _build_user_friendly_message(self, message: str) -> str:
        """Convert technical error to user-friendly message."""
        message_lower = message.lower()

        # JSON parsing errors
        if "valid json" in message_lower or "json" in message_lower:
            return "The artifact contains invalid JSON. Please check for missing commas, quotes, or brackets."

        # Path errors
        if "duplicate" in message_lower and "path" in message_lower:
            return "Two or more files have the same path. Each file must have a unique path."

        if "parent directories" in message_lower or ".." in message:
            return "File paths cannot use '..' to navigate to parent directories for security reasons."

        if "path" in message_lower and ("empty" in message_lower or "invalid" in message_lower):
            return "One or more file paths are empty or invalid. Please provide valid relative paths."

        # Size errors
        if "exceeds" in message_lower and ("maximum" in message_lower or "size" in message_lower):
            if "total" in message_lower:
                return "The artifact is too large. The total size must be less than 800 KB."
            return "One or more files are too large. Each file must be less than 200 KB."

        if "too many files" in message_lower:
            return "The artifact has too many files. The maximum is 40 files per artifact."

        # Content errors
        if "files array" in message_lower or "non-empty files" in message_lower:
            return "The artifact must include at least one file in the files array."

        if "entrypoint" in message_lower:
            return "The specified entrypoint file was not found in the files array."

        if "binary" in message_lower:
            return "Binary files are not supported. Please use text-based files only."

        if "empty" in message_lower:
            return "The artifact block is empty. Please include the artifact JSON data."

        # Type errors
        if "must be" in message_lower:
            if "object" in message_lower:
                return "The artifact data must be a JSON object, not an array or other type."
            if "string" in message_lower:
                return "One or more required fields must be text strings."

        # Generic fallback
        return f"Artifact validation failed: {message}"


def _repair_json_string(raw_json: str) -> str:
    """Attempt to repair common JSON issues from AI-generated content."""
    result = []
    i = 0
    in_string = False
    escape_next = False

    while i < len(raw_json):
        char = raw_json[i]

        if escape_next:
            # Already escaped, keep as-is
            result.append(char)
            escape_next = False
            i += 1
            continue

        if char == '\\':
            if in_string:
                # Check what's being escaped
                if i + 1 < len(raw_json):
                    next_char = raw_json[i + 1]
                    # Valid JSON escapes: " \ / b f n r t u
                    if next_char in '"\\bfnrtu/':
                        result.append(char)
                        escape_next = True
                    elif next_char == '\n':
                        # Escaped literal newline - convert to \n
                        result.append('\\n')
                        i += 1  # Skip the newline
                    else:
                        # Invalid escape - escape the backslash itself
                        result.append('\\\\')
                else:
                    result.append('\\\\')
            else:
                result.append(char)
            i += 1
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            result.append(char)
            i += 1
            continue

        if in_string:
            # Inside a string - escape control characters
            if char == '\n':
                result.append('\\n')
            elif char == '\r':
                result.append('\\r')
            elif char == '\t':
                result.append('\\t')
            elif ord(char) < 32:
                # Other control characters - escape as unicode
                result.append(f'\\u{ord(char):04x}')
            else:
                result.append(char)
        else:
            result.append(char)

        i += 1

    repaired = ''.join(result)

    # Second pass: fix missing commas between elements
    # This handles cases where AI generates: }"} {"path": or }"{" etc.
    repaired = _fix_missing_commas(repaired)

    return repaired


def _fix_missing_commas(json_str: str) -> str:
    """Fix missing commas in JSON structure."""
    result = []
    i = 0
    in_string = False
    escape_next = False

    while i < len(json_str):
        char = json_str[i]

        if escape_next:
            result.append(char)
            escape_next = False
            i += 1
            continue

        if char == '\\' and in_string:
            result.append(char)
            escape_next = True
            i += 1
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            result.append(char)
            i += 1
            continue

        if in_string:
            result.append(char)
            i += 1
            continue

        # Outside string - look for missing comma patterns
        result.append(char)

        if char in '}]':
            # Look ahead for patterns that need a comma
            j = i + 1
            # Skip whitespace
            while j < len(json_str) and json_str[j] in ' \t\n\r':
                j += 1

            if j < len(json_str):
                next_char = json_str[j]
                # Patterns that need a comma before them:
                # } followed by { or " or [ (missing comma between objects/elements)
                # ] followed by { or " or [ (missing comma between array elements)
                if next_char in '{["':
                    # Check if there's already proper JSON structure
                    # Don't add comma if it's the end of the JSON
                    remaining = json_str[j:].lstrip()
                    if remaining and remaining[0] in '{["':
                        # Insert a comma after the current position
                        result.append(',')

        i += 1

    return ''.join(result)


def _aggressive_json_cleanup(raw_json: str) -> str:
    """
    Aggressive JSON cleanup for AI-generated content.
    Handles issues like:
    - Literal newlines inside strings
    - Missing commas between elements
    - Unescaped special characters
    - Truncated JSON (tries to close open structures)
    - AI-generated malformed separators like }","{ instead of },{
    """
    # First, apply the standard repair
    cleaned = _repair_json_string(raw_json)

    # Fix common AI malformations in artifact JSON:

    # Pattern 1: }","{ should be },{
    # AI writes: {"file1":"..."}","{"file2":"..."}
    # Should be: {"file1":"..."},{"file2":"..."}
    cleaned = re.sub(r'\}"\s*,\s*"\{', '},{', cleaned)
    cleaned = re.sub(r'\}"\s*,\s*"\s*\{', '},{', cleaned)

    # Pattern 2: }"{ should be },{
    cleaned = re.sub(r'\}"\s*\{', '},{', cleaned)

    # Pattern 3: },"path" should be },{"path" (missing opening brace for file object)
    # AI writes: {"path":"file1","content":"..."},"path":"file2"
    # Should be: {"path":"file1","content":"..."},{"path":"file2"
    cleaned = re.sub(r'\},\s*"path"', '},{"path"', cleaned)
    cleaned = re.sub(r'\},\s*"language"', '},{"language"', cleaned)

    # Process strings to escape control characters
    try:
        result = []
        i = 0
        in_string = False
        escape_next = False

        while i < len(cleaned):
            char = cleaned[i]

            if escape_next:
                result.append(char)
                escape_next = False
                i += 1
                continue

            if char == '\\':
                if in_string:
                    # Keep the backslash and mark next char as escaped
                    result.append(char)
                    escape_next = True
                else:
                    result.append(char)
                i += 1
                continue

            if char == '"':
                in_string = not in_string
                result.append(char)
                i += 1
                continue

            if in_string:
                # Inside string - escape control characters
                if char == '\n':
                    result.append('\\n')
                elif char == '\r':
                    result.append('\\r')
                elif char == '\t':
                    result.append('\\t')
                elif ord(char) < 32:
                    result.append(f'\\u{ord(char):04x}')
                else:
                    result.append(char)
            else:
                result.append(char)

            i += 1

        # If we ended inside a string, close it
        if in_string:
            result.append('"')

        cleaned = ''.join(result)
    except Exception:
        pass  # Fall back to original cleaned version

    # Try to balance braces/brackets if JSON is truncated
    cleaned = _balance_json_structure(cleaned)

    return cleaned


def _balance_json_structure(json_str: str) -> str:
    """Try to close unclosed JSON structures."""
    # Count unmatched braces and brackets
    brace_count = 0
    bracket_count = 0
    in_string = False
    escape_next = False

    for char in json_str:
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue

        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
        elif char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1

    # Close any unclosed structures
    result = json_str
    if bracket_count > 0:
        result += ']' * bracket_count
    if brace_count > 0:
        result += '}' * brace_count

    # Remove extra closing braces/brackets
    if brace_count < 0 or bracket_count < 0:
        # Try to remove extra closing characters from the end
        while result and brace_count < 0:
            if result[-1] == '}':
                result = result[:-1]
                brace_count += 1
            else:
                break
        while result and bracket_count < 0:
            if result[-1] == ']':
                result = result[:-1]
                bracket_count += 1
            else:
                break

    return result


def _repair_incomplete_artifact_json(raw_json: str, brace_count: int, in_string: bool) -> str:
    """
    Repair truncated artifact JSON by closing unclosed structures.

    This handles cases where AI output was cut off mid-JSON, like:
    - Unclosed strings in file content
    - Missing closing brackets for files array
    - Missing closing braces for the artifact object
    - AI malformed separators like }","{ instead of },{
    """
    result = raw_json.rstrip()

    # Fix common AI malformations:
    # Pattern 1: }","{ should be },{
    result = re.sub(r'\}"\s*,\s*"\{', '},{', result)
    result = re.sub(r'\}"\s*,\s*"\s*\{', '},{', result)
    result = re.sub(r'\}"\s*\{', '},{', result)

    # Pattern 2: },"path" should be },{"path" (missing opening brace)
    result = re.sub(r'\},\s*"path"', '},{"path"', result)
    result = re.sub(r'\},\s*"language"', '},{"language"', result)

    # If we're inside a string, close it
    if in_string:
        # Find where we are - likely inside a "content" field
        # Close the string, then close any structures
        result += '"'

    # Try to make it valid by closing structures intelligently
    # Count what's open
    open_braces = 0
    open_brackets = 0
    in_str = False
    escape = False

    for char in result:
        if escape:
            escape = False
            continue
        if char == '\\' and in_str:
            escape = True
            continue
        if char == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if char == '{':
            open_braces += 1
        elif char == '}':
            open_braces -= 1
        elif char == '[':
            open_brackets += 1
        elif char == ']':
            open_brackets -= 1

    # Check if we're in the middle of a file object (inside files array)
    # Common truncation patterns:
    # 1. {"title":"...","files":[{"path":"...","content":"...  (cut off in content string)
    # 2. {"title":"...","files":[{"path":"...","content":"..."}  (cut off after file)

    # If last non-space char suggests incomplete structure, try to close it
    stripped = result.rstrip()

    # If it ends with an incomplete string value, we already closed the string above
    # Now check if we need to close the object/array

    # Look for common incomplete patterns and fix them
    if stripped.endswith(','):
        # Remove trailing comma before closing
        result = stripped[:-1]
    elif stripped.endswith(':'):
        # Key without value - add empty string
        result = stripped + '""'

    # Close any open brackets (files array)
    if open_brackets > 0:
        # Check if we need to close a file object first
        # If we're inside a file object, close it
        last_brace_pos = result.rfind('{')
        last_bracket_pos = result.rfind('[')
        if last_brace_pos > last_bracket_pos:
            # We're likely inside a file object
            # Check if content is closed
            if not result.rstrip().endswith('}'):
                result += '}'
                open_braces -= 1
        result += ']' * open_brackets

    # Close any open braces (artifact object)
    if open_braces > 0:
        result += '}' * open_braces

    logger.debug(f"Repaired incomplete JSON: added closures, final length={len(result)}")
    return result


def _recover_truncated_files_array(raw_json: str) -> Optional[str]:
    """Last-resort recovery: find the last complete file object in the files array.

    When AI output is truncated mid-file (e.g. CSS cut off), this finds the
    last valid '},{ boundary in the files array and discards everything after.
    Then closes the array and root object.
    """
    # Find "files" key and the opening bracket
    files_match = re.search(r'"files"\s*:\s*\[', raw_json)
    if not files_match:
        return None

    files_start = files_match.end()

    # Find complete file objects by looking for },...{ patterns
    # Each file object ends with } and the next starts with {
    # We want the position of the last complete } before the truncation
    last_complete_end = -1
    in_string = False
    escape_next = False
    brace_depth = 0
    i = files_start

    while i < len(raw_json):
        char = raw_json[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if char == '\\' and in_string:
            escape_next = True
            i += 1
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue

        if char == '{':
            brace_depth += 1
        elif char == '}':
            brace_depth -= 1
            if brace_depth == 0:
                # This is a complete file object
                last_complete_end = i + 1
        elif char == ']' and brace_depth == 0:
            # Files array is actually complete — nothing to recover
            return None

        i += 1

    if last_complete_end <= files_start:
        return None

    # Build the repaired JSON: everything up to last complete file + close array + close object
    prefix = raw_json[:files_match.start()]
    files_content = raw_json[files_start:last_complete_end]
    result = prefix + '"files":[' + files_content + ']}'
    return result


def extract_artifact_block(text: str) -> Optional[Dict[str, Any]]:
    """Return a normalized artifact payload if a ```artifact block is present."""
    if not text:
        return None
    match = ARTIFACT_FENCE_PATTERN.search(text)
    if not match:
        # Try fallback for unclosed artifact blocks (DeepSeek and others)
        match = ARTIFACT_UNCLOSED_PATTERN.search(text)
        if match:
            logger.info("Using fallback pattern for unclosed artifact block")
    if not match:
        return None
    raw_content = match.group(1).strip()
    if not raw_content:
        raise ArtifactParseError("Artifact block is empty.")

    # Find the JSON object in the captured content (handles any whitespace/newlines)
    json_start = raw_content.find('{')
    if json_start == -1:
        raise ArtifactParseError("Artifact block must contain a JSON object starting with '{'.")

    # Find matching closing brace by counting braces
    brace_count = 0
    json_end = -1
    in_string = False
    escape_next = False

    for i, char in enumerate(raw_content[json_start:], start=json_start):
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                json_end = i + 1
                break

    if json_end == -1:
        # JSON is incomplete - try to repair it
        logger.info("Artifact JSON incomplete, attempting repair...")
        raw_json = raw_content[json_start:]  # Take everything from start
        raw_json = _repair_incomplete_artifact_json(raw_json, brace_count, in_string)
    else:
        raw_json = raw_content[json_start:json_end]

    # First attempt: parse as-is
    try:
        payload = json.loads(raw_json)
        return normalize_artifact_payload(payload)
    except json.JSONDecodeError as first_exc:
        logger.debug(f"First JSON parse attempt failed at position {first_exc.pos}: {first_exc.msg}")

    # Second attempt: repair common JSON issues
    try:
        repaired_json = _repair_json_string(raw_json)
        payload = json.loads(repaired_json)
        logger.info("Artifact JSON parsed successfully after repair")
        return normalize_artifact_payload(payload)
    except json.JSONDecodeError as exc:
        logger.debug(f"Second JSON parse attempt failed at position {exc.pos}: {exc.msg}")

    # Third attempt: aggressive cleanup for common AI output issues
    try:
        cleaned_json = _aggressive_json_cleanup(raw_json)
        payload = json.loads(cleaned_json)
        logger.info("Artifact JSON parsed successfully after aggressive cleanup")
        return normalize_artifact_payload(payload)
    except json.JSONDecodeError as exc:
        logger.debug(f"Third JSON parse attempt failed at position {exc.pos}: {exc.msg}")

    # Fourth attempt: truncation recovery — find last complete file object
    # When AI output is cut off mid-file content, discard the truncated file
    # and keep everything before it
    try:
        truncated = _recover_truncated_files_array(raw_json)
        if truncated and truncated != raw_json:
            payload = json.loads(truncated)
            logger.info("Artifact JSON parsed after truncation recovery (dropped incomplete file)")
            return normalize_artifact_payload(payload)
    except json.JSONDecodeError:
        pass

    # All attempts failed
    error_pos = 0
    try:
        json.loads(raw_json)
    except json.JSONDecodeError as final_exc:
        error_pos = final_exc.pos
    start = max(0, error_pos - 50)
    end = min(len(raw_json), error_pos + 50)
    context = raw_json[start:end]
    logger.warning(f"Artifact JSON parse error at position {error_pos}")
    logger.warning(f"Context around error: ...{repr(context)}...")
    raise ArtifactParseError("Artifact block must contain valid JSON.")


def extract_fenced_artifact(text: str) -> Optional[dict]:
    """Parse the new fenced artifact format (artifact-meta + artifact-file blocks).
    Returns normalized artifact payload dict, or None if format not found.
    """
    meta_match = FENCED_META_RE.search(text)
    if not meta_match:
        return None

    meta_json = meta_match.group(1).strip()
    try:
        meta = json.loads(meta_json)
    except json.JSONDecodeError:
        logger.warning(f"Fenced artifact: meta JSON parse failed: {meta_json[:100]}")
        return None

    files = []
    for file_match in FENCED_FILE_RE.finditer(text):
        path = file_match.group(1).strip()
        language = file_match.group(2).strip()
        content = file_match.group(3)
        if content.endswith('\n'):
            content = content[:-1]
        files.append({
            'path': path,
            'language': language,
            'content': content,
            'isBinary': False,
            'byteSize': len(content.encode('utf-8')),
        })

    if not files:
        return None

    artifact_type = meta.get('type', 'project')
    primary_lang = meta.get('primaryLanguage', files[0]['language'])

    result = normalize_artifact_payload({
        'title': meta.get('title', 'Artifact'),
        'primaryLanguage': primary_lang,
        'files': files,
    })
    result['type'] = artifact_type
    result['primaryLanguage'] = primary_lang
    return result


def extract_artifact_any_format(text: str) -> Optional[dict]:
    """Try new fenced format first, fall back to old JSON format."""
    result = extract_fenced_artifact(text)
    if result:
        logger.info(f"Fenced artifact parsed: {len(result.get('files', []))} files")
        return result
    return extract_artifact_block(text)


def extract_artifact_patch_block(text: str) -> Optional[Dict[str, Any]]:
    """Return parsed patch data if a ```artifact_patch block is present."""
    if not text:
        return None
    match = ARTIFACT_PATCH_FENCE_PATTERN.search(text)
    if not match:
        return None
    raw_json = match.group(1).strip()
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("artifact_patch block contains invalid JSON")
        return None
    if not isinstance(payload, dict):
        return None
    patches = payload.get('patches')
    if not isinstance(patches, list) or not patches:
        return None
    for patch in patches:
        if not isinstance(patch, dict):
            return None
        if not patch.get('file') or not isinstance(patch.get('file'), str):
            return None
        if not isinstance(patch.get('old_string'), str) or not patch['old_string']:
            return None
        if not isinstance(patch.get('new_string'), str):
            return None
    return payload


def apply_patches(
    current_files: List[Dict[str, Any]],
    patches: List[Dict[str, str]],
) -> tuple:
    """
    Apply old_string/new_string patches to artifact files.

    Returns:
        (patched_files, results) where results is a list of
        {"file": str, "status": "applied"|"failed", "reason": str}
    """
    import copy
    patched_files = copy.deepcopy(current_files)
    results = []

    file_map = {f['path']: f for f in patched_files}

    for patch in patches:
        target_file = patch.get('file', '')
        old_string = patch.get('old_string', '')
        new_string = patch.get('new_string', '')

        if target_file not in file_map:
            available = list(file_map.keys())
            logger.warning(f"🩹 Patch target '{target_file}' not in files: {available}")
            # Fallback: if only one file, use it regardless of name
            if len(file_map) == 1:
                target_file = list(file_map.keys())[0]
                logger.info(f"🩹 Single-file fallback: using '{target_file}'")
            else:
                results.append({'file': target_file, 'status': 'failed', 'reason': f'File "{target_file}" not found'})
                continue

        file_entry = file_map[target_file]
        content = file_entry.get('content', '')

        logger.debug(f"🩹 Patch target='{target_file}', old_string[:80]={repr(old_string[:80])}")
        logger.debug(f"🩹 Content[:200]={repr(content[:200])}")

        count = content.count(old_string)
        if count == 0:
            logger.info(f"🩹 Exact match failed for old_string ({len(old_string)} chars) in {target_file} ({len(content)} chars)")

            # Try with normalized quotes (curly → straight)
            old_norm_q = old_string.translate(_CURLY_TO_STRAIGHT)
            content_norm_q = content.translate(_CURLY_TO_STRAIGHT)
            if old_norm_q != old_string and content_norm_q.count(old_norm_q) == 1:
                logger.info(f"🩹 Quote normalization matched!")
                # Find the actual position in normalized content
                norm_pos = content_norm_q.index(old_norm_q)
                actual_old = content[norm_pos:norm_pos + len(old_norm_q)]
                # Apply replacement preserving original content around the match
                file_entry['content'] = content[:norm_pos] + new_string + content[norm_pos + len(actual_old):]
                results.append({'file': target_file, 'status': 'applied'})
                continue

            # Try with per-line whitespace stripping (indentation mismatch)
            line_strip_matched = False
            old_lines = [l.strip() for l in old_string.splitlines()]
            content_lines = content.splitlines()
            # Sliding window match on stripped lines
            old_stripped = '\n'.join(old_lines)
            for start_idx in range(len(content_lines) - len(old_lines) + 1):
                window = content_lines[start_idx:start_idx + len(old_lines)]
                window_stripped = '\n'.join(l.strip() for l in window)
                if window_stripped == old_stripped:
                    # Found match with different indentation
                    actual_start = sum(len(l) + 1 for l in content_lines[:start_idx])  # +1 for \n
                    actual_end = actual_start + sum(len(l) + 1 for l in window) - 1  # -1 for last \n
                    actual_old = content[actual_start:actual_end]
                    # Preserve the original indentation pattern in new_string
                    # Use the first matched line's indentation as reference
                    if window and old_lines:
                        original_indent = len(window[0]) - len(window[0].lstrip())
                        old_indent = len(old_string.splitlines()[0]) - len(old_string.splitlines()[0].lstrip()) if old_string.splitlines() else 0
                        indent_diff = original_indent - old_indent
                        if indent_diff != 0:
                            # Adjust new_string indentation to match actual file
                            adjusted_lines = []
                            for line in new_string.splitlines():
                                if line.strip() and indent_diff > 0:
                                    adjusted_lines.append(' ' * indent_diff + line)
                                elif line.strip() and indent_diff < 0:
                                    # Remove indentation (but don't go negative)
                                    stripped = line.lstrip()
                                    current_indent = len(line) - len(stripped)
                                    new_indent = max(0, current_indent + indent_diff)
                                    adjusted_lines.append(' ' * new_indent + stripped)
                                else:
                                    adjusted_lines.append(line)
                            new_string = '\n'.join(adjusted_lines)
                    file_entry['content'] = content[:actual_start] + new_string + content[actual_end:]
                    logger.info(f"🩹 Line-strip matching succeeded (indent diff: {indent_diff if 'indent_diff' in dir() else 'N/A'})")
                    results.append({'file': target_file, 'status': 'applied'})
                    line_strip_matched = True
                    break
            if line_strip_matched:
                continue

            # Try with normalized line endings
            old_norm_nl = old_string.replace('\r\n', '\n').replace('\r', '\n')
            content_norm_nl = content.replace('\r\n', '\n').replace('\r', '\n')
            if old_norm_nl != old_string and content_norm_nl.count(old_norm_nl) == 1:
                logger.info(f"🩹 Line-ending normalization matched!")
                file_entry['content'] = content_norm_nl.replace(old_norm_nl, new_string.replace('\r\n', '\n').replace('\r', '\n'), 1)
                results.append({'file': target_file, 'status': 'applied'})
                continue

            # Fuzzy fallback: try whitespace-normalized matching
            import re
            norm_old = re.sub(r'\s+', ' ', old_string.strip())
            norm_content = re.sub(r'\s+', ' ', content)
            if norm_old in norm_content:
                # Find the original position by matching normalized
                # Walk through content finding the span that matches when normalized
                best_start = -1
                for i in range(len(content)):
                    chunk = content[i:i+len(old_string)*2]  # search window
                    if re.sub(r'\s+', ' ', chunk[:len(old_string)+50].strip()).startswith(norm_old):
                        # Found approximate start, now find exact end
                        accum = ''
                        end = i
                        while end < len(content) and re.sub(r'\s+', ' ', accum.strip()) != norm_old:
                            accum += content[end]
                            end += 1
                            if len(accum) > len(old_string) * 3:
                                break
                        if re.sub(r'\s+', ' ', accum.strip()) == norm_old:
                            old_string = accum  # Use the actual text from the file
                            count = 1
                            best_start = i
                            break
                if count == 0:
                    results.append({'file': target_file, 'status': 'failed', 'reason': f'old_string not found in {target_file}'})
                    continue
            else:
                results.append({'file': target_file, 'status': 'failed', 'reason': f'old_string not found in {target_file}'})
                continue
        if count > 1:
            results.append({'file': target_file, 'status': 'failed', 'reason': f'old_string matches multiple locations in {target_file}'})
            continue

        file_entry['content'] = content.replace(old_string, new_string, 1)
        results.append({'file': target_file, 'status': 'applied'})

    return patched_files, results


def normalize_artifact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ArtifactParseError("Artifact payload must be an object.")
    title = str(payload.get("title") or "Artifact").strip()
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ArtifactParseError("Artifact payload must include a non-empty files array.")
    meta = payload.get("meta") or {}
    if meta and not isinstance(meta, dict):
        raise ArtifactParseError("Artifact meta must be an object.")

    normalized_files: List[Dict[str, Any]] = []
    seen_paths: Dict[str, int] = {}  # path -> index in normalized_files
    total_bytes = 0
    for index, file_entry in enumerate(files):
        normalized = normalize_artifact_file(file_entry, index)
        path_key = normalized["path"].lower()

        # Handle duplicates gracefully: keep latest version (overwrite)
        if path_key in seen_paths:
            old_index = seen_paths[path_key]
            old_file = normalized_files[old_index]
            total_bytes -= old_file["byteSize"]  # Remove old file size
            normalized_files[old_index] = normalized  # Overwrite with new
            logger.debug(f"Duplicate path '{normalized['path']}' - keeping latest version")
        else:
            seen_paths[path_key] = len(normalized_files)
            normalized_files.append(normalized)

        total_bytes += normalized["byteSize"]
        if total_bytes > MAX_ARTIFACT_TOTAL_BYTES:
            raise ArtifactParseError("Artifact exceeds maximum allowed size.")

    entrypoint = payload.get("entrypoint")
    if entrypoint:
        entrypoint = normalize_artifact_path(entrypoint)
        if entrypoint.lower() not in seen_paths:
            raise ArtifactParseError("Entrypoint file must exist inside files array.")
    else:
        entrypoint = normalized_files[0]["path"]

    primary_language = normalized_files[0].get("language") or DEFAULT_PRIMARY_LANGUAGE
    summary_source = next((file for file in normalized_files if file["path"] == entrypoint), normalized_files[0])
    summary_text = summary_source.get("content") or ""
    summary = build_summary(summary_text, max_chars=400) or title

    if len(normalized_files) > MAX_ARTIFACT_FILES:
        raise ArtifactParseError("Artifact includes too many files.")

    return {
        "title": title,
        "entrypoint": entrypoint,
        "files": normalized_files,
        "meta": meta,
        "summary": summary,
        "primaryLanguage": primary_language,
        "fileCount": len(normalized_files),
        "totalBytes": total_bytes,
    }


def normalize_artifact_file(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise ArtifactParseError(f"File entry #{index + 1} must be an object.")
    path = normalize_artifact_path(entry.get("path"))
    content = entry.get("content")
    if not isinstance(content, str):
        raise ArtifactParseError(f"File '{path}' must include string content.")

    is_binary_flag = bool(entry.get("isBinary"))

    if is_binary_flag:
        try:
            # For binary content, we expect a base64 encoded string.
            # We decode it to get the actual byte size.
            byte_size = len(base64.b64decode(content))
        except (TypeError, ValueError) as e:
            raise ArtifactParseError(f"File '{path}' is marked as binary but contains invalid base64 content.") from e
    else:
        # For text content, we calculate the size based on UTF-8 encoding.
        byte_size = len(content.encode("utf-8"))

    if byte_size > MAX_ARTIFACT_FILE_BYTES:
        raise ArtifactParseError(f"File '{path}' exceeds the maximum allowed size of {MAX_ARTIFACT_FILE_BYTES / 1000} KB.")

    language = entry.get("language")
    if language:
        language = str(language).strip().lower()
    else:
        language = guess_language_from_path(path)

    mime = entry.get("mime")
    if mime:
        mime = str(mime)

    return {
        "path": path,
        "language": language or DEFAULT_PRIMARY_LANGUAGE,
        "content": content,
        "mime": mime,
        "isBinary": is_binary_flag,
        "byteSize": byte_size,
    }


def normalize_artifact_path(path: Any) -> str:
    if not isinstance(path, str):
        raise ArtifactParseError("File paths must be strings.")
    cleaned = path.replace("\\", "/").strip()
    if not cleaned:
        raise ArtifactParseError("File path cannot be empty.")
    if cleaned.startswith(("/", "\\")):
        raise ArtifactParseError("File paths must be relative.")
    pure = PurePosixPath(cleaned)
    if ".." in pure.parts:
        raise ArtifactParseError("File paths cannot traverse parent directories.")
    normalized = str(pure)
    if normalized in {"", "."}:
        raise ArtifactParseError("File path is invalid.")
    return normalized


def guess_language_from_path(path: str) -> str:
    _, _, ext = path.rpartition(".")
    ext = ext.lower()
    if ext in ("md", "markdown"):
        return "markdown"
    if ext in ("js", "jsx"):
        return "javascript"
    if ext == "ts":
        return "typescript"
    if ext in ("py", "pyw"):
        return "python"
    if ext in ("rb",):
        return "ruby"
    if ext in ("java",):
        return "java"
    if ext in ("cs",):
        return "csharp"
    if ext in ("cpp", "cxx", "hpp", "cc", "h"):
        return "cpp"
    if ext in ("go",):
        return "go"
    if ext in ("rs",):
        return "rust"
    if ext in ("php",):
        return "php"
    if ext in ("swift",):
        return "swift"
    if ext in ("kt", "kts"):
        return "kotlin"
    if ext in ("json",):
        return "json"
    if ext in ("yaml", "yml"):
        return "yaml"
    if ext in ("toml",):
        return "toml"
    if ext in ("html", "htm"):
        return "html"
    if ext in ("css",):
        return "css"
    return DEFAULT_PRIMARY_LANGUAGE
ARTIFACT_CREATE_KEYWORDS = re.compile(
    r"\b(new artifact|new doc|new coding artifact|start (?:a )?(?:doc|coding artifact)|draft.*artifact|put.*in (an )?artifact|create.*document)\b",
    re.IGNORECASE,
)
ARTIFACT_UPDATE_KEYWORDS = re.compile(
    r"\b(update|revise|edit|shorten|expand|rewrite|tweak|fix|correct|modify|change|improve|refactor) (the )?(artifact|doc|document|coding artifact|code|css|style|app|layout|bug|error|version)\b"
    r"|\b(fix|update|correct|improve|change) (this|that|it)\b"
    r"|\b(still|not) (same|working|broken)\b"
    r"|\bupdat\w+ (the |my )?(artifact|code|version)\b"
    r"|\bcreate (the |a )?(fixed|updated|corrected) version\b"
    r"|\bmake it (work|better|look)\b",
    re.IGNORECASE,
)
ARTIFACT_DISABLE_KEYWORDS = re.compile(
    r"\b(no artifacts|dont use artifacts|don't use artifacts|inline only|keep it inline|no document)\b",
    re.IGNORECASE,
)
CODE_ARTIFACT_REQUEST_KEYWORDS = re.compile(
    r"\b(?:coding|code)\s+(?:artifact|workspace|panel|mode|environment|ide)\b"
    r"|(?:artifact|workspace)\s+(?:ide|for\s+code)"
    r"|(?:claude|anthropic)\s+(?:code|coding)\s+artifact",
    re.IGNORECASE,
)
LANGUAGE_SYNONYMS = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "jsx": "javascript",
    "c#": "csharp",
    "cs": "csharp",
    "c++": "cpp",
    "golang": "go",
    "html5": "html",
    "css3": "css"
}
LANGUAGE_KEYWORDS = [
    ("html", re.compile(r"<!doctype html>|<html\b|<body\b|<div\b|<section\b", re.IGNORECASE)),
    ("css", re.compile(r"\.[A-Za-z0-9_-]+\s*\{|@[a-z]+", re.IGNORECASE)),
    ("javascript", re.compile(r"\b(function|console\.log|document\.|window\.|let\s+\w+|const\s+\w+)\b")),
    ("typescript", re.compile(r":\s*(string|number|boolean)\b|interface\s+\w+")),
    ("python", re.compile(r"\b(def|import|print\(|async\s+def|from\s+\w+\s+import)\b")),
    ("java", re.compile(r"\b(class\s+\w+|public\s+static\s+void\s+main)\b")),
    ("csharp", re.compile(r"\bnamespace\b|\busing\s+System\b")),
    ("cpp", re.compile(r"#include\s+<|\bstd::|\bcout\s*<<")),
    ("go", re.compile(r"\bfunc\s+\w+\s*\(|package\s+\w+")),
    ("ruby", re.compile(r"\bdef\s+\w+|puts\s+")),
]


class ArtifactPreference(Enum):
    NONE = "none"
    CREATE = "create"
    UPDATE = "update"
    FORCE_INLINE = "force_inline"


class OutputMode(Enum):
    """Claude-style output mode decision."""
    ARTIFACT = "artifact"      # Complete, reusable deliverables
    CODE_WINDOW = "code_window"  # Small snippets under ~180 lines
    NORMAL_CHAT = "normal_chat"  # Explanations, guidance, reasoning


# ============================================
# ARTIFACT DECISION - AI-DRIVEN
# ============================================
# The AI model decides the output_mode in its META block.
# These constants are kept for backwards compatibility but
# the primary decision comes from the AI's META block.

# Maximum lines for CODE_WINDOW mode (used in fallback only)
CODE_WINDOW_MAX_LINES = 180


@dataclass
class StructuredResponse:
    meta: Dict[str, Any]
    chat: str
    artifact: str
    remainder: str


@dataclass
class ArtifactDecision:
    render_mode: str
    chat_payload: str
    artifact_payload: Optional[Dict[str, Any]]
    contains_code: bool
    desired_action: ArtifactPreference
    error_message: Optional[str] = None  # User-friendly error message if artifact parse failed


class StructuredBlockParser:
    """Simple streaming parser for META/CHAT/ARTIFACT blocks."""

    def __init__(self) -> None:
        self.buffer = ""
        self.meta: Optional[Dict[str, Any]] = None
        self.chat_text: Optional[str] = None
        self.artifact_text: Optional[str] = None
        self.used_json_fallback = False

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self.buffer += chunk
        self._extract_blocks()

    def _extract_blocks(self) -> None:
        if self.meta is None:
            meta_match = META_PATTERN.search(self.buffer)
            if meta_match:
                raw = meta_match.group(1).strip()
                try:
                    self.meta = json.loads(raw)
                except Exception:
                    logger.debug("Failed to parse META block: \n%s", raw)
                    self.meta = {}
            else:
                # Fallback: try to parse plain JSON at start of buffer
                # Pattern: {"kind":"...","title":"...","contains_code":...} followed by text
                # Use \s* (zero or more whitespace) to handle cases where there's no space after JSON
                json_match = re.match(r'^\s*(\{[^}]+\})\s*(.*)', self.buffer, re.DOTALL)
                if json_match:
                    raw_json = json_match.group(1).strip()
                    remaining_text = json_match.group(2).strip()
                    try:
                        parsed = json.loads(raw_json)
                        if 'kind' in parsed or 'title' in parsed:
                            self.meta = parsed
                            self.used_json_fallback = True
                            # Set chat_text to the remaining text after JSON
                            if self.chat_text is None:
                                self.chat_text = remaining_text if remaining_text else ""
                    except Exception:
                        logger.debug("Failed to parse plain JSON fallback: \n%s", raw_json)

        if self.chat_text is None:
            chat_match = CHAT_PATTERN.search(self.buffer)
            if chat_match:
                self.chat_text = chat_match.group(1).strip()

        if self.artifact_text is None:
            artifact_match = ARTIFACT_PATTERN.search(self.buffer)
            if artifact_match:
                self.artifact_text = artifact_match.group(1).strip()

    def result(self) -> StructuredResponse:
        self._extract_blocks()
        meta = self.meta or {}
        chat = self.chat_text or ""
        artifact = self.artifact_text or ""

        # Clean up remainder by removing structural tags
        remainder = self.buffer

        # Remove <think> tags and their content (AI thinking blocks)
        remainder = re.sub(r"<think>[\s\S]*?</think>", "", remainder, flags=re.IGNORECASE)

        # Remove META, CHAT, ARTIFACT tags
        remainder = re.sub(r"</?(META|CHAT|ARTIFACT)>", "", remainder, flags=re.IGNORECASE)

        # Remove plain JSON metadata at start (when AI outputs raw JSON before content)
        # Pattern: {"output_mode":..., "title":..., ...}
        remainder = re.sub(r'^\s*\{[^}]*"(?:output_mode|kind|title)"[^}]*\}\s*', '', remainder.strip(), count=1)

        # If we used JSON fallback, also try to remove it
        if self.used_json_fallback:
            remainder = re.sub(r'^\s*\{[^}]+\}\s*', '', remainder, count=1)

        remainder = remainder.strip()

        if not chat:
            chat = remainder
        return StructuredResponse(meta=meta, chat=chat, artifact=artifact, remainder=remainder)


def parse_structured_response(text: str) -> StructuredResponse:
    parser = StructuredBlockParser()
    parser.feed(text or "")
    return parser.result()


def contains_code_block(text: str) -> bool:
    if not text:
        return False
    if CODE_FENCE_PATTERN.search(text) or "```" in text or "~~~" in text:
        return True

    lines = [line for line in text.splitlines() if line.strip()]
    keyword_hits = sum(1 for line in lines if CODE_KEYWORD_PATTERN.search(line))
    if keyword_hits >= max(1, len(lines) // 4):
        return True

    noisy_lines = 0
    for line in lines:
        if not line.strip():
            continue
        symbols = len(SYMBOL_PATTERN.findall(line))
        if symbols and (symbols / max(1, len(line))) > 0.18:
            noisy_lines += 1
    return noisy_lines >= max(2, len(lines) // 5)


def detect_artifact_preference(prompt: str) -> ArtifactPreference:
    if not prompt:
        return ArtifactPreference.NONE
    if ARTIFACT_DISABLE_KEYWORDS.search(prompt):
        return ArtifactPreference.FORCE_INLINE
    if ARTIFACT_CREATE_KEYWORDS.search(prompt):
        return ArtifactPreference.CREATE
    if ARTIFACT_UPDATE_KEYWORDS.search(prompt):
        return ArtifactPreference.UPDATE
    return ArtifactPreference.NONE


def wants_code_artifact(prompt: str) -> bool:
    if not prompt:
        return False
    return bool(CODE_ARTIFACT_REQUEST_KEYWORDS.search(prompt))


def build_summary(text: str, max_chars: int = 400) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."


def build_title(text: str) -> str:
    if not text:
        return "Artifact"
    heading_match = re.search(r"^#+\s*(.+)$", text, flags=re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip().capitalize()[:120]
    summary = build_summary(text, 60)
    return summary or "Artifact"


def redact_secrets(text: str) -> str:
    mutated = text
    for pattern in SECRET_PATTERNS:
        mutated = pattern.sub("[REDACTED]", mutated)
    return mutated


def sanitize_artifact_markdown(text: str) -> str:
    if not text:
        return ""
    stripped = CODE_FENCE_PATTERN.sub("", text)
    stripped = stripped.replace("```", "").replace("~~~", "")
    return redact_secrets(stripped).strip()


def sanitize_code_artifact(text: str) -> str:
    if not text:
        return ""
    candidate = text.strip()
    fence_match = re.match(r"^\s*```[\w.+-]*\s*\n(.*?)\n?```\s*$", candidate, flags=re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    candidate = candidate.replace("\r\n", "\n")
    return redact_secrets(candidate).strip()


def sanitize_artifact_content(text: str, artifact_type: str) -> str:
    normalized = (artifact_type or "").lower()
    if normalized == "code":
        return sanitize_code_artifact(text)
    return sanitize_artifact_markdown(text)


def normalize_language(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    lowered = str(label).strip().lower()
    if not lowered:
        return None
    return LANGUAGE_SYNONYMS.get(lowered, lowered)


def detect_language(meta: Dict[str, Any], user_prompt: str, raw_artifact: str, cleaned_artifact: str) -> str:
    meta_lang = normalize_language(meta.get("language") or meta.get("lang"))
    if meta_lang:
        return meta_lang

    fence_match = CODE_FENCE_LANG_PATTERN.search(raw_artifact or cleaned_artifact or "")
    if fence_match:
        normalized = normalize_language(fence_match.group(1))
        if normalized:
            return normalized

    combined = " ".join(filter(None, [user_prompt or "", raw_artifact or "", cleaned_artifact or ""]))
    for lang, pattern in LANGUAGE_KEYWORDS:
        if pattern.search(combined):
            return lang

    if "<" in (cleaned_artifact or ""):
        return "html"
    return "code"


class ArtifactDecisionEngine:
    """
    Claude-style artifact decision logic.

    Core principle: If the output is something the user would reasonably want to
    save, reuse, edit, or come back to, it MUST be delivered as an Artifact.

    Decision hierarchy (apply in order):
    1. ARTIFACT (default for deliverables)
    2. CODE_WINDOW (small snippets only)
    3. NORMAL_CHAT (explanations/advice)
    """

    @staticmethod
    def decide_output_mode(
        user_prompt: str,
        response_content: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> OutputMode:
        """
        Determine the appropriate output mode based on AI's META block decision.

        The AI model includes its decision in the META block:
        <META>{"output_mode": "artifact|code_window|normal_chat"}</META>

        This is the most accurate method since the AI understands context.
        """
        meta = meta or {}

        # === PRIMARY: Read AI's decision from META block ===
        # The AI model decides and includes output_mode in META
        ai_decision = meta.get("output_mode") or meta.get("mode") or meta.get("kind")

        if ai_decision:
            ai_decision_lower = str(ai_decision).lower().strip()
            logger.info(f"ArtifactDecisionEngine: AI specified output_mode='{ai_decision_lower}'")

            if ai_decision_lower in ("artifact", "project", "deliverable", "file"):
                logger.debug("ArtifactDecisionEngine: AI decided → ARTIFACT")
                return OutputMode.ARTIFACT

            if ai_decision_lower in ("code_window", "codewindow", "snippet", "code"):
                logger.debug("ArtifactDecisionEngine: AI decided → CODE_WINDOW")
                return OutputMode.CODE_WINDOW

            if ai_decision_lower in ("normal_chat", "chat", "inline", "text", "explanation"):
                logger.debug("ArtifactDecisionEngine: AI decided → NORMAL_CHAT")
                return OutputMode.NORMAL_CHAT

            # If AI specified something but we don't recognize it, log and continue to fallback
            logger.warning(f"ArtifactDecisionEngine: Unrecognized output_mode '{ai_decision_lower}', using fallback")

        # === FALLBACK: Simple heuristics if AI didn't specify ===
        # Only use these if META block is missing or has no output_mode

        # If contains code blocks → CODE_WINDOW (safe default for code)
        if contains_code_block(response_content):
            logger.debug("ArtifactDecisionEngine: Fallback - code detected → CODE_WINDOW")
            return OutputMode.CODE_WINDOW

        # Default to NORMAL_CHAT
        logger.debug("ArtifactDecisionEngine: Fallback → NORMAL_CHAT")
        return OutputMode.NORMAL_CHAT

    @staticmethod
    def should_be_artifact(user_prompt: str, response_content: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        """
        Quick helper: Returns True if the output should be an Artifact.

        Use this for simple yes/no artifact decisions.
        """
        mode = ArtifactDecisionEngine.decide_output_mode(user_prompt, response_content, meta)
        return mode == OutputMode.ARTIFACT


class ArtifactRouter:
    def __init__(self) -> None:
        self.default_type = "code"

    def route(
        self,
        structured: StructuredResponse,
        user_prompt: str,
        provider_flagged_code: bool = False,
    ) -> ArtifactDecision:
        artifact_block = None
        artifact_block_error = None
        artifact_block_user_error = None

        # Check for artifact_patch block first (surgical updates)
        search_text_early = structured.remainder or structured.chat or ""
        patch_block = extract_artifact_patch_block(search_text_early)
        if patch_block:
            logger.info(f"🩹 Found artifact_patch block with {len(patch_block.get('patches', []))} patches")
            chat_text = structured.chat.strip() or structured.remainder or ""
            # Strip the patch fence from chat text
            chat_text = ARTIFACT_PATCH_FENCE_PATTERN.sub("", chat_text).strip()
            return ArtifactDecision(
                render_mode="artifact_patch",
                chat_payload=chat_text or "Applied code patches",
                artifact_payload=patch_block,
                contains_code=True,
                desired_action=ArtifactPreference.UPDATE,
            )

        try:
            search_text = structured.remainder or structured.chat or ""
            logger.info(f"🔍 Searching for artifact block in text (length={len(search_text)}): {search_text[:500]}...")
            artifact_block = extract_artifact_block(search_text)
            if artifact_block:
                logger.info(f"✅ Found artifact block with {len(artifact_block.get('files', []))} files")
            else:
                logger.info("❌ No artifact block found in response")
        except ArtifactParseError as exc:
            artifact_block_error = exc.technical_message
            artifact_block_user_error = exc.user_message
            logger.warning("Artifact block rejected: %s", artifact_block_error)
            if exc.technical_details:
                logger.debug("Artifact validation details: %s", exc.technical_details)

        preference = detect_artifact_preference(user_prompt)
        meta = structured.meta or {}
        chat_text = structured.chat.strip() or structured.remainder or ""
        contains_code = bool(meta.get("contains_code")) or provider_flagged_code
        if not contains_code:
            contains_code = contains_code_block(structured.remainder or "")

        if artifact_block:
            # Strip out the artifact fence block from chat text to get clean summary
            clean_chat_text = ARTIFACT_FENCE_PATTERN.sub("", chat_text).strip()

            # Remove common artifact introduction phrases for cleaner display
            introduction_patterns = [
                r"^here'?s?\s+the\s+(?:updated|complete|revised)?\s*(?:artifact|code|files?).*?[:.]?\s*",
                r"^i'?ve\s+(?:updated|created|made).*?[:.]?\s*",
                r"^the\s+(?:updated|complete)?\s*(?:artifact|code).*?[:.]?\s*",
            ]
            for pattern in introduction_patterns:
                clean_chat_text = re.sub(pattern, "", clean_chat_text, flags=re.IGNORECASE).strip()

            summary = clean_chat_text or artifact_block.get("summary") or artifact_block.get("title")

            artifact_block.setdefault("primaryLanguage", artifact_block.get("language") or DEFAULT_PRIMARY_LANGUAGE)
            artifact_block["language"] = artifact_block.get("primaryLanguage") or DEFAULT_PRIMARY_LANGUAGE
            artifact_block.setdefault("type", "project")
            desired_action = preference if preference == ArtifactPreference.CREATE else ArtifactPreference.UPDATE
            return ArtifactDecision(
                render_mode="artifact",
                chat_payload=summary or artifact_block["title"],
                artifact_payload=artifact_block,
                contains_code=True,
                desired_action=desired_action,
            )

        if preference == ArtifactPreference.FORCE_INLINE:
            contains_code = True

        # Use Claude-style decision engine to determine output mode
        output_mode = ArtifactDecisionEngine.decide_output_mode(
            user_prompt=user_prompt,
            response_content=chat_text,
            meta=meta,
        )

        # FALLBACK: If AI specified artifact mode but parsing failed, try to construct artifact from code blocks
        if output_mode == OutputMode.ARTIFACT and artifact_block is None and artifact_block_error:
            logger.info("🔧 Attempting to construct artifact from code blocks (parsing failed)")
            fallback_artifact = self._construct_artifact_from_code_blocks(
                text=chat_text,
                meta=meta,
            )
            if fallback_artifact:
                logger.info(f"✅ Constructed fallback artifact with {len(fallback_artifact.get('files', []))} files")
                desired_action = preference if preference == ArtifactPreference.CREATE else ArtifactPreference.UPDATE
                return ArtifactDecision(
                    render_mode="artifact",
                    chat_payload=meta.get("title") or fallback_artifact.get("title") or "Artifact",
                    artifact_payload=fallback_artifact,
                    contains_code=True,
                    desired_action=desired_action,
                )
            else:
                logger.warning("❌ Could not construct artifact from code blocks")

        # Map OutputMode to render_mode string
        if output_mode == OutputMode.CODE_WINDOW:
            render_mode = "code_window"
            # Force contains_code=True for code_window mode so frontend renders code blocks
            contains_code = True
        elif output_mode == OutputMode.ARTIFACT:
            # Even without artifact block, engine determined this should be artifact
            render_mode = "artifact_suggested"
        else:
            render_mode = "inline"

        return ArtifactDecision(
            render_mode=render_mode,
            chat_payload=chat_text,
            artifact_payload=None,
            contains_code=contains_code,
            desired_action=ArtifactPreference.NONE,
            error_message=artifact_block_user_error,
        )

    def _construct_artifact_from_code_blocks(
        self,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Construct an artifact payload from code blocks in the text.
        This is used as a fallback when the AI intended to create an artifact
        but the JSON parsing failed.
        """
        if not text:
            return None

        meta = meta or {}
        files = []

        # Find all code blocks with language annotations
        code_block_pattern = re.compile(r"```(\w+)?\s*\n(.*?)```", re.DOTALL)
        matches = code_block_pattern.findall(text)

        if not matches:
            return None

        # Try to identify file names from content or use defaults
        for i, (lang, content) in enumerate(matches):
            lang = (lang or "").strip().lower()
            content = content.strip()

            if not content:
                continue

            # Try to infer filename from content or use default
            path = self._infer_filename(content, lang, i)
            language = normalize_language(lang) or guess_language_from_path(path)

            files.append({
                "path": path,
                "language": language,
                "content": content,
                "mime": None,
                "isBinary": False,
                "byteSize": len(content.encode("utf-8")),
            })

        if not files:
            return None

        # Determine title from meta or first file
        title = meta.get("title") or "Generated Artifact"
        primary_language = files[0].get("language") or DEFAULT_PRIMARY_LANGUAGE
        entrypoint = files[0]["path"]

        # Calculate total bytes
        total_bytes = sum(f["byteSize"] for f in files)

        return {
            "title": title,
            "entrypoint": entrypoint,
            "files": files,
            "meta": {},
            "summary": title,
            "primaryLanguage": primary_language,
            "fileCount": len(files),
            "totalBytes": total_bytes,
            "type": "project",
        }

    def _infer_filename(self, content: str, lang: str, index: int) -> str:
        """Infer a filename from content and language."""
        # Map language to common extensions
        lang_extensions = {
            "javascript": "js",
            "typescript": "ts",
            "python": "py",
            "html": "html",
            "css": "css",
            "json": "json",
            "jsx": "jsx",
            "tsx": "tsx",
            "java": "java",
            "go": "go",
            "rust": "rs",
            "ruby": "rb",
            "php": "php",
            "swift": "swift",
            "kotlin": "kt",
            "yaml": "yaml",
            "markdown": "md",
            "sql": "sql",
            "shell": "sh",
            "bash": "sh",
        }

        ext = lang_extensions.get(lang, lang or "txt")

        # Try to detect specific file types from content
        if "<html" in content.lower() or "<!doctype html" in content.lower():
            return "index.html"
        if "def " in content and "import " in content:
            return f"main.py" if index == 0 else f"module{index}.py"
        if "function " in content or "const " in content or "let " in content:
            if lang in ("jsx", "tsx"):
                return f"App.{ext}" if index == 0 else f"Component{index}.{ext}"
            return f"script.{ext}" if index == 0 else f"file{index}.{ext}"
        if lang == "css":
            return "style.css" if index == 0 else f"style{index}.css"
        if lang == "json":
            if '"name"' in content and '"version"' in content:
                return "package.json"
            return f"data{index}.json" if index > 0 else "data.json"

        # Default naming
        if index == 0:
            return f"main.{ext}"
        return f"file{index}.{ext}"


class ArtifactStore:
    def __init__(self, db_client: gc_firestore.Client) -> None:
        self.db = db_client

    def _session_ref(self, user_id: str, session_id: str) -> gc_firestore.DocumentReference:
        return self.db.collection("users").document(user_id).collection("chat_sessions").document(session_id)

    def _artifact_collection(self, user_id: str, session_id: str) -> gc_firestore.CollectionReference:
        return self._session_ref(user_id, session_id).collection("artifacts")

    def list_artifacts(self, user_id: str, session_id: str) -> Dict[str, Any]:
        query = self._artifact_collection(user_id, session_id).order_by("updatedAt", direction=gc_firestore.Query.DESCENDING)
        artifacts: List[Dict[str, Any]] = []
        for doc in query.stream():
            artifacts.append(self._serialize_artifact_doc(doc.id, doc.to_dict() or {}))
        return {"artifacts": artifacts}

    def get_artifact(self, user_id: str, session_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        artifact_ref = self._artifact_collection(user_id, session_id).document(artifact_id)
        snapshot = artifact_ref.get()
        if not snapshot.exists:
            return None
        return self._build_artifact_response(artifact_ref, snapshot)

    def get_artifact_version(
        self,
        user_id: str,
        session_id: str,
        artifact_id: str,
        version_id: str,
        include_files: bool = True,
    ) -> Optional[Dict[str, Any]]:
        artifact_ref = self._artifact_collection(user_id, session_id).document(artifact_id)
        if include_files:
            return self._load_version_with_files(artifact_ref, version_id)
        version_ref = artifact_ref.collection("versions").document(version_id)
        snapshot = version_ref.get()
        if not snapshot.exists:
            return None
        return self._serialize_version_doc(snapshot.id, snapshot.to_dict() or {})

    def download_version_zip(
        self,
        user_id: str,
        session_id: str,
        artifact_id: str,
        version_id: str,
    ) -> Optional[bytes]:
        version = self.get_artifact_version(user_id, session_id, artifact_id, version_id, include_files=True)
        if not version:
            return None
        from io import BytesIO
        import zipfile

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in version.get("files", []):
                archive.writestr(file["path"], file.get("content", ""))
        buffer.seek(0)
        return buffer.read()

    def set_active_version(
        self,
        user_id: str,
        session_id: str,
        artifact_id: str,
        version_id: str,
    ) -> Optional[Dict[str, Any]]:
        artifact_ref = self._artifact_collection(user_id, session_id).document(artifact_id)
        version_ref = artifact_ref.collection("versions").document(version_id)

        logger = logging.getLogger(__name__)
        try:
            logger.info(f"set_active_version: Attempting to set artifact {artifact_id} version {version_id} as active")

            # Get references to all artifacts in this session (outside transaction)
            try:
                artifact_refs = [doc.reference for doc in self._artifact_collection(user_id, session_id).stream()]
                logger.info(f"set_active_version: Found {len(artifact_refs)} artifacts in session")
            except Exception as e:
                logger.error(f"Error fetching artifact references: {e}", exc_info=True)
                raise

            # Use Google Cloud Firestore transaction (not Firebase Admin)
            transaction = self.db.transaction()

            @gc_firestore.transactional
            def _txn(transaction):
                try:
                    artifact_snapshot = artifact_ref.get(transaction=transaction)
                    if not artifact_snapshot.exists:
                        logger.warning(f"Artifact {artifact_id} does not exist")
                        return None

                    version_snapshot = version_ref.get(transaction=transaction)
                    if not version_snapshot.exists:
                        logger.warning(f"Version {version_id} does not exist for artifact {artifact_id}")
                        return None

                    logger.info(f"Artifact and version exist, deactivating other artifacts...")

                    # Deactivate all other artifacts in this session
                    for other_ref in artifact_refs:
                        if other_ref.id != artifact_id:
                            try:
                                # Fetch the artifact inside the transaction
                                other_snapshot = other_ref.get(transaction=transaction)
                                if other_snapshot.exists:
                                    other_data = other_snapshot.to_dict()
                                    # Only update if it's currently active
                                    if other_data and other_data.get("isActive", False):
                                        transaction.update(other_ref, {"isActive": False})
                                        logger.info(f"Deactivated artifact {other_ref.id}")
                            except Exception as e:
                                logger.error(f"Error deactivating artifact {other_ref.id}: {e}")
                                # Continue with others even if one fails

                    # Activate this artifact with the specified version
                    transaction.update(artifact_ref, {
                        "activeVersionId": version_id,
                        "isActive": True,
                        "updatedAt": gc_firestore.SERVER_TIMESTAMP
                    })
                    logger.info(f"Successfully activated artifact {artifact_id}")
                    return True
                except Exception as e:
                    logger.error(f"Error in transaction: {e}", exc_info=True)
                    raise

            updated = _txn(transaction)
            if not updated:
                logger.warning(f"Transaction returned None for artifact {artifact_id}")
                return None

            result = self._build_artifact_response(artifact_ref, artifact_ref.get())
            logger.info(f"Successfully built artifact response for {artifact_id}")
            return result
        except Exception as e:
            logger.error(f"Error in set_active_version: {e}", exc_info=True)
            raise

    def persist_decision(
        self,
        user_id: str,
        session_id: str,
        decision: ArtifactDecision,
        source_message_id: Optional[str],
        created_by: str = "assistant",
    ) -> Optional[Dict[str, Any]]:
        logger = logging.getLogger(__name__)
        if not decision.artifact_payload:
            logger.warning("persist_decision: No artifact payload provided")
            return None
        payload = decision.artifact_payload
        preference = decision.desired_action
        logger.info(f"persist_decision: preference={preference}, session={session_id}")

        active_snapshot = self._get_active_snapshot(user_id, session_id)
        if active_snapshot:
            active_data = active_snapshot.to_dict() or {}
            logger.info(f"persist_decision: Found active artifact {active_snapshot.id}, isActive={active_data.get('isActive')}, versionCount={active_data.get('versionCount')}")
        else:
            logger.info("persist_decision: No active artifact found in session")

        logger.info(f"persist_decision: Decision - preference={preference}, CREATE={ArtifactPreference.CREATE}, will_create_new={preference == ArtifactPreference.CREATE or not active_snapshot}")

        if preference == ArtifactPreference.CREATE or not active_snapshot:
            logger.info(f"persist_decision: Creating NEW artifact (preference={preference}, has_active={bool(active_snapshot)})")
            return self._create_artifact(user_id, session_id, payload, source_message_id, created_by)

        logger.info(f"persist_decision: Appending version to artifact {active_snapshot.id}")
        return self._append_version(active_snapshot, payload, source_message_id)

    def _get_active_snapshot(self, user_id: str, session_id: str) -> Optional[gc_firestore.DocumentSnapshot]:
        # First try to find an artifact explicitly marked as active
        query = (
            self._artifact_collection(user_id, session_id)
            .where("isActive", "==", True)
            .limit(1)
        )
        docs = list(query.stream())
        if docs:
            return docs[0]

        # If no artifact is explicitly active, use the most recently updated one
        # This handles the case where isActive flags aren't set
        query = (
            self._artifact_collection(user_id, session_id)
            .order_by("updatedAt", direction=gc_firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        return docs[0] if docs else None

    def _create_artifact(
        self,
        user_id: str,
        session_id: str,
        payload: Dict[str, Any],
        source_message_id: Optional[str],
        created_by: str,
    ) -> Optional[Dict[str, Any]]:
        artifact_id = payload.get("artifactId") or f"art_{uuid.uuid4().hex}"
        artifact_ref = self._artifact_collection(user_id, session_id).document(artifact_id)
        version_id = f"ver_{uuid.uuid4().hex}"

        artifact_doc = {
            "artifactId": artifact_id,
            "conversationId": session_id,
            "title": payload.get("title") or "Artifact",
            "entrypointPath": payload.get("entrypoint"),
            "type": payload.get("type") or "project",
            "meta": payload.get("meta") or {},
            "createdAt": gc_firestore.SERVER_TIMESTAMP,
            "updatedAt": gc_firestore.SERVER_TIMESTAMP,
            "versionCount": 1,
            "activeVersionId": version_id,
            "createdBy": created_by,
            "isActive": True,
        }
        version_doc = self._version_document(payload, version_id, 1, source_message_id)

        # Get references to artifacts that might be active (outside transaction)
        other_artifact_refs = [doc.reference for doc in self._artifact_collection(user_id, session_id).where("isActive", "==", True).stream()]

        # Use Google Cloud Firestore transaction (not Firebase Admin)
        transaction = self.db.transaction()

        @gc_firestore.transactional
        def _txn(transaction):
            # Deactivate other artifacts
            for other_ref in other_artifact_refs:
                if other_ref.id != artifact_id:
                    transaction.update(other_ref, {"isActive": False})

            # Create the new artifact
            transaction.set(artifact_ref, artifact_doc)
            transaction.set(artifact_ref.collection("versions").document(version_id), version_doc)

        _txn(transaction)
        self._write_version_files(artifact_ref.collection("versions").document(version_id), payload.get("files", []))
        return self._build_artifact_response(artifact_ref, artifact_ref.get())

    def _append_version(
        self,
        snapshot: gc_firestore.DocumentSnapshot,
        payload: Dict[str, Any],
        source_message_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not snapshot.exists:
            return None
        artifact_ref = snapshot.reference
        version_id = f"ver_{uuid.uuid4().hex}"

        # Use Google Cloud Firestore transaction (not Firebase Admin)
        transaction = self.db.transaction()

        @gc_firestore.transactional
        def _txn(transaction):
            current = artifact_ref.get(transaction=transaction)
            data = current.to_dict() or {}
            next_version_number = int(data.get("versionCount") or 0) + 1
            updates = {
                "title": payload.get("title") or data.get("title") or "Artifact",
                "entrypointPath": payload.get("entrypoint") or data.get("entrypointPath"),
                "type": payload.get("type") or data.get("type") or "project",
                "meta": payload.get("meta") or data.get("meta") or {},
                "updatedAt": gc_firestore.SERVER_TIMESTAMP,
                "versionCount": next_version_number,
                "activeVersionId": version_id,
                "isActive": True,
            }
            version_doc = self._version_document(payload, version_id, next_version_number, source_message_id)
            transaction.update(artifact_ref, updates)
            transaction.set(artifact_ref.collection("versions").document(version_id), version_doc)

        _txn(transaction)
        version_ref = artifact_ref.collection("versions").document(version_id)
        self._write_version_files(version_ref, payload.get("files", []))
        return self._build_artifact_response(artifact_ref, artifact_ref.get())

    def _write_version_files(self, version_ref: gc_firestore.DocumentReference, files: List[Dict[str, Any]]) -> None:
        if not files:
            return
        batch = self.db.batch()
        for file in files:
            file_ref = version_ref.collection("files").document(uuid.uuid4().hex)
            batch.set(file_ref, {
                "path": file.get("path"),
                "language": file.get("language"),
                "content": file.get("content"),
                "mime": file.get("mime"),
                "isBinary": bool(file.get("isBinary")),
                "byteSize": file.get("byteSize"),
            })
        batch.commit()

    def _build_artifact_response(
        self,
        artifact_ref: gc_firestore.DocumentReference,
        snapshot: gc_firestore.DocumentSnapshot,
    ) -> Dict[str, Any]:
        data = snapshot.to_dict() or {}
        serialized = self._serialize_artifact_doc(snapshot.id, data)
        versions = self._load_versions(artifact_ref)
        active_version_id = data.get("activeVersionId") or (versions[0]["versionId"] if versions else None)
        active_version = None
        if active_version_id:
            active_version = self._load_version_with_files(artifact_ref, active_version_id)
        serialized["versions"] = versions
        serialized["activeVersionId"] = active_version_id
        serialized["activeVersion"] = active_version
        return serialized

    def _load_versions(self, artifact_ref: gc_firestore.DocumentReference) -> List[Dict[str, Any]]:
        versions: List[Dict[str, Any]] = []
        for version_doc in artifact_ref.collection("versions").order_by("versionNumber", direction=gc_firestore.Query.DESCENDING).stream():
            versions.append(self._serialize_version_doc(version_doc.id, version_doc.to_dict() or {}))
        return versions

    def _load_version_with_files(
        self,
        artifact_ref: gc_firestore.DocumentReference,
        version_id: str,
    ) -> Optional[Dict[str, Any]]:
        version_ref = artifact_ref.collection("versions").document(version_id)
        snapshot = version_ref.get()
        if not snapshot.exists:
            return None
        version = self._serialize_version_doc(snapshot.id, snapshot.to_dict() or {})
        version["files"] = self._load_files(version_ref)
        return version

    def _load_files(self, version_ref: gc_firestore.DocumentReference) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        for file_doc in version_ref.collection("files").order_by("path").stream():
            content = file_doc.to_dict() or {}
            files.append({
                "id": file_doc.id,
                "path": content.get("path"),
                "language": content.get("language"),
                "content": content.get("content"),
                "mime": content.get("mime"),
                "isBinary": bool(content.get("isBinary")),
                "byteSize": content.get("byteSize"),
            })
        return files

    def _version_document(
        self,
        payload: Dict[str, Any],
        version_id: str,
        version_number: int,
        source_message_id: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "versionId": version_id,
            "versionNumber": version_number,
            "entrypointPath": payload.get("entrypoint"),
            "primaryLanguage": payload.get("primaryLanguage"),
            "summary": payload.get("summary"),
            "fileCount": payload.get("fileCount", len(payload.get("files", []))),
            "totalBytes": payload.get("totalBytes"),
            "sourceMessageId": source_message_id,
            "createdAt": gc_firestore.SERVER_TIMESTAMP,
        }

    @staticmethod
    def _serialize_timestamp(value: Any) -> Optional[str]:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    def _serialize_artifact_doc(self, artifact_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "artifactId": artifact_id,
            "conversationId": data.get("conversationId"),
            "title": data.get("title", "Artifact"),
            "type": data.get("type") or "project",
            "entrypointPath": data.get("entrypointPath"),
            "meta": data.get("meta") or {},
            "createdAt": self._serialize_timestamp(data.get("createdAt")),
            "updatedAt": self._serialize_timestamp(data.get("updatedAt")),
            "versionCount": data.get("versionCount", 0),
            "activeVersionId": data.get("activeVersionId"),
            "createdBy": data.get("createdBy") or "assistant",
            "isActive": bool(data.get("isActive", True)),
        }

    def _serialize_version_doc(self, version_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "versionId": version_id,
            "versionNumber": data.get("versionNumber"),
            "entrypointPath": data.get("entrypointPath"),
            "primaryLanguage": data.get("primaryLanguage") or DEFAULT_PRIMARY_LANGUAGE,
            "summary": data.get("summary"),
            "fileCount": data.get("fileCount", 0),
            "totalBytes": data.get("totalBytes", 0),
            "createdAt": self._serialize_timestamp(data.get("createdAt")),
            "sourceMessageId": data.get("sourceMessageId"),
        }


__all__ = [
    # Core classes
    "ArtifactDecision",
    "ArtifactDecisionEngine",
    "ArtifactPreference",
    "ArtifactParseError",
    "ArtifactRouter",
    "ArtifactStore",
    "OutputMode",
    "StructuredBlockParser",
    # Functions
    "contains_code_block",
    "detect_language",
    "extract_artifact_block",
    "normalize_artifact_payload",
    "parse_structured_response",
    "sanitize_artifact_content",
    "sanitize_artifact_markdown",
    "sanitize_code_artifact",
    # Constants
    "CODE_WINDOW_MAX_LINES",
    # Helpers migrated from views.py
    "ARTIFACT_UPDATE_PATTERNS",
    "detect_artifact_update_intent",
    "load_current_artifact_context",
    "delete_local_attachments",
]


# ==================== Artifact update detection (migrated from views.py) ====================

ARTIFACT_UPDATE_PATTERNS = [
    # Direct action on artifact/code
    r"\b(?:fix|update|correct|change|modify|edit|revise|improve|refactor)\s+(?:the\s+)?(?:artifact|code|bug|error|issue|css|style|layout|design|app)\b",
    # "fix this/that/it"
    r"\b(?:fix|correct|resolve|update)\s+(?:this|that|it|the)\b",
    # "update the artifact/code"
    r"\bupdat\w*\s+(?:the\s+)?(?:artifact|code|version|my)\b",
    # Polite requests
    r"\b(?:can you|please|could you)\s+(?:fix|update|correct|change|modify)\b",
    # Technical fixes
    r"\bfix\s+(?:the\s+)?(?:syntax|babel|runtime|compile|css|style|render)\s*(?:error|issue)?\b",
    # "make it work/better"
    r"\bmake\s+(?:it|the\s+\w+)\s+(?:work|better|responsive|look)\b",
    # Keywords that imply artifact modification
    r"\b(?:still\s+(?:not|same|broken)|doesn.t\s+work|not\s+working|same\s+version)\b",
    # "add/remove X to/from the code/app"
    r"\b(?:add|remove|delete)\s+.{0,20}(?:to|from|in)\s+(?:the\s+)?(?:code|app|artifact|component)\b",
    # References to updating version
    r"\b(?:new|next|updated|fixed)\s+version\b",
]


def detect_artifact_update_intent(user_prompt: str) -> bool:
    """Detect if user is asking to fix/update an artifact."""
    if not user_prompt:
        return False
    prompt_lower = user_prompt.lower().strip()

    update_keywords = {'fix', 'update', 'change', 'modify', 'edit', 'correct', 'improve', 'refactor', 'revise'}
    words = set(prompt_lower.split())
    if not words & update_keywords:
        for word in words:
            for kw in update_keywords:
                if len(word) >= 3 and len(kw) >= 3:
                    if word[:3] == kw[:3] or word[-3:] == kw[-3:]:
                        break
            else:
                continue
            break
        else:
            return False

    for pattern in ARTIFACT_UPDATE_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True

    if len(prompt_lower.split()) <= 10 and words & update_keywords:
        return True

    return False


def load_current_artifact_context(user_id: str, session_id: str) -> typing.Optional[str]:
    """Load the current active artifact's code and format it as context."""
    try:
        store = ArtifactStore.__new__(ArtifactStore)
        # Use lazy import to avoid circular dependency
        from app.config import db as _db
        store.db = _db
        artifacts_data = store.list_artifacts(user_id, session_id)
        artifacts = artifacts_data.get('artifacts', [])

        active_artifact = None
        for art in artifacts:
            if art.get('isActive'):
                active_artifact = art
                break
        if not active_artifact and artifacts:
            active_artifact = artifacts[0]
        if not active_artifact:
            return None

        artifact_id = active_artifact.get('artifactId')
        if not artifact_id:
            return None

        full_artifact = store.get_artifact(user_id, session_id, artifact_id)
        if not full_artifact:
            return None

        active_version = full_artifact.get('activeVersion')
        if not active_version:
            return None

        files = active_version.get('files', [])
        if not files:
            return None

        context_parts = [
            "CURRENT ARTIFACT (for reference when fixing/updating)",
            f"Title: {full_artifact.get('title', 'Untitled')}",
            f"Type: {full_artifact.get('type', 'project')}",
            f"Files: {len(files)}",
        ]

        for file in files:
            path = file.get('path', 'unknown')
            language = file.get('language', 'text')
            content = file.get('content', '')
            context_parts.append(f"\n{path} ({language}):")
            context_parts.append(f"```{language}")
            context_parts.append(content)
            context_parts.append("```")

        context_parts.append("\nWhen updating this artifact, regenerate ALL files with changes applied.")
        context_parts.append("Output a complete ```artifact block with the fixed/updated code.\n")

        return "\n".join(context_parts)

    except Exception as e:
        _logger = logging.getLogger(__name__)
        _logger.warning(f"Failed to load artifact context: {e}")
        return None


def delete_local_attachments(messages: typing.List[typing.Dict[str, typing.Any]]) -> None:
    """Remove any local files referenced in message attachments."""
    if not messages:
        return

    from app.config import Config as _Config
    base_upload_path = os.path.abspath(_Config.UPLOAD_FOLDER)
    _logger = logging.getLogger(__name__)
    for message in messages:
        for field_name in ('attachments', 'user_attachments'):
            attachments = message.get(field_name, [])
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                local_path = attachment.get('local_path')
                if not local_path:
                    continue
                try:
                    if os.path.isabs(local_path):
                        abs_path = os.path.abspath(local_path)
                    else:
                        abs_path = os.path.abspath(os.path.join(_Config.UPLOAD_FOLDER, local_path))
                    if not abs_path.startswith(base_upload_path):
                        _logger.warning(f"Skipping attachment outside upload dir: {abs_path}")
                        continue
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                        _logger.info(f"Deleted attachment file {abs_path}")
                except Exception as e:
                    _logger.warning(f"Failed to delete attachment {local_path}: {e}")
