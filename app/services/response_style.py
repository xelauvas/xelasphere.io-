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


import re

# Compiled patterns. Order matters — first match wins.
# See docs/superpowers/specs/2026-05-01-content-aware-formatting-organism-design.md §3.

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|sure|cool|nice|"
    r"got it|right|y(?:eah|es|up|a)|n(?:o|ah|ope))\b",
    re.IGNORECASE,
)

_COMPARISON_RE = re.compile(
    r"\b(vs\.?|versus|compared? to|differences? between|"
    r"which is better|which should i (?:use|pick|choose)|"
    r"should i (?:use|pick|choose) \w+ or \w+)\b",
    re.IGNORECASE,
)

_CREATIVE_RE = re.compile(
    r"^(write (?:me )?(?:a |an )?(?:short )?(?:story|poem|haiku|sonnet|"
    r"scene|essay|monologue|dialogue|fable)|"
    r"tell me a story|"
    r"describe (?:a |the )?(?:scene|setting)|"
    r"continue the story|"
    r"roleplay\b)",
    re.IGNORECASE,
)

_TECHNICAL_VERB_RE = re.compile(
    r"\b(write|implement|fix|debug|refactor|optimi[sz]e|build|create)\b.*?"
    r"\b(function|method|class|component|module|script|test|loop|"
    r"query|api|endpoint|migration|hook|reducer|service|controller|"
    r"middleware|regex|bug|code)\b",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(r"```")
_IN_LANGUAGE_RE = re.compile(
    r"\bin (?:python|javascript|typescript|js|ts|rust|go|golang|"
    r"java|c\+\+|c#|csharp|sql|bash|zsh|ruby|php|swift|kotlin)\b",
    re.IGNORECASE,
)

_TIPS_RE = re.compile(
    r"^(give me |list |what are )?(?:tips|ways|best practices|hacks|"
    r"ideas|examples|reasons|techniques|strategies) (?:on|for|to)\b",
    re.IGNORECASE,
)
_HOW_TO_RE = re.compile(
    r"^how (?:do i|to|can i|should i|do you)\b",
    re.IGNORECASE,
)
_TOP_N_RE = re.compile(r"^(?:top|best) \d+\b", re.IGNORECASE)

_EDITORIAL_RE = re.compile(
    r"^(explain|why does|why is|how does|what is the (?:meaning|history|"
    r"theory|reason|significance|origin)|tell me about)\b",
    re.IGNORECASE,
)


def classify_by_rules(message: str) -> ResponseStyle | None:
    """Return a ResponseStyle if a regex matches; None to defer to the LLM path.

    Order: chat (short/greeting) → comparison → creative → technical → modern_blog → editorial.
    See spec §3.
    """
    if message is None:
        return ResponseStyle.CHAT

    msg = message.strip()

    # Rule 1: very short or empty
    if len(msg) < 4:
        return ResponseStyle.CHAT

    # Rule 2: greeting / affirmation prefix
    if _GREETING_RE.match(msg):
        return ResponseStyle.CHAT

    # Rule 3: comparison
    if _COMPARISON_RE.search(msg):
        return ResponseStyle.COMPARISON

    # Rule 4: creative writing
    if _CREATIVE_RE.match(msg):
        return ResponseStyle.CREATIVE

    # Rule 5: technical (verb-noun, code fence, "in <language>")
    if _TECHNICAL_VERB_RE.search(msg) or _CODE_FENCE_RE.search(msg) or _IN_LANGUAGE_RE.search(msg):
        return ResponseStyle.TECHNICAL

    # Rule 6: tips / how-to / top-N → modern_blog
    if _TIPS_RE.search(msg) or _HOW_TO_RE.match(msg) or _TOP_N_RE.match(msg):
        return ResponseStyle.MODERN_BLOG

    # Rule 7: longform-explainer (≥ 50 chars to avoid catching "explain X" tersely)
    if _EDITORIAL_RE.match(msg) and len(msg) >= 50:
        return ResponseStyle.EDITORIAL

    return None
