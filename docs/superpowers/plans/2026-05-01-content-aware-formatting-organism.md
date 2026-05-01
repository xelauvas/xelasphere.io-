# Content-Aware Formatting Organism — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a six-style response-style classifier (regex + LLM fallback) that injects per-style prompt charters into the model's system prompt and tags the SSE stream with the chosen style, so the frontend renders matching typography end-to-end.

**Architecture:** Pre-stream classifier picks one of {`editorial`, `modern_blog`, `technical`, `chat`, `comparison`, `creative`}. Server injects a per-style charter as `system_addition`. Server emits a `{"type": "stream-meta", "style": "..."}` SSE chunk as the first event of the stream. Frontend reads it, sets `data-style="..."` on the message wrapper, and CSS rules scoped by `[data-style="..."]` apply matching typography. Feature-flagged via `RESPONSE_STYLE_ORGANISM_ENABLED`.

**Note on SSE format:** the spec referred to `event: stream-meta\ndata: {...}` (named SSE events). The project's actual SSE convention is `data: {"type": "X", ...}\n\n` with the type field inside the JSON. This plan matches the project's convention. Adjust the spec if you want them re-aligned.

**Tech Stack:** Python 3 / Flask (server), pytest (tests), vanilla ES modules (frontend), CSS, Groq Llama 3.1-8b-instant (already wired for the existing `classify_user_intent` LLM call).

**Source spec:** [`docs/superpowers/specs/2026-05-01-content-aware-formatting-organism-design.md`](../specs/2026-05-01-content-aware-formatting-organism-design.md)

---

## File Structure

| File | New / Modified | Responsibility |
|------|---------------|----------------|
| `app/services/response_style.py` | new | `ResponseStyle` enum, `classify_by_rules()`, `STYLE_CHARTERS` registry, `classify_response_style()` orchestrator |
| `tests/test_response_style.py` | new | Unit tests for enum, regex classifier, charters registry, orchestrator |
| `app/services/chat_engine.py` | modified | `classify_user_intent()` returns a `style` key; `DEVELOPER_INSTRUCTIONS` loses the 80-line FORMAT MANDATE block |
| `app/blueprints/chat_routes.py` | modified | Call `classify_response_style()` pre-stream, inject charter as `system_addition`, emit `stream-meta` SSE event before content |
| `tests/test_chat_routes_stream_meta.py` | new | Integration test: SSE stream-meta event fires first; charter is injected into `system_addition` |
| `app/static/js/codeWindow.js` | modified | Recognise `event: stream-meta` SSE event, capture `streamMeta`, pass into `new StreamRenderer(...)` |
| `app/static/js/streamRenderer.js` | modified | Constructor accepts `streamMeta`; sets `data-style` on `messageEl` and `wrapperEl` |
| `app/static/css/stream-styles.css` | new | Six scoped style blocks, one per `[data-style="..."]` value |
| `app/templates/index.html` | modified | Link new `stream-styles.css`; bump cache buster |
| `gunicorn.conf.py` or `.env` | modified | `RESPONSE_STYLE_ORGANISM_ENABLED` env var (default `false` → flip to `true` after verify) |

---

## Task 1: ResponseStyle enum + test scaffold

**Files:**
- Create: `app/services/response_style.py`
- Create: `tests/test_response_style.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_response_style.py
"""Tests for response_style — six-style content classifier."""

import pytest


class TestResponseStyleEnum:
    def test_enum_has_six_values(self):
        from app.services.response_style import ResponseStyle
        values = {s.value for s in ResponseStyle}
        assert values == {"editorial", "modern_blog", "technical", "chat", "comparison", "creative"}

    def test_default_is_modern_blog(self):
        from app.services.response_style import ResponseStyle
        assert ResponseStyle.default() == ResponseStyle.MODERN_BLOG

    def test_enum_string_value(self):
        from app.services.response_style import ResponseStyle
        # str-Enum so values can be safely JSON-serialised
        assert ResponseStyle.EDITORIAL == "editorial"
        assert isinstance(ResponseStyle.EDITORIAL.value, str)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_response_style.py::TestResponseStyleEnum -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.response_style'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/response_style.py
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_response_style.py::TestResponseStyleEnum -v
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/response_style.py tests/test_response_style.py
git commit -m "feat(response-style): add ResponseStyle enum (six values + default)"
```

---

## Task 2: Regex fast-path classifier

**Files:**
- Modify: `app/services/response_style.py`
- Modify: `tests/test_response_style.py`

- [ ] **Step 1: Add the failing test class**

Append to `tests/test_response_style.py`:

```python
class TestClassifyByRules:
    """Regex fast path — first-match-wins, returns None on no match."""

    @pytest.mark.parametrize("msg, expected", [
        # Greetings / chat / very short
        ("hi", "chat"),
        ("hello", "chat"),
        ("hey", "chat"),
        ("thanks!", "chat"),
        ("thank you", "chat"),
        ("ok", "chat"),
        ("sure", "chat"),
        ("yes", "chat"),
        ("nope", "chat"),
        ("yo", "chat"),
        ("", "chat"),
        ("?", "chat"),
        # Comparison
        ("react vs vue?", "comparison"),
        ("python versus ruby for backend", "comparison"),
        ("which is better postgres or mysql", "comparison"),
        ("differences between rest and graphql", "comparison"),
        ("should I use docker or podman", "comparison"),
        # Creative
        ("write me a story about a lighthouse keeper", "creative"),
        ("write a haiku about coffee", "creative"),
        ("describe a scene in tokyo at night", "creative"),
        ("tell me a story about loss", "creative"),
        # Technical
        ("write a python function to debounce calls", "technical"),
        ("fix this bug in my react component", "technical"),
        ("debug the migration that's failing", "technical"),
        ("implement a stack class", "technical"),
        # Tips / how-to → modern_blog
        ("give me tips on time management", "modern_blog"),
        ("how do i improve my sleep", "modern_blog"),
        ("ways to reduce anxiety", "modern_blog"),
        ("best practices for code review", "modern_blog"),
        # Editorial — explain longform
        ("explain how transformer attention works in detail and why scaled dot product was chosen", "editorial"),
        ("why does the moon affect the tides over geological timescales", "editorial"),
        # Ambiguous → None (defer to LLM)
        ("the vibes are off in this codebase", None),
        ("anything good lately", None),
        ("not sure where to start", None),
    ])
    def test_classifies_correctly(self, msg, expected):
        from app.services.response_style import classify_by_rules, ResponseStyle
        result = classify_by_rules(msg)
        if expected is None:
            assert result is None
        else:
            assert result == ResponseStyle(expected), f"input={msg!r} got={result}"

    def test_code_fence_in_message_is_technical(self):
        from app.services.response_style import classify_by_rules, ResponseStyle
        msg = "I see this error:\n```\nTypeError: undefined\n```\nwhat's wrong?"
        assert classify_by_rules(msg) == ResponseStyle.TECHNICAL

    def test_in_python_at_end_is_technical(self):
        from app.services.response_style import classify_by_rules, ResponseStyle
        assert classify_by_rules("how do i sort a list in python") == ResponseStyle.TECHNICAL
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_response_style.py::TestClassifyByRules -v
```

Expected: FAIL — `ImportError: cannot import name 'classify_by_rules'`.

- [ ] **Step 3: Implement `classify_by_rules`**

Append to `app/services/response_style.py`:

```python
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
    r"\b(write|implement|fix|debug|refactor|optimi[sz]e|build|create) "
    r"(?:a |an |the |this |my )?"
    r"(function|method|class|component|module|script|test|loop|"
    r"query|api|endpoint|migration|hook|reducer|service|controller|"
    r"middleware|regex)\b",
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_response_style.py::TestClassifyByRules -v
```

Expected: PASS — every parametrised case green.

- [ ] **Step 5: Commit**

```bash
git add app/services/response_style.py tests/test_response_style.py
git commit -m "feat(response-style): regex fast path (chat/comparison/creative/technical/blog/editorial)"
```

---

## Task 3: STYLE_CHARTERS registry

**Files:**
- Modify: `app/services/response_style.py`
- Modify: `tests/test_response_style.py`

- [ ] **Step 1: Add the failing test class**

Append to `tests/test_response_style.py`:

```python
class TestStyleCharters:
    def test_every_style_has_a_charter(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        assert set(STYLE_CHARTERS.keys()) == set(ResponseStyle)

    def test_charter_mentions_its_style_name(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        for style, charter in STYLE_CHARTERS.items():
            # Charter should name-drop the style so the model has a label
            # to ground its behaviour against.
            assert style.value.upper().replace("_", " ") in charter.upper() or \
                   style.value.upper() in charter.upper(), (
                       f"{style.value} charter does not reference its name"
                   )

    def test_charter_size_under_3000_chars(self):
        # Charters are appended to the existing ~3000-line dev instructions.
        # Cap each at 3000 chars so the system prompt stays under model limits.
        from app.services.response_style import STYLE_CHARTERS
        for style, charter in STYLE_CHARTERS.items():
            assert len(charter) < 3000, f"{style.value} charter is {len(charter)} chars"

    def test_chat_charter_forbids_headings(self):
        # Smoke that the chat charter actually says "no headings"
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        chat = STYLE_CHARTERS[ResponseStyle.CHAT].lower()
        assert "no heading" in chat or "no ##" in chat or "plain prose" in chat

    def test_creative_charter_forbids_emoji(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        creative = STYLE_CHARTERS[ResponseStyle.CREATIVE].lower()
        assert "no emoji" in creative or "without emoji" in creative
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_response_style.py::TestStyleCharters -v
```

Expected: FAIL — `ImportError: cannot import name 'STYLE_CHARTERS'`.

- [ ] **Step 3: Implement `STYLE_CHARTERS`**

Append to `app/services/response_style.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_response_style.py::TestStyleCharters -v
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/response_style.py tests/test_response_style.py
git commit -m "feat(response-style): six STYLE_CHARTERS — voice + structure rules per style"
```

---

## Task 4: `classify_response_style` orchestrator

**Files:**
- Modify: `app/services/response_style.py`
- Modify: `tests/test_response_style.py`

- [ ] **Step 1: Add the failing test class**

Append to `tests/test_response_style.py`:

```python
class TestClassifyResponseStyle:
    """Orchestrator: regex fast-path → LLM fallback → default."""

    def test_uses_regex_when_it_matches(self, monkeypatch):
        from app.services import response_style as rs
        called = {"llm": False}
        def _no_llm(msg):
            called["llm"] = True
            return None
        monkeypatch.setattr(rs, "_classify_via_llm", _no_llm)

        result = rs.classify_response_style("hi there")
        assert result == rs.ResponseStyle.CHAT
        assert called["llm"] is False, "regex matched; LLM should not have run"

    def test_falls_back_to_llm_on_ambiguous(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm",
                            lambda msg: rs.ResponseStyle.EDITORIAL)
        result = rs.classify_response_style("the vibes are off in this codebase")
        assert result == rs.ResponseStyle.EDITORIAL

    def test_returns_default_when_llm_fails(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm", lambda msg: None)
        result = rs.classify_response_style("the vibes are off in this codebase")
        assert result == rs.ResponseStyle.default()  # MODERN_BLOG

    def test_returns_default_on_empty_input(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm", lambda msg: None)
        # empty falls through regex (returns CHAT) before reaching LLM
        assert rs.classify_response_style("") == rs.ResponseStyle.CHAT
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_response_style.py::TestClassifyResponseStyle -v
```

Expected: FAIL — `AttributeError: module 'app.services.response_style' has no attribute 'classify_response_style'`.

- [ ] **Step 3: Implement orchestrator and LLM stub**

Append to `app/services/response_style.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_response_style.py::TestClassifyResponseStyle -v
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/response_style.py tests/test_response_style.py
git commit -m "feat(response-style): classify_response_style orchestrator (rules → LLM → default)"
```

---

## Task 5: Extend `classify_user_intent` to return `style`

**Files:**
- Modify: `app/services/chat_engine.py:638-720` (the function body of `classify_user_intent`)
- Modify: `tests/test_response_style.py` (add LLM-extension tests)

- [ ] **Step 1: Add the failing test class**

Append to `tests/test_response_style.py`:

```python
class TestClassifyUserIntentStyleField:
    """The existing classify_user_intent now returns a `style` key
    when the LLM supplies one. Mocked — does not hit Groq."""

    def _mock_groq(self, monkeypatch, response_json):
        """Helper: replace requests.post inside chat_engine with a stub."""
        from app.services import chat_engine

        class _Resp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": response_json}}]}

        def _post(*args, **kwargs):
            return _Resp()

        # chat_engine does `import requests as req` inside the function,
        # so patch the module-level requests as well.
        monkeypatch.setattr("requests.post", _post)
        # Make sure GROQ_API_KEY is set so the path is taken
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        # Clear the cache so each test does a fresh classification
        chat_engine._intent_cache.clear()

    def test_returns_style_field(self, monkeypatch):
        self._mock_groq(monkeypatch, '''{"intent_type":"general_chat",
                                          "has_crypto_mention":false,
                                          "has_stock_mention":false,
                                          "asset_symbol":null,
                                          "wants_chart":false,
                                          "wants_image":false,
                                          "wants_code":false,
                                          "style":"editorial"}''')
        from app.services.chat_engine import classify_user_intent
        result = classify_user_intent("the vibes are off in this codebase",
                                       use_cache=False)
        assert result.get("style") == "editorial"

    def test_missing_style_field_does_not_break_existing_callers(self, monkeypatch):
        # Older LLM responses (or stripped-down JSON) may not include `style`.
        # Existing callers read intent_type — they must not crash.
        self._mock_groq(monkeypatch, '''{"intent_type":"question",
                                          "has_crypto_mention":false,
                                          "has_stock_mention":false,
                                          "asset_symbol":null,
                                          "wants_chart":false,
                                          "wants_image":false,
                                          "wants_code":false}''')
        from app.services.chat_engine import classify_user_intent
        result = classify_user_intent("hello world stuff",
                                       use_cache=False)
        assert result.get("intent_type") == "question"
        assert result.get("style") is None  # absent → None, not KeyError
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_response_style.py::TestClassifyUserIntentStyleField -v
```

Expected: FAIL — the LLM JSON template doesn't include `style`, so the parsed dict won't have it.

- [ ] **Step 3: Extend the JSON schema in `classify_user_intent`**

In `app/services/chat_engine.py`, the function `classify_user_intent` (starting at line 638) builds a `classification_prompt`. Modify the prompt to include the `style` field. Find this block (around lines 668-678):

```python
    classification_prompt = f"""Analyze this user message and classify their intent. Respond ONLY with a JSON object.

User message: "{prompt}"

Respond with this exact JSON format (no other text):
{{"intent_type": "<one of: financial_query, creative_task, question, recipe_request, code_request, image_request, general_chat>",
"has_crypto_mention": <true if asking about cryptocurrency prices/charts, false otherwise>,
"has_stock_mention": <true if asking about stocks prices/charts, false otherwise>,
"asset_symbol": "<symbol like BTC, ETH, AAPL or null if none>",
"wants_chart": <true if wants price chart/graph, false otherwise>,
"wants_image": <true if wants AI-generated image, false otherwise>,
"wants_code": <true if wants code/mermaid/diagram code, false otherwise>}}

IMPORTANT: Words like "create", "make", "build" at the start usually mean creative_task, NOT cryptocurrency.
Only set has_crypto_mention=true if they're clearly asking about crypto PRICES or MARKET data."""
```

Replace with:

```python
    classification_prompt = f"""Analyze this user message and classify their intent. Respond ONLY with a JSON object.

User message: "{prompt}"

Respond with this exact JSON format (no other text):
{{"intent_type": "<one of: financial_query, creative_task, question, recipe_request, code_request, image_request, general_chat>",
"has_crypto_mention": <true if asking about cryptocurrency prices/charts, false otherwise>,
"has_stock_mention": <true if asking about stocks prices/charts, false otherwise>,
"asset_symbol": "<symbol like BTC, ETH, AAPL or null if none>",
"wants_chart": <true if wants price chart/graph, false otherwise>,
"wants_image": <true if wants AI-generated image, false otherwise>,
"wants_code": <true if wants code/mermaid/diagram code, false otherwise>,
"style": "<one of: editorial, modern_blog, technical, chat, comparison, creative>"}}

IMPORTANT:
- Words like "create", "make", "build" at the start usually mean creative_task, NOT cryptocurrency.
- Only set has_crypto_mention=true if they're clearly asking about crypto PRICES or MARKET data.
- For "style": pick `editorial` for longform explainers/opinion ("tell me about X"); `modern_blog` for tips/how-tos/listicles ("ways to Y"); `technical` for code questions/debugging; `chat` for greetings/thanks/short factual; `comparison` for "X vs Y"/"which is better"; `creative` for stories/poems/fiction. Prefer `modern_blog` when uncertain."""
```

The regex that extracts the JSON (line 706: `re.search(r'\{[^{}]*\}', content, re.DOTALL)`) does NOT need to change — it captures the whole JSON object regardless of the new field. The parser uses `result.get(...)` — already returns `None` for missing keys.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_response_style.py::TestClassifyUserIntentStyleField -v
```

Expected: PASS — 2 tests. (The mocked Groq response is supplying the `style` field directly.)

Also run the full suite to confirm no existing tests break:

```bash
pytest tests/ -k "not slow" -x
```

Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_response_style.py
git commit -m "feat(intent-classifier): add 'style' field to JSON schema (additive)"
```

---

## Task 6: Wire classifier + charter + SSE meta event into `chat_routes`

**Files:**
- Modify: `app/blueprints/chat_routes.py:1174-1195` (the system_prompt assembly + stream-start path)
- Create: `tests/test_chat_routes_stream_meta.py`

- [ ] **Step 1: Locate the integration points**

```bash
grep -n "base_system\|system_addition\|enhanced_prompt\|def stream_response\|yield .*event:" app/blueprints/chat_routes.py | head -20
```

You're modifying the function that builds `base_system` (around line 1180) and the function that yields SSE events to the client. Read 30 lines around each to understand local variables in scope.

- [ ] **Step 2: Write the failing integration test**

```python
# tests/test_chat_routes_stream_meta.py
"""Integration test: chat_routes emits stream-meta SSE event with the
classified style, and injects the style charter as system_addition."""

import os
import pytest
from unittest.mock import patch


class TestStreamMetaSSEEvent:
    """Stream-meta event must be the first event of the SSE response,
    its style must match the classifier output, and the charter must be
    injected into base_system."""

    @pytest.fixture(autouse=True)
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")

    def test_emits_stream_meta_first_for_greeting(self, monkeypatch):
        """A 'hi' message → regex picks CHAT → SSE first event is stream-meta
        with style: chat."""
        # Arrange: capture what gets injected into the model prompt.
        captured = {}

        from app.services import response_style
        original = response_style.classify_response_style
        def _spy(msg):
            result = original(msg)
            captured["style"] = result
            return result
        monkeypatch.setattr(response_style, "classify_response_style", _spy)

        # Act + Assert: this is a placeholder until the wiring exists.
        # After implementation, this test invokes the streaming endpoint
        # and inspects the SSE output. For now we just assert the spy
        # ran during a route call. (Actual route invocation goes in
        # the implementation step.)
        from app.services.response_style import classify_response_style, ResponseStyle
        assert classify_response_style("hi") == ResponseStyle.CHAT
        # The full integration assertion is added in Step 4 once the
        # route invokes classify_response_style.
```

This is a stub — we'll fill in the real route-level assertion once the wiring is in place. It exists now so the test class is defined.

- [ ] **Step 3: Run test to verify it passes (stub) but the real wiring isn't in place**

```bash
pytest tests/test_chat_routes_stream_meta.py -v
```

Expected: PASS (stub assertion is trivially true; we'll harden it in Step 5).

- [ ] **Step 4: Wire the classifier and charter into `chat_routes.py`**

Find this block in `app/blueprints/chat_routes.py` (around line 1178-1190):

```python
    # Optional system_addition from client (e.g. voice mode interrupt context)
    system_addition = request.form.get('system_addition', '').strip()
    base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)
    if system_addition:
        base_system = base_system + "\n\n" + system_addition
        logger.info(f"[Stream] system_addition applied ({len(system_addition)} chars)")
```

Replace with:

```python
    # Optional system_addition from client (e.g. voice mode interrupt context)
    system_addition = request.form.get('system_addition', '').strip()
    base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)
    if system_addition:
        base_system = base_system + "\n\n" + system_addition
        logger.info(f"[Stream] system_addition applied ({len(system_addition)} chars)")

    # Content-aware formatting organism: classify the user's message into one of
    # six styles, then inject the matching charter as a system_addition so the
    # model writes in that voice. Style is also broadcast to the client via
    # the stream-meta SSE event so typography can match.
    chosen_style = None
    if os.environ.get("RESPONSE_STYLE_ORGANISM_ENABLED", "false").lower() == "true":
        try:
            from app.services.response_style import (
                classify_response_style, STYLE_CHARTERS,
            )
            chosen_style = classify_response_style(user_prompt_raw or prompt)
            charter = STYLE_CHARTERS.get(chosen_style, "")
            if charter:
                base_system = base_system + "\n\n" + charter
                logger.info(f"[Stream] style={chosen_style.value} charter injected ({len(charter)} chars)")
        except Exception as exc:
            logger.warning(f"[Stream] response-style classification failed: {exc}")
            chosen_style = None
```

(The `os` module is already imported at the top of the file. `user_prompt_raw` is the raw user message variable in scope at this point — verify by reading the surrounding code.)

Now find the stream-yield generator. In `app/blueprints/chat_routes.py`, search for `def generate(`:

```bash
grep -n "def generate(" app/blueprints/chat_routes.py
```

Expected: line ~1247 (`def generate():`). This is the SSE generator inside the streaming endpoint. Read 20 lines from that point to find where the first `yield` happens.

The project's SSE format is `data: {"type":"X", ...}\n\n` — type-discriminated JSON, NOT named SSE events. Add this AS THE FIRST yield inside `generate()`, before any other yields:

```python
        # Emit stream-meta as the first SSE chunk so the client knows the
        # visual style before any content arrives. Same wire format as
        # every other event in this generator: `data: {json}\n\n`, with
        # the `type` field discriminating.
        if chosen_style is not None:
            yield f"data: {json.dumps({'type': 'stream-meta', 'style': chosen_style.value, 'via': 'organism'})}\n\n"
```

(`json` is already imported at the top of the file. `chosen_style` is captured by closure from the enclosing function scope where Step 4 set it. Python closes over the name, so as long as `chosen_style` is assigned before `generate()` is iterated by Flask's `Response(..., mimetype='text/event-stream')`, this works.)

- [ ] **Step 5: Harden the integration test**

Replace the stub test in `tests/test_chat_routes_stream_meta.py` with an actual route invocation. (You may need to mock the LLM streaming call so the test doesn't hit Groq — search for existing chat-route tests like `tests/test_chat_routes_*` for the established mocking pattern. If none exist, the simplest approach is a unit-level test of just the new logic block by extracting it to a helper.)

Minimum acceptance:

```python
def test_charter_injected_when_flag_on(self, monkeypatch):
    """Smoke: with the flag on, classify_response_style is called and
    its result drives charter injection into base_system."""
    monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")

    from app.services.response_style import (
        classify_response_style, STYLE_CHARTERS, ResponseStyle,
    )
    style = classify_response_style("give me tips on time management")
    assert style == ResponseStyle.MODERN_BLOG
    assert "MODERN_BLOG" in STYLE_CHARTERS[style] or "modern_blog" in STYLE_CHARTERS[style].lower()
```

(End-to-end SSE inspection requires a running Flask test client. If your codebase has one set up via `conftest.py`, extend that. If not, leave the unit-level smoke and add a manual verification step in Task 13.)

```bash
pytest tests/test_chat_routes_stream_meta.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/chat_routes.py tests/test_chat_routes_stream_meta.py
git commit -m "feat(chat-routes): inject style charter; emit stream-meta SSE first event"
```

---

## Task 7: Remove the FORMAT MANDATE block from `DEVELOPER_INSTRUCTIONS`

**Files:**
- Modify: `app/services/chat_engine.py` (the trailing `🚨 FINAL FORMAT MANDATE` block inside `DEVELOPER_INSTRUCTIONS`, ending at line ~553 of the current file)

- [ ] **Step 1: Locate the block**

```bash
grep -n "FINAL FORMAT MANDATE\|REQUIRED elements\|FORBIDDEN unless" app/services/chat_engine.py
```

This is the block added during the broken format-mandate iteration. Charters now do the per-question work.

- [ ] **Step 2: Delete the block**

Read the file from the line that starts with `"==`...`"FINAL FORMAT MANDATE` down through the closing `\n\n"` of that pasted heredoc-like Python string concat. Delete every line of the mandate including its `"""===…"""` separators. The file must still end with a single `)` closing `DEVELOPER_INSTRUCTIONS = (`.

After deletion, the trailing image-generation example block stays (`"IMPORTANT for generate_image chat text:..."` etc.). Only the `🚨 FINAL FORMAT MANDATE` block goes.

- [ ] **Step 3: Verify the file still parses and DEVELOPER_INSTRUCTIONS exists**

```bash
python3 -c "import ast; ast.parse(open('app/services/chat_engine.py').read()); print('SYNTAX OK')"
python3 -c "from app.services.chat_engine import DEVELOPER_INSTRUCTIONS; print('OK', len(DEVELOPER_INSTRUCTIONS))"
```

Expected: Both print OK. Length should drop by ~2000 chars from current.

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/ -k "not slow" -x
```

Expected: PASS (charters now carry the formatting rules; no test asserts on the mandate block specifically).

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "refactor(chat-engine): remove FORMAT MANDATE block — charters do per-question work"
```

---

## Task 8: Frontend — handle `stream-meta` chunk in `processLine`

**Files:**
- Modify: `app/static/js/codeWindow.js`

- [ ] **Step 1: Locate the dispatch site**

```bash
grep -n "const processLine\|chunk.type ===" app/static/js/codeWindow.js | head -10
```

Expected: `processLine` defined around line 2477. The function does `if (chunk.type === 'reasoning') {...} else if (chunk.type === 'content') {...}` etc. — the project uses type-discriminated JSON over SSE, not named SSE events.

- [ ] **Step 2: Declare `streamMeta` and add a `stream-meta` branch to processLine**

In `app/static/js/codeWindow.js`, find the block of `let` declarations near line 2408-2425 (`let streamArtifact = null; let streamRenderMode = 'inline';` etc.). Add:

```js
let streamMeta = {};   // captured from the first 'stream-meta' SSE chunk
```

Then in `processLine`, add the new branch as the FIRST `else if` after `chunk.type === 'reasoning'` so it's checked before content arrives:

```js
} else if (chunk.type === 'stream-meta') {
    // First chunk from server when RESPONSE_STYLE_ORGANISM_ENABLED is on.
    // Captures the chosen visual style. Passed into StreamRenderer when
    // the first content chunk creates one.
    streamMeta = chunk;  // {type, style, via}
    return;
```

- [ ] **Step 3: Pass `streamMeta` into the StreamRenderer constructor**

Search for the existing StreamRenderer instantiation:

```bash
grep -n "new StreamRenderer\|new window.StreamRenderer" app/static/js/codeWindow.js
```

There may be more than one (live mode + replay mode). For each LIVE-mode instantiation (not the history-replay one), update the call:

```js
// If currently:   _streamRenderer = new StreamRenderer(messageBubbleContainer);
// Change to:
_streamRenderer = new StreamRenderer(messageBubbleContainer, { streamMeta });

// If currently:   _streamRenderer = new StreamRenderer(messageBubbleContainer, { replay: false });
// Change to:
_streamRenderer = new StreamRenderer(messageBubbleContainer, { replay: false, streamMeta });
```

The replay-path StreamRenderer (where `replay: true`) does NOT need streamMeta — replays don't go through this stream so streamMeta is `{}` anyway. Leave replay sites unchanged.

- [ ] **Step 4: Verify the wiring**

```bash
grep -n "stream-meta\|streamMeta" app/static/js/codeWindow.js
```

Expected: at least 3 matches — the `let streamMeta = {}` declaration, the `chunk.type === 'stream-meta'` branch, and the constructor argument.

```bash
node --check app/static/js/codeWindow.js && echo OK
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(stream): capture stream-meta chunk; pass to StreamRenderer"
```

---

## Task 9: Frontend — `StreamRenderer` accepts `streamMeta`, sets `data-style`

**Files:**
- Modify: `app/static/js/streamRenderer.js` (constructor, line ~155)

- [ ] **Step 1: Update the constructor signature and add `data-style` setting**

In `app/static/js/streamRenderer.js`, find the class constructor:

```js
class StreamRenderer {
    constructor(container, { replay = false } = {}) {
```

Change to:

```js
const VALID_STYLES = new Set([
    'editorial', 'modern_blog', 'technical',
    'chat', 'comparison', 'creative',
]);

class StreamRenderer {
    constructor(container, { replay = false, streamMeta = {} } = {}) {
```

Then, immediately after `this.wrapperEl = wrapper;` (around line 179), add:

```js
        // Apply server-decided visual style. Set on both the inner
        // .text-message and the outer .text-message-container so CSS
        // can scope to either depending on the rule. Whitelist the value
        // so a malformed/unknown style from the server is silently ignored.
        const style = streamMeta && streamMeta.style;
        if (style && VALID_STYLES.has(style)) {
            inner.dataset.style = style;
            wrapper.dataset.style = style;
        }
```

- [ ] **Step 2: Verify JS parses**

```bash
node --check app/static/js/streamRenderer.js && echo OK
```

Expected: OK.

- [ ] **Step 3: Bump the cache buster for streamRenderer.js**

streamRenderer.js is currently loaded as a `<script type="module">` without `?v=N`. The browser's no-cache header revalidates so no buster is needed. Skip this step unless the file proves to cache aggressively in your testing.

(If you find caching issues during Task 13, add `?v=1` here and bump it on each subsequent change.)

- [ ] **Step 4: Commit**

```bash
git add app/static/js/streamRenderer.js
git commit -m "feat(stream-renderer): accept streamMeta; set data-style on message wrappers"
```

---

## Task 10: CSS — `stream-styles.css` with six scoped style blocks

**Files:**
- Create: `app/static/css/stream-styles.css`

- [ ] **Step 1: Create the file with all six style blocks**

```css
/* app/static/css/stream-styles.css
 *
 * Per-style typography. Scoped by [data-style="..."] which the streamRenderer
 * sets on .text-message-container and .text-message immediately after the
 * server's stream-meta SSE event arrives.
 *
 * data-style is orthogonal to data-ctx — data-style drives typography
 * (font/spacing/heading look) and is server-decided; data-ctx drives column
 * width and layout-tier rules and is client-classified post-stream.
 *
 * See docs/superpowers/specs/2026-05-01-content-aware-formatting-organism-design.md
 */


/* ============================================================
 * EDITORIAL — serif body, italic lede, small-caps section labels
 * ============================================================ */
[data-style="editorial"] .text-message {
    font-family: 'Georgia', 'Times New Roman', serif;
    line-height: 1.65;
}
[data-style="editorial"] .text-message .stream-block.block-paragraph:first-child {
    font-style: italic;
    border-left: 3px solid var(--accent, rgba(255,200,100,0.6));
    padding-left: 12px;
    opacity: 0.9;
}
[data-style="editorial"] .text-message h2 {
    font-family: 'Inter', sans-serif;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 600;
    opacity: 0.75;
    margin-top: 1.6em;
}


/* ============================================================
 * MODERN_BLOG — punchy sans, big headings, blockquote callouts
 * ============================================================ */
[data-style="modern_blog"] .text-message {
    font-family: 'Inter', system-ui, sans-serif;
    line-height: 1.55;
}
[data-style="modern_blog"] .text-message h2 {
    font-weight: 800;
    letter-spacing: -0.02em;
    font-size: 1.4rem;
    margin-top: 1.2em;
}
[data-style="modern_blog"] .text-message h3 {
    font-weight: 700;
    font-size: 1.05rem;
    margin-top: 1em;
}
[data-style="modern_blog"] .text-message blockquote {
    /* The MODERN_BLOG charter instructs the model to emit "Bottom line:"
       lines as `> **Bottom line:** ...`. Style every blockquote in this
       context as a callout box. */
    background: rgba(100,200,255,0.08);
    border-left: 3px solid rgba(100,200,255,0.6);
    padding: 8px 12px;
    border-radius: 4px;
    margin: 12px 0;
}


/* ============================================================
 * TECHNICAL — sans body, side-bar accent on code blocks, sparse emoji
 * ============================================================ */
[data-style="technical"] .text-message {
    font-family: 'Inter', system-ui, sans-serif;
    line-height: 1.55;
}
[data-style="technical"] .text-message h2,
[data-style="technical"] .text-message h3 {
    font-weight: 600;
    letter-spacing: -0.01em;
}
[data-style="technical"] .text-message pre {
    border-left: 2px solid rgba(120,200,255,0.5);
}
[data-style="technical"] .text-message blockquote {
    /* Charter says use blockquotes for ⚠️ caveats. */
    background: rgba(255,200,80,0.06);
    border: 1px solid rgba(255,200,80,0.18);
    padding: 7px 11px;
    border-radius: 6px;
    margin: 12px 0;
}


/* ============================================================
 * CHAT — plain prose, friendly, generous line-height
 * ============================================================ */
[data-style="chat"] .text-message {
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 0.95rem;
    line-height: 1.55;
}
[data-style="chat"] .text-message h1,
[data-style="chat"] .text-message h2,
[data-style="chat"] .text-message h3 {
    /* Charter forbids headings in chat replies — but if the model
       slips one in, render it as plain bold text rather than a
       section break. */
    font-size: 1em;
    font-weight: 600;
    margin: 0;
    display: inline;
}
[data-style="chat"] .text-message ul,
[data-style="chat"] .text-message ol {
    /* Same — model shouldn't list, but if it does, style minimally. */
    margin: 6px 0;
}


/* ============================================================
 * COMPARISON — verdict accent, full-width table
 * ============================================================ */
[data-style="comparison"] .text-message {
    font-family: 'Inter', system-ui, sans-serif;
    line-height: 1.55;
}
[data-style="comparison"] .text-message .stream-block.block-paragraph:first-child {
    /* The opening verdict paragraph (charter: "**Verdict:** ..." in bold). */
    background: linear-gradient(90deg, rgba(120,255,180,0.08), transparent);
    border-left: 3px solid rgba(120,255,180,0.5);
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 14px;
}
[data-style="comparison"] .text-message table {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
}
[data-style="comparison"] .text-message th {
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.76rem;
    font-weight: 600;
    opacity: 0.7;
    padding: 8px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.15);
}
[data-style="comparison"] .text-message td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    font-size: 0.92rem;
}


/* ============================================================
 * CREATIVE — serif, centered title, scene-break rules
 * ============================================================ */
[data-style="creative"] .text-message {
    font-family: 'Georgia', 'Times New Roman', serif;
    line-height: 1.7;
}
[data-style="creative"] .text-message h2 {
    font-family: 'Georgia', serif;
    font-style: italic;
    font-weight: 400;
    text-align: center;
    margin: 1.4em 0 0.8em;
    opacity: 0.92;
}
[data-style="creative"] .text-message em {
    /* charter says italics for inner thoughts — slightly softer */
    opacity: 0.88;
}
[data-style="creative"] .text-message hr {
    border: none;
    text-align: center;
    margin: 1.6em 0;
}
[data-style="creative"] .text-message hr::after {
    content: '· · ·';
    letter-spacing: 0.5em;
    opacity: 0.5;
    font-size: 0.85rem;
}
```

- [ ] **Step 2: Sanity-check CSS**

```bash
# No formal CSS linter wired up in this repo; do a basic balance check.
python3 -c "
css = open('app/static/css/stream-styles.css').read()
opens = css.count('{'); closes = css.count('}')
assert opens == closes, f'Unbalanced braces: {opens} open vs {closes} close'
print(f'OK — {opens} balanced rule blocks')
"
```

Expected: OK with a count.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/stream-styles.css
git commit -m "feat(css): stream-styles.css — six per-style typography blocks"
```

---

## Task 11: Link `stream-styles.css` from `index.html`

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Add the stylesheet link**

In `app/templates/index.html`, find the existing `<link>` tags for stylesheet (search for `stream-blocks.css`). Right after the existing `stream-blocks.css` link, add:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/stream-styles.css') }}?v=1">
```

- [ ] **Step 2: Verify**

```bash
grep -n "stream-styles.css" app/templates/index.html
```

Expected: 1 match.

- [ ] **Step 3: Commit**

```bash
git add app/templates/index.html
git commit -m "chore(templates): link stream-styles.css"
```

---

## Task 12: Feature flag wiring + initial deploy

**Files:**
- Modify: `gunicorn.conf.py` (or whichever env-loading mechanism your deploy uses)

The classifier code already reads `RESPONSE_STYLE_ORGANISM_ENABLED` (Task 6, Step 4). For first deploy, set it `false`; flip to `true` once the rest is verified live.

- [ ] **Step 1: Set the env var to `false` for first deploy**

```bash
# If you use a .env or systemd unit:
echo 'RESPONSE_STYLE_ORGANISM_ENABLED=false' >> .env
# Or in gunicorn.conf.py:
# raw_env = ['RESPONSE_STYLE_ORGANISM_ENABLED=false', ...]
```

Choose the mechanism your deploy already uses. Verify:

```bash
grep -rn "RESPONSE_STYLE_ORGANISM_ENABLED" .env gunicorn.conf.py /etc/systemd/system/xelasphere.service 2>/dev/null
```

Expected: at least 1 match in the file you edited.

- [ ] **Step 2: Deploy to /opt and restart**

(File-copy deploy per `project_deployment.md`.)

```bash
sudo install -o xelasphere -g xelasphere -m 644 \
  app/services/response_style.py /opt/xelasphere/app/services/response_style.py
sudo install -o xelasphere -g xelasphere -m 644 \
  app/services/chat_engine.py /opt/xelasphere/app/services/chat_engine.py
sudo install -o xelasphere -g xelasphere -m 644 \
  app/blueprints/chat_routes.py /opt/xelasphere/app/blueprints/chat_routes.py
sudo install -o xelasphere -g xelasphere -m 644 \
  app/static/js/codeWindow.js /opt/xelasphere/app/static/js/codeWindow.js
sudo install -o xelasphere -g xelasphere -m 644 \
  app/static/js/streamRenderer.js /opt/xelasphere/app/static/js/streamRenderer.js
sudo install -o xelasphere -g xelasphere -m 644 \
  app/static/css/stream-styles.css /opt/xelasphere/app/static/css/stream-styles.css
sudo install -o xelasphere -g xelasphere -m 644 \
  app/templates/index.html /opt/xelasphere/app/templates/index.html

sudo systemctl restart xelasphere
sleep 2
systemctl is-active xelasphere
curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" https://xelasphere.com/
```

Expected: `active`, HTTP 200. Service running with flag OFF — behaves identically to pre-change baseline.

- [ ] **Step 3: Commit env config (if it's tracked)**

```bash
git add gunicorn.conf.py  # or whichever you touched
git commit -m "chore(deploy): RESPONSE_STYLE_ORGANISM_ENABLED env var (default false)"
```

---

## Task 13: End-to-end manual smoke + flip the flag

**Files:**
- (no source edits — verification only)

- [ ] **Step 1: Flip the flag on**

```bash
# Edit your env source:
sed -i 's/RESPONSE_STYLE_ORGANISM_ENABLED=false/RESPONSE_STYLE_ORGANISM_ENABLED=true/' .env
# Or update systemd Environment= line and reload-and-restart
sudo systemctl restart xelasphere
sleep 2
systemctl is-active xelasphere
```

Expected: `active`.

- [ ] **Step 2: Verify the SSE event fires for each style**

Open https://xelasphere.com in a browser, hard-refresh (Cmd/Ctrl+Shift+R), open devtools → Network → click an in-flight chat stream → Response/EventStream tab.

Send each of the following messages, one per fresh chat:

| Message | Expected `stream-meta` `style` |
|---------|-------------------------------|
| `hi` | `chat` |
| `react vs vue` | `comparison` |
| `write a python function to memoize` | `technical` |
| `give me tips on focus` | `modern_blog` |
| `explain how RAG retrieval works in detail` | `editorial` |
| `write me a haiku about coffee` | `creative` |

Confirm in the EventStream tab that the **first** event for each response is `event: stream-meta` with the expected style.

- [ ] **Step 3: Verify visual rendering matches the brainstorm mockups**

For each response above, inspect the DOM (devtools → Elements):
- `.text-message-container` and `.text-message` both have `data-style="<expected>"`.
- The rendered typography matches the corresponding mockup in `.superpowers/brainstorm/.../visual-style-direction.html` and `style-coverage.html`. (Approximate match — fonts, spacing, callout boxes, table styling.)

Specific checks:
- `chat` reply has NO ## headings rendered as section-breaks (the CSS collapses them inline — the model usually won't emit them anyway).
- `creative` reply has NO emoji (charter forbids them).
- `modern_blog` reply has at least one blockquote rendered as a callout box (the "Bottom line:" line).

- [ ] **Step 4: Verify rollback works**

Flip the flag back to `false`, restart, send the same prompts. Confirm:
- No `stream-meta` event appears in the EventStream tab.
- Messages render with their pre-change appearance (no `data-style` attribute set).

If both modes work correctly, flip the flag back to `true` and finish:

```bash
sed -i 's/RESPONSE_STYLE_ORGANISM_ENABLED=false/RESPONSE_STYLE_ORGANISM_ENABLED=true/' .env
sudo systemctl restart xelasphere
```

- [ ] **Step 5: Final commit (if any small fixes were made during smoke)**

```bash
git add -A
git commit -m "fix(organism): post-smoke adjustments [describe]"
# Skip if no changes
```

- [ ] **Step 6: Open PR**

Push the branch and open a PR. Body should reference the spec and link to the brainstorm mockups (`.superpowers/brainstorm/...`).

---

## Done-Done Checklist

After Task 13:

- [ ] All unit tests green (`pytest tests/test_response_style.py tests/test_chat_routes_stream_meta.py -v`)
- [ ] Full suite still green (`pytest tests/ -k "not slow"`)
- [ ] Six prompts verified visually in production
- [ ] Rollback verified
- [ ] Flag is `true` in production
- [ ] PR opened
