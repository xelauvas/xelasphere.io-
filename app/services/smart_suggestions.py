"""
Smart Suggestions Service

Manages a pool of AI-generated personalized suggestions for users.
The pool is stored in Firestore and refreshed as needed.
"""

import json
import logging
import random
import re
import requests
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

from app.config import Config
from app.grog_api import TaskOption

logger = logging.getLogger(__name__)

# Pool configuration constants
POOL_SIZE = 10
DISPLAY_SIZE = 3
SLOT_TYPES = ("continue", "today", "discover")


@dataclass
class Suggestion:
    """
    A single smart suggestion for a user.

    The `reasoning` field captures why this suggestion was generated.
    It is intentionally excluded from `to_dict()` to avoid exposing
    internal AI reasoning to the client.
    """
    title: str
    subtitle: str
    prompt: str
    slot_type: str
    category: str
    icon: str
    reasoning: str
    shown_count: int = 0
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize the suggestion for Firestore storage or API responses.

        Note: `reasoning` is intentionally excluded here — it must never
        be sent to the client.
        """
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "prompt": self.prompt,
            "slot_type": self.slot_type,
            "category": self.category,
            "icon": self.icon,
            "shown_count": self.shown_count,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }

    def to_task_option(self) -> TaskOption:
        """Convert this suggestion to the existing TaskOption format."""
        return TaskOption(
            title=self.title,
            subtitle=self.subtitle,
            action=f"sendMessage('{self.prompt.replace(chr(39), chr(92) + chr(39))}')",
            icon=self.icon,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Suggestion":
        """Deserialize a Suggestion from a Firestore document dict."""
        created_at_raw = data.get("created_at")
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw)
            except ValueError:
                created_at = datetime.now(timezone.utc)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = datetime.now(timezone.utc)

        return cls(
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            prompt=data.get("prompt", ""),
            slot_type=data.get("slot_type", ""),
            category=data.get("category", ""),
            icon=data.get("icon", "star"),
            reasoning=data.get("reasoning", ""),
            shown_count=data.get("shown_count", 0),
            created_at=created_at,
        )


def save_pool(db, user_id: str, suggestions: List[Suggestion], reason: str) -> None:
    """
    Persist the suggestion pool to Firestore.

    Writes to: users/{user_id}/smart_suggestions/pool
    """
    pool_data = {
        "suggestions": [s.to_dict() for s in suggestions],
        "last_refresh": datetime.now(timezone.utc).isoformat(),
        "refresh_reason": reason,
        "pool_size": len(suggestions),
    }
    (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .set(pool_data)
    )


def load_pool(db, user_id: str) -> List[Suggestion]:
    """
    Load the suggestion pool from Firestore.

    Returns an empty list if no pool document exists yet.
    """
    snapshot = (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .get()
    )
    if not snapshot.exists:
        return []

    data = snapshot.to_dict() or {}
    raw_suggestions = data.get("suggestions", [])
    return [Suggestion.from_dict(s) for s in raw_suggestions]


def get_pool_metadata(db, user_id: str) -> Dict[str, Any]:
    """
    Return metadata about the current suggestion pool.

    Returns a dict with last_refresh, refresh_reason, and pool_size.
    Returns defaults if the pool document does not exist.
    """
    snapshot = (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .get()
    )
    if not snapshot.exists:
        return {
            "last_refresh": None,
            "refresh_reason": None,
            "pool_size": 0,
        }

    data = snapshot.to_dict() or {}
    return {
        "last_refresh": data.get("last_refresh"),
        "refresh_reason": data.get("refresh_reason"),
        "pool_size": data.get("pool_size", 0),
    }


# Time-of-day keyword ranges (inclusive on both ends, except night which wraps).
_TIME_KEYWORDS: Dict[str, tuple] = {
    "morning": (5, 11),
    "afternoon": (12, 16),
    "evening": (17, 21),
    "night": (22, 4),  # wraps midnight
}


def _score_today_slot(suggestion: Suggestion, hour: int) -> int:
    """
    Score a today-slot suggestion based on time-of-day keywords in its
    title and subtitle.

    Returns 1 if a keyword matches the given hour, 0 otherwise.
    Multiple keyword matches still return 1 (presence, not frequency).
    """
    text = (suggestion.title + " " + suggestion.subtitle).lower()
    for keyword, (start, end) in _TIME_KEYWORDS.items():
        if keyword not in text:
            continue
        if start <= end:
            if start <= hour <= end:
                return 1
        else:
            # Wraps midnight (e.g. night: 22-4)
            if hour >= start or hour <= end:
                return 1
    return 0


def select_display_slots(
    pool: List[Suggestion],
    hour: Optional[int] = None,
    per_slot: int = 1,
) -> List[Suggestion]:
    """
    Pick up to per_slot suggestions per slot type (continue/today/discover).

    Default is 1 per slot = 3 total (matching the 2x2 grid: 3 + More button).

    Selection rules:
    - continue: lowest shown_count, random tiebreaker
    - today: highest _score_today_slot(), random tiebreaker among top scorers
    - discover: lowest shown_count, random tiebreaker
    - If a slot type has no candidates in the pool it is skipped.

    Returns fewer items when slot types are missing or have fewer candidates.
    """
    if not pool:
        return []

    if hour is None:
        hour = datetime.now(timezone.utc).hour

    # Group suggestions by slot_type
    by_slot: Dict[str, List[Suggestion]] = {}
    for s in pool:
        by_slot.setdefault(s.slot_type, []).append(s)

    selected: List[Suggestion] = []

    # --- continue slot ---
    if "continue" in by_slot:
        candidates = sorted(by_slot["continue"], key=lambda s: (s.shown_count, random.random()))
        selected.extend(candidates[:per_slot])

    # --- today slot ---
    if "today" in by_slot:
        candidates = by_slot["today"]
        scored = sorted(candidates, key=lambda s: (-_score_today_slot(s, hour), s.shown_count, random.random()))
        selected.extend(scored[:per_slot])

    # --- discover slot ---
    if "discover" in by_slot:
        candidates = sorted(by_slot["discover"], key=lambda s: (s.shown_count, random.random()))
        selected.extend(candidates[:per_slot])

    return selected


# ---------------------------------------------------------------------------
# LLM Pool Generation
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM_PROMPT = (
    "You are a personalization engine for an AI assistant app. "
    "Generate exactly {pool_size} smart suggestions tailored to the user based on the context provided.\n\n"
    "Return ONLY a valid JSON array with no additional text. Each item must have these fields:\n"
    "- title: short action phrase (max 6 words)\n"
    "- subtitle: one-line description (max 10 words)\n"
    "- prompt: the full message to send to the AI on behalf of the user\n"
    "- slot_type: one of 'continue', 'today', or 'discover'\n"
    "- category: a short category label (e.g. 'work', 'learning', 'health', 'creative')\n"
    "- icon: a single Material Icons name (e.g. 'star', 'bolt', 'book', 'fitness_center')\n"
    "- reasoning: brief internal note explaining why this suggestion fits the user (not shown to user)\n\n"
    "Rules:\n"
    "- Do NOT suggest titles in the exclusion list.\n"
    "- Distribute slot_types: include both 'continue', 'today', and 'discover' entries.\n"
    "- Make prompts specific and actionable, not generic.\n"
    "- Tailor suggestions to the user's memories, recent topics, and time of day."
)

_GENERATION_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def build_generation_prompt(context: Dict[str, Any]) -> str:
    """
    Build a user prompt for LLM suggestion generation.

    Args:
        context: dict with keys:
            memories (list of dicts with type/key/value),
            recent_topics (list of strings),
            recent_prompts (list of strings - actual user prompts from recent sessions),
            suggestion_profile (dict with top_categories, avoided_categories),
            hour (int),
            day_of_week (str),
            exclusion_titles (list of strings)

    Returns:
        A single prompt string.
    """
    memories = (context.get("memories") or [])[:20]
    recent_topics = (context.get("recent_topics") or [])[:5]
    recent_prompts = (context.get("recent_prompts") or [])[:5]
    profile = context.get("suggestion_profile") or {}
    hour = context.get("hour", 12)
    day_of_week = context.get("day_of_week", "")
    exclusion_titles = (context.get("exclusion_titles") or [])[:20]

    lines = []

    # Memories section
    lines.append("## User Memories")
    if memories:
        for m in memories:
            lines.append(f"- [{m.get('type', '')}] {m.get('key', '')}: {m.get('value', '')}")
    else:
        lines.append("(none)")

    # Recent topics section
    lines.append("\n## Recent Topics")
    if recent_topics:
        for t in recent_topics:
            lines.append(f"- {t}")
    else:
        lines.append("(none)")

    # Recent user prompts — what the user actually asked about
    lines.append("\n## Recent User Prompts")
    if recent_prompts:
        for p in recent_prompts:
            lines.append(f"- {p}")
    else:
        lines.append("(none)")

    # Suggestion profile section
    lines.append("\n## Suggestion Profile")
    top_cats = profile.get("top_categories") or []
    avoided_cats = profile.get("avoided_categories") or []
    if top_cats:
        lines.append(f"Preferred categories: {', '.join(top_cats)}")
    if avoided_cats:
        lines.append(f"Avoid these categories: {', '.join(avoided_cats)}")
    if not top_cats and not avoided_cats:
        lines.append("(no profile data yet)")

    # Current context section
    lines.append("\n## Current Context")
    lines.append(f"Hour of day (24h): {hour}")
    if day_of_week:
        lines.append(f"Day of week: {day_of_week}")

    # Exclusions section
    lines.append("\n## Do NOT Suggest")
    if exclusion_titles:
        for title in exclusion_titles:
            lines.append(f"- {title}")
    else:
        lines.append("(no exclusions)")

    return "\n".join(lines)


def parse_llm_suggestions(raw: Optional[str]) -> List[Suggestion]:
    """
    Parse the LLM response JSON into a list of Suggestion objects.

    Handles:
    - Plain JSON array
    - Markdown code-block wrapped (```json ... ```)
    - Malformed JSON → returns []
    - Empty or None input → returns []
    - Items missing title or prompt are skipped

    Returns:
        List of Suggestion objects (may be empty).
    """
    if not raw:
        return []

    text = raw.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.debug("parse_llm_suggestions: JSON decode failed")
        return []

    if not isinstance(data, list):
        logger.debug("parse_llm_suggestions: expected JSON array, got %s", type(data).__name__)
        return []

    suggestions = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("title") or not item.get("prompt"):
            continue
        try:
            suggestions.append(Suggestion.from_dict(item))
        except Exception as exc:
            logger.debug("parse_llm_suggestions: skipping item due to error: %s", exc)

    return suggestions


def generate_pool_from_llm(context: Dict[str, Any]) -> List[Suggestion]:
    """
    Call the Groq LLM API to generate a suggestion pool.

    Tries models in order: llama-3.3-70b-versatile, llama-3.1-8b-instant.
    Returns a list of parsed Suggestion objects, or [] on any failure.
    """
    api_key = Config.GROQ_API_KEY
    if not api_key:
        logger.warning("generate_pool_from_llm: GROQ_API_KEY not configured")
        return []

    system_prompt = _GENERATION_SYSTEM_PROMPT.replace("{pool_size}", str(POOL_SIZE))
    user_prompt = build_generation_prompt(context)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for model in _GENERATION_MODELS:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        }
        try:
            response = requests.post(
                Config.API_URL,
                json=payload,
                headers=headers,
                timeout=20,
            )
            if response.status_code != 200:
                logger.warning(
                    "generate_pool_from_llm: model %s returned status %d",
                    model,
                    response.status_code,
                )
                continue

            raw = response.json()["choices"][0]["message"]["content"]
            suggestions = parse_llm_suggestions(raw)
            if suggestions:
                logger.info(
                    "generate_pool_from_llm: got %d suggestions from model %s",
                    len(suggestions),
                    model,
                )
                return suggestions

            logger.warning("generate_pool_from_llm: model %s returned empty parse, trying next", model)

        except Exception as exc:
            logger.warning("generate_pool_from_llm: model %s failed: %s", model, exc)

    return []


# ---------------------------------------------------------------------------
# Click Tracking & Suggestion Profile
# ---------------------------------------------------------------------------

def _profile_ref(db, user_id: str):
    """Return the Firestore document reference for a user's suggestion profile."""
    return (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("profile")
    )


def record_click(db, user_id: str, click_data: Dict[str, Any]) -> None:
    """
    Record a suggestion click to the user's suggestion profile in Firestore.

    click_data must contain: category, slot_type, title.
    Appends an entry with a clicked_at timestamp to the click_history array.
    Uses set(..., merge=True) so the document is created if it does not exist.
    """
    try:
        from google.cloud.firestore import ArrayUnion

        entry = {
            "category": click_data.get("category", ""),
            "slot_type": click_data.get("slot_type", ""),
            "title": click_data.get("title", ""),
            "clicked_at": datetime.now(timezone.utc).isoformat(),
        }
        _profile_ref(db, user_id).set(
            {"click_history": ArrayUnion([entry])},
            merge=True,
        )
    except Exception as exc:
        logger.warning("record_click: failed to record click for user %s: %s", user_id, exc)


def increment_shown_counts(db, user_id: str, shown_titles: List[str]) -> None:
    """
    Increment shown_count for each suggestion in the pool whose title appears
    in shown_titles.

    Reads the pool from Firestore, updates shown_count in-memory, and writes
    back with update({"pool": ...}).
    """
    try:
        snapshot = (
            db.collection("users")
            .document(user_id)
            .collection("smart_suggestions")
            .document("pool")
            .get()
        )
        if not snapshot.exists:
            return

        data = snapshot.to_dict() or {}
        raw_suggestions = data.get("suggestions", [])

        title_set = set(shown_titles)
        for s in raw_suggestions:
            if s.get("title") in title_set:
                s["shown_count"] = s.get("shown_count", 0) + 1

        (
            db.collection("users")
            .document(user_id)
            .collection("smart_suggestions")
            .document("pool")
            .update({"pool": raw_suggestions})
        )
    except Exception as exc:
        logger.warning(
            "increment_shown_counts: failed to update shown counts for user %s: %s",
            user_id,
            exc,
        )


def build_suggestion_profile(click_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze click history to build a suggestion profile.

    Args:
        click_history: list of dicts with keys category, slot_type, title.

    Returns:
        dict with:
            top_categories: list of categories sorted by click count (most first)
            avoided_categories: empty list (reserved for future use)
            preferred_slot: most-clicked slot type, or None if no history
            engagement_by_category: dict mapping category -> fraction of total clicks
    """
    if not click_history:
        return {
            "top_categories": [],
            "avoided_categories": [],
            "preferred_slot": None,
            "engagement_by_category": {},
        }

    category_counts: Counter = Counter()
    slot_counts: Counter = Counter()

    for entry in click_history:
        cat = entry.get("category", "")
        slot = entry.get("slot_type", "")
        if cat:
            category_counts[cat] += 1
        if slot:
            slot_counts[slot] += 1

    total = sum(category_counts.values())
    top_categories = [cat for cat, _ in category_counts.most_common()]
    preferred_slot = slot_counts.most_common(1)[0][0] if slot_counts else None
    engagement_by_category = {
        cat: count / total for cat, count in category_counts.items()
    }

    return {
        "top_categories": top_categories,
        "avoided_categories": [],
        "preferred_slot": preferred_slot,
        "engagement_by_category": engagement_by_category,
    }


def load_suggestion_profile(db, user_id: str) -> Dict[str, Any]:
    """
    Load the suggestion profile for a user from Firestore.

    Reads click_history from users/{uid}/smart_suggestions/profile,
    passes it to build_suggestion_profile, and returns the result.
    Returns an empty profile on any error or if the document does not exist.
    """
    empty_profile: Dict[str, Any] = {
        "top_categories": [],
        "avoided_categories": [],
        "preferred_slot": None,
        "engagement_by_category": {},
    }
    try:
        snapshot = _profile_ref(db, user_id).get()
        if not snapshot.exists:
            return empty_profile

        data = snapshot.to_dict() or {}
        click_history = data.get("click_history", [])
        return build_suggestion_profile(click_history)
    except Exception as exc:
        logger.warning(
            "load_suggestion_profile: failed to load profile for user %s: %s",
            user_id,
            exc,
        )
        return empty_profile


# ---------------------------------------------------------------------------
# Signal-Based New User Suggestions
# ---------------------------------------------------------------------------

def infer_signals(
    hour: int = 12,
    day_of_week: str = "",
    user_agent: str = "",
    language: str = "en",
    referrer: str = "",
) -> Dict[str, Any]:
    """
    Extract contextual signals from request metadata.

    Returns a dict with:
        time_period: "morning" | "afternoon" | "evening" | "night"
        is_weekend: bool
        is_mobile: bool
        language: str
        referrer: str
        hour: int
    """
    # Determine time period
    if 5 <= hour <= 11:
        time_period = "morning"
    elif 12 <= hour <= 16:
        time_period = "afternoon"
    elif 17 <= hour <= 21:
        time_period = "evening"
    else:
        time_period = "night"

    # Detect weekend
    is_weekend = day_of_week.lower() in ("saturday", "sunday")

    # Detect mobile
    ua_lower = user_agent.lower()
    is_mobile = any(kw in ua_lower for kw in ("iphone", "android", "mobile"))

    return {
        "time_period": time_period,
        "is_weekend": is_weekend,
        "is_mobile": is_mobile,
        "language": language,
        "referrer": referrer,
        "hour": hour,
    }


# Curated fallback suggestions for new users (no personalization data yet).
# Mix of slot types: continue (demo quality), today (time-variant), discover.
_CURATED_NEW_USER: List[Suggestion] = [
    # --- continue slot ---
    Suggestion(
        title="Build a landing page",
        subtitle="HTML, CSS, responsive design",
        prompt="Build me a modern responsive landing page for a SaaS product with a hero section, features grid, and pricing table.",
        slot_type="continue",
        category="coding",
        icon="code",
        reasoning="Demo-quality coding task to show AI capability",
    ),
    Suggestion(
        title="Write a cold email",
        subtitle="Compelling outreach that gets replies",
        prompt="Write me a cold outreach email for a B2B SaaS product that gets replies. Short, specific, no fluff.",
        slot_type="continue",
        category="writing",
        icon="edit",
        reasoning="High-value writing task for new users",
    ),
    Suggestion(
        title="Explain a concept simply",
        subtitle="Any topic, made clear",
        prompt="Explain how compound interest works like I'm 10 years old.",
        slot_type="continue",
        category="learning",
        icon="school",
        reasoning="Approachable learning task that shows AI value",
    ),
    # --- today slot (time-variant) ---
    Suggestion(
        title="Plan my morning routine",
        subtitle="Start the day with intention",
        prompt="Design me a productive morning routine I can complete in 45 minutes before work.",
        slot_type="today",
        category="wellness",
        icon="wb_sunny",
        reasoning="Morning-relevant planning suggestion",
    ),
    Suggestion(
        title="Evening wind-down plan",
        subtitle="Relax and recharge tonight",
        prompt="Create a calming evening routine to help me decompress after work and sleep better.",
        slot_type="today",
        category="wellness",
        icon="nights_stay",
        reasoning="Evening-relevant wellness suggestion",
    ),
    Suggestion(
        title="Weekend project ideas",
        subtitle="Make the most of your weekend",
        prompt="Give me 5 meaningful weekend project ideas I can start and finish in a single day.",
        slot_type="today",
        category="creativity",
        icon="weekend",
        reasoning="Weekend-relevant creativity suggestion",
    ),
    Suggestion(
        title="Quick 5-minute task",
        subtitle="Fast win for a busy day",
        prompt="Give me a productive task I can complete in 5 minutes right now to feel accomplished.",
        slot_type="today",
        category="productivity",
        icon="bolt",
        reasoning="Mobile-friendly quick task suggestion",
    ),
    # --- discover slot ---
    Suggestion(
        title="Name my startup",
        subtitle="10 creative options",
        prompt="Give me 10 creative startup name ideas for an AI-powered personal assistant app. Include available .com domain suggestions.",
        slot_type="discover",
        category="creativity",
        icon="lightbulb",
        reasoning="Creative discovery task for new users",
    ),
    Suggestion(
        title="Plan a 7-day trip",
        subtitle="Day-by-day itinerary",
        prompt="Plan a 7-day trip to Japan for a first-time visitor. Include must-see spots, food recommendations, and a rough daily schedule.",
        slot_type="discover",
        category="lifestyle",
        icon="flight",
        reasoning="Aspirational lifestyle discovery suggestion",
    ),
    Suggestion(
        title="Learn something new",
        subtitle="30-day study plan",
        prompt="Create a 30-day study plan to learn Python from scratch. Include daily goals, resources, and mini-projects.",
        slot_type="discover",
        category="learning",
        icon="book",
        reasoning="Learning path discovery for curious new users",
    ),
]


def _score_curated(suggestion: Suggestion, signals: Dict[str, Any]) -> float:
    """
    Score a curated suggestion against contextual signals.

    Scoring:
        +2.0 if time_period keyword matches in title/subtitle
        +1.5 if is_weekend and "weekend" in title/subtitle
        +1.0 if is_mobile and ("quick" or "5-minute") in title/subtitle
        + small random tiebreaker (random.random() * 0.05)

    Returns a float score (higher = better match).
    """
    text = (suggestion.title + " " + suggestion.subtitle).lower()
    score = 0.0

    time_period = signals.get("time_period", "")
    if time_period and time_period in text:
        score += 2.0

    if signals.get("is_weekend") and "weekend" in text:
        score += 1.5

    if signals.get("is_mobile") and ("quick" in text or "5-minute" in text):
        score += 1.0

    score += random.random() * 0.05

    return score


def get_signal_based_suggestions(
    hour: int = 12,
    day_of_week: str = "",
    user_agent: str = "",
    per_slot: int = 1,
) -> List[Suggestion]:
    """
    Pick curated suggestions for a new user based on contextual signals.

    Scores all _CURATED_NEW_USER suggestions against signals and returns the
    top per_slot per slot type: today (most signal-dependent), continue, discover.

    Default is 1 per slot = 3 total (matching the 2x2 grid: 3 + More button).
    """
    signals = infer_signals(hour=hour, day_of_week=day_of_week, user_agent=user_agent)

    # Group and score by slot type
    by_slot: Dict[str, List[tuple]] = {}
    for s in _CURATED_NEW_USER:
        score = _score_curated(s, signals)
        by_slot.setdefault(s.slot_type, []).append((score, s))

    selected: List[Suggestion] = []

    # today slot first — most signal-dependent
    if "today" in by_slot:
        top = sorted(by_slot["today"], key=lambda x: x[0], reverse=True)[:per_slot]
        selected.extend(s for _, s in top)

    # continue slot
    if "continue" in by_slot:
        top = sorted(by_slot["continue"], key=lambda x: x[0], reverse=True)[:per_slot]
        selected.extend(s for _, s in top)

    # discover slot
    if "discover" in by_slot:
        top = sorted(by_slot["discover"], key=lambda x: x[0], reverse=True)[:per_slot]
        selected.extend(s for _, s in top)

    return selected


# ---------------------------------------------------------------------------
# Background Refresh Orchestrator
# ---------------------------------------------------------------------------

POOL_EXPIRY_HOURS = 6
CONVERSATION_THRESHOLD = 3


def should_refresh_pool(metadata: Dict[str, Any], conversations_since: int = 0) -> bool:
    """
    Determine whether the suggestion pool needs refreshing.

    Returns True if:
    - metadata is None or empty
    - no last_refresh in metadata
    - pool_size is 0
    - time since last_refresh exceeds POOL_EXPIRY_HOURS
    - conversations_since >= CONVERSATION_THRESHOLD

    Returns False otherwise.
    """
    if not metadata:
        return True

    last_refresh = metadata.get("last_refresh")
    if not last_refresh:
        return True

    pool_size = metadata.get("pool_size", 0)
    if pool_size == 0:
        return True

    if conversations_since >= CONVERSATION_THRESHOLD:
        return True

    # last_refresh may already be a datetime object
    if isinstance(last_refresh, datetime):
        refresh_dt = last_refresh
    else:
        try:
            refresh_dt = datetime.fromisoformat(last_refresh)
        except (ValueError, TypeError):
            return True

    # Ensure timezone-aware for comparison
    if refresh_dt.tzinfo is None:
        refresh_dt = refresh_dt.replace(tzinfo=timezone.utc)

    age = datetime.now(timezone.utc) - refresh_dt
    if age > timedelta(hours=POOL_EXPIRY_HOURS):
        return True

    return False


def _get_user_memory_context(db, user_id: str) -> Dict[str, Any]:
    """
    Gather context for LLM suggestion generation.

    Returns a dict with:
        memories (list), recent_topics (list), suggestion_profile (dict),
        exclusion_titles (list), hour (int), day_of_week (str)
    """
    now = datetime.now(timezone.utc)

    # Load memories
    memories: list = []
    try:
        from app.memory_v2.store import MemoryStoreV2
        store = MemoryStoreV2(user_id)
        raw_memories = store.get_all(include_expired=False)
        memories = raw_memories[:20]
    except Exception as exc:
        logger.warning("_get_user_memory_context: failed to load memories for %s: %s", user_id, exc)

    # Load recent session titles and user prompts
    recent_topics: list = []
    recent_prompts: list = []
    try:
        sessions_ref = (
            db.collection("users")
            .document(user_id)
            .collection("chat_sessions")
            .order_by("updated_at", direction="DESCENDING")
            .limit(5)
        )
        for doc in sessions_ref.stream():
            data = doc.to_dict() or {}
            title = data.get("title", "")
            if title:
                recent_topics.append(title)
            # Extract first user prompt from each session
            messages = data.get("messages", [])
            if isinstance(messages, list):
                for msg in messages[:3]:
                    if isinstance(msg, dict) and msg.get("prompt"):
                        prompt_text = msg["prompt"].strip()
                        if len(prompt_text) > 200:
                            prompt_text = prompt_text[:200] + "..."
                        recent_prompts.append(prompt_text)
                        break
    except Exception as exc:
        logger.warning("_get_user_memory_context: failed to load sessions for %s: %s", user_id, exc)

    # Load suggestion profile
    suggestion_profile: Dict[str, Any] = {}
    try:
        suggestion_profile = load_suggestion_profile(db, user_id)
    except Exception as exc:
        logger.warning("_get_user_memory_context: failed to load profile for %s: %s", user_id, exc)

    # Load current pool titles as exclusions
    exclusion_titles: list = []
    try:
        pool = load_pool(db, user_id)
        exclusion_titles = [s.title for s in pool]
    except Exception as exc:
        logger.warning("_get_user_memory_context: failed to load pool for %s: %s", user_id, exc)

    return {
        "memories": memories,
        "recent_topics": recent_topics,
        "recent_prompts": recent_prompts,
        "suggestion_profile": suggestion_profile,
        "exclusion_titles": exclusion_titles,
        "hour": now.hour,
        "day_of_week": now.strftime("%A"),
    }


def refresh_pool_background(db, user_id: str, reason: str) -> bool:
    """
    Generate a new suggestion pool in the background and save it.

    Returns True if suggestions were generated and saved, False otherwise.
    """
    try:
        context = _get_user_memory_context(db, user_id)
        suggestions = generate_pool_from_llm(context)
        if suggestions:
            save_pool(db, user_id, suggestions, reason)
            logger.info(
                "refresh_pool_background: saved %d suggestions for user %s (reason: %s)",
                len(suggestions),
                user_id,
                reason,
            )
            return True
        logger.warning(
            "refresh_pool_background: no suggestions generated for user %s", user_id
        )
        return False
    except Exception as exc:
        logger.warning(
            "refresh_pool_background: failed for user %s: %s", user_id, exc
        )
        return False
