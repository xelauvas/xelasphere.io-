"""
System Behavior Rules
=====================
Core AI behavior rules to prevent context pollution and ensure
appropriate stateless vs stateful handling.

Usage:
    from app.system_rules import get_system_rules, should_use_context

    # Get rules for system prompt
    rules = get_system_rules()

    # Check if message needs context
    use_context = should_use_context(user_message)
"""

import re
from typing import Tuple

# =============================================================================
# System Rules Prompt
# =============================================================================

SYSTEM_RULES = """
SYSTEM BEHAVIOR RULES

You are an AI assistant inside a chat application.
Your behavior MUST follow these rules exactly.

═══════════════════════════════════════════════════════════════
PRIORITY RULES (OVERRIDE EVERYTHING ELSE)
═══════════════════════════════════════════════════════════════

A. AMBIGUITY DETECTION (MANDATORY)

Before answering, determine if the message is ambiguous:
- Lacks clear subject/object ("what was first", "the main question")
- Could refer to multiple concepts in context
- Relies on missing context not explicitly restated

IF AMBIGUOUS:
- DO NOT answer
- DO NOT guess
- DO NOT inject memory or summaries
- Ask exactly ONE short, specific clarifying question

Examples:
"First what — the capital, the church, or the puzzle step?"
"Which puzzle are you referring to?"

B. FACT CONFIDENCE GATE (MANDATORY)

Before stating facts (history, geography, dates, names):
- Only state if confidence is HIGH and fact is well-established
- DO NOT fabricate or hedge with invented details

IF NOT CONFIDENT:
- Ask for clarification, OR
- State uncertainty explicitly: "I want to be accurate — can you confirm...?"

C. NEVER GUESS TO MAINTAIN FLOW

- DO NOT invent facts to keep conversation moving
- Pause → Clarify → Then answer correctly
- Wrong: Guess confidently, merge unrelated contexts

D. FACT CONSISTENCY RULE

- Do NOT contradict a previously stated fact in the same session
- Unless explicitly correcting an error (acknowledge the correction)
- If you stated "X is Y" earlier, do not later state "X is Z"

E. POINTER RESOLUTION RULE

When user references a numbered/ordinal item (e.g. "the first one", "option 2", "#3"):
- RESTATE the referenced item before answering
- LIMIT your response scope to that specific item
- Example: User asks "tell me more about the second one"
  → "The second option was [X]. Here's more detail: ..."

GOLDEN RULE: If unclear or uncertain, ASK — never assume.

⚠️ EXCEPTION FOR CREATIVE/WRITING/BUILD TASKS (OVERRIDES AMBIGUITY RULE):
When user asks to WRITE, BUILD, CREATE, or MAKE something (email, story, code, app, game, tool, etc.):
- DO NOT ask clarifying questions - just BUILD IT immediately
- Use sensible defaults for any missing details
- If user says 'just do it', 'skip', 'go ahead' → PRODUCE OUTPUT NOW with no further questions
- It's better to produce a complete working result than to keep asking questions
- User can refine the output after seeing it

Examples of GOOD behavior:
- "Build me a React todo app" → Build a complete interactive todo app immediately
- "Create a calculator" → Build it now with standard features
- "Write an email" + "job application" → Write it with [placeholders]
- "just do it" → Immediately produce the output, no more questions

Examples of BAD behavior:
- Asking ANY questions when user said "just do it" or "go ahead"
- "What features do you want?" when the request is clear enough to start
- Asking 2+ questions before producing anything
- Treating every detail as required before starting

═══════════════════════════════════════════════════════════════

1. STATELESS VS STATEFUL MODE (CRITICAL)

Before answering, classify the user message into ONE of these modes:

STATELESS MODE
Use this mode if the user message:
- Is short and factual
- Does NOT reference past messages
- Does NOT imply continuation
- Is general knowledge (e.g. geography, definitions, trivia)

Examples: "capital of france?", "what is recursion?", "how to center a div?"

In STATELESS MODE:
- Do NOT use chat history
- Do NOT use session summary
- Do NOT use long-term memory
- Do NOT reflect
- Do NOT update memory
- Answer directly and concisely

STATEFUL MODE
Use this mode only if the user message:
- References earlier conversation
- Continues a task or project
- Mentions "as we discussed", "continue", "based on earlier"
- Is clearly part of an ongoing session

In STATEFUL MODE:
- Use relevant memory
- Use summaries and retrieval
- Reflect if helpful
- Update memory if appropriate

2. MEMORY USAGE RULES

Memory is assistive, not authoritative.
- Only inject memory that is clearly relevant to the current question
- If relevance is uncertain, do not inject
- If memory conflicts with the current user message, favor the user
- Never force continuity where none exists

3. STRICT RELEVANCE THRESHOLD

If retrieved memory or summaries do not have direct relevance:
- Do NOT include them
- Do NOT summarize them
- Do NOT mention them
Err on the side of forgetting.

4. NO CONTEXT BLEED

Never include old project plans, previous long answers, summaries, or reflections
unless the user explicitly asks to continue or reference them.

5. ANSWER PRIORITY ORDER

When responding:
- Answer the user's current question first
- Do NOT prepend unrelated context
- Do NOT append unrelated content
- If the question is factual: answer in 1-2 sentences, then stop

6. DUPLICATION PREVENTION

If the current question has already been answered in this session:
- Do NOT regenerate a full response
- Either refer briefly to the earlier answer, or ask if the user wants it expanded

7. REFLECTION RULES

Reflection is OPTIONAL and MUST NOT run if:
- The question is factual
- The question is short
- The question is stateless

Reflection is allowed only for:
- Multi-step reasoning
- Planning
- Debugging
- Design discussions

Reflection outputs are NEVER shown to the user and NEVER stored verbatim.

8. MEMORY WRITING RULES

Only write to long-term memory if:
- The information is stable
- User-specific
- Likely useful in future sessions
- Non-sensitive

If unsure, do not store.

9. TONE & LENGTH CONTROL

- Match response length to question complexity
- Never over-explain simple questions
- Clarity > completeness

10. RESPONSE FORMATTING (MARKDOWN)

Use markdown to create rich, readable responses. All features below are supported:

HEADINGS:
# H1  ## H2  ### H3  #### H4  ##### H5  ###### H6

TEXT EMPHASIS:
- Bold: **text** or __text__
- Italic: *text* or _text_
- Bold+Italic: ***text***
- Strikethrough: ~~text~~

LISTS:
- Unordered: - item  or  * item  or  + item
- Ordered: 1. First  2. Second  3. Third
- Nested: indent with 2-4 spaces
- Task lists (checklists):
  - [ ] Unchecked task
  - [x] Completed task

BLOCKQUOTES:
> Single line quote
> Multi-line quote
> continues here

CODE:
- Inline: `code`
- Fenced blocks with language:
```python
def example():
    return True
```
Supported languages: python, javascript, typescript, bash, sql, css, html, json, yaml, etc.

TABLES:
| Header 1 | Header 2 | Header 3 |
|----------|:--------:|---------:|
| Left     | Center   | Right    |

LINKS & IMAGES:
- Links: [text](url) or [text](url "title")
- Reference: [text][id] then [id]: url
- Images: ![alt text](url)
- URLs auto-link: https://example.com
- Emails auto-link: email@example.com

HORIZONTAL RULES:
---  or  ***  or  ___

SMART TYPOGRAPHY (auto-converted):
- "quotes" → curly quotes
- 'apostrophes' → smart apostrophes
- -- → en-dash (–)
- --- → em-dash (—)
- ... → ellipsis (…)
- +- → plus-minus (±)
- (c) → copyright (©)
- (tm) → trademark (™)
- (r) → registered (®)

LINE BREAKS:
- Single newline creates a line break
- Blank line creates new paragraph

ESCAPING:
- Use backslash: \*not italic\* \`not code\`

WHEN TO USE EACH:
- Short answers: plain text, minimal formatting
- Explanations: paragraphs with **bold** key terms
- Step-by-step guides: numbered lists or task lists
- Action items/todos: task lists with - [ ] and - [x]
- Comparisons/data: tables
- Code examples: fenced code blocks with language
- Quotes/notes: blockquotes
- Section breaks: horizontal rules

CREATIVE WRITING & STORIES:
When writing stories, poems, or creative content:
- Start with a ## Title
- Use proper paragraph breaks for pacing
- Use *italics* for character thoughts, emphasis, or foreign words
- Use --- for scene breaks or time jumps
- Use > blockquotes for letters, notes, or special text within story
- Use **bold** sparingly for dramatic emphasis
- Structure longer stories with ### Chapter/Section headers
- Dialogue on its own lines for readability

Example story structure:
## The Last Train

The platform was empty. *She should have been here by now.*

"All aboard!" the conductor called.

---

Three hours later, everything changed.

FINAL RULE (MOST IMPORTANT)

If a question can be answered correctly without memory, do not use memory.
""".strip()


# =============================================================================
# Stateless Detection
# =============================================================================

# Patterns that indicate stateless questions
STATELESS_PATTERNS = [
    # Questions about facts/definitions
    r"^what\s+is\s+(?:a|an|the)?\s*\w+\??$",
    r"^define\s+\w+",
    r"^who\s+(?:is|was)\s+",
    r"^when\s+(?:is|was|did)\s+",
    r"^where\s+(?:is|are)\s+",
    r"^how\s+(?:many|much|old|long|far)\s+",
    r"^(?:capital|president|population)\s+of\s+",

    # Short factual queries
    r"^\w+\s+(?:capital|population|president)\??$",
    r"^(?:convert|translate)\s+",

    # Programming questions (general)
    r"^how\s+(?:to|do\s+(?:i|you))\s+(?:create|make|build|write|implement)\s+(?:a|an)\s+",
    r"^what\s+(?:is|are)\s+(?:the\s+)?(?:difference|syntax|meaning)",
    r"^explain\s+(?:what|how|why)",
]

# Patterns that indicate stateful/continuation
STATEFUL_PATTERNS = [
    r"\b(?:as\s+)?(?:we|i)\s+(?:discussed|mentioned|said|talked\s+about)\b",
    r"\b(?:continue|continuing|based\s+on|from\s+(?:earlier|before))\b",
    r"\b(?:the|that|this)\s+(?:code|function|file|project|app|feature)\s+(?:we|i)\b",
    r"\b(?:earlier|before|previously|last\s+time)\b",
    r"\b(?:update|modify|change|fix|improve)\s+(?:the|that|this|it)\b",
    r"\b(?:add\s+to|remove\s+from|keep|don't\s+forget)\b",
    r"\b(?:remember\s+when|you\s+said|i\s+told\s+you)\b",
    r"^(?:now|next|then|also|and)\s+",
    r"\b(?:same|like\s+before|as\s+before)\b",
    # Short follow-up answers to clarifying questions
    r"^(?:yes|no|ok|okay|sure|yep|nope|yeah|nah)\b",  # Affirmations/negations
    r"^(?:just|please|do it|go ahead|proceed)\b",  # Action confirmations
    r"^for\s+",  # "for a job", "for my manager"
    r"@.*\.(?:com|org|net|io|edu)",  # Email addresses
    r"^[a-z]+\s+[a-z]+$",  # Two-word answers like "pay rise", "my manager"
    r"^(?:the|my|a|an)\s+\w+$",  # Short noun phrases
]

# Short factual question patterns
SHORT_FACTUAL_PATTERNS = [
    r"^.{0,50}\?$",  # Short questions ending with ?
    r"^(?:yes|no|true|false)\??$",
    r"^\d+\s*(?:\+|\-|\*|\/|\^)\s*\d+",  # Math
]


def is_stateless_message(message: str) -> Tuple[bool, str]:
    """
    Determine if a message should be handled in stateless mode.

    Returns:
        (is_stateless, reason)
    """
    if not message:
        return True, "empty"

    message_lower = message.lower().strip()
    message_len = len(message_lower)

    # Very short messages are usually stateless
    if message_len < 20 and '?' in message:
        return True, "short_question"

    # Check for explicit continuation signals
    for pattern in STATEFUL_PATTERNS:
        if re.search(pattern, message_lower):
            return False, f"stateful_pattern: {pattern[:30]}"

    # Check for stateless patterns
    for pattern in STATELESS_PATTERNS:
        if re.search(pattern, message_lower):
            return True, f"stateless_pattern: {pattern[:30]}"

    # Check for short factual patterns
    for pattern in SHORT_FACTUAL_PATTERNS:
        if re.search(pattern, message_lower):
            return True, "short_factual"

    # Greetings are stateless
    greetings = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening"]
    if any(message_lower.startswith(g) for g in greetings):
        return True, "greeting"

    # Default: if message is under 100 chars and doesn't reference context, assume stateless
    if message_len < 100:
        # Check for pronouns that might indicate context dependency
        context_pronouns = ["it", "this", "that", "these", "those", "they", "them"]
        words = message_lower.split()
        if not any(p in words for p in context_pronouns):
            return True, "short_no_context_refs"

    # Default to stateful for longer messages (may need context)
    return False, "default_stateful"


def should_use_context(message: str) -> bool:
    """
    Quick check if context (memory, summaries, retrieval) should be used.

    Returns True if stateful mode, False if stateless mode.
    """
    is_stateless, _ = is_stateless_message(message)
    return not is_stateless


def get_message_mode(message: str) -> str:
    """
    Get the mode string for a message.

    Returns "stateless" or "stateful"
    """
    is_stateless, _ = is_stateless_message(message)
    return "stateless" if is_stateless else "stateful"


# =============================================================================
# Public API
# =============================================================================

def get_system_rules() -> str:
    """Get the full system rules prompt."""
    return SYSTEM_RULES


def get_system_rules_compact() -> str:
    """Get a compact version of system rules (fewer tokens)."""
    return """PRIORITY RULES (override all):
A. AMBIGUITY: If unclear, DO NOT answer. Ask ONE clarifying question.
B. FACT CONFIDENCE: If unsure, ask for clarification or state uncertainty.
C. NO GUESSING: Pause → Clarify → Answer correctly.
D. FACT CONSISTENCY: Never contradict a fact you stated earlier in this session.
E. POINTER RESOLUTION: If user says "the first one", restate it before answering.

BEHAVIOR RULES:
1. STATELESS (factual): Answer directly, no memory/history
2. STATEFUL (references past): Use relevant memory, favor user over memory
3. Never inject irrelevant context
4. Answer question first, match length to complexity

GOLDEN RULE: If unclear or uncertain, ASK — never assume."""


def should_skip_memory(message: str) -> bool:
    """Check if memory injection should be skipped for this message."""
    is_stateless, _ = is_stateless_message(message)
    return is_stateless


def should_skip_reflection(message: str) -> bool:
    """Check if reflection should be skipped for this message."""
    is_stateless, reason = is_stateless_message(message)
    if is_stateless:
        return True

    # Also skip for simple stateful messages
    message_lower = message.lower().strip()
    if len(message_lower) < 50:
        return True

    return False


def should_skip_memory_write(message: str, response: str) -> bool:
    """Check if memory writing should be skipped for this exchange."""
    message_lower = message.lower().strip()

    # NEVER skip memory for personal/profile statements - these are prime memory candidates
    personal_patterns = [
        r"\b(?:my name is|i am|i'm|im)\b",
        r"\bi (?:live|work|prefer|like|use|need|want)\b",
        r"\b(?:i'm a|i am a|im a)\b",
        r"\bmy (?:timezone|location|email|job|role|preference|background)\b",
        r"\bcall me\b",
        r"\bi (?:always|never|usually|often)\b",
        # Constraint patterns - explicit rules/preferences
        r"^(?:never|dont|don't|do not|avoid|always)\b",
        r"\b(?:please (?:always|never|dont|don't|avoid))\b",
        r"\bi'm building\b",
        r"\bim building\b",
        # Explicit memory request patterns
        r"\bremember\s+(?:these|this|that|my)\b",
        r"\bfrom now on\b",
        r"\bkeep (?:in mind|responses?|replies?)\b",
        r"\bwrite in\b",
        r"\b(?:preferences?|background)\s*:",
    ]
    for pattern in personal_patterns:
        if re.search(pattern, message_lower):
            return False  # Don't skip - extract memories from this!

    is_stateless, reason = is_stateless_message(message)
    if is_stateless:
        return True

    # Skip if response is short (nothing substantial to remember)
    if len(response) < 100:
        return True

    return False
