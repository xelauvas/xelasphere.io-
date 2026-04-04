# Ghost Suggestion Model Selector

**Date:** 2026-04-04  
**Status:** Approved  
**Replaces:** Header dropdown model selector

## Summary

Replace the dropdown model selector with a "Ghost Suggestion" system. The AI auto-detects which model best fits the user's message and shows its choice as a faint ghost label below the chat input. The user can override by clicking the ghost. No dropdown, no menu, no header widget — just an intelligent hint that stays out of the way.

## Design Principles

- **Invisible until relevant** — no UI chrome consuming space
- **Smart by default** — leverages the existing smart router to pick models automatically
- **Overridable** — one click to veto the suggestion
- **Informational** — shows what model handled each response after the fact

## Components

### 1. Ghost Label (Idle State)

A faint right-aligned label below the chat input:

```
                          via Auto ⚡ ✦
```

- Font size: ~0.7rem
- Opacity: ~0.4, increases to 0.6 on hover
- `⚡` icon changes per detected task type (see Icon Map below)
- `✦` pulses once on page load to signal interactivity, then stays still
- Cursor: pointer on hover

### 2. Live Intent Detection

As the user types, the ghost label updates to reflect which model the smart router would pick. Detection runs client-side using regex pattern matching (a JS port of `smart_router.py`'s detection patterns).

**Behavior:**
- Debounce: 400ms after last keystroke before updating
- Transition: 300ms crossfade (fade out old, fade in new)
- Empty input: shows `via Auto ⚡`
- No API calls — purely client-side regex matching

**Task Type → Model → Icon mapping:**

| Task Type | Model | Icon |
|-----------|-------|------|
| chat (default) | Auto ⚡ | ⚡ |
| coding | DeepSeek V3 | 💻 |
| reasoning | Qwen 32B | 🧠 |
| vision | Llama 4 Maverick | 👁 |
| uncensored | Venice | 🔓 |
| long_context | Kimi Latest | 📚 |

**Client-side patterns (ported from smart_router.py):**

```javascript
const CODING_PATTERNS = [
    /\b(code|coding|program|function|class|method|api|debug|fix\s+(?:the\s+)?bug)\b/i,
    /\b(python|javascript|typescript|java|rust|go|c\+\+|html|css|sql|react|node)\b/i,
    /\b(implement|refactor|optimize|write\s+(?:a\s+)?(?:script|program|function))\b/i,
    /```\w*\n/,
    /\b(error|exception|traceback|stack\s*trace)\b/i,
];

const REASONING_PATTERNS = [
    /\b(explain|analyze|compare|evaluate|think\s+through|step\s+by\s+step)\b/i,
    /\b(why|how\s+does|what\s+(?:is|are)\s+the\s+(?:reason|cause|difference))\b/i,
    /\b(math|calculate|equation|formula|proof|logic)\b/i,
    /\b(pros?\s+and\s+cons?|trade-?offs?|advantages?\s+(?:and|vs)\s+disadvantages?)\b/i,
];

const UNCENSORED_PATTERNS = [
    /\b(uncensored|unfiltered|no\s+restrictions?|without\s+limits?)\b/i,
    /\b(roleplay|fiction|creative\s+writing|story)\b/i,
];
```

Detection requires 2+ pattern matches for coding/reasoning (same threshold as smart_router.py). Uncensored triggers on 1 match.

### 3. Override (Veto) — Desktop

Click the ghost label to expand an inline pill row below the input:

```
 Auto  Llama 70B  DeepSeek V3  Qwen 32B  DR1  Venice  Kimi  Gemma 4
                   ▔▔▔▔▔▔▔▔▔▔
```

**Behavior:**
- Pills appear with a 200ms slide-down animation
- The ghost-suggested model is pre-highlighted
- Click a pill to override — pills collapse, ghost updates
- Click away or press Escape — pills collapse, keeps ghost suggestion
- Override applies to **this message only** — resets after sending
- Permanent override: if user wants to lock a model, they double-click a pill. Ghost shows `via DeepSeek V3 💻 ×` with a clear button

**Pill styling:**
- Small, rounded, semi-transparent background
- Active/highlighted pill: slightly brighter, subtle border
- Font size: ~0.75rem
- Horizontal scroll if pills overflow container width

### 4. Override — Mobile

Click the ghost label to open a bottom sheet:

- Slides up from bottom, 2-column grid of models
- Each cell: icon + model name
- Tap to select, swipe down to dismiss
- Same override behavior as desktop (per-message or permanent)

### 5. Response Attribution

After the AI responds, the model that handled the response appears right-aligned at the bottom of the response bubble:

```
                          via DeepSeek V3
```

- Same styling as the input ghost: ~0.7rem, ~0.4 opacity
- If fallback occurred: `via Llama 70B (fallback)`
- Not clickable — purely informational
- Data comes from the existing `model_id` field in the chat response

### 6. Input Ghost Reset

After sending a message:
- If override was per-message: ghost resets to `via Auto ⚡`
- If override was permanent (double-click): ghost keeps showing the locked model with `×`
- Intent detection resumes on next keystroke

## Files Changed

### Deleted:
- `app/static/css/model-select.css` — replaced entirely
- Header dropdown HTML in `index.html` (the `model-select-wrapper` block)
- Mobile model selector HTML in `index.html` (the `mobile-model-select` block)

### New:
- `app/static/css/ghost-model.css` — ghost label, pills, bottom sheet styles
- `app/static/js/ghostModel.js` — intent detection, pill picker, ghost label logic

### Modified:
- `app/templates/index.html` — remove old dropdown, add ghost label HTML below input, add response attribution template
- `app/static/js/modelSelect.js` — gut the CustomDropdown class, replace with GhostModel class (or rename file)
- `app/static/css/mobile.css` — remove old mobile model selector styles, add bottom sheet styles

### Untouched:
- `app/model_catalog.py` — no changes
- `app/smart_router.py` — no changes
- `app/utilities.py` — no changes
- `app/services/chat_engine.py` — no changes (already returns model_id in response)

## Data Flow

```
User types → JS debounce (400ms) → regex intent detection → update ghost label
User clicks ghost → show pills/bottom sheet → user picks model → set override
User sends message → override sent as model param (or "auto") → backend smart_route() picks final model
Response arrives → model_id from response → render attribution in message bubble
Next message → reset override if per-message → ghost returns to auto-detect
```

## Edge Cases

- **Empty input:** Ghost shows `via Auto ⚡`
- **Guest users:** Ghost still shows auto-detection but override pills are disabled (same restriction as old dropdown)
- **Ollama down:** Backend fallback handles this transparently; response attribution shows `via Llama 70B (fallback)`
- **Very long pill row:** Horizontal scroll with fade-out edges on desktop; bottom sheet on mobile handles it naturally
- **User has images attached:** Ghost shows `via Llama 4 Maverick 👁` (vision detection)
- **Previous response was refused:** Ghost shows `via Venice 🔓` (refusal retry detection — this one can't be detected client-side from typing alone, so the backend still handles it; ghost shows the result after response)

## Accessibility

- Ghost label has `role="status"` and `aria-live="polite"` for screen readers
- Pills have `role="listbox"` with `role="option"` children
- Keyboard: Tab to focus ghost, Enter to open pills, arrow keys to navigate, Enter to select, Escape to close
- Bottom sheet has `role="dialog"` with focus trap
- All interactive elements have visible focus indicators
