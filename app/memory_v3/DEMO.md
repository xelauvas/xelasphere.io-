# V3 Adaptive Intelligence Demo

**Duration**: 2 minutes
**Purpose**: Show V3 adapts response style to context + user state

---

## Demo Script

Run these 4 prompts in sequence. Each shows a different V3 behavior mode.

### 1. DEBUG + RUSHED
```
User: "Fix this bug. Just tell me what to change."
```

**V3 Detection**:
- Context: DEBUG (error, fix)
- Pace: RUSHED (just, short message)
- Mode: EXECUTING

**Expected Response Style**:
- Short, step-by-step checklist
- No lengthy explanations
- Direct fixes only

---

### 2. DESIGN + DEEP
```
User: "Design a memory system for an AI assistant. Include trade-offs and architecture considerations."
```

**V3 Detection**:
- Context: DESIGN (design, architecture, trade-offs)
- Pace: DEEP (include, considerations)
- Mode: EXPLORING

**Expected Response Style**:
- Structured with sections
- Thorough exploration of options
- Pros/cons for each approach
- Takes time to be comprehensive

---

### 3. EXPLAIN + UNCERTAIN
```
User: "I don't really get what 'rate limiting' means. Can you help?"
```

**V3 Detection**:
- Context: EXPLAIN (what does X mean)
- Certainty: UNCERTAIN (don't get, help)
- Mode: EXPLORING

**Expected Response Style**:
- Simple, jargon-free explanation
- Concrete example
- Quick check question ("Does that make sense?")
- Reassuring tone

---

### 4. EXECUTE + CONFIDENT
```
User: "Generate a Python function that validates email addresses."
```

**V3 Detection**:
- Context: EXECUTE (generate)
- Certainty: CONFIDENT (direct request)
- Mode: EXECUTING

**Expected Response Style**:
- Delivers code directly
- Minimal preamble
- Brief usage note if needed
- No unnecessary explanation

---

## What to Highlight

1. **No Configuration Required** - V3 detects context automatically
2. **Adapts in Real-Time** - Each message is analyzed fresh
3. **No User Data Stored** - Signals are ephemeral (TTL-based)
4. **Tunable** - V3_INTENSITY controls how much adaptation happens

---

## Live Demo Commands

```python
from app.memory_v3 import generate_v3_signal, get_v3_prompt_block

# Show what V3 detects for each prompt
prompts = [
    "Fix this bug. Just tell me what to change.",
    "Design a memory system for an AI assistant. Include trade-offs.",
    "I don't really get what 'rate limiting' means. Can you help?",
    "Generate a Python function that validates email addresses.",
]

for prompt in prompts:
    signal = generate_v3_signal(prompt)
    print(f"\nPrompt: {prompt[:50]}...")
    print(f"Context: {signal.context.mode.value}")
    print(f"Pace: {signal.state.pace.value}")
    print(f"Certainty: {signal.state.certainty.value}")
    print(f"Verbosity: {signal.verbosity}")
    print(f"Tone: {signal.tone}")
```

---

## Selling Points

| Without V3 | With V3 |
|------------|---------|
| Same verbose response every time | Adapts to user's pace |
| User has to ask "shorter please" | Detects rushed users automatically |
| Confused users get same tone | Reassuring tone when uncertain |
| Design questions get quick answers | Thorough when depth requested |

**Bottom line**: V3 makes your AI feel like it's actually listening.
