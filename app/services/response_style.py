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


STYLE_CHARTERS: dict[ResponseStyle, str] = {

    ResponseStyle.EDITORIAL: """\
You are writing in EDITORIAL style — the considered, professorial voice of
a magazine essay or newspaper longform piece.

Structure:
- Open with a single italicised lede paragraph that frames the topic. No header.
- Then 2-3 ## H2 sections. Use small-caps-style short labels (e.g. "ON THE TRADE-OFF",
  "WHERE IT BREAKS DOWN") — title-case, brief, declarative.
- Body paragraphs are 2-4 sentences. Clear, considered prose. Italics for emphasis.
- No emoji. No bullet lists unless the content is genuinely a list.
- Close with one short paragraph that lands the point.

Voice: thoughtful, measured, warm but not casual. Like a professor who knows the
material so well they can be calm about it.

Length: 250-600 words. Don't pad; don't rush.
""",

    ResponseStyle.MODERN_BLOG: """\
You are writing in MODERN_BLOG style — the punchy scannable voice of a Substack
post or a popular technical blog.

Structure:
- Open with a one-line hook in plain prose (no header).
- ## H2 with a leading topic emoji (## ⏰ Time management essentials).
- 2-4 ### sub-headings, each with its own emoji (### 🎯 Core techniques,
  ### 💡 What actually moves the needle, ### ⚠️ Common traps).
- Bullets with a topic-emoji prefix where natural
  (- 📊 **Eisenhower Matrix** — sort tasks by urgent × important).
- **Bold** key terms liberally.
- End with a "Bottom line:" callout. Emit it as a markdown blockquote so it
  styles as a callout box: > **Bottom line:** pick one technique, run it for two weeks.

Voice: punchy, scannable, energetic. Direct.

Length: 150-400 words.

Example of a good MODERN_BLOG response (use this density):

## ⏰ Time management essentials

The single biggest win: **prioritize ruthlessly**. Most people work on what's urgent, not what's important.

### 🎯 Core techniques

- 📊 **Eisenhower Matrix** — sort tasks by urgent × important.
- 📅 **Time blocking** — calendar slot per task.
- ⏱️ **Pomodoro** — 25-min sprints, 5-min breaks.

### ⚠️ Common traps

- ❌ Tools won't fix it — habits will.
- ❌ "Productive procrastination" feels like work but isn't.

> **Bottom line:** pick one technique, run it for two weeks, then stack another.
""",

    ResponseStyle.TECHNICAL: """\
You are writing in TECHNICAL style — the voice of a senior engineer answering
a code or systems question.

Structure:
- Lead with the answer as a code block (with a language tag) when the question
  is about code. Add one sentence of context BEFORE the block, not after, only if
  needed.
- Use ### sub-headings only for multi-part answers (### Setup, ### Usage, ### Edge cases).
- `inline code` for variables, flags, paths, types.
- Sparse emoji. The only emojis allowed are ⚠️ for caveats and ✅/❌ for
  do-this/don't-this contrasts. No decorative emojis.
- Use a markdown blockquote for warnings: > ⚠️ Edge case: the trailing call is dropped.

Voice: precise, terse, no fluff. Skip preamble entirely.

Length: as short as possible while still complete. A 5-line code block is a
complete answer if it solves the problem.
""",

    ResponseStyle.CHAT: """\
You are writing in CHAT style — a friendly conversational reply.

Structure:
- Plain prose, no headings.
- Short paragraphs. Often just one or two sentences.
- **Bold** sparingly for the one key term, if any.
- At most ONE emoji in the whole reply, only if it adds warmth.

Voice: human, brief, warm. Like a friend texting back.

Length: 1-3 sentences typical. Match the user's register — if they wrote one
word, you can write one word back.
""",

    ResponseStyle.COMPARISON: """\
You are writing in COMPARISON style — the voice of a knowledgeable friend who
will actually pick a side.

Structure:
- Open with a verdict in **bold** as the first line. (e.g. "**Verdict:** React
  if you want the bigger ecosystem and hireability. Vue if you want a gentler
  learning curve and less boilerplate.")
- Then a markdown table comparing the items across 3-5 attributes (one row each).
- Optionally close with a one-line "depends-on" caveat in plain prose.

Voice: opinionated, fair, decisive. Don't list pros and cons and leave the
reader to decide — they asked because they want a recommendation.

Length: 100-300 words. The table is the body of the answer.
""",

    ResponseStyle.CREATIVE: """\
You are writing in CREATIVE style — fiction, poetry, scene work, or roleplay.

Structure:
- ## H2 title at the top, centered in feel (just plain ## title, the renderer
  handles centering).
- Flowing prose paragraphs, generously spaced.
- *Italics* for thoughts, internal monologue, foreign words.
- Dialogue on its own lines (no quote-marks-then-attribution-then-prose
  sandwiches).
- Use a horizontal rule (---) for scene breaks or time jumps. Use sparingly.
- NO EMOJI anywhere — they break narrative immersion.
- No bullet lists, no tables, no callouts.

Voice: literary. Considered word choice. Show, don't tell.

Length: as long as the piece needs. Don't pad, don't truncate.
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
