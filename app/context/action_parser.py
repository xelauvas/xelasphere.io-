# app/context/action_parser.py
"""ActionParser — replaces META tag parsing with ACTION: first-line protocol.

Every model response should begin with:
    ACTION: chat
    ACTION: create_artifact
    ACTION: patch_artifact

Falls back to fence-detection heuristics when the ACTION line is missing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Match "ACTION: xyz", "-- ACTION: xyz --", "**ACTION: xyz**" etc.
# Group 2 captures params (e.g. "bitcoin" in "ACTION: show_chart bitcoin")
_ACTION_LINE_RE = re.compile(r"^\s*[-*]*\s*ACTION:\s*(\w+)(?:\s+([^-*]+?))?\s*[-*]*\s*$", re.IGNORECASE)
_ARTIFACT_FENCE_RE = re.compile(r"```artifact\s*([\s\S]*?)\s*```", re.IGNORECASE)
_ARTIFACT_UNCLOSED_RE = re.compile(r"```artifact\s*([\s\S]+)", re.IGNORECASE)
_PATCH_FENCE_RE = re.compile(r"```artifact_patch\s*([\s\S]*?)\s*```", re.IGNORECASE)

_VALID_ACTIONS = {"chat", "create_artifact", "patch_artifact", "show_chart", "generate_image", "web_search"}

# Actions that use multi-line header mode (key: value pairs after the ACTION line)
_MULTILINE_HEADER_ACTIONS = {"generate_image", "web_search"}

# Regex for key-value header lines: "description: some text" or "source: explicit"
_HEADER_KV_RE = re.compile(r"^\s*(\w+)\s*:\s*(.+?)\s*$")


@dataclass
class ParsedAction:
    """Result of parsing a model response."""

    action: str  # 'chat' | 'create_artifact' | 'patch_artifact' | 'show_chart' | 'generate_image' | 'web_search'
    chat_text: str  # Everything except artifact/patch fence
    artifact_payload: Optional[Dict[str, Any]]  # Parsed artifact JSON (if create)
    patch_payload: Optional[Dict[str, Any]]  # Parsed patch JSON (if patch)
    chart_payload: Optional[Dict[str, Any]] = None  # Parsed chart info (if show_chart)
    image_payload: Optional[Dict[str, str]] = None  # {description, source} (if generate_image)
    search_payload: Optional[Dict[str, str]] = None  # {query, search_type, reason, embed_video} (if web_search)
    raw_response: str = ""  # Full unmodified response
    used_fallback: bool = False  # True if ACTION line was missing


class ActionParser:
    """Parse ACTION: protocol from model responses."""

    @staticmethod
    def parse(response_text: str) -> ParsedAction:
        """Parse a complete model response into a ParsedAction."""
        if not response_text:
            return ParsedAction(
                action="chat",
                chat_text="",
                artifact_payload=None,
                patch_payload=None,
                raw_response="",
            )

        raw = response_text
        action = None
        used_fallback = False

        # Step 1: Try to extract ACTION line
        lines = response_text.split("\n", 1)
        first_line = lines[0].strip()
        match = _ACTION_LINE_RE.match(first_line)

        action_params = None
        header_fields = {}
        if match:
            keyword = match.group(1).lower()
            if keyword in _VALID_ACTIONS:
                action = keyword
                action_params = match.group(2).strip() if match.group(2) else None
                response_text = lines[1] if len(lines) > 1 else ""
                response_text = response_text.lstrip("\n")

                # Multi-line header parsing for generate_image
                if action in _MULTILINE_HEADER_ACTIONS:
                    remaining_lines = response_text.split("\n")
                    header_end = 0
                    for i, line in enumerate(remaining_lines):
                        kv = _HEADER_KV_RE.match(line)
                        if kv:
                            header_fields[kv.group(1).lower()] = kv.group(2)
                            header_end = i + 1
                        else:
                            # Blank line or non-kv line terminates header
                            break
                    response_text = "\n".join(remaining_lines[header_end:]).lstrip("\n")

        # Step 2: If no ACTION line, use fence-detection fallback
        if action is None:
            used_fallback = True
            if _PATCH_FENCE_RE.search(response_text):
                action = "patch_artifact"
            elif _ARTIFACT_FENCE_RE.search(response_text) or _ARTIFACT_UNCLOSED_RE.search(response_text):
                action = "create_artifact"
            else:
                action = "chat"

            if used_fallback and action != "chat":
                logger.info(
                    f"ActionParser fallback triggered: action={action}, "
                    f"first_100={raw[:100]!r}"
                )

        # Step 3: Extract payloads based on action
        artifact_payload = None
        patch_payload = None
        chart_payload = None
        image_payload = None
        search_payload = None
        chat_text = response_text

        if action == "create_artifact":
            artifact_payload, chat_text = _extract_artifact(response_text)
        elif action == "patch_artifact":
            patch_payload, chat_text = _extract_patch(response_text)
        elif action == "show_chart":
            chart_payload = _extract_chart(action_params)
            if not chart_payload:
                action = "chat"
        elif action == "generate_image":
            description = header_fields.get("description", "").strip()
            source = header_fields.get("source", "explicit").strip().lower()
            if source not in ("explicit", "inferred"):
                source = "explicit"
            if description:
                image_payload = {"description": description, "source": source}
            else:
                logger.warning("generate_image ACTION missing description, falling back to chat")
                action = "chat"
        elif action == "web_search":
            query = header_fields.get("query", "").strip()
            if query:
                search_payload = {
                    "query": query,
                    "search_type": header_fields.get("search_type", "general").strip(),
                    "reason": header_fields.get("reason", "").strip(),
                    "embed_video": header_fields.get("embed_video", "").strip() or None,
                    "render_hint": header_fields.get("render_hint", "text_only").strip(),
                }
            else:
                logger.warning("web_search ACTION missing query, falling back to chat")
                action = "chat"

        return ParsedAction(
            action=action,
            chat_text=chat_text.strip(),
            artifact_payload=artifact_payload,
            patch_payload=patch_payload,
            chart_payload=chart_payload,
            image_payload=image_payload,
            search_payload=search_payload,
            raw_response=raw,
            used_fallback=used_fallback,
        )


def _extract_artifact(text: str):
    """Extract artifact from fenced blocks. Tries new format first, falls back to old JSON.
    Returns (payload, chat_text)."""
    from app.artifacts import extract_fenced_artifact, extract_artifact_block
    from app.artifacts import FENCED_META_RE, FENCED_FILE_RE, ARTIFACT_FENCE_PATTERN

    # Try new fenced format first (artifact-meta + artifact-file blocks)
    payload = extract_fenced_artifact(text)
    if payload:
        # Strip all artifact blocks from chat text
        chat_text = FENCED_META_RE.sub("", text)
        chat_text = FENCED_FILE_RE.sub("", chat_text).strip()
        return payload, chat_text

    # Fall back to old JSON format
    try:
        payload = extract_artifact_block(text)
    except Exception as exc:
        logger.warning(f"ActionParser: artifact extraction failed: {exc}")
        payload = None

    chat_text = ARTIFACT_FENCE_PATTERN.sub("", text).strip()
    if not chat_text or chat_text == text:
        chat_text = _ARTIFACT_UNCLOSED_RE.sub("", text).strip()

    return payload, chat_text


def _extract_chart(params: Optional[str]) -> Optional[Dict[str, Any]]:
    """Extract chart asset info from ACTION: show_chart params.

    Expected formats:
        ACTION: show_chart bitcoin
        ACTION: show_chart AAPL stock
        ACTION: show_chart solana crypto
    """
    if not params:
        return None

    from app.asset_config import CRYPTO_SYMBOLS, STOCK_SYMBOLS, COMPANY_TO_SYMBOL

    parts = params.lower().strip().split()
    asset_name = parts[0]
    asset_hint = parts[1] if len(parts) > 1 else None

    # Explicit stock hint
    if asset_hint == 'stock' or asset_name.upper() in STOCK_SYMBOLS:
        return {'symbol': asset_name.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}

    # Company name
    if asset_name in COMPANY_TO_SYMBOL:
        return {'symbol': COMPANY_TO_SYMBOL[asset_name].upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}

    # Crypto — check known symbols first
    if asset_name in CRYPTO_SYMBOLS:
        return {'coin': CRYPTO_SYMBOLS[asset_name], 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

    # Strip trailing 's' for plurals
    singular = asset_name.rstrip('s') if asset_name.endswith('s') and len(asset_name) > 3 else asset_name
    if singular in CRYPTO_SYMBOLS:
        return {'coin': CRYPTO_SYMBOLS[singular], 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

    # Dynamic CoinGecko lookup
    try:
        from app.crypto_api import get_coin_id
        coin_id = get_coin_id(asset_name) or (get_coin_id(singular) if singular != asset_name else None)
        if coin_id:
            return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}
    except Exception as e:
        logger.warning(f"Chart asset lookup failed: {e}")

    logger.warning(f"Could not resolve chart asset: {params}")
    return None


def _extract_patch(text: str):
    """Extract patch JSON from fenced block. Returns (payload, chat_text)."""
    from app.artifacts import extract_artifact_patch_block

    payload = extract_artifact_patch_block(text)
    chat_text = _PATCH_FENCE_RE.sub("", text).strip()
    return payload, chat_text


class StreamingActionBuffer:
    """Buffer streaming chunks to parse the ACTION line.

    Streaming chunks don't respect line boundaries. This buffer
    accumulates chunks until the first newline, then parses the
    ACTION line.

    For multi-line header actions (generate_image), continues buffering
    key-value pairs until a blank line or timeout (3s).
    """

    HEADER_TIMEOUT_MS = 3000  # 3 seconds

    def __init__(self) -> None:
        self.buffer = ""
        self.action: Optional[str] = None
        self.action_params: Optional[str] = None  # e.g. "bitcoin" for show_chart
        self.header_fields: Dict[str, str] = {}  # multi-line header key-value pairs
        self.remainder: str = ""
        self._resolved = False
        self._in_header_mode = False
        self._header_start_time: float = 0.0
        self._header_lines: list = []

    def feed(self, chunk: str) -> Optional[str]:
        """Feed a chunk. Returns action string when resolved, else None."""
        if self._resolved:
            return self.action

        self.buffer += chunk

        # Multi-line header mode: parsing key-value pairs after ACTION: generate_image
        if self._in_header_mode:
            elapsed = time.time() - self._header_start_time
            if elapsed > self.HEADER_TIMEOUT_MS / 1000:
                # Timeout: flush buffer as plain text, abort multiline header path
                logger.warning(f"{self.action} header timeout — falling back to chat")
                self.action = "chat"
                self.remainder = self.buffer
                self._resolved = True
                return self.action

            # Process complete lines in the buffer
            while "\n" in self.buffer:
                newline_idx = self.buffer.find("\n")
                line = self.buffer[:newline_idx]
                self.buffer = self.buffer[newline_idx + 1:]

                stripped = line.strip()
                if not stripped:
                    # Blank line terminates header
                    self.remainder = self.buffer
                    self._resolved = True
                    return self.action

                kv = _HEADER_KV_RE.match(stripped)
                if kv:
                    self.header_fields[kv.group(1).lower()] = kv.group(2)
                else:
                    # Non-kv line terminates header; this line is chat text
                    self.remainder = line + "\n" + self.buffer
                    self._resolved = True
                    return self.action

            return None  # Need more data

        # Standard single-line ACTION parsing
        newline_idx = self.buffer.find("\n")
        if newline_idx == -1:
            return None

        first_line = self.buffer[:newline_idx].strip()
        rest = self.buffer[newline_idx + 1:]

        match = _ACTION_LINE_RE.match(first_line)
        if match:
            keyword = match.group(1).lower()
            if keyword in _VALID_ACTIONS:
                self.action = keyword
                self.action_params = match.group(2).strip() if match.group(2) else None

                # Enter multi-line header mode for generate_image
                if keyword in _MULTILINE_HEADER_ACTIONS:
                    self._in_header_mode = True
                    self._header_start_time = time.time()
                    self.buffer = rest  # Continue parsing remaining buffer
                    # Process any complete lines already in the buffer
                    return self.feed("")
                else:
                    self.remainder = rest
                    self._resolved = True
                    return self.action

        self.action = "chat"
        self.remainder = self.buffer
        self._resolved = True
        logger.info(
            f"StreamingActionBuffer: no ACTION line found, defaulting to chat. "
            f"First line: {first_line[:100]!r}"
        )
        return self.action
