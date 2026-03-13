"""
Session Summary - State-Based Rolling Summary
==============================================
Maintains a structured "project state" summary instead of narrative history.

Format:
    GOAL: Current objective
    DECISIONS: Key choices made
    CONSTRAINTS: Limitations, requirements
    DEFINITIONS: "X means Y" clarifications
    OPEN: Unresolved questions, next steps

Uses diff-based updates: old summary + new messages → add only new facts.
This prevents summary drift and keeps context stable.

Usage:
    from app.session_summary import update_session_summary

    new_summary = update_session_summary(
        existing_summary="GOAL: Build auth system...",
        user_prompt="Use JWT instead of sessions",
        assistant_response="Switching to JWT tokens...",
    )
"""

import json
import logging
import re
import requests
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Summary Prompts
# =============================================================================

DIFF_SUMMARY_PROMPT = """You maintain a structured project state summary. Given the existing state and new conversation, extract ONLY new important facts.

EXISTING STATE:
{existing_summary}

NEW CONVERSATION:
User: {user_prompt}
Assistant: {assistant_response}

RULES:
1. Output ONLY new facts not already in the existing state
2. Use this exact format for new facts (omit sections with no new info):
   GOAL: [only if goal changed/clarified]
   DECISIONS: [new decisions made]
   CONSTRAINTS: [new limitations discovered]
   DEFINITIONS: [new "X means Y" clarifications]
   OPEN: [new questions or next steps]

3. Be extremely concise - bullet points, no prose
4. Skip chit-chat, greetings, acknowledgments
5. If nothing important to add, respond with: NO_UPDATE

Example output:
DECISIONS: - Using PostgreSQL over MongoDB
CONSTRAINTS: - Must support offline mode
OPEN: - Need to decide on auth method"""

INIT_SUMMARY_PROMPT = """Extract a structured project state from this conversation. Be extremely concise.

User: {user_prompt}
Assistant: {assistant_response}

Output format (omit empty sections):
GOAL: [main objective in <10 words]
DECISIONS: [bullet points of choices made]
CONSTRAINTS: [bullet points of limitations]
DEFINITIONS: [bullet points of "X means Y"]
OPEN: [bullet points of unresolved items]

Skip greetings, chit-chat, repeated info. If nothing substantial, respond with: NO_UPDATE"""


# =============================================================================
# Summary State Parser
# =============================================================================

SECTION_ORDER = ["GOAL", "DECISIONS", "CONSTRAINTS", "DEFINITIONS", "OPEN"]


def parse_summary(summary: str) -> dict:
    """Parse structured summary into sections."""
    sections = {s: [] for s in SECTION_ORDER}
    current_section = None

    for line in summary.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Check for section header
        for section in SECTION_ORDER:
            if line.startswith(f"{section}:"):
                current_section = section
                # Get content after header on same line
                content = line[len(section) + 1:].strip()
                if content and content != "-":
                    sections[section].append(content)
                break
        else:
            # Not a header, add to current section
            if current_section and line.startswith("- "):
                sections[current_section].append(line[2:])
            elif current_section and line:
                sections[current_section].append(line)

    return sections


def merge_summaries(existing: str, diff: str) -> str:
    """Merge existing summary with diff updates."""
    if not diff or diff.strip() == "NO_UPDATE":
        return existing

    existing_sections = parse_summary(existing) if existing else {s: [] for s in SECTION_ORDER}
    diff_sections = parse_summary(diff)

    # Merge sections
    for section in SECTION_ORDER:
        if section == "GOAL":
            # Goal: newer replaces older
            if diff_sections[section]:
                existing_sections[section] = diff_sections[section]
        elif section == "OPEN":
            # Open items: replace entirely (old questions may be resolved)
            if diff_sections[section]:
                existing_sections[section] = diff_sections[section]
        else:
            # Other sections: append new items, dedupe
            existing_set = {item.lower().strip() for item in existing_sections[section]}
            for item in diff_sections[section]:
                if item.lower().strip() not in existing_set:
                    existing_sections[section].append(item)

    return format_summary(existing_sections)


def format_summary(sections: dict) -> str:
    """Format sections back into summary string."""
    lines = []

    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue

        if section == "GOAL":
            # Goal is single line
            lines.append(f"GOAL: {items[0]}")
        else:
            lines.append(f"{section}:")
            for item in items[:10]:  # Cap at 10 items per section
                lines.append(f"- {item}")

    return "\n".join(lines)


# =============================================================================
# LLM Integration
# =============================================================================

def _call_summary_llm(prompt: str) -> Optional[str]:
    """Call LLM for summary generation."""
    try:
        api_key = Config.GROQ_API_KEY
        if not api_key:
            return None

        response = requests.post(
            Config.API_URL,
            json={
                "messages": [{"role": "user", "content": prompt}],
                "model": "llama-3.1-8b-instant",
                "max_tokens": 400,
                "temperature": 0.2
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=5
        )

        if response.status_code != 200:
            logger.warning(f"Summary LLM returned {response.status_code}")
            return None

        content = response.json()["choices"][0]["message"]["content"].strip()
        return content

    except Exception as e:
        logger.warning(f"Summary LLM call failed: {e}")
        return None


# =============================================================================
# Main Interface
# =============================================================================

def update_session_summary(
    existing_summary: str,
    user_prompt: str,
    assistant_response: str,
    max_tokens: int = 600
) -> str:
    """
    Update session summary with new conversation turn.

    Uses diff-based updates: extracts only new facts and merges them.
    Falls back to simple append if LLM fails.

    Args:
        existing_summary: Current structured summary
        user_prompt: Latest user message
        assistant_response: Latest assistant response
        max_tokens: Max tokens for summary (default 600)

    Returns:
        Updated structured summary
    """
    # Skip trivial messages
    if len(user_prompt) < 10 or len(assistant_response) < 20:
        return existing_summary

    # Skip chit-chat patterns
    chit_chat = ["hello", "hi ", "thanks", "thank you", "bye", "ok", "okay", "sure"]
    if any(user_prompt.lower().strip().startswith(p) for p in chit_chat):
        return existing_summary

    try:
        if existing_summary and len(existing_summary) > 50:
            # Diff update: extract only new facts
            prompt = DIFF_SUMMARY_PROMPT.format(
                existing_summary=existing_summary[:1500],
                user_prompt=user_prompt[:500],
                assistant_response=assistant_response[:800]
            )
            diff = _call_summary_llm(prompt)

            if diff and diff.strip() != "NO_UPDATE":
                new_summary = merge_summaries(existing_summary, diff)
            else:
                new_summary = existing_summary
        else:
            # Initial summary
            prompt = INIT_SUMMARY_PROMPT.format(
                user_prompt=user_prompt[:500],
                assistant_response=assistant_response[:800]
            )
            result = _call_summary_llm(prompt)

            if result and result.strip() != "NO_UPDATE":
                new_summary = result
            else:
                new_summary = existing_summary or ""

        # Trim if too long
        if len(new_summary) > max_tokens * 4:  # Rough char estimate
            sections = parse_summary(new_summary)
            # Trim older items from each section
            for section in ["DECISIONS", "CONSTRAINTS", "DEFINITIONS"]:
                if len(sections[section]) > 5:
                    sections[section] = sections[section][-5:]
            new_summary = format_summary(sections)

        return new_summary

    except Exception as e:
        logger.warning(f"Summary update failed: {e}")
        return existing_summary


def get_summary_for_prompt(summary: str) -> str:
    """
    Format summary for injection into AI prompt.
    Returns empty string if summary is empty/trivial.
    """
    if not summary or len(summary) < 20:
        return ""

    return f"Session state:\n{summary}\n"
