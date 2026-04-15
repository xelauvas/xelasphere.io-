# Structure Intelligence v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide response structure *before* the main model streams its first token, inject it into the system prompt, and deliver it to the renderer as the first SSE event so `data-ctx` is set before any content arrives.

**Architecture:** Extend the existing `classify_user_intent()` in `app/services/chat_engine.py` — additive only. One new key in the return dict (`scaffold`). Scaffold instruction gets appended to the base system prompt in `/stream` (`app/blueprints/chat_routes.py`) and also emitted as a `data: {type: 'scaffold', ...}` SSE event before any content tokens. The client chunk handler at `app/static/js/codeWindow.js:2397` dispatches on `chunk.type === 'scaffold'` and sets `data-ctx` on the active message wrapper. Post-stream `classifyContext()` in `streamRenderer.js` becomes a fallback for when the server-side hint is absent.

**Tech Stack:** Python 3 / Flask SSE, OpenAI `gpt-4.1-nano` via existing `app/memory_v2/llm_retrieval.py` provider config (fallback to Groq `llama-3.1-8b-instant`), vanilla JS on the client, `pytest` for server tests.

**Spec:** `docs/superpowers/specs/2026-04-15-structure-intelligence-v1-design.md`

---

## File Structure

**Modify:**
- `app/services/chat_engine.py` — add `Scaffold` dataclass + `scaffold_to_instruction()` + `_scaffold_from_task_type()` + extend `classify_user_intent()` (one file per spec §1, no new module)
- `app/blueprints/chat_routes.py` — call `classify_user_intent` in `/stream` generator, emit `scaffold` SSE event, append scaffold instruction to `base_system`
- `app/static/js/codeWindow.js` — handle `chunk.type === 'scaffold'` (~line 2397 in the SSE parser)
- `app/static/js/streamRenderer.js` — skip post-stream `classifyContext()` if `data-ctx` already set

**Create:**
- `tests/services/test_structure_scaffold.py` — dataclass, serializer, fallback map, classifier extension
- `tests/services/test_scaffold_css_vocabulary.py` — asserts every `Scaffold.layout` value has a matching CSS rule
- `tests/services/test_intent_accuracy.py` — baseline vs unified-prompt regression test

No new packages, no new services, no new caches.

---

## Layout vocabulary (locked)

The `Scaffold.layout` field MUST be one of these 7 values — each one has an existing CSS rule in `app/static/css/stream-blocks.css`:

```
conversational | prose | long-prose | technical | code-heavy | list-heavy | structured
```

Do **not** add `sequential` or `creative` — these were in the original scaffold idea but have no CSS rule yet. Defer to a later spec.

---

## Task 1: `Scaffold` dataclass

**Files:**
- Modify: `app/services/chat_engine.py` (add near the existing imports / dataclass area)
- Create: `tests/services/test_structure_scaffold.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_structure_scaffold.py`:

```python
"""Tests for Scaffold dataclass and serialization helpers (spec §2, §5)."""
import pytest
from app.services.chat_engine import Scaffold


ALLOWED_LAYOUTS = {
    "conversational", "prose", "long-prose", "technical",
    "code-heavy", "list-heavy", "structured",
}


def test_scaffold_construct_valid():
    s = Scaffold(
        layout="long-prose",
        opening="hook",
        separator=False,
        sections=[],
        emphasis="italic",
        max_paragraph_sentences=3,
        list_threshold=3,
        width_hint="narrow",
    )
    assert s.layout == "long-prose"
    assert s.opening == "hook"
    assert s.separator is False
    assert s.max_paragraph_sentences == 3
    assert s.width_hint == "narrow"


def test_scaffold_rejects_unknown_layout():
    with pytest.raises(ValueError, match="layout"):
        Scaffold(
            layout="bogus",
            opening="direct", separator=False, sections=[],
            emphasis="none", max_paragraph_sentences=3,
            list_threshold=3, width_hint="medium",
        )


def test_scaffold_to_dict_roundtrip():
    s = Scaffold(
        layout="technical", opening="direct", separator=False,
        sections=[{"title": "Setup", "emoji": "⚙️", "style": "steps"}],
        emphasis="bold", max_paragraph_sentences=3,
        list_threshold=3, width_hint="wide",
    )
    d = s.to_dict()
    assert d["layout"] == "technical"
    assert d["sections"][0]["title"] == "Setup"
    s2 = Scaffold.from_dict(d)
    assert s2 == s
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: FAIL with `ImportError: cannot import name 'Scaffold' from 'app.services.chat_engine'`

- [ ] **Step 3: Implement `Scaffold` in `app/services/chat_engine.py`**

Add these imports at the top of the file if not already present:
```python
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Literal, Optional
```

Add this block just before `def classify_user_intent` (line 566 in current file):

```python
# ============================================
# SCAFFOLD (spec §2)
# ============================================

ALLOWED_LAYOUTS = (
    "conversational", "prose", "long-prose", "technical",
    "code-heavy", "list-heavy", "structured",
)
ALLOWED_OPENINGS = ("hook", "direct", "question", "none")
ALLOWED_EMPHASIS = ("bold", "italic", "none")
ALLOWED_WIDTHS = ("narrow", "medium", "wide")


@dataclass
class Scaffold:
    """Pre-stream response scaffold. See spec §2."""
    layout: str
    opening: str
    separator: bool
    sections: List[Dict]
    emphasis: str
    max_paragraph_sentences: int
    list_threshold: int
    width_hint: str

    def __post_init__(self):
        if self.layout not in ALLOWED_LAYOUTS:
            raise ValueError(f"layout must be one of {ALLOWED_LAYOUTS}, got {self.layout!r}")
        if self.opening not in ALLOWED_OPENINGS:
            raise ValueError(f"opening must be one of {ALLOWED_OPENINGS}, got {self.opening!r}")
        if self.emphasis not in ALLOWED_EMPHASIS:
            raise ValueError(f"emphasis must be one of {ALLOWED_EMPHASIS}, got {self.emphasis!r}")
        if self.width_hint not in ALLOWED_WIDTHS:
            raise ValueError(f"width_hint must be one of {ALLOWED_WIDTHS}, got {self.width_hint!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Scaffold":
        return cls(**d)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/services/test_structure_scaffold.py
git commit -m "feat(chat): add Scaffold dataclass for pre-stream structure planning"
```

---

## Task 2: `scaffold_to_instruction()` system-prompt serializer

**Files:**
- Modify: `app/services/chat_engine.py`
- Modify: `tests/services/test_structure_scaffold.py`

- [ ] **Step 1: Append failing tests to the existing test file**

Add to `tests/services/test_structure_scaffold.py`:

```python
from app.services.chat_engine import scaffold_to_instruction


def test_instruction_for_conversational_scaffold_is_empty_or_minimal():
    """A conversational scaffold should produce no heavy formatting directives."""
    s = Scaffold(
        layout="conversational", opening="direct", separator=False,
        sections=[], emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "section" not in out.lower()
    assert "---" not in out  # no separator
    assert "Answer in the first sentence" in out


def test_instruction_for_structured_scaffold_includes_sections():
    s = Scaffold(
        layout="structured", opening="hook", separator=True,
        sections=[
            {"title": "Core Idea", "emoji": "🎯", "style": "prose"},
            {"title": "Tradeoffs",  "emoji": "⚖️", "style": "list"},
        ],
        emphasis="bold", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "RESPONSE FORMAT INSTRUCTIONS" in out
    assert "## 🎯 Core Idea" in out
    assert "## ⚖️ Tradeoffs" in out
    assert "---" in out
    assert "Open with 1-2 sentences" in out
    assert "Maximum 3 sentences per paragraph" in out
    assert "Bold exactly one key phrase" in out


def test_instruction_italic_emphasis_excludes_bold_directive():
    s = Scaffold(
        layout="prose", opening="direct", separator=False,
        sections=[], emphasis="italic", max_paragraph_sentences=3,
        list_threshold=3, width_hint="narrow",
    )
    out = scaffold_to_instruction(s)
    assert "italic" in out.lower()
    assert "Bold exactly" not in out
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 3 new tests FAIL with `ImportError: cannot import name 'scaffold_to_instruction'`.

- [ ] **Step 3: Implement `scaffold_to_instruction()`**

Append to the same section in `app/services/chat_engine.py` (just below the `Scaffold` dataclass):

```python
def scaffold_to_instruction(scaffold: Scaffold) -> str:
    """Serialize a Scaffold into a system-prompt fragment. See spec §5."""
    parts: List[str] = []

    # Opening
    if scaffold.opening == "hook":
        parts.append("Open with 1-2 sentences that frame the topic — no header, no preamble.")
    elif scaffold.opening == "direct":
        parts.append("Answer in the first sentence. No preamble.")
    elif scaffold.opening == "question":
        parts.append("Open with a single clarifying question only if the request is ambiguous; otherwise answer directly.")

    # Separator
    if scaffold.separator:
        parts.append("Add --- on its own line after the opening paragraph.")

    # Sections
    if scaffold.sections:
        section_lines = "\n".join(
            f"- ## {s.get('emoji', '').strip()} {s.get('title', '').strip()}".rstrip()
            for s in scaffold.sections
        )
        parts.append("Structure the response with these exact sections:\n" + section_lines)

    # Paragraph shape
    parts.append(f"Maximum {scaffold.max_paragraph_sentences} sentences per paragraph.")

    # Emphasis
    if scaffold.emphasis == "bold":
        parts.append("Bold exactly one key phrase per section. No more.")
    elif scaffold.emphasis == "italic":
        parts.append("Use italic for emphasis. No bold.")
    elif scaffold.emphasis == "none":
        parts.append("No bold. No italic.")

    # Lists
    parts.append(
        f"Only use bullet lists when there are {scaffold.list_threshold}+ parallel items."
    )

    return "RESPONSE FORMAT INSTRUCTIONS:\n" + "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/services/test_structure_scaffold.py
git commit -m "feat(chat): scaffold_to_instruction serializer for system prompt injection"
```

---

## Task 3: Task-type → Scaffold fallback map

**Files:**
- Modify: `app/services/chat_engine.py`
- Modify: `tests/services/test_structure_scaffold.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/services/test_structure_scaffold.py`:

```python
from app.services.chat_engine import _scaffold_from_task_type


def test_fallback_coding_task_maps_to_technical_wide():
    s = _scaffold_from_task_type("coding")
    assert s.layout == "technical"
    assert s.width_hint == "wide"
    assert s.opening == "direct"


def test_fallback_reasoning_task_maps_to_prose_medium():
    s = _scaffold_from_task_type("reasoning")
    assert s.layout == "prose"
    assert s.width_hint == "medium"
    assert s.opening == "hook"


def test_fallback_long_context_maps_to_long_prose_narrow():
    s = _scaffold_from_task_type("long_context")
    assert s.layout == "long-prose"
    assert s.width_hint == "narrow"


def test_fallback_unknown_task_returns_conversational_default():
    s = _scaffold_from_task_type("definitely-not-a-real-task-type")
    assert s.layout == "conversational"
    assert s.opening == "direct"


def test_fallback_never_returns_none_for_any_registered_task():
    """Per spec §4: fallback must always return a fully populated Scaffold."""
    from app.smart_router import detect_task_type  # imports its public vocabulary
    # Each task type from smart_router must have a non-None scaffold mapping.
    for task in ("coding", "reasoning", "vision", "image_gen",
                 "uncensored", "long_context", "chat"):
        s = _scaffold_from_task_type(task)
        assert s is not None
        assert s.layout in {
            "conversational", "prose", "long-prose", "technical",
            "code-heavy", "list-heavy", "structured",
        }
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 5 new tests FAIL with `ImportError: cannot import name '_scaffold_from_task_type'`.

- [ ] **Step 3: Implement the fallback map**

Append to `app/services/chat_engine.py` in the same scaffold section:

```python
# Task-type → default Scaffold fallback. Spec §4.
_TASK_TYPE_SCAFFOLD_DEFAULTS: Dict[str, Dict] = {
    "coding":       {"layout": "technical",      "opening": "direct", "width_hint": "wide"},
    "reasoning":    {"layout": "prose",          "opening": "hook",   "width_hint": "medium"},
    "image_gen":    {"layout": "conversational", "opening": "direct", "width_hint": "medium"},
    "vision":       {"layout": "prose",          "opening": "direct", "width_hint": "medium"},
    "uncensored":   {"layout": "prose",          "opening": "direct", "width_hint": "medium"},
    "long_context": {"layout": "long-prose",     "opening": "direct", "width_hint": "narrow"},
    "chat":         {"layout": "conversational", "opening": "direct", "width_hint": "medium"},
}


def _scaffold_from_task_type(task_type: str) -> Scaffold:
    """Derive a sensible default Scaffold when LLM classification is unavailable."""
    base = _TASK_TYPE_SCAFFOLD_DEFAULTS.get(task_type, _TASK_TYPE_SCAFFOLD_DEFAULTS["chat"])
    return Scaffold(
        layout=base["layout"],
        opening=base["opening"],
        separator=False,
        sections=[],
        emphasis="none",
        max_paragraph_sentences=3,
        list_threshold=3,
        width_hint=base["width_hint"],
    )
```

- [ ] **Step 4: Run and verify all tests pass**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 11 tests pass total.

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/services/test_structure_scaffold.py
git commit -m "feat(chat): task-type → default Scaffold fallback map"
```

---

## Task 4: Extend `classify_user_intent()` — unified prompt

**Files:**
- Modify: `app/services/chat_engine.py` (around line 566–646)
- Modify: `tests/services/test_structure_scaffold.py`

- [ ] **Step 1: Write failing tests that stub the HTTP call**

Append to `tests/services/test_structure_scaffold.py`:

```python
from unittest.mock import patch, MagicMock
from app.services import chat_engine


def _mk_openai_response(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": __import__("json").dumps(body)}}]}
    return resp


def test_classify_returns_scaffold_when_llm_succeeds(monkeypatch):
    chat_engine._intent_cache.clear()
    fake_body = {
        "intent_type": "creative_task",
        "has_crypto_mention": False,
        "has_stock_mention": False,
        "asset_symbol": None,
        "wants_chart": False,
        "wants_image": False,
        "wants_code": False,
        "scaffold": {
            "layout": "prose", "opening": "hook", "separator": False,
            "sections": [], "emphasis": "italic",
            "max_paragraph_sentences": 3, "list_threshold": 3,
            "width_hint": "narrow",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mk_openai_response(fake_body)
        result = chat_engine.classify_user_intent("write me a haiku about rain", use_cache=False)
    assert result["intent_type"] == "creative_task"
    assert "scaffold" in result
    assert isinstance(result["scaffold"], dict)
    assert result["scaffold"]["layout"] == "prose"
    assert result["scaffold"]["width_hint"] == "narrow"


def test_classify_falls_back_to_task_type_default_on_llm_failure(monkeypatch):
    chat_engine._intent_cache.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = chat_engine.classify_user_intent("write a python script", use_cache=False)
    # No LLM available → scaffold is still populated via task-type fallback.
    assert "scaffold" in result
    assert result["scaffold"] is not None
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }


def test_classify_scaffold_validates_before_returning(monkeypatch):
    """If the LLM returns an invalid layout, fall back — never propagate garbage."""
    chat_engine._intent_cache.clear()
    bad_body = {
        "intent_type": "general_chat",
        "scaffold": {
            "layout": "spaghetti",  # invalid
            "opening": "direct", "separator": False, "sections": [],
            "emphasis": "none", "max_paragraph_sentences": 3,
            "list_threshold": 3, "width_hint": "medium",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mk_openai_response(bad_body)
        result = chat_engine.classify_user_intent("hey", use_cache=False)
    assert result["scaffold"]["layout"] != "spaghetti"
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 3 new tests FAIL — `scaffold` key missing from return dict.

- [ ] **Step 3: Replace `classify_user_intent()` with the extended version**

In `app/services/chat_engine.py`, replace lines 566–646 with:

```python
def classify_user_intent(prompt: str, use_cache: bool = True) -> dict:
    """
    Classify user intent AND plan response scaffold in one LLM call.
    See spec §1, §3.

    Returns dict with (existing keys preserved):
    - intent_type, has_crypto_mention, has_stock_mention, asset_symbol,
      wants_chart, wants_image, wants_code, confidence
    NEW:
    - scaffold: dict | None — scaffold schema (spec §2). Non-None except in
      exceptional import/runtime failures; see spec §4.
    """
    if not prompt or len(prompt.strip()) < 3:
        return {"intent_type": "unknown", "confidence": 0, "scaffold": None}

    cache_key = hashlib.md5(prompt.lower().strip().encode()).hexdigest()
    if use_cache and cache_key in _intent_cache:
        cached, timestamp = _intent_cache[cache_key]
        if time.time() - timestamp < _intent_cache_ttl:
            return cached

    classification_prompt = f"""Analyze this user message. Respond ONLY with a JSON object.

User message: "{prompt}"

Respond with this exact JSON shape (no other text):
{{"intent_type": "<one of: financial_query, creative_task, question, recipe_request, code_request, image_request, general_chat>",
"has_crypto_mention": <bool>,
"has_stock_mention": <bool>,
"asset_symbol": "<symbol like BTC, ETH, AAPL or null>",
"wants_chart": <bool>, "wants_image": <bool>, "wants_code": <bool>,
"scaffold": {{
  "layout": "<one of: conversational, prose, long-prose, technical, code-heavy, list-heavy, structured>",
  "opening": "<hook|direct|question|none>",
  "separator": <bool>,
  "sections": [{{"title": "...", "emoji": "...", "style": "prose|list|steps"}}],
  "emphasis": "<bold|italic|none>",
  "max_paragraph_sentences": <2|3|4>,
  "list_threshold": <2|3|4>,
  "width_hint": "<narrow|medium|wide>"
}}}}

SCAFFOLD RULES:
- conversational + short reply → layout:conversational, no sections, no separator, emphasis:none
- emotional/supportive → layout:prose, emphasis:none, no sections
- factual + short → layout:prose, opening:direct, no sections
- code_request → layout:technical, opening:direct, width_hint:wide
- creative_task → layout:prose or long-prose, emphasis:italic, width_hint:narrow
- planning / multi-topic → layout:structured, opening:hook, separator:true, 2-4 sections
- comparison → layout:structured, opening:direct, 2+ sections

IMPORTANT: Words like "create/make/build" at the start usually mean creative_task, NOT cryptocurrency.
Only set has_crypto_mention=true if they're asking about crypto PRICES or MARKET data."""

    # Try OpenAI gpt-4.1-nano first (spec §3), fall back to Groq llama-3.1-8b-instant.
    providers = [
        ("openai", "https://api.openai.com/v1/chat/completions", "gpt-4.1-nano",
         os.environ.get("OPENAI_API_KEY")),
        ("groq", "https://api.groq.com/openai/v1/chat/completions", "llama-3.1-8b-instant",
         os.environ.get("GROQ_API_KEY")),
    ]

    task_type_hint = None  # populated by smart_router for fallback if LLM fails
    try:
        from app.smart_router import detect_task_type
        task_type_hint = detect_task_type(prompt)
    except Exception:
        pass

    import requests as req
    last_err: Optional[Exception] = None

    for name, url, model, key in providers:
        if not key:
            continue
        try:
            response = req.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": classification_prompt}],
                    "max_tokens": 250,
                    "temperature": 0.1,
                },
                timeout=1.5,  # spec §3 hard cap
            )
            if response.status_code != 200:
                last_err = Exception(f"{name} {response.status_code}")
                continue

            content = response.json()["choices"][0]["message"]["content"].strip()
            # Handle both plain JSON and JSON inside a markdown code fence.
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                last_err = Exception(f"{name} no JSON match")
                continue
            raw = json.loads(json_match.group())

            scaffold_dict = raw.get("scaffold")
            scaffold_obj: Optional[Scaffold] = None
            if isinstance(scaffold_dict, dict):
                try:
                    scaffold_obj = Scaffold.from_dict(scaffold_dict)
                except (TypeError, ValueError) as e:
                    logger.debug(f"Scaffold validation failed ({name}): {e}, using task-type default")
                    scaffold_obj = _scaffold_from_task_type(task_type_hint or "chat")
            else:
                scaffold_obj = _scaffold_from_task_type(task_type_hint or "chat")

            result = {
                "intent_type": raw.get("intent_type", "unknown"),
                "has_crypto_mention": bool(raw.get("has_crypto_mention", False)),
                "has_stock_mention": bool(raw.get("has_stock_mention", False)),
                "asset_symbol": raw.get("asset_symbol"),
                "wants_chart": bool(raw.get("wants_chart", False)),
                "wants_image": bool(raw.get("wants_image", False)),
                "wants_code": bool(raw.get("wants_code", False)),
                "confidence": 0.9,
                "scaffold": scaffold_obj.to_dict() if scaffold_obj else None,
            }
            _intent_cache[cache_key] = (result, time.time())
            logger.info(
                f"🧠 Intent: {result['intent_type']} / scaffold: "
                f"{scaffold_obj.layout if scaffold_obj else 'none'} via {name}"
            )
            return result
        except Exception as e:
            last_err = e
            logger.debug(f"Intent provider {name} failed: {e}")
            continue

    # All LLM providers exhausted → fallback to task-type-derived default.
    fallback_scaffold = _scaffold_from_task_type(task_type_hint or "chat")
    result = {
        "intent_type": "unknown",
        "confidence": 0,
        "scaffold": fallback_scaffold.to_dict(),
    }
    logger.info(f"🧠 Intent classification unavailable ({last_err}); using task-type fallback: "
                f"{fallback_scaffold.layout}")
    return result
```

Add at the top of the file if not already imported:
```python
import json
import re
import os
import time
import hashlib
```

- [ ] **Step 4: Run and verify tests pass**

Run: `pytest tests/services/test_structure_scaffold.py -v`
Expected: 14 tests pass total.

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/services/test_structure_scaffold.py
git commit -m "feat(chat): unified intent + scaffold classifier (spec §1 §3)"
```

---

## Task 5: CSS vocabulary consistency test

**Files:**
- Create: `tests/services/test_scaffold_css_vocabulary.py`

- [ ] **Step 1: Write the test**

Create `tests/services/test_scaffold_css_vocabulary.py`:

```python
"""Spec §8 risk: guard against Scaffold.layout drifting from CSS data-ctx rules."""
import re
from pathlib import Path
from app.services.chat_engine import ALLOWED_LAYOUTS


def test_every_allowed_layout_has_a_css_rule():
    css_path = Path(__file__).resolve().parents[2] / "app/static/css/stream-blocks.css"
    assert css_path.exists(), f"Missing CSS file: {css_path}"
    css = css_path.read_text(encoding="utf-8")
    defined_in_css = set(re.findall(r'\[data-ctx="([^"]+)"\]', css))
    missing = set(ALLOWED_LAYOUTS) - defined_in_css
    assert not missing, (
        f"Layouts in ALLOWED_LAYOUTS but NOT in stream-blocks.css: {missing}. "
        "Either add CSS rules or remove from ALLOWED_LAYOUTS."
    )
```

- [ ] **Step 2: Run and verify it passes against the current CSS**

Run: `pytest tests/services/test_scaffold_css_vocabulary.py -v`
Expected: PASS — all 7 allowed layouts (`conversational`, `prose`, `long-prose`, `technical`, `code-heavy`, `list-heavy`, `structured`) already have CSS rules at `stream-blocks.css:270–340`.

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_scaffold_css_vocabulary.py
git commit -m "test(chat): guard Scaffold layout vocabulary against CSS drift"
```

---

## Task 6: Wire scaffold into `/stream` route — SSE event + system prompt

**Files:**
- Modify: `app/blueprints/chat_routes.py` (around lines 999 and 1066)

- [ ] **Step 1: Call classifier and inject into `base_system` before `ContextBuilder.build()`**

In `app/blueprints/chat_routes.py`, find lines 999–1009:

```python
    base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)
    if system_addition:
        base_system = base_system + "\n\n" + system_addition
        logger.info(f"[Stream] system_addition applied ({len(system_addition)} chars)")
    built_context = context_builder.build(
        system_prompt=base_system,
        full_history=previous_messages,
        artifact_state=artifact_tracker if should_inject_artifact else None,
        user_message=prompt,
        memory_context=long_term_memory_stream or None,
    )
```

Insert a classification call + scaffold injection just before `built_context = ...`:

```python
    base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)
    if system_addition:
        base_system = base_system + "\n\n" + system_addition
        logger.info(f"[Stream] system_addition applied ({len(system_addition)} chars)")

    # --- Structure Intelligence (spec 2026-04-15) ---
    # Classify + plan scaffold once; use for both system-prompt injection
    # and the scaffold SSE event emitted below in generate().
    from app.services.chat_engine import (
        classify_user_intent as _classify_for_scaffold,
        scaffold_to_instruction as _scaffold_to_instr,
        Scaffold as _Scaffold,
    )
    try:
        _intent_envelope = _classify_for_scaffold(user_prompt_raw)
    except Exception as e:
        logger.debug(f"[Stream] scaffold classification errored, continuing without: {e}")
        _intent_envelope = {"scaffold": None}

    _scaffold_dict = _intent_envelope.get("scaffold")
    _scaffold_instruction = ""
    if _scaffold_dict:
        try:
            _scaffold_obj = _Scaffold.from_dict(_scaffold_dict)
            _scaffold_instruction = _scaffold_to_instr(_scaffold_obj)
            base_system = base_system + "\n\n" + _scaffold_instruction
            logger.info(f"[Stream] scaffold injected: layout={_scaffold_obj.layout}")
        except Exception as e:
            logger.debug(f"[Stream] scaffold materialization failed: {e}")
            _scaffold_dict = None

    built_context = context_builder.build(
        system_prompt=base_system,
        full_history=previous_messages,
        artifact_state=artifact_tracker if should_inject_artifact else None,
        user_message=prompt,
        memory_context=long_term_memory_stream or None,
    )
```

The `context_builder.build(...)` argument list is IDENTICAL to what was already at lines 1003-1009 — only `base_system` has been mutated above. If the existing arg list drifts from what is shown here, use what is on disk rather than this snippet.

- [ ] **Step 2: Emit scaffold SSE event as the absolute first event in `generate()`**

**Invariant (non-negotiable):** the `scaffold` event MUST be the first SSE event on every code path inside `generate()` — happy path AND action-router paths (web search, image gen, artifact flows). Before emitting ANY other `yield f"data: ..."`, the scaffold must have gone out.

Find line 1079 in the same file:

```python
        yield f"data: {json.dumps({'type': 'tts_voice', 'voice': tts_voice, 'lang': tts_lang})}\n\n"
```

Add the scaffold emit **before** this line — and then grep the rest of `generate()` for any `yield f"data:"` lines emitted *earlier* than this point (e.g., fast-path `search_started`, `artifact_*`, error events) and ensure the scaffold emit still precedes all of them.

```python
        # Spec §5: scaffold event emitted FIRST, before any other SSE event on
        # any code path (happy path, search fast-path, action router). If the
        # scaffold failed to materialize, skip and let the client fall back to
        # post-stream classifyContext().
        if _scaffold_dict:
            yield f"data: {json.dumps({'type': 'scaffold', **_scaffold_dict})}\n\n"

        yield f"data: {json.dumps({'type': 'tts_voice', 'voice': tts_voice, 'lang': tts_lang})}\n\n"
```

**Verification for this step:** grep every `yield f"data:` inside `generate()` and confirm none precede the scaffold yield:

```bash
grep -n 'yield f"data:' app/blueprints/chat_routes.py | head -20
```

Every hit should be at or after the line where scaffold is emitted. If an action-router branch (`search_started`, `search_result`, image_gen, artifact) has its own early `yield` that fires before scaffold, move the scaffold emit even earlier — or route the scaffold emit through a helper that runs at `generate()` entry.

- [ ] **Step 3: Syntax-check and run existing test suite**

Run: `python -c "import app.blueprints.chat_routes"`
Expected: no ImportError / syntax error.

Run: `pytest tests/services/test_structure_scaffold.py tests/services/test_scaffold_css_vocabulary.py -v`
Expected: all previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(chat): wire scaffold into /stream — SSE event + system prompt injection"
```

---

## Task 7: Client — handle `chunk.type === 'scaffold'` and set `data-ctx` early

**Files:**
- Modify: `app/static/js/codeWindow.js` (around line 2397)

- [ ] **Step 1: Add scaffold handler to the SSE parser**

In `app/static/js/codeWindow.js`, find the existing `processLine` function at line 2397:

```javascript
                const processLine = (line) => {
                    if (!line.startsWith('data: ')) return;
                    const jsonStr = line.slice(6);

                    try {
                        const chunk = JSON.parse(jsonStr);

                        if (chunk.type === 'reasoning') {
                            ...
                        } else if (chunk.type === 'content') {
                            ...
```

Add a new `scaffold` branch **above** the `reasoning` branch (so it's handled before anything content-adjacent):

```javascript
                const processLine = (line) => {
                    if (!line.startsWith('data: ')) return;
                    const jsonStr = line.slice(6);

                    try {
                        const chunk = JSON.parse(jsonStr);

                        if (chunk.type === 'scaffold') {
                            // Spec §5: server decided structure before streaming.
                            // Set data-ctx immediately on the active message wrapper
                            // so CSS (stream-blocks.css) adapts width/rhythm before
                            // the first content token renders.
                            try {
                                const wrapper = document.querySelector(
                                    '.message.ai-message.streaming .text-message-container'
                                ) || document.querySelector('.message.ai-message:last-of-type .text-message-container');
                                if (wrapper) {
                                    wrapper.dataset.ctx = chunk.layout;
                                    wrapper.dataset.ctxSource = 'server';  // marker for Task 8
                                    const msgEl = wrapper.closest('.message, .ai-message');
                                    if (msgEl) {
                                        msgEl.dataset.ctx = chunk.layout;
                                        msgEl.dataset.ctxSource = 'server';
                                    }
                                }
                                console.log('[scaffold] server layout:', chunk.layout);
                            } catch (e) {
                                console.warn('[scaffold] failed to apply data-ctx', e);
                            }
                            return;
                        }

                        if (chunk.type === 'reasoning') {
                            // ...existing reasoning branch — keep as currently written in
                            // codeWindow.js:2404 and downward. Do NOT modify it.
```

- [ ] **Step 2: Smoke-check (no server required)**

Run: `node -e "const f=require('fs').readFileSync('app/static/js/codeWindow.js','utf8'); if(f.indexOf(\"chunk.type === 'scaffold'\") === -1) process.exit(1); console.log('ok')"`
Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(stream): client handles 'scaffold' SSE event, sets data-ctx pre-stream"
```

---

## Task 8: Client — post-stream classifier defers when server already set `data-ctx`

**Files:**
- Modify: `app/static/js/streamRenderer.js` (around line 178–184)

- [ ] **Step 1: Gate the post-stream classifier**

In `app/static/js/streamRenderer.js`, find lines 178–184:

```javascript
        try {
            const profile = buildMessageProfile(this.ast.blocks());
            const ctx = classifyContext(profile);
            if (this.wrapperEl) this.wrapperEl.dataset.ctx = ctx;
            const messageEl = this.wrapperEl && this.wrapperEl.closest('.message, .ai-message');
            if (messageEl) messageEl.dataset.ctx = ctx;
        } catch (_e) { /* non-critical */ }
```

Replace with:

```javascript
        try {
            // If the server already set data-ctx via the scaffold SSE event
            // (spec §5), don't overwrite it — the server's pre-stream decision
            // is authoritative. Post-stream classifyContext() remains as fallback
            // when no scaffold event arrived (spec §4 step 3).
            const serverSet = this.wrapperEl && this.wrapperEl.dataset.ctxSource === 'server';
            if (!serverSet) {
                const profile = buildMessageProfile(this.ast.blocks());
                const ctx = classifyContext(profile);
                if (this.wrapperEl) this.wrapperEl.dataset.ctx = ctx;
                const messageEl = this.wrapperEl && this.wrapperEl.closest('.message, .ai-message');
                if (messageEl) messageEl.dataset.ctx = ctx;
            }
        } catch (_e) { /* non-critical */ }
```

- [ ] **Step 2: Smoke-check**

Run: `node -e "const f=require('fs').readFileSync('app/static/js/streamRenderer.js','utf8'); if(f.indexOf('ctxSource') === -1) process.exit(1); console.log('ok')"`
Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/static/js/streamRenderer.js
git commit -m "feat(stream): post-stream classifier defers to server scaffold when present"
```

---

## Task 9: End-to-end integration test (manual + scripted)

**Files:**
- Create: `tests/integration/test_scaffold_stream_e2e.py`

- [ ] **Step 1: Write a scripted integration test**

Create `tests/integration/test_scaffold_stream_e2e.py`:

```python
"""End-to-end: the /stream endpoint emits a scaffold event first, then content."""
import json
import pytest
from app import create_app


@pytest.fixture
def client(monkeypatch):
    # Force the classifier path that doesn't need a live LLM by zeroing keys.
    # With no keys, classify_user_intent falls back to task-type default, which
    # still produces a valid scaffold — good enough to verify plumbing.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _collect_sse_types(resp_iter, limit_events=5):
    """Pull up to `limit_events` SSE data: lines and return the parsed 'type' field."""
    types = []
    buf = b""
    for chunk in resp_iter:
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if line.startswith(b"data: "):
                    try:
                        payload = json.loads(line[6:].decode())
                        types.append(payload.get("type"))
                    except Exception:
                        pass
        if len(types) >= limit_events:
            break
    return types


def test_scaffold_event_arrives_before_content(client):
    resp = client.post("/stream", data={"prompt": "hello", "model": "auto"}, buffered=False)
    types = _collect_sse_types(resp.response, limit_events=3)
    # Either scaffold-first-then-tts, or tts-first when scaffold unavailable.
    # What we MUST see: if scaffold is present, it comes before any 'content'.
    if "scaffold" in types:
        assert types.index("scaffold") < (
            types.index("content") if "content" in types else 999
        ), f"scaffold must precede content, got order: {types}"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/integration/test_scaffold_stream_e2e.py -v`
Expected: PASS. If the route path or app factory differs, adjust the fixture — the assertion logic itself is the acceptance criterion.

- [ ] **Step 3: Manual browser sanity check**

1. Start the dev server: `flask run` (or the project's normal dev command).
2. Open the chat UI in a browser with DevTools → Network.
3. Send: `"plan a 3-day Tokyo trip with food, sights, and a day trip"`.
4. In Network, find the `/stream` response, inspect the event stream, and verify:
   - First `data: {...}` line has `"type": "scaffold"` with `"layout": "structured"` (expected) and `"separator": true`
   - Message element in DOM has `data-ctx="structured"` and `data-ctx-source="server"` **before** any content paints
5. Send: `"hey what's up"` — expect `layout: conversational`, no separator.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_scaffold_stream_e2e.py
git commit -m "test(stream): e2e — scaffold SSE event precedes content"
```

---

## Task 10: Intent-accuracy regression measurement (spec §3 guardrail)

**⚠️ Execution order note:** Step 2 of this task (baseline measurement on `main`) MUST run **before** Task 4's changes are merged. If you execute Task 4 first, you lose the ability to measure regression — the baseline no longer exists. If you realize partway through that Task 4 is already merged without a recorded baseline, revert Task 4 temporarily, record the baseline, then re-apply Task 4.

**Files:**
- Create: `tests/services/test_intent_accuracy.py`

- [ ] **Step 1: Write a fixture-driven accuracy test**

Create `tests/services/test_intent_accuracy.py`:

```python
"""Spec §3 guardrail: unified prompt must not regress intent accuracy by more than
5 percentage points absolute on a representative fixture set. If this fails,
refactor classify_user_intent() into two sequential internal calls (spec §3
split-call variant)."""
import os
import pytest
from app.services import chat_engine


# Representative fixture — each (prompt, expected_intent). Cover the
# seven intent labels the existing classifier emits.
FIXTURES = [
    ("what's BTC trading at today?",              "financial_query"),
    ("ETH price chart please",                    "financial_query"),
    ("write me a haiku about the ocean",          "creative_task"),
    ("compose a short poem about stars",          "creative_task"),
    ("why is the sky blue?",                      "question"),
    ("explain RAG like I'm five",                 "question"),
    ("give me a recipe for chicken curry",        "recipe_request"),
    ("how do I make pancakes?",                   "recipe_request"),
    ("write a python script to sort a list",      "code_request"),
    ("show me a for-loop in javascript",          "code_request"),
    ("draw me a picture of a dragon",             "image_request"),
    ("generate an image of a sunset",             "image_request"),
    ("hey, how's it going?",                      "general_chat"),
    ("good morning!",                             "general_chat"),
]

PASS_THRESHOLD_PP = 5  # percentage points absolute drop allowed (spec §3)


@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="Requires LLM access for accuracy measurement",
)
def test_intent_accuracy_on_fixture_set():
    """Not a pass/fail gate — logs the rate and stores it for comparison.

    CI should record this value and compare manually against the documented
    baseline (see design doc §3). If new_rate < baseline_rate - 5pp, refactor
    to split-call variant.
    """
    chat_engine._intent_cache.clear()
    hits = 0
    total = len(FIXTURES)
    for prompt, expected in FIXTURES:
        result = chat_engine.classify_user_intent(prompt, use_cache=False)
        got = result.get("intent_type")
        if got == expected:
            hits += 1
        else:
            print(f"  miss: {prompt!r} → expected {expected!r} got {got!r}")

    rate = (hits / total) * 100
    print(f"\nINTENT ACCURACY: {hits}/{total} = {rate:.1f}%")

    # Documented baseline before unified prompt: record manually after first
    # run against main (pre-change). Assertion here is ONLY the spec §3
    # guardrail — does unified accuracy meet a minimum floor?
    MIN_ACCEPTABLE_FLOOR = 70.0  # percent — absolute floor regardless of baseline
    assert rate >= MIN_ACCEPTABLE_FLOOR, (
        f"Unified prompt dropped intent accuracy to {rate:.1f}% "
        f"(floor {MIN_ACCEPTABLE_FLOOR}%). Per spec §3, refactor "
        "classify_user_intent() into two sequential internal LLM calls: "
        "(1) original intent prompt, (2) scaffold-only prompt."
    )
```

- [ ] **Step 2: Record baseline before the change**

Before the Task 4 changes are merged, or by checking out `main` briefly:

```bash
git stash        # park current work
git checkout main
pytest tests/services/test_intent_accuracy.py::test_intent_accuracy_on_fixture_set -v -s > /tmp/baseline_accuracy.txt || true
git checkout -
git stash pop
```

Open `/tmp/baseline_accuracy.txt` and record the printed `INTENT ACCURACY: X/Y = Z.Z%` value in the design-doc §3 as `BASELINE`.

- [ ] **Step 3: Run the test against the unified version**

Run: `pytest tests/services/test_intent_accuracy.py -v -s`
Expected: PASS (rate above 70% floor AND within 5pp of recorded baseline).

- [ ] **Step 4: Decide split-call fork (conditional)**

- If accuracy rate ≥ `baseline - 5pp`: **no action needed, unified prompt ships**.
- If accuracy rate < `baseline - 5pp`: stop, file a follow-up task to split `classify_user_intent()` into two sequential internal calls. The public return shape does not change; only the implementation does. See spec §3 for the split-call design.

- [ ] **Step 5: Commit**

```bash
git add tests/services/test_intent_accuracy.py
git commit -m "test(chat): intent accuracy regression guard for unified scaffold prompt"
```

---

## Task 11: Final verification pass

- [ ] **Step 1: Full test suite**

Run: `pytest tests/services/test_structure_scaffold.py tests/services/test_scaffold_css_vocabulary.py tests/services/test_intent_accuracy.py tests/integration/test_scaffold_stream_e2e.py -v`
Expected: all pass.

- [ ] **Step 2: Manual UX check (4 acceptance cases from spec §7)**

Start dev server; send each of these four and inspect the `data-ctx` rendered:

| Prompt | Expected data-ctx | Expected behavior |
|---|---|---|
| `"hey what's up"` | `conversational` | Narrow column, no sections, direct reply |
| `"explain how RAG works"` | `prose` or `long-prose` | Narrow column, 1-2 sentence hook opening |
| `"write a python script to parse CSV"` | `technical` | Wide column, direct opening, no preamble |
| `"plan a 3-day Tokyo trip with food, sights, and a day trip"` | `structured` | Hook opening, `---` separator, ≥3 `## emoji Title` sections |

For each, confirm in DevTools that `data-ctx-source="server"` is present (proves the scaffold event arrived and was honored — not the post-stream fallback).

- [ ] **Step 3: Fallback verification**

In a second browser session, temporarily unset both API keys in the dev server env, restart, and send `"hey what's up"`. Verify:
- No `scaffold` SSE event arrives (check Network)
- Message still renders normally, post-stream `classifyContext()` sets a `data-ctx` value after finalize
- No JS console errors

- [ ] **Step 4: Commit final verification notes**

If everything passes, no further commit needed — the plan's feature flag (spec §9, `STRUCTURE_INTELLIGENCE_ENABLED`) can be introduced in a follow-up if rollback is ever needed. For v1, behavior is already backward-compatible by construction.

---

## Done when

- ✅ All tests green (`test_structure_scaffold`, `test_scaffold_css_vocabulary`, `test_intent_accuracy`, `test_scaffold_stream_e2e`)
- ✅ Four acceptance prompts render their expected `data-ctx` with `data-ctx-source="server"`
- ✅ Fallback path verified (no keys → no scaffold event → post-stream classifier still works, no regression)
- ✅ Intent accuracy within 5 pp of baseline (or split-call refactor merged)
- ✅ No JavaScript console errors, no Python stack traces in server logs
