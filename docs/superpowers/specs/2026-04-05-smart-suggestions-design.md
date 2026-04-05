# Smart Suggestions System — Design Spec

## Overview

A personalized suggestion system that learns from user memory, adapts to behavior, and creates a compelling first impression. Suggestions are precomputed by LLM in the background, stored in Firestore, and served instantly on app load. The system transitions seamlessly from signal-inferred suggestions for new users to deeply personalized ones for returning users.

## The Three Slots

Every app load shows exactly 3 suggestion cards + More button.

| Slot | Purpose | Returning User Label (6+ sessions) | New User Fallback |
|------|---------|-------------------------------------|-------------------|
| **Continue** | Resume unfinished work or revisit recent topics | `pick up where you left off` | Best demo suggestion from curated pool |
| **Today** | Time-aware, context-aware, pattern-aware | `for your {morning/evening/weekend}` | Signal-inferred from time + device |
| **Discover** | Fresh category the user hasn't explored | `something new` | Random from curated pool |

"More" cycles through the remaining precomputed pool (10 total, 3 displayed). When exhausted, triggers a background refresh.

## Hybrid Architecture

### On App Load (instant, no LLM)

1. Fetch 10 precomputed suggestions from Firestore `users/{uid}/smart_suggestions`
2. Pick best 3 using slot logic: 1 continue + 1 today + 1 discover
3. "Today" slot uses current time/day to pick the most relevant from pool
4. Display 3 + More
5. "More" cycles through remaining 7

### Background LLM Generation (not on the hot path)

Triggered by: Dream consolidation, 3+ new conversations, pool older than 6 hours, pool exhausted, or first conversation for new users.

**LLM Input:**
- User memory (profile, preferences, projects, feedback, references)
- suggestion_profile (click patterns, preferred categories, avoided categories)
- Last 5 conversation topics (from session titles)
- Current day of week + time of day
- Previously shown suggestions (exclusion list)
- never_shown_categories

**LLM Output:** JSON array of 10 suggestions:

```json
[
  {
    "title": "Continue your Flask chat app",
    "subtitle": "Add the cost tracking feature",
    "prompt": "Help me add real-time token cost tracking to my Flask chat app. Show cost per message and total session cost.",
    "slot_type": "continue",
    "category": "coding",
    "reasoning": "User is building xelasphere, mentioned cost tracking in gap analysis"
  }
]
```

**LLM Generation Rules:**
- Every `prompt` must be specific enough to produce an immediate result (no follow-up questions needed)
- `continue` slots must reference real projects/topics from memory — never hallucinate
- `today` slots must be relevant to current time/day
- `discover` slots must be from categories the user hasn't clicked
- Never repeat a suggestion shown in the last 3 sessions
- `reasoning` field stored for debugging, never shown to user

## Firestore Storage

```
users/{uid}/smart_suggestions/
  pool: [
    {
      title: string,
      subtitle: string,
      prompt: string,
      slot_type: "continue" | "today" | "discover",
      category: string,
      reasoning: string,
      created_at: timestamp,
      shown_count: number
    }
  ]
  last_refresh: timestamp
  refresh_reason: "dream" | "conversations" | "expired" | "exhausted" | "first_conversation"
  suggestion_profile: {
    top_categories: ["coding", "writing"],
    avoided_categories: ["wellness"],
    preferred_slot: "continue",
    avg_pool_consumption: 6.5,
    engagement_by_category: { coding: 0.85, planning: 0.60, creative: 0.40 }
  }
  click_history: [
    {
      category: string,
      slot_type: string,
      title: string,
      clicked_at: timestamp,
      resulted_in_conversation: boolean,
      conversation_length: number
    }
  ]
  never_shown_categories: [string]
```

## Refresh Triggers

| Trigger | Condition | Why |
|---------|-----------|-----|
| Dream consolidation | After memory dream runs | Memory changed, suggestions should reflect new knowledge |
| Conversation count | 3+ new conversations since last refresh | User has new context |
| Time expiry | Pool older than 6 hours | Keep "today" slot relevant |
| Pool exhausted | User clicked "More" through all 10 | Need fresh ideas |
| First conversation | New user finishes first chat | Bootstrap personalization |

Refresh is always background. User never waits. If refresh is in progress, serve existing pool. If pool is empty (new user), fall back to curated signal-based suggestions.

## New User First Impression

### Available Signals (zero friction)

| Signal | Inference | Example |
|--------|-----------|---------|
| Time of day | Morning = planning, Evening = creative/leisure | 9 AM → "Plan your day" |
| Day of week | Weekday = work, Weekend = personal | Saturday → "Weekend project" |
| Device | Mobile = quick tasks, Desktop = deep work | Phone → shorter suggestions |
| Browser language | Cultural relevance | `ar` → Arabic-friendly content |
| Referral source | Why they came | From GitHub → coding suggestions |
| UTM params | Campaign context | utm_content=writers → writing suggestions |

### Selection Logic

1. Score every suggestion in curated pool against available signals
2. Pick top 1 for "today" slot (time-weighted)
3. Pick top 1 for "continue" slot (most universally compelling — demo slot for new users)
4. Pick 1 random for "discover" slot
5. After first conversation, trigger LLM generation in background

### Transition Timeline

| Visit | Experience |
|-------|-----------|
| 1st (no memory) | Signal-inferred from curated pool. No labels. |
| 2nd (1 conversation) | First LLM-generated pool. Partially personalized. No labels. |
| 3rd-5th | Increasingly personalized. Still no labels. |
| 6th+ | Full personalization. Subtle labels: `pick up where you left off`, `for your evening`, `something new` |

## Click Tracking & Learning Loop

### What Gets Tracked

**On click:**
```
{
  category: "coding",
  slot_type: "continue",
  title: "Continue your Flask chat app",
  clicked_at: timestamp,
  resulted_in_conversation: true,
  conversation_length: 8
}
```

**On ignore:** Suggestions shown but never clicked increment `shown_count`. After `shown_count >= 3` with no click, suggestion is retired from pool.

### How Learning Feeds Back

| Signal | Effect on Next Generation |
|--------|--------------------------|
| Clicks coding 70% of the time | LLM told: "4-5 of 10 suggestions should be coding" |
| Never clicks wellness | Added to `never_shown_categories` |
| Clicks "continue" most | LLM generates more continue-type (4 of 10 instead of 3) |
| Long conversations after "discover" | Weight discover slot higher |
| Clicks "More" frequently | Increase pool from 10 to 15 on next refresh |

The `suggestion_profile` is updated during Dream consolidation and passed to LLM on next generation.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `app/services/smart_suggestions.py` | **New** | Core: generation, slot selection, signal inference, click tracking |
| `app/memory_v2/dream/phase2.py` | **Modify** | Add suggestion refresh trigger after dream consolidation |
| `app/blueprints/pages_routes.py` | **Modify** | Serve precomputed suggestions, handle click tracking |
| `app/blueprints/chat_routes.py` | **Modify** | After 3 conversations, trigger background refresh |
| `app/static/js/fetchMoreOptions.js` | **Modify** | Consume new API, cycle through pool, report clicks |
| `app/grog_api.py` | **Modify** | Keep curated pool as fallback only |

No changes to: CSS/layout, scrollState.js, memory V2 schema.

## Curated Pool (Safety Net)

The existing high-quality suggestions remain as the fallback for:
- New users before first LLM generation
- LLM generation failures
- Rate-limited or offline scenarios

These are the specific, actionable suggestions already in `grog_api.py` and `fetchMoreOptions.js`.
