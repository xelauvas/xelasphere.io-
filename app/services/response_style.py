"""Response-style classifier — picks one of six visual styles per turn.

See docs/superpowers/specs/2026-05-01-content-aware-formatting-organism-design.md
for the design rationale and style table.
"""

from enum import Enum


class ResponseStyle(str, Enum):
    """The six visual styles the organism can produce.

    str-Enum so values JSON-serialise as plain strings (e.g. for the
    stream-meta SSE event payload).
    """

    EDITORIAL = "editorial"
    MODERN_BLOG = "modern_blog"
    TECHNICAL = "technical"
    CHAT = "chat"
    COMPARISON = "comparison"
    CREATIVE = "creative"

    @classmethod
    def default(cls) -> "ResponseStyle":
        """Fallback when classification fails or returns nothing usable.

        MODERN_BLOG is closest to ChatGPT's house style and the safest
        fall-through for "I don't know what kind of question this is."
        """
        return cls.MODERN_BLOG
