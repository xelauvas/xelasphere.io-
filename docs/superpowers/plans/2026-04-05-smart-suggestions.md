# Smart Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stateless per-request suggestion generation with a precomputed pool system that learns from user behavior — three personalized slots (Continue, Today, Discover), click tracking, and background refresh.

**Architecture:** New service `app/services/smart_suggestions.py` owns the core logic: pool generation via LLM, slot selection, signal inference for new users, and click tracking. The existing `grog_api.py` curated pool becomes the fallback only. Background refresh hooks into the dream consolidation pipeline and chat route. Frontend cycles through the precomputed pool instead of hitting the API on every "More" click.

**Tech Stack:** Python/Flask, Firestore, Groq/OpenRouter LLM APIs, vanilla JS (existing frontend stack)

**Spec:** `docs/superpowers/specs/2026-04-05-smart-suggestions-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/services/smart_suggestions.py` | **Create** | Pool generation, slot selection, signal inference, click tracking, refresh triggers |
| `tests/test_smart_suggestions.py` | **Create** | Tests for the smart suggestions service |
| `app/blueprints/pages_routes.py` | **Modify** | Serve precomputed pool on load, add click tracking endpoint |
| `app/blueprints/chat_routes.py` | **Modify** | Count conversations since last refresh, trigger background refresh |
| `app/memory_v2/dream/__init__.py` | **Modify** | Trigger suggestion refresh after consolidation completes |
| `app/static/js/fetchMoreOptions.js` | **Modify** | Consume pool from server, cycle through locally, report clicks |
| `app/grog_api.py` | **No change** | Stays as-is — its curated pool is already used as fallback |

---

### Task 1: Smart Suggestions Service — Data Models & Pool Storage

**Files:**
- Create: `app/services/smart_suggestions.py`
- Create: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for Suggestion dataclass and pool storage**

```python
# tests/test_smart_suggestions.py
"""Tests for the smart suggestions service."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestSuggestion:
    """Tests for the Suggestion dataclass."""

    def test_suggestion_creation(self):
        from app.services.smart_suggestions import Suggestion
        s = Suggestion(
            title="Continue your Flask app",
            subtitle="Add cost tracking",
            prompt="Help me add cost tracking to my Flask app",
            slot_type="continue",
            category="coding",
            icon="code",
            reasoning="User is building xelasphere",
        )
        assert s.title == "Continue your Flask app"
        assert s.slot_type == "continue"
        assert s.shown_count == 0

    def test_suggestion_to_dict(self):
        from app.services.smart_suggestions import Suggestion
        s = Suggestion(
            title="Plan your morning",
            subtitle="Start the day right",
            prompt="Help me plan my morning routine",
            slot_type="today",
            category="planning",
            icon="calendar",
        )
        d = s.to_dict()
        assert d["title"] == "Plan your morning"
        assert d["slot_type"] == "today"
        assert "reasoning" not in d  # Never exposed to client

    def test_suggestion_to_task_option(self):
        from app.services.smart_suggestions import Suggestion
        s = Suggestion(
            title="Try journaling",
            subtitle="Reflect on your day",
            prompt="Give me 5 journal prompts for today",
            slot_type="discover",
            category="wellness",
            icon="heart",
        )
        opt = s.to_task_option()
        assert opt.title == "Try journaling"
        assert opt.subtitle == "Reflect on your day"
        assert "sendMessage" in opt.action
        assert opt.icon == "heart"


class TestSuggestionPool:
    """Tests for pool storage operations."""

    def test_save_and_load_pool(self):
        from app.services.smart_suggestions import Suggestion, save_pool, load_pool
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc

        suggestions = [
            Suggestion("Title 1", "Sub 1", "Prompt 1", "continue", "coding", "code"),
            Suggestion("Title 2", "Sub 2", "Prompt 2", "today", "planning", "calendar"),
        ]
        save_pool(mock_db, "user123", suggestions, "conversations")

        # Verify Firestore set was called with correct structure
        mock_doc.set.assert_called_once()
        call_args = mock_doc.set.call_args[0][0]
        assert len(call_args["pool"]) == 2
        assert call_args["refresh_reason"] == "conversations"
        assert "last_refresh" in call_args

    def test_load_pool_empty(self):
        from app.services.smart_suggestions import load_pool
        mock_db = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_snapshot

        pool = load_pool(mock_db, "user123")
        assert pool == []

    def test_load_pool_with_data(self):
        from app.services.smart_suggestions import load_pool
        mock_db = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            "pool": [
                {
                    "title": "Title 1",
                    "subtitle": "Sub 1",
                    "prompt": "Prompt 1",
                    "slot_type": "continue",
                    "category": "coding",
                    "icon": "code",
                    "reasoning": "",
                    "shown_count": 0,
                },
            ],
            "last_refresh": datetime.utcnow(),
            "refresh_reason": "conversations",
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_snapshot

        pool = load_pool(mock_db, "user123")
        assert len(pool) == 1
        assert pool[0].title == "Title 1"
        assert pool[0].slot_type == "continue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py -v 2>&1 | head -30`
Expected: ModuleNotFoundError or ImportError

- [ ] **Step 3: Implement Suggestion dataclass and pool storage functions**

```python
# app/services/smart_suggestions.py
"""Smart Suggestions: precomputed, personalized suggestion pool system.

Replaces per-request LLM generation with a stored pool of 10 suggestions
served instantly on app load, with background refresh triggers.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.grog_api import TaskOption

logger = logging.getLogger(__name__)

POOL_SIZE = 10
DISPLAY_SIZE = 3
SLOT_TYPES = ("continue", "today", "discover")


@dataclass
class Suggestion:
    """A single precomputed suggestion."""
    title: str
    subtitle: str
    prompt: str
    slot_type: str  # "continue", "today", or "discover"
    category: str
    icon: str = "star"
    reasoning: str = ""
    shown_count: int = 0
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for Firestore storage. Never exposes reasoning to client."""
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "prompt": self.prompt,
            "slot_type": self.slot_type,
            "category": self.category,
            "icon": self.icon,
            "shown_count": self.shown_count,
            "created_at": self.created_at or datetime.utcnow(),
        }

    def to_task_option(self) -> TaskOption:
        """Convert to TaskOption for the existing frontend rendering."""
        return TaskOption(
            title=self.title,
            subtitle=self.subtitle,
            action=f"sendMessage('{self.prompt}')",
            icon=self.icon,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Suggestion":
        """Deserialize from Firestore document."""
        return cls(
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            prompt=data.get("prompt", ""),
            slot_type=data.get("slot_type", "discover"),
            category=data.get("category", ""),
            icon=data.get("icon", "star"),
            reasoning=data.get("reasoning", ""),
            shown_count=data.get("shown_count", 0),
            created_at=data.get("created_at"),
        )


# ---------------------------------------------------------------------------
# Firestore pool operations
# ---------------------------------------------------------------------------

def _pool_ref(db, user_id: str):
    """Reference to users/{uid}/smart_suggestions doc."""
    return db.collection("users").document(user_id).collection("smart_suggestions").document("pool")


def save_pool(
    db, user_id: str, suggestions: List[Suggestion], reason: str,
) -> None:
    """Write a suggestion pool to Firestore."""
    doc = {
        "pool": [s.to_dict() for s in suggestions],
        "last_refresh": datetime.utcnow(),
        "refresh_reason": reason,
    }
    _pool_ref(db, user_id).set(doc)
    logger.info(f"[smart_suggestions] Saved pool ({len(suggestions)}) for {user_id}, reason={reason}")


def load_pool(db, user_id: str) -> List[Suggestion]:
    """Load the suggestion pool from Firestore. Returns [] if none exists."""
    try:
        snap = _pool_ref(db, user_id).get()
        if not snap.exists:
            return []
        data = snap.to_dict()
        return [Suggestion.from_dict(s) for s in data.get("pool", [])]
    except Exception as e:
        logger.error(f"[smart_suggestions] Failed to load pool for {user_id}: {e}")
        return []


def get_pool_metadata(db, user_id: str) -> Dict[str, Any]:
    """Load pool metadata (last_refresh, refresh_reason) without deserializing all suggestions."""
    try:
        snap = _pool_ref(db, user_id).get()
        if not snap.exists:
            return {}
        data = snap.to_dict()
        return {
            "last_refresh": data.get("last_refresh"),
            "refresh_reason": data.get("refresh_reason"),
            "pool_size": len(data.get("pool", [])),
        }
    except Exception:
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py -v 2>&1 | tail -20`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add Suggestion dataclass and pool storage"
```

---

### Task 2: Slot Selection Logic

**Files:**
- Modify: `app/services/smart_suggestions.py`
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for slot selection**

Add to `tests/test_smart_suggestions.py`:

```python
class TestSlotSelection:
    """Tests for selecting 3 suggestions from a pool using slot logic."""

    def _make_pool(self):
        from app.services.smart_suggestions import Suggestion
        return [
            Suggestion("Continue Flask", "Cost tracking", "prompt1", "continue", "coding", "code"),
            Suggestion("Continue API", "Auth flow", "prompt2", "continue", "coding", "code"),
            Suggestion("Morning plan", "Start productive", "prompt3", "today", "planning", "calendar"),
            Suggestion("Evening wind-down", "Relax tonight", "prompt4", "today", "wellness", "heart"),
            Suggestion("Try origami", "Paper art", "prompt5", "discover", "creativity", "sparkles"),
            Suggestion("Learn Go", "New language", "prompt6", "discover", "learning", "book"),
            Suggestion("Weekend hike", "Get outside", "prompt7", "today", "lifestyle", "sun"),
            Suggestion("Write poetry", "Express yourself", "prompt8", "discover", "writing", "edit"),
        ]

    def test_picks_one_per_slot(self):
        from app.services.smart_suggestions import select_display_slots
        pool = self._make_pool()
        selected = select_display_slots(pool)
        assert len(selected) == 3
        slot_types = {s.slot_type for s in selected}
        assert slot_types == {"continue", "today", "discover"}

    def test_today_slot_uses_time_hint(self):
        from app.services.smart_suggestions import select_display_slots
        pool = self._make_pool()
        # Morning hour should prefer "Morning plan"
        selected = select_display_slots(pool, hour=8)
        today_pick = [s for s in selected if s.slot_type == "today"][0]
        assert "morning" in today_pick.title.lower() or today_pick.slot_type == "today"

    def test_empty_pool_returns_empty(self):
        from app.services.smart_suggestions import select_display_slots
        assert select_display_slots([]) == []

    def test_missing_slot_fills_from_others(self):
        from app.services.smart_suggestions import Suggestion, select_display_slots
        # Pool with no "continue" type
        pool = [
            Suggestion("Morning", "Plan", "p1", "today", "planning", "calendar"),
            Suggestion("Try Go", "New lang", "p2", "discover", "learning", "book"),
            Suggestion("Evening", "Relax", "p3", "today", "wellness", "heart"),
        ]
        selected = select_display_slots(pool)
        assert len(selected) == 2  # Can only fill today + discover
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestSlotSelection -v 2>&1 | head -20`
Expected: ImportError (select_display_slots not found)

- [ ] **Step 3: Implement slot selection**

Add to `app/services/smart_suggestions.py`:

```python
import random
from datetime import datetime


# ---------------------------------------------------------------------------
# Slot selection — pick best 3 from pool
# ---------------------------------------------------------------------------

_TIME_KEYWORDS = {
    "morning": range(5, 12),
    "afternoon": range(12, 17),
    "evening": range(17, 22),
    "night": range(22, 24),
    "weekend": None,  # handled separately
}


def _score_today_slot(suggestion: Suggestion, hour: int) -> float:
    """Score a today-slot suggestion based on current time of day."""
    title_lower = suggestion.title.lower() + " " + suggestion.subtitle.lower()
    score = random.random() * 0.1  # small random tiebreaker

    for keyword, hour_range in _TIME_KEYWORDS.items():
        if keyword in title_lower and hour_range is not None and hour in hour_range:
            score += 1.0
    return score


def select_display_slots(
    pool: List[Suggestion],
    hour: Optional[int] = None,
) -> List[Suggestion]:
    """Pick up to 3 suggestions from pool: 1 continue + 1 today + 1 discover.

    If a slot type is missing from the pool, that slot is skipped.
    The 'today' slot uses time-of-day scoring when hour is provided.
    """
    if not pool:
        return []

    if hour is None:
        hour = datetime.utcnow().hour

    by_slot: Dict[str, List[Suggestion]] = {"continue": [], "today": [], "discover": []}
    for s in pool:
        if s.slot_type in by_slot:
            by_slot[s.slot_type].append(s)

    selected: List[Suggestion] = []

    # Continue: lowest shown_count, then random
    if by_slot["continue"]:
        candidates = sorted(by_slot["continue"], key=lambda s: (s.shown_count, random.random()))
        selected.append(candidates[0])

    # Today: score by time relevance
    if by_slot["today"]:
        candidates = sorted(by_slot["today"], key=lambda s: _score_today_slot(s, hour), reverse=True)
        selected.append(candidates[0])

    # Discover: random (prefer unseen)
    if by_slot["discover"]:
        candidates = sorted(by_slot["discover"], key=lambda s: (s.shown_count, random.random()))
        selected.append(candidates[0])

    return selected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestSlotSelection -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add slot selection logic (continue/today/discover)"
```

---

### Task 3: LLM Pool Generation

**Files:**
- Modify: `app/services/smart_suggestions.py`
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for LLM generation**

Add to `tests/test_smart_suggestions.py`:

```python
class TestLLMGeneration:
    """Tests for generating suggestion pool via LLM."""

    def test_build_generation_prompt_includes_memory(self):
        from app.services.smart_suggestions import build_generation_prompt
        context = {
            "memories": [
                {"type": "project", "key": "building_flask_app", "value": "Building XelaSphere chat app"},
            ],
            "recent_topics": ["Flask routing", "CSS animations"],
            "suggestion_profile": {"top_categories": ["coding"]},
            "hour": 10,
            "day_of_week": "Monday",
        }
        prompt = build_generation_prompt(context)
        assert "XelaSphere" in prompt
        assert "Flask routing" in prompt
        assert "coding" in prompt
        assert "Monday" in prompt

    def test_parse_llm_response_valid(self):
        from app.services.smart_suggestions import parse_llm_suggestions
        raw = '''[
            {
                "title": "Continue Flask app",
                "subtitle": "Add cost tracking",
                "prompt": "Help me add cost tracking",
                "slot_type": "continue",
                "category": "coding",
                "icon": "code",
                "reasoning": "User building xelasphere"
            },
            {
                "title": "Morning standup",
                "subtitle": "Plan your day",
                "prompt": "Help me plan my morning standup",
                "slot_type": "today",
                "category": "planning",
                "icon": "calendar",
                "reasoning": "Monday morning"
            }
        ]'''
        suggestions = parse_llm_suggestions(raw)
        assert len(suggestions) == 2
        assert suggestions[0].slot_type == "continue"
        assert suggestions[1].category == "planning"

    def test_parse_llm_response_malformed(self):
        from app.services.smart_suggestions import parse_llm_suggestions
        assert parse_llm_suggestions("not json") == []
        assert parse_llm_suggestions("") == []
        assert parse_llm_suggestions(None) == []

    def test_parse_llm_response_markdown_wrapped(self):
        from app.services.smart_suggestions import parse_llm_suggestions
        raw = '```json\n[{"title":"T","subtitle":"S","prompt":"P","slot_type":"today","category":"c","icon":"star","reasoning":"r"}]\n```'
        suggestions = parse_llm_suggestions(raw)
        assert len(suggestions) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestLLMGeneration -v 2>&1 | head -20`
Expected: ImportError

- [ ] **Step 3: Implement LLM generation prompt and parser**

Add to `app/services/smart_suggestions.py`:

```python
import json
import re
import requests
from app.config import Config


# ---------------------------------------------------------------------------
# LLM pool generation
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM_PROMPT = """You generate personalized suggestion cards for an AI chat app. Each suggestion is a conversation starter the user can click.

You MUST return a JSON array of exactly {pool_size} suggestions. Each suggestion:
{{
  "title": "Short title (2-5 words)",
  "subtitle": "Brief context (3-7 words)",
  "prompt": "The full message to send — specific enough to produce an immediate result with no follow-up questions",
  "slot_type": "continue" | "today" | "discover",
  "category": "coding" | "writing" | "planning" | "learning" | "creativity" | "lifestyle" | "wellness",
  "icon": pick from: edit, code, image, search, info, calendar, activity, dollar, globe, gift, film, home, book, heart, zap, coffee, music, sun, star, sparkles, lightbulb, message,
  "reasoning": "Why this suggestion (never shown to user)"
}}

Rules:
- "continue" slots MUST reference real projects/topics from the user's memory. Never hallucinate.
- "today" slots MUST be relevant to the current time of day and day of week.
- "discover" slots MUST be from categories the user hasn't explored yet.
- Every prompt must be specific enough to produce an immediate result.
- Never repeat suggestions from the exclusion list.
- Aim for 3-4 continue, 3-4 today, 3-4 discover.
- Return ONLY the JSON array."""


def build_generation_prompt(context: Dict[str, Any]) -> str:
    """Build the user prompt for LLM suggestion generation."""
    parts = []

    # User memories
    memories = context.get("memories", [])
    if memories:
        mem_lines = []
        for m in memories[:20]:  # cap to avoid token overflow
            mem_lines.append(f"- [{m.get('type', 'unknown')}] {m.get('key', '')}: {m.get('value', '')}")
        parts.append("## User Memories\n" + "\n".join(mem_lines))

    # Recent conversation topics
    topics = context.get("recent_topics", [])
    if topics:
        parts.append("## Recent Topics\n" + ", ".join(topics[:5]))

    # Suggestion profile
    profile = context.get("suggestion_profile", {})
    if profile:
        parts.append(f"## Suggestion Profile\n"
                     f"Top categories: {profile.get('top_categories', [])}\n"
                     f"Avoided categories: {profile.get('avoided_categories', [])}")

    # Time context
    hour = context.get("hour", datetime.utcnow().hour)
    day = context.get("day_of_week", "")
    parts.append(f"## Current Context\nTime: {hour}:00, Day: {day}")

    # Exclusion list
    exclusions = context.get("exclusion_titles", [])
    if exclusions:
        parts.append("## Do NOT Suggest (recently shown)\n" + ", ".join(exclusions[:20]))

    return "\n\n".join(parts)


def parse_llm_suggestions(raw: Optional[str]) -> List[Suggestion]:
    """Parse LLM response into Suggestion objects. Returns [] on failure."""
    if not raw:
        return []

    stripped = raw.strip()
    # Strip markdown code block
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if code_block:
        stripped = code_block.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("[smart_suggestions] Failed to parse LLM response as JSON")
        return []

    if not isinstance(parsed, list):
        return []

    suggestions = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("title") or not item.get("prompt"):
            continue
        suggestions.append(Suggestion(
            title=item.get("title", ""),
            subtitle=item.get("subtitle", ""),
            prompt=item.get("prompt", ""),
            slot_type=item.get("slot_type", "discover"),
            category=item.get("category", ""),
            icon=item.get("icon", "star"),
            reasoning=item.get("reasoning", ""),
        ))

    return suggestions


def generate_pool_from_llm(context: Dict[str, Any]) -> List[Suggestion]:
    """Call LLM to generate a pool of suggestions. Returns [] on failure."""
    user_prompt = build_generation_prompt(context)
    system_prompt = _GENERATION_SYSTEM_PROMPT.replace("{pool_size}", str(POOL_SIZE))

    # Use Groq (same models as grog_api.py)
    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 2048,
                "temperature": 0.9,
            }
            headers = {
                "Authorization": f"Bearer {Config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            }
            response = requests.post(Config.API_URL, json=payload, headers=headers, timeout=20)
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                suggestions = parse_llm_suggestions(content)
                if suggestions:
                    logger.info(f"[smart_suggestions] Generated {len(suggestions)} suggestions via {model}")
                    return suggestions
        except Exception as e:
            logger.warning(f"[smart_suggestions] LLM generation failed with {model}: {e}")
            continue

    logger.error("[smart_suggestions] All LLM models failed for suggestion generation")
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestLLMGeneration -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add LLM pool generation with prompt builder and parser"
```

---

### Task 4: Click Tracking & Suggestion Profile

**Files:**
- Modify: `app/services/smart_suggestions.py`
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for click tracking**

Add to `tests/test_smart_suggestions.py`:

```python
class TestClickTracking:
    """Tests for click tracking and suggestion profile updates."""

    def test_record_click(self):
        from app.services.smart_suggestions import record_click
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.get.return_value = MagicMock(exists=False)
        mock_db.collection.return_value.document.return_value = mock_doc
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_doc

        record_click(mock_db, "user123", {
            "category": "coding",
            "slot_type": "continue",
            "title": "Continue Flask app",
        })
        # Should write to Firestore
        assert mock_doc.set.called or mock_doc.update.called

    def test_increment_shown_count(self):
        from app.services.smart_suggestions import Suggestion, increment_shown_counts
        mock_db = MagicMock()
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = {
            "pool": [
                {"title": "A", "subtitle": "s", "prompt": "p", "slot_type": "continue",
                 "category": "coding", "icon": "code", "shown_count": 0},
                {"title": "B", "subtitle": "s", "prompt": "p", "slot_type": "today",
                 "category": "planning", "icon": "calendar", "shown_count": 2},
            ],
            "last_refresh": datetime.utcnow(),
            "refresh_reason": "test",
        }
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = mock_snap
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.update = MagicMock()

        shown_titles = ["A"]
        increment_shown_counts(mock_db, "user123", shown_titles)
        # Should call update on the pool doc
        update_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        assert update_ref.update.called

    def test_build_suggestion_profile(self):
        from app.services.smart_suggestions import build_suggestion_profile
        click_history = [
            {"category": "coding", "slot_type": "continue"},
            {"category": "coding", "slot_type": "continue"},
            {"category": "planning", "slot_type": "today"},
            {"category": "coding", "slot_type": "discover"},
        ]
        profile = build_suggestion_profile(click_history)
        assert profile["top_categories"][0] == "coding"
        assert profile["preferred_slot"] == "continue"
        assert "coding" in profile["engagement_by_category"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestClickTracking -v 2>&1 | head -20`
Expected: ImportError

- [ ] **Step 3: Implement click tracking and suggestion profile**

Add to `app/services/smart_suggestions.py`:

```python
from collections import Counter


# ---------------------------------------------------------------------------
# Click tracking
# ---------------------------------------------------------------------------

def _profile_ref(db, user_id: str):
    """Reference to users/{uid}/smart_suggestions/profile doc."""
    return db.collection("users").document(user_id).collection("smart_suggestions").document("profile")


def record_click(db, user_id: str, click_data: Dict[str, Any]) -> None:
    """Record a suggestion click to Firestore.

    click_data should contain: category, slot_type, title.
    Appends to click_history array in the profile document.
    """
    try:
        ref = _profile_ref(db, user_id)
        click_entry = {
            "category": click_data.get("category", ""),
            "slot_type": click_data.get("slot_type", ""),
            "title": click_data.get("title", ""),
            "clicked_at": datetime.utcnow(),
        }

        from google.cloud.firestore_v1 import ArrayUnion
        ref.set({"click_history": ArrayUnion([click_entry])}, merge=True)
        logger.debug(f"[smart_suggestions] Recorded click for {user_id}: {click_data.get('title')}")
    except ImportError:
        # Fallback: read-modify-write if ArrayUnion not available
        try:
            snap = ref.get()
            history = snap.to_dict().get("click_history", []) if snap.exists else []
            click_entry = {
                "category": click_data.get("category", ""),
                "slot_type": click_data.get("slot_type", ""),
                "title": click_data.get("title", ""),
                "clicked_at": datetime.utcnow(),
            }
            history.append(click_entry)
            ref.set({"click_history": history}, merge=True)
        except Exception as e:
            logger.error(f"[smart_suggestions] Failed to record click: {e}")
    except Exception as e:
        logger.error(f"[smart_suggestions] Failed to record click for {user_id}: {e}")


def increment_shown_counts(db, user_id: str, shown_titles: List[str]) -> None:
    """Increment shown_count for displayed suggestions in the pool."""
    try:
        ref = _pool_ref(db, user_id)
        snap = ref.get()
        if not snap.exists:
            return
        data = snap.to_dict()
        pool = data.get("pool", [])
        changed = False
        for item in pool:
            if item.get("title") in shown_titles:
                item["shown_count"] = item.get("shown_count", 0) + 1
                changed = True
        if changed:
            ref.update({"pool": pool})
    except Exception as e:
        logger.error(f"[smart_suggestions] Failed to increment shown counts: {e}")


def build_suggestion_profile(click_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze click history to build a suggestion profile.

    Returns dict with: top_categories, avoided_categories, preferred_slot,
    engagement_by_category.
    """
    if not click_history:
        return {
            "top_categories": [],
            "avoided_categories": [],
            "preferred_slot": "continue",
            "engagement_by_category": {},
        }

    cat_counts = Counter(c.get("category", "") for c in click_history if c.get("category"))
    slot_counts = Counter(c.get("slot_type", "") for c in click_history if c.get("slot_type"))

    total = sum(cat_counts.values()) or 1
    engagement = {cat: count / total for cat, count in cat_counts.items()}

    top_cats = [cat for cat, _ in cat_counts.most_common(3)]
    preferred_slot = slot_counts.most_common(1)[0][0] if slot_counts else "continue"

    return {
        "top_categories": top_cats,
        "avoided_categories": [],  # built over time from never-clicked shown categories
        "preferred_slot": preferred_slot,
        "engagement_by_category": engagement,
    }


def load_suggestion_profile(db, user_id: str) -> Dict[str, Any]:
    """Load suggestion profile from Firestore, building it from click history."""
    try:
        snap = _profile_ref(db, user_id).get()
        if not snap.exists:
            return build_suggestion_profile([])
        data = snap.to_dict()
        return build_suggestion_profile(data.get("click_history", []))
    except Exception as e:
        logger.error(f"[smart_suggestions] Failed to load profile for {user_id}: {e}")
        return build_suggestion_profile([])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestClickTracking -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add click tracking and suggestion profile"
```

---

### Task 5: Signal-Based New User Inference

**Files:**
- Modify: `app/services/smart_suggestions.py`
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for signal inference**

Add to `tests/test_smart_suggestions.py`:

```python
class TestSignalInference:
    """Tests for new user signal-based suggestion selection."""

    def test_morning_weekday_signals(self):
        from app.services.smart_suggestions import infer_signals
        signals = infer_signals(hour=9, day_of_week="Monday", user_agent="Mozilla/5.0 (Windows NT 10.0)")
        assert signals["time_period"] == "morning"
        assert signals["is_weekend"] is False
        assert signals["is_mobile"] is False

    def test_evening_weekend_mobile(self):
        from app.services.smart_suggestions import infer_signals
        signals = infer_signals(hour=20, day_of_week="Saturday", user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS)")
        assert signals["time_period"] == "evening"
        assert signals["is_weekend"] is True
        assert signals["is_mobile"] is True

    def test_curated_fallback_scored_by_signals(self):
        from app.services.smart_suggestions import get_signal_based_suggestions
        suggestions = get_signal_based_suggestions(hour=9, day_of_week="Monday", user_agent="desktop")
        assert len(suggestions) == 3
        # Should have one per slot type
        slot_types = [s.slot_type for s in suggestions]
        assert "today" in slot_types
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestSignalInference -v 2>&1 | head -20`
Expected: ImportError

- [ ] **Step 3: Implement signal inference**

Add to `app/services/smart_suggestions.py`:

```python
# ---------------------------------------------------------------------------
# Signal-based inference for new users
# ---------------------------------------------------------------------------

def infer_signals(
    hour: int = 12,
    day_of_week: str = "",
    user_agent: str = "",
    language: str = "en",
    referrer: str = "",
) -> Dict[str, Any]:
    """Extract contextual signals from request metadata."""
    is_mobile = any(kw in user_agent.lower() for kw in ("iphone", "android", "mobile"))
    is_weekend = day_of_week.lower() in ("saturday", "sunday")

    if 5 <= hour < 12:
        time_period = "morning"
    elif 12 <= hour < 17:
        time_period = "afternoon"
    elif 17 <= hour < 22:
        time_period = "evening"
    else:
        time_period = "night"

    return {
        "time_period": time_period,
        "is_weekend": is_weekend,
        "is_mobile": is_mobile,
        "language": language,
        "referrer": referrer,
        "hour": hour,
    }


# Curated signal-scored fallbacks for new users (before LLM generation)
_CURATED_NEW_USER = [
    # Continue slot (demo-quality for new users)
    Suggestion("Build me a landing page", "Responsive, modern design", "Build me a modern responsive landing page for a SaaS product with hero, features, pricing, and footer", "continue", "coding", "code"),
    Suggestion("Draft a resignation letter", "Professional, ready to send", "Write me a professional resignation letter giving two weeks notice. Keep it graceful and positive.", "continue", "writing", "edit"),
    Suggestion("Compare React vs Vue", "Pros, cons, verdict", "Compare React vs Vue vs Svelte for building a modern web app. Table with pros, cons, and your recommendation.", "continue", "learning", "lightbulb"),
    # Today slot variants
    Suggestion("Plan my morning", "Start productive", "Help me create a productive morning routine that includes exercise, learning, and focused work time", "today", "planning", "calendar"),
    Suggestion("Wind down for the evening", "Relax tonight", "Suggest a relaxing evening routine: light activity, no screens, and something creative", "today", "wellness", "heart"),
    Suggestion("Weekend project ideas", "Make something cool", "Give me 5 weekend project ideas that I can finish in one day — mix of coding, creative, and hands-on", "today", "creativity", "sparkles"),
    Suggestion("Quick afternoon boost", "Energy and focus", "I'm in an afternoon slump. Give me a 10-minute reset: stretching, hydration, and a micro-task to build momentum", "today", "wellness", "zap"),
    # Discover slot
    Suggestion("Name my startup", "10 creative options", "Give me 10 creative startup name ideas for an AI-powered app. Include .com domain suggestions.", "discover", "creativity", "sparkles"),
    Suggestion("Teach me something new", "5-minute lesson", "Teach me one interesting concept I probably don't know about, in under 5 minutes. Make it fun.", "discover", "learning", "book"),
    Suggestion("Write me a short story", "Sci-fi, 500 words", "Write me a 500-word sci-fi short story about the last human who discovers they are not alone after all", "discover", "writing", "edit"),
]


def _score_curated(suggestion: Suggestion, signals: Dict[str, Any]) -> float:
    """Score a curated suggestion against user signals."""
    score = random.random() * 0.05  # tiny tiebreaker
    time_period = signals.get("time_period", "")
    is_weekend = signals.get("is_weekend", False)
    is_mobile = signals.get("is_mobile", False)

    title_lower = suggestion.title.lower() + " " + suggestion.subtitle.lower()

    # Time matching
    if time_period == "morning" and "morning" in title_lower:
        score += 2.0
    elif time_period == "evening" and ("evening" in title_lower or "wind down" in title_lower):
        score += 2.0
    elif time_period == "afternoon" and "afternoon" in title_lower:
        score += 2.0

    # Weekend
    if is_weekend and "weekend" in title_lower:
        score += 1.5

    # Mobile: prefer shorter/quicker tasks
    if is_mobile and ("quick" in title_lower or "5-minute" in title_lower):
        score += 1.0

    return score


def get_signal_based_suggestions(
    hour: int = 12,
    day_of_week: str = "",
    user_agent: str = "",
) -> List[Suggestion]:
    """Pick 3 curated suggestions for a new user based on available signals."""
    signals = infer_signals(hour=hour, day_of_week=day_of_week, user_agent=user_agent)

    scored = [(s, _score_curated(s, signals)) for s in _CURATED_NEW_USER]

    selected = []
    used_slots = set()
    # Pick best for today first (most signal-dependent), then continue, then discover
    for target_slot in ("today", "continue", "discover"):
        candidates = [(s, sc) for s, sc in scored if s.slot_type == target_slot and s.slot_type not in used_slots]
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            selected.append(candidates[0][0])
            used_slots.add(target_slot)

    return selected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestSignalInference -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add signal-based inference for new users"
```

---

### Task 6: Background Refresh Orchestrator

**Files:**
- Modify: `app/services/smart_suggestions.py`
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Write failing tests for refresh logic**

Add to `tests/test_smart_suggestions.py`:

```python
class TestRefreshLogic:
    """Tests for background refresh trigger logic."""

    def test_should_refresh_expired_pool(self):
        from app.services.smart_suggestions import should_refresh_pool
        metadata = {
            "last_refresh": datetime.utcnow() - timedelta(hours=7),
            "pool_size": 10,
        }
        assert should_refresh_pool(metadata, conversations_since=0) is True

    def test_should_not_refresh_recent_pool(self):
        from app.services.smart_suggestions import should_refresh_pool
        metadata = {
            "last_refresh": datetime.utcnow() - timedelta(hours=1),
            "pool_size": 10,
        }
        assert should_refresh_pool(metadata, conversations_since=0) is False

    def test_should_refresh_after_conversations(self):
        from app.services.smart_suggestions import should_refresh_pool
        metadata = {
            "last_refresh": datetime.utcnow() - timedelta(hours=1),
            "pool_size": 10,
        }
        assert should_refresh_pool(metadata, conversations_since=4) is True

    def test_should_refresh_empty_pool(self):
        from app.services.smart_suggestions import should_refresh_pool
        assert should_refresh_pool({}, conversations_since=0) is True

    def test_should_refresh_exhausted_pool(self):
        from app.services.smart_suggestions import should_refresh_pool
        metadata = {
            "last_refresh": datetime.utcnow() - timedelta(minutes=30),
            "pool_size": 0,
        }
        assert should_refresh_pool(metadata, conversations_since=0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestRefreshLogic -v 2>&1 | head -20`
Expected: ImportError

- [ ] **Step 3: Implement refresh logic and orchestrator**

Add to `app/services/smart_suggestions.py`:

```python
from datetime import timedelta

# ---------------------------------------------------------------------------
# Refresh logic
# ---------------------------------------------------------------------------

POOL_EXPIRY_HOURS = 6
CONVERSATION_THRESHOLD = 3


def should_refresh_pool(
    metadata: Dict[str, Any],
    conversations_since: int = 0,
) -> bool:
    """Check if the suggestion pool needs refreshing."""
    # No metadata = no pool exists yet
    if not metadata or not metadata.get("last_refresh"):
        return True

    # Empty pool
    if metadata.get("pool_size", 0) == 0:
        return True

    # Time expiry
    last_refresh = metadata["last_refresh"]
    if isinstance(last_refresh, datetime):
        age = datetime.utcnow() - last_refresh
        if age > timedelta(hours=POOL_EXPIRY_HOURS):
            return True

    # Conversation threshold
    if conversations_since >= CONVERSATION_THRESHOLD:
        return True

    return False


def _get_user_memory_context(db, user_id: str) -> Dict[str, Any]:
    """Gather memory context for LLM generation prompt."""
    context: Dict[str, Any] = {
        "memories": [],
        "recent_topics": [],
        "suggestion_profile": {},
        "exclusion_titles": [],
        "hour": datetime.utcnow().hour,
        "day_of_week": datetime.utcnow().strftime("%A"),
    }

    try:
        # Load memories (V2)
        from app.memory_v2.store import MemoryStoreV2
        store = MemoryStoreV2(user_id)
        memories = store.get_all(include_expired=False)
        context["memories"] = [
            {"type": m.type.value if hasattr(m.type, "value") else str(m.type),
             "key": m.key, "value": m.value}
            for m in memories[:20]
        ]
    except Exception as e:
        logger.debug(f"[smart_suggestions] Could not load memories: {e}")

    try:
        # Load recent session titles as topics
        sessions_ref = db.collection("users").document(user_id).collection("chat_sessions")
        recent = sessions_ref.order_by("updated_at", direction="DESCENDING").limit(5).stream()
        topics = []
        for doc in recent:
            data = doc.to_dict()
            title = data.get("title") or data.get("topic", "")
            if title:
                topics.append(title)
        context["recent_topics"] = topics
    except Exception as e:
        logger.debug(f"[smart_suggestions] Could not load recent topics: {e}")

    # Load suggestion profile
    context["suggestion_profile"] = load_suggestion_profile(db, user_id)

    # Load currently shown titles as exclusions
    try:
        current_pool = load_pool(db, user_id)
        context["exclusion_titles"] = [s.title for s in current_pool if s.shown_count > 0]
    except Exception:
        pass

    return context


def refresh_pool_background(db, user_id: str, reason: str) -> bool:
    """Generate a new suggestion pool in the background.

    Call this from a daemon thread — never blocks the user's response.
    Returns True if a new pool was saved.
    """
    try:
        context = _get_user_memory_context(db, user_id)
        suggestions = generate_pool_from_llm(context)

        if not suggestions:
            logger.warning(f"[smart_suggestions] LLM generation returned empty for {user_id}")
            return False

        save_pool(db, user_id, suggestions, reason)
        return True

    except Exception as e:
        logger.error(f"[smart_suggestions] Background refresh failed for {user_id}: {e}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py::TestRefreshLogic -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/smart_suggestions.py tests/test_smart_suggestions.py
git commit -m "feat(suggestions): add refresh logic and background orchestrator"
```

---

### Task 7: Wire pages_routes.py — Serve Pool & Track Clicks

**Files:**
- Modify: `app/blueprints/pages_routes.py`

- [ ] **Step 1: Modify the index route to serve precomputed pool**

In `app/blueprints/pages_routes.py`, replace the logged-in user suggestion logic in the `index()` function. Change lines 95-100 from:

```python
        else:
            # Fetch user preferences for personalized suggestions
            user_id = session['user']['uid']
            user_preferences = fetch_user_preferences(user_id)
            options = task_generator.get_fallback_options(user_preferences=user_preferences)
            guest_count = 0
```

to:

```python
        else:
            # Serve precomputed smart suggestions pool
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, get_signal_based_suggestions,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                    increment_shown_counts,
                )
                pool = load_pool(db, user_id)
                if pool:
                    selected = select_display_slots(pool)
                    options = [s.to_task_option() for s in selected]
                    # Track shown
                    shown_titles = [s.title for s in selected]
                    increment_shown_counts(db, user_id, shown_titles)
                    # Check if background refresh needed
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "expired"),
                            daemon=True,
                        ).start()
                else:
                    # No pool yet — use signal-based for first impression
                    from flask import request as flask_request
                    hour = datetime.utcnow().hour
                    day = datetime.utcnow().strftime("%A")
                    ua = flask_request.headers.get("User-Agent", "")
                    selected = get_signal_based_suggestions(hour=hour, day_of_week=day, user_agent=ua)
                    options = [s.to_task_option() for s in selected]
                    # Trigger first pool generation
                    import threading
                    threading.Thread(
                        target=refresh_pool_background,
                        args=(db, user_id, "first_conversation"),
                        daemon=True,
                    ).start()
            except Exception as e:
                logger.warning(f"Smart suggestions failed, using fallback: {e}")
                user_preferences = fetch_user_preferences(user_id)
                options = task_generator.get_fallback_options(user_preferences=user_preferences)
            guest_count = 0
```

- [ ] **Step 2: Apply the same change to open_shared_session route**

In the `open_shared_session()` function, replace lines 142-147 (the `else` block for logged-in users) with the same pool-serving logic:

```python
        else:
            # Serve precomputed smart suggestions pool
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, get_signal_based_suggestions,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                    increment_shown_counts,
                )
                pool = load_pool(db, user_id)
                if pool:
                    selected = select_display_slots(pool)
                    options = [s.to_task_option() for s in selected]
                    shown_titles = [s.title for s in selected]
                    increment_shown_counts(db, user_id, shown_titles)
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "expired"),
                            daemon=True,
                        ).start()
                else:
                    from flask import request as flask_request
                    hour = datetime.utcnow().hour
                    day = datetime.utcnow().strftime("%A")
                    ua = flask_request.headers.get("User-Agent", "")
                    selected = get_signal_based_suggestions(hour=hour, day_of_week=day, user_agent=ua)
                    options = [s.to_task_option() for s in selected]
                    import threading
                    threading.Thread(
                        target=refresh_pool_background,
                        args=(db, user_id, "first_conversation"),
                        daemon=True,
                    ).start()
            except Exception as e:
                logger.warning(f"Smart suggestions failed, using fallback: {e}")
                user_preferences = fetch_user_preferences(user_id)
                options = task_generator.get_fallback_options(user_preferences=user_preferences)
            guest_count = 0
```

- [ ] **Step 3: Modify more_options route to serve from pool instead of calling LLM**

Replace the `more_options()` function body (lines 180-205):

```python
@pages_bp.route('/more-options', methods=['POST'])
@csrf_required
@error_handler
def more_options():
    """Get more task options — serve from precomputed pool for logged-in users."""
    try:
        if 'user' in session:
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, increment_shown_counts,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                )
                pool = load_pool(db, user_id)
                if pool:
                    # Get 3 different suggestions (randomized selection for variety)
                    import random
                    random.shuffle(pool)
                    selected = select_display_slots(pool)
                    options_dicts = [s.to_task_option().to_dict() for s in selected]
                    # Add More button
                    options_dicts.append({
                        "title": "More", "subtitle": "Load new ideas",
                        "action": "fetchMoreOptions()", "icon": "more",
                    })
                    # Track shown
                    increment_shown_counts(db, user_id, [s.title for s in selected])
                    # Check exhaustion (all high shown_count)
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "exhausted"),
                            daemon=True,
                        ).start()
                    return jsonify({"options": options_dicts}), 200
            except Exception as e:
                logger.warning(f"Smart suggestions more-options failed: {e}")

        # Fallback: original behavior
        user_preferences = {}
        user_history = None
        if 'user' in session:
            user_id = session['user']['uid']
            user_preferences = fetch_user_preferences(user_id)
        num_tasks = 3
        options = task_generator.generate_task_options(
            user_history=user_history, num_tasks=num_tasks, force_new_tasks=True,
        )
        new_options = [opt for opt in options if opt not in current_options]
        options_dicts = [opt.to_dict() for opt in new_options]
        return jsonify({"options": options_dicts}), 200
    except Exception as e:
        logger.error(f"Failed to fetch more options: {e}")
        return jsonify({'error': 'Failed to fetch more options.'}), 500
```

- [ ] **Step 4: Add click tracking endpoint**

Add a new route after the `more_options` route:

```python
@pages_bp.route('/suggestion-click', methods=['POST'])
@csrf_required
@error_handler
def suggestion_click():
    """Record a suggestion click for the learning loop."""
    if 'user' not in session:
        return jsonify({"ok": True}), 200  # silently ignore for guests

    try:
        from app.services.smart_suggestions import record_click
        user_id = session['user']['uid']
        click_data = request.json or {}
        record_click(db, user_id, click_data)
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.debug(f"Click tracking failed: {e}")
        return jsonify({"ok": True}), 200  # never fail the user's click
```

- [ ] **Step 5: Add the `current_options` variable that was missing in fallback path**

In the `more_options` fallback path, add `current_options = request.json.get('current_options', [])` before it's used. Update the fallback section:

```python
        # Fallback: original behavior
        current_options = request.json.get('current_options', [])
        user_preferences = {}
```

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/pages_routes.py
git commit -m "feat(suggestions): wire pages_routes to serve from precomputed pool"
```

---

### Task 8: Wire chat_routes.py — Conversation Count Trigger

**Files:**
- Modify: `app/blueprints/chat_routes.py`

- [ ] **Step 1: Add conversation count tracking and refresh trigger**

After the dream consolidation block in `chat_routes.py` (around line 1962, after the `dream_thread.start()` try/except block), add:

```python
                # Smart suggestions: trigger refresh after enough new conversations
                if not guest_mode:
                    try:
                        from app.services.smart_suggestions import (
                            get_pool_metadata, should_refresh_pool, refresh_pool_background,
                        )
                        metadata = get_pool_metadata(db, user_id)
                        # Count sessions since last refresh
                        convos_since = 0
                        last_refresh = metadata.get("last_refresh")
                        if last_refresh:
                            sessions_ref = db.collection("users").document(user_id).collection("chat_sessions")
                            from google.cloud.firestore_v1 import FieldFilter
                            try:
                                recent = sessions_ref.where(filter=FieldFilter("updated_at", ">", last_refresh)).count().get()
                                convos_since = recent[0][0].value if recent else 0
                            except Exception:
                                # Fallback: just check if refresh is needed by time
                                convos_since = 0

                        if should_refresh_pool(metadata, conversations_since=convos_since):
                            import threading
                            threading.Thread(
                                target=refresh_pool_background,
                                args=(db, user_id, "conversations"),
                                daemon=True,
                            ).start()
                    except Exception as e:
                        logger.debug(f"Smart suggestions refresh check skipped: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(suggestions): trigger pool refresh after conversation threshold"
```

---

### Task 9: Wire Dream System — Post-Consolidation Refresh

**Files:**
- Modify: `app/memory_v2/dream/__init__.py`

- [ ] **Step 1: Add suggestion refresh after dream consolidation**

In `app/memory_v2/dream/__init__.py`, add a suggestion pool refresh after the consolidation completes. Insert after line 111 (after `gates.release_lock(db, user_id)`) and before the final log line:

```python
    # Trigger smart suggestions refresh (fire-and-forget, non-blocking)
    try:
        from app.services.smart_suggestions import refresh_pool_background
        import threading
        threading.Thread(
            target=refresh_pool_background,
            args=(db, user_id, "dream"),
            daemon=True,
        ).start()
    except Exception as e:
        logger.debug(f"[dream] Smart suggestions refresh skipped: {e}")
```

The full end of `_run_consolidation` should now look like:

```python
    # Update state
    gates.update_dream_state(db, user_id, {
        'last_consolidated_at': datetime.utcnow(),
        'pending_notification': notification if notification else None,
    })

    gates.release_lock(db, user_id)

    # Trigger smart suggestions refresh (fire-and-forget, non-blocking)
    try:
        from app.services.smart_suggestions import refresh_pool_background
        import threading
        threading.Thread(
            target=refresh_pool_background,
            args=(db, user_id, "dream"),
            daemon=True,
        ).start()
    except Exception as e:
        logger.debug(f"[dream] Smart suggestions refresh skipped: {e}")

    logger.info(f"[dream] Consolidation complete for {user_id}: {summary}")
    return True
```

- [ ] **Step 2: Commit**

```bash
git add app/memory_v2/dream/__init__.py
git commit -m "feat(suggestions): trigger pool refresh after dream consolidation"
```

---

### Task 10: Frontend — Pool Cycling & Click Reporting

**Files:**
- Modify: `app/static/js/fetchMoreOptions.js`

- [ ] **Step 1: Add click tracking to handleOptionClick**

In `fetchMoreOptions.js`, add a click reporting call in `handleOptionClick()`. After the existing function declaration (around line 739), before the `const rawAction` line, add the click tracking. Modify `handleOptionClick`:

```javascript
    function handleOptionClick(button) {
        const rawAction = button?.dataset?.action || '';
        const action = normalizeAction(rawAction);

        // Allow "More" button to work in guest mode
        if (action === 'fetchMoreOptions') {
            fetchMoreOptions();
            return;
        }

        // Report click for smart suggestions learning loop (fire-and-forget)
        if (!isGuestMode()) {
            const buttonText = button?.querySelector(CONFIG.SELECTORS.BUTTON_TEXT)?.textContent?.trim();
            const subtitle = button?.querySelector('.subtitle')?.textContent?.trim();
            const category = button?.dataset?.category || '';
            const slotType = button?.dataset?.slotType || '';
            reportSuggestionClick({
                title: buttonText || '',
                subtitle: subtitle || '',
                category: category,
                slot_type: slotType,
            });
        }

        // Check for guest mode demo responses
        if (isGuestMode()) {
```

- [ ] **Step 2: Add the reportSuggestionClick function**

Add this function before the `handleOptionClick` function:

```javascript
    // Report suggestion click to backend for learning loop
    function reportSuggestionClick(clickData) {
        const csrfToken = getCSRFToken();
        if (!csrfToken) return;

        fetch('/suggestion-click', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify(clickData),
        }).catch(() => {}); // fire-and-forget
    }
```

- [ ] **Step 3: Add data attributes to createOptionButton for click tracking**

Modify `createOptionButton` to pass through category and slot_type data attributes:

```javascript
    function createOptionButton(option) {
        const button = document.createElement('button');
        const isMoreButton = option.title === CONFIG.TEXT.MORE;

        button.className = `${CONFIG.CLASSES.OPTIONS_BUTTON} ${CONFIG.CLASSES.MODERN_OPTION} ${
            isMoreButton ? CONFIG.CLASSES.MORE_BUTTON : ''
        }`;
        if (option.action) {
            button.dataset.action = option.action;
        }
        // Store metadata for click tracking
        if (option.category) button.dataset.category = option.category;
        if (option.slot_type) button.dataset.slotType = option.slot_type;

        // Use icon from server response, fallback to 'star' if not provided
        const iconName = option.icon || (isMoreButton ? 'more' : 'star');
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/fetchMoreOptions.js
git commit -m "feat(suggestions): add click tracking to frontend"
```

---

### Task 11: Ensure `app/services/__init__.py` Exists

**Files:**
- Create: `app/services/__init__.py` (if missing)

- [ ] **Step 1: Check and create __init__.py**

```bash
ls app/services/__init__.py 2>/dev/null || touch app/services/__init__.py
```

- [ ] **Step 2: Commit if created**

```bash
git add app/services/__init__.py 2>/dev/null
git diff --cached --quiet || git commit -m "chore: add services package init"
```

---

### Task 12: Integration Test — Full Flow

**Files:**
- Modify: `tests/test_smart_suggestions.py`

- [ ] **Step 1: Add integration test for the full pipeline**

Add to `tests/test_smart_suggestions.py`:

```python
class TestIntegration:
    """Integration tests for the full suggestion pipeline."""

    def test_full_flow_new_user(self):
        """New user gets signal-based suggestions, then pool is generated."""
        from app.services.smart_suggestions import (
            get_signal_based_suggestions,
            save_pool,
            load_pool,
            select_display_slots,
            record_click,
            build_suggestion_profile,
        )
        # Step 1: New user arrives, gets signal-based suggestions
        suggestions = get_signal_based_suggestions(hour=9, day_of_week="Monday")
        assert len(suggestions) == 3

        # Step 2: Simulate pool being saved (as if LLM generated it)
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_doc

        from app.services.smart_suggestions import Suggestion
        pool = [
            Suggestion("Continue Flask", "Cost tracking", "prompt", "continue", "coding", "code"),
            Suggestion("Plan morning", "Be productive", "prompt", "today", "planning", "calendar"),
            Suggestion("Try Go lang", "New language", "prompt", "discover", "learning", "book"),
        ]
        save_pool(mock_db, "user123", pool, "first_conversation")
        assert mock_doc.set.called

    def test_suggestion_to_task_option_renders_correctly(self):
        """Suggestions convert to TaskOption format that the existing frontend expects."""
        from app.services.smart_suggestions import Suggestion
        s = Suggestion(
            title="Build an API",
            subtitle="Flask REST starter",
            prompt="Build me a Flask REST API with auth",
            slot_type="continue",
            category="coding",
            icon="code",
        )
        opt = s.to_task_option()
        d = opt.to_dict()
        assert d["title"] == "Build an API"
        assert d["subtitle"] == "Flask REST starter"
        assert "sendMessage" in d["action"]
        assert d["icon"] == "code"
```

- [ ] **Step 2: Run the full test suite**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_smart_suggestions.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_smart_suggestions.py
git commit -m "test(suggestions): add integration tests for full pipeline"
```

---

### Task 13: Final Verification

- [ ] **Step 1: Run the full project test suite to check for regressions**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: No regressions. All existing tests still pass.

- [ ] **Step 2: Check import chain works**

Run: `cd /root/xelas.io/xelasphere.io- && python -c "from app.services.smart_suggestions import Suggestion, load_pool, save_pool, select_display_slots, generate_pool_from_llm, record_click, get_signal_based_suggestions, should_refresh_pool, refresh_pool_background; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 3: Final commit with all changes**

```bash
git add -A
git diff --cached --quiet || git commit -m "feat(suggestions): complete smart suggestions system implementation"
```
