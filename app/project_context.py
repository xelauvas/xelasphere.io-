"""Pure helper — builds the Project context block prepended to the system prompt
when a chat session belongs to a Project. Spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

from typing import List, Dict

INJECTION_BUDGET_CHARS = 80_000


def build_project_context_block(*, instructions: str, files: List[Dict[str, str]]) -> str:
    """Assemble the addendum that gets concatenated to the system prompt.

    Files are included whole oldest-first; if one would push past the budget,
    it is included partially and the rest are dropped. A trailing notice is
    appended if any truncation occurred.
    """
    parts: List[str] = []
    instructions = (instructions or '').strip()
    if instructions:
        parts.append('--- Project instructions ---')
        parts.append(instructions)

    truncated = False
    files_parts: List[str] = []
    budget = INJECTION_BUDGET_CHARS
    for f in files:
        text = (f.get('extracted_text') or '').strip()
        if not text:
            continue
        header = f"\n{f.get('filename', 'file')}:"
        budget_for_body = budget - len(header)
        if budget_for_body <= 0:
            truncated = True
            break
        if len(text) <= budget_for_body:
            files_parts.append(header)
            files_parts.append(text)
            budget -= len(header) + len(text)
        else:
            files_parts.append(header)
            files_parts.append(text[:budget_for_body])
            truncated = True
            budget = 0
            break

    if files_parts:
        parts.append('--- Project knowledge ---')
        parts.extend(files_parts)
        if truncated:
            parts.append('\n(Some project files were truncated to fit in context.)')

    if not parts:
        return ''
    return '\n\n'.join(parts).strip() + '\n'
