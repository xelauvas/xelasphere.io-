"""StreamNormalizer — model-agnostic stream normalization layer.

Sits between raw model output and StreamingActionBuffer.
Converts any model's streaming format into clean line-by-line chunks
that the action buffer can parse reliably.

Key behaviors:
- Buffers partial lines until complete
- Detects complete ACTION header fields even without trailing \\n
- Flushes remaining buffer on stream end
- Never modifies content — only adds missing \\n where needed
- Zero model-specific logic in the core path
"""

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# Known ACTION header field names — used for partial-line detection
# Excludes ACTION itself (that's the first line, parsed differently)
_HEADER_FIELD_PATTERN = re.compile(
    r'^(query|search_type|reason|render_hint|embed_video|'
    r'description|source):\s*.+$'
)

# Per-model config — only controls flush_on_complete_field aggressiveness
NORMALIZER_CONFIG = {
    'deepseek': {'flush_on_complete_field': True},
    'llama':    {'flush_on_complete_field': True},
    'gpt-4':    {'flush_on_complete_field': False},
    'openai':   {'flush_on_complete_field': False},
    'kimi':     {'flush_on_complete_field': True},
    'qwen':     {'flush_on_complete_field': True},
    'small':    {'flush_on_complete_field': True},
    'default':  {'flush_on_complete_field': True},  # safe default
}


def get_model_family(model: str) -> str:
    """Detect model family from model ID for normalizer config."""
    m = model.lower()
    if 'deepseek' in m:
        return 'deepseek'
    if 'llama' in m:
        return 'llama'
    if 'qwen' in m or 'qwq' in m:
        return 'qwen'
    if 'kimi' in m:
        return 'kimi'
    if 'gpt' in m:
        return 'openai'
    if 'gemma' in m or 'dolphin' in m:
        return 'small'
    return 'default'


class StreamNormalizer:
    """Normalizes raw model stream chunks into clean line-by-line output.

    Usage:
        normalizer = StreamNormalizer(model_family='deepseek')

        for raw_chunk in model_stream:
            for clean_chunk in normalizer.feed(raw_chunk):
                result = action_buffer.feed(clean_chunk)
                # ... handle result

        # On stream end — flush remaining buffer
        for clean_chunk in normalizer.flush():
            action_buffer.feed(clean_chunk)
    """

    def __init__(self, model_family: str = 'default'):
        config = NORMALIZER_CONFIG.get(model_family, NORMALIZER_CONFIG['default'])
        self._flush_on_complete_field = config['flush_on_complete_field']
        self._line_buffer = ''
        self._model_family = model_family

    def feed(self, raw_chunk: str) -> List[str]:
        """Process a raw chunk. Returns list of normalized chunks ready for buffer.

        Each returned chunk is either:
        - A complete line ending with \\n
        - A blank line (\\n alone) — header terminator
        - Plain text content (not a header field)
        """
        if not raw_chunk:
            return []

        self._line_buffer += raw_chunk
        output: List[str] = []

        # Extract all complete lines (those ending with \n)
        while '\n' in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split('\n', 1)
            output.append(line + '\n')

        # Remaining buffer stays accumulated — it will be flushed by
        # flush() when the stream ends, or completed by the next \n.
        # We don't try to detect complete fields during feed() because
        # token-by-token streaming makes it impossible to know if the
        # value is complete (e.g. "query: weather" vs "query: weather in London").

        return output

    def flush(self) -> List[str]:
        """Call when stream ends. Flushes any remaining buffer content.

        Adds a trailing \\n and blank line terminator so the action buffer
        can resolve any in-progress header block.
        """
        output: List[str] = []
        if self._line_buffer.strip():
            logger.debug(
                "[Normalizer:%s] end-of-stream flush: '%s'",
                self._model_family, self._line_buffer.strip()[:60]
            )
            output.append(self._line_buffer + '\n')
            self._line_buffer = ''
        # Always emit blank line on flush — terminates any open header block
        output.append('\n')
        return output

    def _is_complete_header_field(self, text: str) -> bool:
        """Return True if text looks like a complete ACTION header field.

        Matches: key: value
        Where key is a known ACTION field name.
        Does NOT match partial lines like 'rea' or 'query: wea'
        — we need the value to be present and non-empty.
        """
        return bool(_HEADER_FIELD_PATTERN.match(text.strip()))


# ---------------------------------------------------------------------------
# Paragraph break normalization (for SSE output, not action parsing)
# ---------------------------------------------------------------------------

# Pre-compiled regex: single \n between text lines → \n\n
# Skips list items (- * + digit.), headers (#), blockquotes (>), code fences (```)
# Lookahead checks what the NEXT line starts with (after the \n)
_PARA_BREAK_RE = re.compile(
    r'(?<!\n)\n(?!\n|[-*+#>]|[0-9]+\.|[ \t]*```)'
)


def normalize_paragraph_breaks(text: str) -> str:
    """Convert single \\n between prose paragraphs to \\n\\n.

    Models like DeepSeek, Llama, Groq send single \\n where markdown
    expects \\n\\n for paragraph separation. This normalizes the stream
    so markdown-it produces <p> tags (with CSS margins) instead of <br>
    (no margin).

    Preserves single \\n for:
    - List items (- * + 1.)
    - Headers (#)
    - Blockquotes (>)
    - Code fences (```)
    - Already-doubled \\n\\n
    """
    return _PARA_BREAK_RE.sub('\n\n', text)


class FenceAwareParagraphNormalizer:
    """Pass-through: the stream now carries the model's text exactly as written.

    This used to turn every single newline outside code fences into a blank line so
    that prose got paragraph spacing. It also put a blank line between table rows,
    between the lines of a list item and between the lines of a poem, so the live
    answer rendered differently from the same message after a reload (the stored
    text was never rewritten). The client renderer now treats a single newline as a
    line break, so nothing needs rewriting on the way out.

    The feed()/flush() interface stays so the stream route does not change shape.
    """

    def feed(self, text: str) -> str:
        return text or ''

    def flush(self) -> str:
        return ''
