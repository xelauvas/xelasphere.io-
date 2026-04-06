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
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ACTION_LINE_RE = re.compile(r"^\s*ACTION:\s*(\w+)", re.IGNORECASE)
_ARTIFACT_FENCE_RE = re.compile(r"```artifact\s*([\s\S]*?)\s*```", re.IGNORECASE)
_ARTIFACT_UNCLOSED_RE = re.compile(r"```artifact\s*([\s\S]+)", re.IGNORECASE)
_PATCH_FENCE_RE = re.compile(r"```artifact_patch\s*([\s\S]*?)\s*```", re.IGNORECASE)

_VALID_ACTIONS = {"chat", "create_artifact", "patch_artifact"}


@dataclass
class ParsedAction:
    """Result of parsing a model response."""

    action: str  # 'chat' | 'create_artifact' | 'patch_artifact'
    chat_text: str  # Everything except artifact/patch fence
    artifact_payload: Optional[Dict[str, Any]]  # Parsed artifact JSON (if create)
    patch_payload: Optional[Dict[str, Any]]  # Parsed patch JSON (if patch)
    raw_response: str  # Full unmodified response
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

        if match:
            keyword = match.group(1).lower()
            if keyword in _VALID_ACTIONS:
                action = keyword
                response_text = lines[1] if len(lines) > 1 else ""
                response_text = response_text.lstrip("\n")

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
        chat_text = response_text

        if action == "create_artifact":
            artifact_payload, chat_text = _extract_artifact(response_text)
        elif action == "patch_artifact":
            patch_payload, chat_text = _extract_patch(response_text)

        return ParsedAction(
            action=action,
            chat_text=chat_text.strip(),
            artifact_payload=artifact_payload,
            patch_payload=patch_payload,
            raw_response=raw,
            used_fallback=used_fallback,
        )


def _extract_artifact(text: str):
    """Extract artifact JSON from fenced block. Returns (payload, chat_text)."""
    from app.artifacts import extract_artifact_block, ARTIFACT_FENCE_PATTERN

    try:
        payload = extract_artifact_block(text)
    except Exception as exc:
        logger.warning(f"ActionParser: artifact extraction failed: {exc}")
        payload = None

    chat_text = ARTIFACT_FENCE_PATTERN.sub("", text).strip()
    if not chat_text or chat_text == text:
        chat_text = _ARTIFACT_UNCLOSED_RE.sub("", text).strip()

    return payload, chat_text


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
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.action: Optional[str] = None
        self.remainder: str = ""
        self._resolved = False

    def feed(self, chunk: str) -> Optional[str]:
        """Feed a chunk. Returns action string when resolved, else None."""
        if self._resolved:
            return self.action

        self.buffer += chunk

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
