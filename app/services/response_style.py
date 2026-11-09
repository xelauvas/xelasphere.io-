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

        MODERN_BLOG (readable, lightly structured prose) is the safest
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


_HOUSE_RULES = """\
Formatting rules that always apply:
- No emoji and no decorative symbols, in headings, lists or prose. Use one only
  if the user explicitly asks for emoji.
- Answer in the language the user wrote in.
- Start with the answer itself. No preamble ("Great question", "Sure!"), and no
  closing offer or summary of what you just said.
- Plain Markdown only. Leave a blank line between paragraphs, before and after
  lists, headings, tables and code blocks.
- Formatting is the exception: use a heading, list or table only when the
  content has that shape. Never bold whole sentences; bold at most a few key terms.
- Headings are short and in sentence case ("Why it matters", not "Why It Matters").
"""

STYLE_CHARTERS: dict[ResponseStyle, str] = {

    ResponseStyle.EDITORIAL: """\
Write this answer as a clear explanation in prose.

- Open with one or two sentences that answer the question directly.
- Then explain in short paragraphs of 2-4 sentences, each making one point.
- Use ## headings only if the answer has three or more distinct parts; otherwise none.
- Use a list only for things that really are a list (steps, options, items).
- End when the explanation is complete. No recap paragraph.

Voice: calm, precise, warm. Like a knowledgeable colleague explaining something
they understand well.

Length: 150-450 words. Shorter is fine when the question is simple.

""" + _HOUSE_RULES,

    ResponseStyle.MODERN_BLOG: """\
Write this answer so it is easy to read and act on.

- Open with one or two plain sentences that give the main point.
- If the answer is a set of tips, steps or options, give them as one list:
  each item starts with a short bold label, then a sentence or two of substance.
  Numbered when order matters, bullets otherwise. 3-6 items; pick the ones that
  matter instead of listing everything.
- If the answer is not list-shaped, write short paragraphs instead.
- Use ## headings only when there are several separate topics. Most answers need none.
- End with one plain sentence on where to start or what matters most, if useful.

Voice: direct, friendly, concrete. No hype.

Length: 120-350 words.

""" + _HOUSE_RULES,

    ResponseStyle.TECHNICAL: """\
Write this answer the way a senior engineer answers a colleague.

- When the question is about code, lead with the code block (with a language tag).
  Put at most one sentence of context before it.
- After the code, explain only what is not obvious from reading it.
- Use ### headings only for multi-part answers (Setup, Usage, Edge cases).
- `inline code` for names, flags, paths, commands and types.
- State caveats in plain sentences ("Note: the trailing call is dropped.").

Voice: precise and terse. No filler.

Length: as short as possible while complete. A five-line code block can be a
complete answer.

""" + _HOUSE_RULES,

    ResponseStyle.CHAT: """\
Write this as a short conversational reply.

- Plain prose, no headings, no lists.
- One to three sentences is typical; match the length and tone of the user's message.
- No bold unless a single term truly needs it.

Voice: natural, brief, warm.

""" + _HOUSE_RULES,

    ResponseStyle.COMPARISON: """\
Write this as a comparison that ends in a recommendation.

- First sentence: the recommendation and the main reason, in plain prose.
- Then a Markdown table comparing the options across 3-5 attributes, one row per
  attribute, with short cells.
- Close with one or two sentences on when the other option is the better choice.

Voice: fair and decisive. The user asked because they want a recommendation.

Length: 100-300 words.

""" + _HOUSE_RULES,

    ResponseStyle.CREATIVE: """\
This is a creative writing request: fiction, poetry, scene work or roleplay.

- A ## title at the top only if the piece needs one.
- Flowing prose paragraphs, or line breaks as the form requires.
- *Italics* for thoughts or emphasis. Dialogue on its own lines.
- A horizontal rule (---) for a scene break, used sparingly.
- No emoji. No lists, tables or callouts.

Voice: literary. Considered word choice. Show, don't tell.

Length: as long as the piece needs.

Answer in the language the user wrote in.
""",
}


import logging

logger = logging.getLogger(__name__)


def _classify_via_llm(message: str) -> ResponseStyle | None:
    """LLM fallback path. Defers to chat_engine.classify_user_intent's
    extended return dict (added in Task 5). Returns None on any error
    so the caller can fall through to the default.

    Imported lazily inside the function to avoid a circular import:
    chat_engine imports from this module too.
    """
    try:
        from app.services import chat_engine
        result = chat_engine.classify_user_intent(message)
        style_str = result.get("style") if isinstance(result, dict) else None
        if style_str:
            try:
                return ResponseStyle(style_str)
            except ValueError:
                logger.warning("LLM returned unknown style %r", style_str)
                return None
        return None
    except Exception as exc:
        logger.warning("classify_via_llm failed: %s", exc)
        return None


def classify_response_style(message: str) -> ResponseStyle:
    """Pick a ResponseStyle for this user message.

    Order:
      1. regex fast path (free, ~0ms, covers the obvious 80%)
      2. LLM fallback (cached, ~3s timeout via existing classify_user_intent)
      3. ResponseStyle.default()  (always returns a value — never None)
    """
    rule_match = classify_by_rules(message)
    if rule_match is not None:
        return rule_match

    llm_match = _classify_via_llm(message)
    if llm_match is not None:
        return llm_match

    return ResponseStyle.default()
