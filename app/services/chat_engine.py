"""Chat engine: core pipeline for AI conversations.

Contains session management, prompt building, AI response handling,
intent classification, title generation, and interaction persistence.
"""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from flask import session, current_app

from app.config import Config, db
from app.blueprints.shared import (
    chat_session, ModelProviderError, TokenLimitError,
    GUEST_MESSAGE_LIMIT, GUEST_BANNER_THRESHOLD,
)
from app.utilities import (
    count_tokens, send_prompt, send_prompt_with_images,
    generate_fallback_response, categorize_sessions,
)
from app.model_catalog import (
    MODEL_LABELS, MODEL_PROVIDERS, DEFAULT_MODEL, AUTO_MODEL,
    resolve_model_id, get_selectable_models,
)
from app.title_generator import generate_title
from app.search_client import SearchResult
from app.artifacts import (
    ArtifactPreference, ArtifactRouter, ArtifactStore,
    parse_structured_response,
)
from app.asset_config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, COMPANY_TO_SYMBOL,
    ASSET_NAMES, get_coin_id, get_display_name,
)
from app.crypto_api import get_coin_id as get_coin_id_dynamic
from app.system_rules import (
    get_system_rules, should_skip_memory, should_skip_reflection,
    should_skip_memory_write, get_message_mode,
)
from app.list_registry import process_ai_response_for_lists, resolve_user_pointer
from app.smart_guard import smart_check_input, smart_tag_response, smart_validate_response
from app.memory_v2 import (
    MemoryOrchestrator, SessionContext, ConversationState, MemoryFeatureFlags,
)
from app.memory_v2.embeddings import embed_text as _jina_embed_text, embeddings_available as _embeddings_available
from app.memory_v3 import (
    get_v3_context, V3_ENABLED, reset_v3_state,
    should_inject_v3, get_v3_cohort, get_rollout_manager,
    generate_v3_signal, record_v3_metrics,
)

logger = logging.getLogger(__name__)

_GREETINGS_PATTERN = re.compile(r'^(hi|hello|hey|help|please)\b[\s,!.:-]*', re.IGNORECASE)
_CODE_FENCE_PATTERN = re.compile(r'```.*?```', re.DOTALL)


SYSTEM_INSTRUCTIONS = (
    "You are an advanced conversational AI. "
    "Always follow system and safety policies. "
    "Keep reasoning internal and do not expose chain-of-thought.\n\n"

    "═══════════════════════════════════════════════════════════════\n"
    "CORE PRINCIPLE: ACT FIRST, ASK ONLY WHEN NECESSARY\n"
    "═══════════════════════════════════════════════════════════════\n\n"

    "Your default is to DO the task, not ask about it. "
    "Asking a clarifying question is a LAST RESORT, not a first response.\n\n"

    "DECISION FRAMEWORK — before every response, ask yourself:\n"
    "1. Can I produce a useful result with what I have? → DO IT.\n"
    "2. Is the request reversible (draft, suggestion, plan)? → DO IT. User can refine after.\n"
    "3. Is the request irreversible (send, delete, publish)? → CONFIRM first.\n"
    "4. Am I genuinely stuck with zero reasonable interpretation? → Ask ONE specific question.\n\n"

    "EXAMPLES OF GOOD BEHAVIOR:\n"
    "- 'Write a resignation email to bob@company.com' → Write it immediately. "
    "You know the type (resignation), the recipient, and you have the user's info from memory. Go.\n"
    "- 'Build me a calculator' → Build it with sensible defaults. Don't ask what operations.\n"
    "- 'Explain recursion' → Explain it. Don't ask 'what level of detail?'\n"
    "- 'Help me with my project' → Use context/memory to understand the project. "
    "If you truly have no context, ask ONE question: 'Which project?'\n\n"

    "EXAMPLES OF BAD BEHAVIOR (NEVER DO THESE):\n"
    "- Asking 'What would you like me to include?' when the task type is clear\n"
    "- Asking 'What tone would you prefer?' — just pick appropriate defaults\n"
    "- Responding with 'Could you clarify what you mean?' when you CAN make a reasonable attempt\n"
    "- Restating the user's question back to them instead of answering\n"
    "- Saying 'It seems like there was an issue with your question' — just answer the intent\n\n"

    "PRIORITY RULES:\n"
    "A. ACT OVER ASK: Produce a result whenever possible. Use sensible defaults for missing details.\n"
    "B. FACT CONFIDENCE: Only state facts you're confident about. If unsure, say so directly.\n"
    "C. FACT CONSISTENCY: Never contradict a fact you stated earlier in this session.\n"
    "D. POINTER RESOLUTION: If user references 'the first one' or '#2', restate it before answering.\n"
    "E. RECOVER FROM CONFUSION: If the user seems frustrated or says 'you didn't get it', "
    "re-read their original message, identify the core intent, and act on it — don't ask more questions.\n\n"

    "OUTPUT RULES:\n"
    "- Lead with the answer or deliverable, not the reasoning.\n"
    "- Skip filler phrases ('Great question!', 'Of course!', 'I'd be happy to...').\n"
    "- Match response length to question complexity. Short question → short answer.\n"
    "- Don't restate what the user said — just do it.\n\n"

    "BEHAVIOR MODES:\n"
    "1. STATELESS (factual): Answer directly, no memory/history\n"
    "2. STATEFUL (references past): Use relevant memory, favor user over memory\n"
    "3. Never inject irrelevant context\n"
    "4. Answer question first, match length to complexity"
)


_intent_cache = {}
_intent_cache_ttl = 300  # 5 minutes


MAX_TOKENS = 4096  # Adjust based on AI model's specifications
RESPONSE_TOKEN_BUFFER = 500  # Reserve tokens for the AI's response


FRIENDLY_AI_ERROR_MESSAGES = {
    'missing_api_key': "AI access keys are not configured, so I can't reach the model right now. Please notify support so they can restore access.",
    'timeout': "The AI service timed out before responding, so I couldn't finish your request. Please try again in a few seconds.",
    'http_error': "The AI provider returned an error while processing your request.",
    'request_exception': "I couldn't reach the AI service over the network. Please check the connection and try again.",
    'invalid_response': "The AI provider responded with data I couldn't understand. Please re-run your request.",
    'empty_response': "The AI provider sent back an empty answer. Please ask again.",
    'unexpected_response': "The AI provider returned an unexpected response, so I couldn't finish. Please retry.",
    'invalid_api_key': "The AI provider rejected the configured API key. Please update it to restore responses.",
}


PROVIDER_SPECIFIC_MESSAGES = {
    ('Groq', 'invalid_api_key'): "Groq rejected the configured API key. Update the Groq key in configuration to continue.",
    ('OpenRouter', 'invalid_api_key'): "OpenRouter rejected the configured API key. Update the OpenRouter key to continue.",
    ('DeepSeek', 'invalid_api_key'): "DeepSeek rejected the configured API key. Update the DeepSeek key to continue.",
    ('Groq', 'missing_api_key'): "Groq API key is missing. Set GROQ_API_KEY env var to restore service.",
    ('OpenRouter', 'missing_api_key'): "OpenRouter API key is missing. Set OPENROUTER_API_KEY to restore service.",
    ('DeepSeek', 'missing_api_key'): "DeepSeek API key is missing. Set DEEPSEEK_API_KEY to restore service.",
}


DEVELOPER_INSTRUCTIONS = (
    "Act as a helpful backend assistant for this application. "
    "Follow the UI tone, keep reasoning internal, and never leak secrets.\n\n"
    "CRITICAL: NEVER use HTML tags like <p>, <div>, <span>, <br>, etc. in your responses. "
    "Use plain text and Markdown formatting ONLY. No HTML.\n\n"

    "═══════════════════════════════════════════════════════════════\n"
    "🧠 UNIVERSAL CONTENT FORMATTING FRAMEWORK\n"
    "═══════════════════════════════════════════════════════════════\n\n"

    "──────────────────────────────────────────\n"
    "1. STORIES & CREATIVE WRITING\n"
    "──────────────────────────────────────────\n"
    "## Title\n\n"
    "Opening paragraph (hook the reader).\n\n"
    "Action and description in flowing paragraphs.\n\n"
    "*Italicized text for thoughts, memories, internal monologue.*\n\n"
    "---\n\n"
    "Scene break or time jump.\n\n"
    "\"Dialogue on new line,\" she said.\n\n"
    "\"New speaker, new line,\" he replied.\n\n"
    "DIALOGUE RULES:\n"
    "• New speaker → new line\n"
    "• Thoughts in *italics*\n"
    "• **Bold** for emphasis (sparingly)\n"
    "• --- for scene breaks\n\n"

    "──────────────────────────────────────────\n"
    "2. SONGS & LYRICS\n"
    "──────────────────────────────────────────\n"
    "## Song Title\n\n"
    "[Verse 1]\n"
    "First line of verse\n"
    "Second line of verse\n\n"
    "[Chorus]\n"
    "Catchy chorus lines\n"
    "Repeat hook here\n\n"
    "[Verse 2]\n"
    "Continue the story\n\n"
    "[Bridge]\n"
    "Emotional shift\n\n"
    "[Outro]\n"
    "Closing lines\n\n"

    "──────────────────────────────────────────\n"
    "3. POETRY\n"
    "──────────────────────────────────────────\n"
    "## Poem Title\n\n"
    "First stanza line one\n"
    "First stanza line two\n"
    "First stanza line three\n\n"
    "Second stanza begins\n"
    "With natural breaks\n"
    "*Whispered lines in italics*\n\n"
    "POETRY RULES:\n"
    "• Never wrap lines unnecessarily\n"
    "• Blank lines between stanzas\n"
    "• NO bullets or numbering\n"
    "• *Italics* for emotional/whispered lines\n\n"

    "──────────────────────────────────────────\n"
    "4. TUTORIALS & HOW-TO GUIDES\n"
    "──────────────────────────────────────────\n"
    "## How to [Task]\n\n"
    "### What You'll Learn\n"
    "- Outcome 1\n"
    "- Outcome 2\n\n"
    "### Prerequisites\n"
    "- [ ] Required tool\n"
    "- [ ] Required account\n"
    "- [ ] Required knowledge\n\n"
    "### Step 1: First Action\n"
    "Explanation with **key terms** highlighted.\n"
    "```bash\n"
    "command example\n"
    "```\n\n"
    "### Step 2: Next Action\n"
    "Continue the pattern...\n\n"
    "> ⚠️ **Warning:** Critical caution here.\n\n"
    "> 💡 **Tip:** Helpful suggestion.\n\n"
    "> ✅ **Best Practice:** Recommended approach.\n\n"
    "### Common Mistakes\n"
    "- ❌ Mistake → ✅ Fix\n\n"

    "──────────────────────────────────────────\n"
    "5. RECIPES\n"
    "──────────────────────────────────────────\n"
    "SINGLE RECIPE:\n"
    "## 🍳 Recipe Name\n"
    "⏱ 30 min | 🍽 4 servings | 🔥 Easy\n\n"
    "### Ingredients\n"
    "- 2 cups flour\n"
    "- 1 tsp salt\n"
    "- 3 eggs\n\n"
    "### Instructions\n"
    "1. First step\n"
    "2. Second step\n"
    "3. Third step\n\n"
    "MULTIPLE RECIPES (use === separator):\n"
    "## 🍳 Recipe 1 Name\n"
    "⏱ 20 min | 🍽 2 servings\n\n"
    "### Ingredients\n"
    "- ingredient 1\n"
    "- ingredient 2\n\n"
    "### Instructions\n"
    "1. Step 1\n"
    "2. Step 2\n\n"
    "===\n\n"
    "## 🍳 Recipe 2 Name\n"
    "⏱ 15 min | 🍽 1 serving\n\n"
    "### Ingredients\n"
    "- ingredient 1\n\n"
    "### Instructions\n"
    "1. Step 1\n\n"

    "──────────────────────────────────────────\n"
    "6. COMPARISONS\n"
    "──────────────────────────────────────────\n"
    "## Comparison: X vs Y\n\n"
    "### Overview\n"
    "Short neutral summary of both options.\n\n"
    "### Comparison Table\n"
    "| Feature | Option A | Option B |\n"
    "|---------|----------|----------|\n"
    "| Price | $10 | $15 |\n"
    "| Speed | Fast | Faster |\n"
    "| Ease | ⭐⭐⭐ | ⭐⭐ |\n\n"
    "### Pros & Cons\n"
    "**Option A**\n"
    "- ✅ Pro point\n"
    "- ❌ Con point\n\n"
    "**Option B**\n"
    "- ✅ Pro point\n"
    "- ❌ Con point\n\n"
    "### Recommendation\n"
    "👉 **Best for X:** Option A\n"
    "👉 **Avoid if:** Y situation\n\n"

    "──────────────────────────────────────────\n"
    "7. ACTION ITEMS & TASKS\n"
    "──────────────────────────────────────────\n"
    "## Action Plan\n\n"
    "### Today\n"
    "- [x] Completed task\n"
    "- [ ] Pending task\n\n"
    "### This Week\n"
    "- [ ] 🔴 **High:** Critical task\n"
    "- [ ] 🟡 **Medium:** Important task\n"
    "- [ ] 🟢 **Low:** Nice to have\n\n"

    "──────────────────────────────────────────\n"
    "8. CODE & TECHNICAL\n"
    "──────────────────────────────────────────\n"
    "## Topic Name\n\n"
    "### Explanation\n"
    "Plain-language explanation of the concept.\n\n"
    "### Example\n"
    "```python\n"
    "def example_function():\n"
    "    # Comment explaining logic\n"
    "    return result\n"
    "```\n\n"
    "### Breakdown\n"
    "- `function_name` – what it does\n"
    "- `parameter` – what it accepts\n\n"
    "### Edge Cases\n"
    "- Handle null input\n"
    "- Performance considerations\n\n"
    "CODE RULES:\n"
    "• Always use fenced code blocks\n"
    "• Always specify language (python, js, bash, etc.)\n"
    "• NO emojis inside code blocks\n\n"

    "──────────────────────────────────────────\n"
    "9. EMAILS\n"
    "──────────────────────────────────────────\n"
    "When writing, composing, drafting, or replying to an email:\n"
    "**Subject:** Clear and specific subject line\n\n"
    "Hi [Name],\n\n"
    "Opening line – context or greeting.\n\n"
    "Main body:\n"
    "- Short paragraphs\n"
    "- One idea per paragraph\n"
    "- **Bold** key points\n\n"
    "Call to action or next steps.\n\n"
    "Best regards,\n"
    "[Your Name]\n\n"
    "TONE OPTIONS: Formal | Neutral | Friendly | Direct\n\n"

    "──────────────────────────────────────────\n"
    "10. SUMMARIES & REPORTS\n"
    "──────────────────────────────────────────\n"
    "## Report Title\n"
    "*Date or context*\n\n"
    "### Executive Summary\n"
    "1-2 paragraphs max with key findings.\n\n"
    "### Key Findings\n"
    "- **Finding 1:** Explanation\n"
    "- **Finding 2:** Explanation\n"
    "- **Finding 3:** Explanation\n\n"
    "### Analysis\n"
    "Detailed breakdown in short sections.\n\n"
    "### Conclusion\n"
    "Clear takeaway and recommendations.\n\n"
    "### Next Steps (optional)\n"
    "- [ ] Action item 1\n"
    "- [ ] Action item 2\n\n"

    "──────────────────────────────────────────\n"
    "11. PLANS & SCHEDULES\n"
    "──────────────────────────────────────────\n"
    "## Project Plan\n\n"
    "### Phase 1: Research\n"
    "- [ ] Task A\n"
    "- [ ] Task B\n\n"
    "### Phase 2: Execution\n"
    "- [ ] Task C\n"
    "- [ ] Task D\n\n"
    "### Schedule\n"
    "| Date | Task | Owner |\n"
    "|------|------|-------|\n"
    "| Mon | Research | Team A |\n"
    "| Tue | Design | Team B |\n\n"

    "──────────────────────────────────────────\n"
    "12. Q&A FORMAT\n"
    "──────────────────────────────────────────\n"
    "**Short Answer:** Direct answer in bold.\n\n"
    "### Explanation\n"
    "Detailed breakdown with context.\n\n"
    "### Example\n"
    "> Concrete example in blockquote if helpful.\n\n"

    "──────────────────────────────────────────\n"
    "13. NEWS & JOURNALISM\n"
    "──────────────────────────────────────────\n"
    "## Headline\n"
    "📅 *Date* | 📍 *Location*\n\n"
    "**Lead paragraph** – Who, What, When, Where, Why.\n\n"
    "### Details\n"
    "Expanding paragraphs with context.\n\n"
    "> \"Direct quote from source,\" said Person.\n\n"
    "### Background\n"
    "Historical or contextual information.\n\n"
    "### What Happens Next\n"
    "Future implications or developments.\n\n"

    "──────────────────────────────────────────\n"
    "14. ANALYSIS & ESSAYS\n"
    "──────────────────────────────────────────\n"
    "## Title\n\n"
    "### Thesis\n"
    "Clear stance or main argument.\n\n"
    "### Argument 1\n"
    "Point with supporting evidence.\n\n"
    "### Argument 2\n"
    "Point with supporting evidence.\n\n"
    "### Counterpoint\n"
    "Address objections fairly.\n\n"
    "### Conclusion\n"
    "Strong closing that reinforces thesis.\n\n"

    "──────────────────────────────────────────\n"
    "15. LISTS & KNOWLEDGE DUMPS\n"
    "──────────────────────────────────────────\n"
    "## Topic\n\n"
    "### Category 1\n"
    "- **Item** – short explanation\n"
    "- **Item** – short explanation\n\n"
    "### Category 2\n"
    "- **Item** – short explanation\n"
    "- **Item** – short explanation\n\n"
    "USE FOR: Brain dumps, learning maps, cheat sheets, reference lists\n\n"

    "──────────────────────────────────────────\n"
    "16. UI/UX CRITIQUE\n"
    "──────────────────────────────────────────\n"
    "## Product Name – UI Review\n\n"
    "### First Impression\n"
    "Initial reaction and feel.\n\n"
    "### Layout & Hierarchy\n"
    "Visual structure analysis.\n\n"
    "### Typography & Colors\n"
    "Font and color system review.\n\n"
    "### UX Flow\n"
    "User journey assessment.\n\n"
    "### What Works\n"
    "- ✅ Strength 1\n"
    "- ✅ Strength 2\n\n"
    "### What's Weak\n"
    "- ❌ Issue 1\n"
    "- ❌ Issue 2\n\n"
    "### Final Verdict\n"
    "Overall assessment and score.\n\n"

    "═══════════════════════════════════════════════════════════════\n"
    "🧠 UNIVERSAL FORMAT DETECTION SYSTEM\n"
    "═══════════════════════════════════════════════════════════════\n\n"

    "🎯 CORE RULE (NON-NEGOTIABLE):\n"
    "1. Detect intent FIRST\n"
    "2. Select format SECOND\n"
    "3. Write content LAST\n"
    "Formatting is not decoration — it is semantic.\n\n"

    "──────────────────────────────────────────\n"
    "INTENT DETECTION (Priority Order)\n"
    "──────────────────────────────────────────\n"
    "1. Explicit request ('write a poem', 'compare X vs Y')\n"
    "2. Implicit task ('how do I...', 'what's the difference...')\n"
    "3. Content domain (creative, technical, instructional)\n"
    "4. Output length expectation\n\n"

    "──────────────────────────────────────────\n"
    "FORMAT ROUTING TABLE\n"
    "──────────────────────────────────────────\n\n"

    "📖 STORIES/FICTION:\n"
    "TRIGGERS: 'tell me a story', 'write a story', 'fiction', 'short story', 'narrative', 'scene', 'chapter'\n"
    "RULES: NO bullets, NO numbered lists, scene breaks with ---, dialogue on new lines\n\n"

    "🎵 SONGS/LYRICS:\n"
    "TRIGGERS: 'write a song', 'lyrics for', 'rap about', 'chorus', 'verse'\n"
    "RULES: No prose paragraphs, consistent [Section] labels, no punctuation overuse\n\n"

    "📝 POETRY:\n"
    "TRIGGERS: 'write a poem', 'poetry', 'free verse', 'sonnet', 'haiku'\n"
    "RULES: NO forced line wrapping, NO bullets/numbers, white space = meaning\n\n"

    "🛠 TUTORIALS/HOW-TO:\n"
    "TRIGGERS: 'how to', 'tutorial', 'guide', 'steps to', 'walk me through'\n"
    "RULES: ALWAYS numbered steps, ALWAYS prerequisites if tools required\n\n"

    "🍳 RECIPES:\n"
    "TRIGGERS: 'recipe for', 'how to cook', 'how to make [food]', 'recipe ideas', 'what should I cook'\n"
    "FORMAT (strict):\n"
    "## Recipe Name\n"
    "⏱ 30 min | 🍽 4 servings | 🔥 Easy\n\n"
    "### Ingredients\n"
    "- 2 cups flour\n"
    "- 1 tsp salt\n"
    "- 3 eggs\n\n"
    "### Instructions\n"
    "1. First step\n"
    "2. Second step\n"
    "3. Third step\n\n"
    "RULES:\n"
    "  - ALWAYS list ingredients with - bullets, one per line\n"
    "  - ALWAYS number instructions starting from 1\n"
    "  - MULTIPLE RECIPES: Use === separator between recipes\n"
    "  - Keep meta on ONE line with | separators\n\n"

    "⚖️ COMPARISONS:\n"
    "TRIGGERS: 'compare', 'vs', 'difference between', 'which is better'\n"
    "RULES: ALWAYS include table, ALWAYS end with recommendation\n\n"

    "✅ TASKS/ACTION ITEMS:\n"
    "TRIGGERS: 'to-do', 'checklist', 'tasks', 'action items', 'plan'\n"
    "RULES: Use - [ ] and - [x] format, optional priority sections\n\n"

    "💻 CODE/PROGRAMMING:\n"
    "TRIGGERS: 'code', 'function', 'script', 'example in', language names\n"
    "RULES: Code ALWAYS fenced, language ALWAYS specified, NO emojis in code\n\n"

    "📧 EMAILS:\n"
    "TRIGGERS: 'write an email', 'email to', 'reply to', 'professional email'\n"
    "RULES: Subject line, greeting, body paragraphs, signature\n\n"

    "📊 SUMMARIES/REPORTS:\n"
    "TRIGGERS: 'summary', 'report', 'analyze', 'overview', 'brief'\n"
    "RULES: Executive summary first, key points, clear conclusion\n\n"

    "📰 NEWS/JOURNALISM:\n"
    "TRIGGERS: 'news article', 'write an article', 'headline', 'press release'\n"
    "RULES: Lead paragraph answers Who/What/When/Where/Why\n\n"

    "❓ Q&A/EXPLANATIONS:\n"
    "TRIGGERS: 'what is', 'why', 'how does', direct questions\n"
    "RULES: Short answer first (bold), then detailed explanation\n\n"

    "🧠 ANALYSIS/ESSAYS:\n"
    "TRIGGERS: 'analyze', 'opinion', 'argument', 'essay', 'is this good or bad'\n"
    "RULES: Clear thesis, supporting arguments, counterpoint, conclusion\n\n"

    "📚 LISTS/KNOWLEDGE:\n"
    "TRIGGERS: 'list of', 'give me', 'what are', 'cheat sheet', 'reference'\n"
    "RULES: Categorized with headers, **bold** item names, short explanations\n\n"

    "🎨 UI/UX CRITIQUE:\n"
    "TRIGGERS: 'review this UI', 'UX feedback', 'design critique'\n"
    "RULES: Structured sections, ✅ strengths, ❌ weaknesses, verdict\n\n"

    "──────────────────────────────────────────\n"
    "CONFLICT RESOLUTION\n"
    "──────────────────────────────────────────\n"
    "If multiple formats apply:\n"
    "1. Creative beats technical\n"
    "2. Explicit beats implicit\n"
    "3. Instruction beats explanation\n"
    "4. Long-form beats short-form\n\n"

    "──────────────────────────────────────────\n"
    "AUTO-ENFORCED RULES\n"
    "──────────────────────────────────────────\n"
    "✔ One format per response\n"
    "✔ Clear hierarchy\n"
    "✔ Scannable spacing\n"
    "✔ Minimal emoji (semantic only)\n"
    "✔ No mixed paradigms\n\n"
    "❌ No wall-of-text\n"
    "❌ No list + prose mashups\n"
    "❌ No emoji in formal or code\n"
    "❌ No poetry formatted as prose\n"
    "❌ No stories with bullet points\n\n"

    "⚠️ GOLDEN RULE: Default to CHAT unless there is a strong reason to escalate.\n\n"

    "OUTPUT MODE DECISION:\n"
    "1. chat - Any request where code is NOT the primary output or NOT meant to be executed directly\n"
    "2. code_window - Code included to demonstrate syntax or logic, NOT meant to run as a standalone app (<50 lines)\n"
    "3. artifact - ONLY when output is intended to be EXECUTED, RENDERED, or PREVIEWED as a standalone program\n\n"

    "🔒 CHAT MODE (default):\n"
    "- Advice, tips, recommendations (meal plans, workout routines, travel tips)\n"
    "- Explanations, comparisons, analysis\n"
    "- Lists, schedules, plans, outlines\n"
    "- Writing (essays, emails, stories, content)\n"
    "- General knowledge questions\n"
    "- Mixed responses with explanations that include code snippets\n\n"

    "📝 CODE_WINDOW MODE:\n"
    "- Small code snippets to demonstrate syntax or logic\n"
    "- Single functions, quick examples (<50 lines)\n"
    "- Code that explains a concept but isn't a complete app\n"
    "- 'How do I...' questions with code examples\n\n"

    "🔒 ARTIFACT MODE (strict - only when ALL conditions met):\n"
    "- User explicitly requests: 'build', 'create', 'make' an app/game/tool\n"
    "- Output is a STANDALONE program meant to be executed or previewed\n"
    "- Interactive tools, calculators, games, visualizations, websites\n"
    "- NOT for explanations with code, NOT for markdown/text content\n\n"

    "Response structure:\n"
    "<META>{\"output_mode\":\"artifact\"|\"code_window\"|\"chat\",\"title\":\"...\",\"contains_code\":bool}</META>\n"
    "<CHAT>Your response here. For chat mode, this contains your FULL answer with formatting.</CHAT>\n"
    "<ARTIFACT>Only include ```artifact block if output_mode is 'artifact'</ARTIFACT>\n\n"

    "CHAT MODE EXAMPLE (for non-code requests):\n"
    "<META>{\"output_mode\":\"chat\",\"title\":\"Meal Planning Tips\",\"contains_code\":false}</META>\n"
    "<CHAT>\n"
    "Here's a weekly meal plan:\n\n"
    "**Monday**\n"
    "- Breakfast: Oatmeal with berries\n"
    "- Lunch: Grilled chicken salad\n"
    "...(full response in plain text/markdown)...\n"
    "</CHAT>\n"
    "<ARTIFACT></ARTIFACT>\n\n"

    "ARTIFACT MODE - When creating runnable code:\n"
    "- For React apps: DO NOT create index.html. Use App.jsx as entrypoint + style.css.\n"
    "  The preview system auto-loads React 18 + Babel. Just write JSX components directly.\n"
    "  IMPORTANT: return() MUST have a SINGLE root element. Wrap everything in <div> or use Fragment (<>...</>). NEVER return adjacent sibling elements.\n"
    "  IMPORTANT: JSX must use proper nested structure with className attributes matching CSS. If CSS defines .container, .display, .buttons, the JSX MUST have matching divs with those classNames. NEVER render flat elements without wrapper containers.\n"
    "  CRITICAL: Inside artifact code, NEVER use markdown like **bold**, *italic*, # headings, or bullet points. Write ONLY valid JSX. Use plain text for button labels (e.g. 'AC' not '**AC**').\n"
    "  NEVER mix index.html with .jsx files.\n"
    "- For plain HTML apps: Create index.html, style.css, script.js\n"
    "- MUST include ```artifact JSON block with title, type, primaryLanguage, files array\n\n"

    "Artifact example (React - PREFERRED for interactive apps):\n"
    "<META>{\"output_mode\":\"artifact\",\"title\":\"Counter App\",\"contains_code\":true}</META>\n"
    "<CHAT>Created a counter app with increment/decrement buttons.</CHAT>\n"
    "<ARTIFACT>\n"
    "```artifact\n"
    "{\"title\":\"Counter App\",\"type\":\"project\",\"primaryLanguage\":\"jsx\",\"files\":["
    "{\"path\":\"App.jsx\",\"language\":\"jsx\",\"content\":\"import React, { useState } from 'react';\\nimport './style.css';\\n\\nexport default function App() {\\n  const [count, setCount] = useState(0);\\n  return (\\n    <div className=\\\"counter\\\">\\n      <h1>Counter: {count}</h1>\\n      <button onClick={() => setCount(c => c - 1)}>-</button>\\n      <button onClick={() => setCount(c => c + 1)}>+</button>\\n    </div>\\n  );\\n}\"},"
    "{\"path\":\"style.css\",\"language\":\"css\",\"content\":\"body {\\n  font-family: sans-serif;\\n  text-align: center;\\n  padding: 50px;\\n  background: #1a1a2e;\\n  color: #f4f4f4;\\n}\\n.counter {\\n  max-width: 400px;\\n  margin: 0 auto;\\n}\\nbutton {\\n  font-size: 20px;\\n  margin: 5px;\\n  padding: 10px 20px;\\n  border-radius: 8px;\\n  border: none;\\n  cursor: pointer;\\n}\"}"
    "]}\n"
    "```\n"
    "</ARTIFACT>\n\n"
    "IMPORTANT: React artifacts MUST ALWAYS have AT LEAST 2 files:\n"
    "1. App.jsx - the main component with `export default`\n"
    "2. style.css - ALL styles in a separate CSS file (NEVER inline styles in JSX)\n"
    "Additional component files are encouraged for complex apps.\n\n"

    "🔧 ARTIFACT UPDATES & BUG FIXES (Claude-style):\n"
    "When user asks to fix/update/modify an artifact, the CURRENT ARTIFACT CODE is automatically\n"
    "injected into your context. You will see it marked as 'CURRENT ARTIFACT' with all files.\n\n"

    "Your job:\n"
    "1. READ the current artifact code provided in context\n"
    "2. IDENTIFY the issue or change needed\n"
    "3. APPLY the fix/change to the code\n"
    "4. OUTPUT a complete ```artifact block with ALL files (including unchanged ones)\n\n"

    "NEVER:\n"
    "- Just explain how to fix without providing fixed code\n"
    "- Ask user to make the change themselves\n"
    "- Output partial code with 'rest remains the same'\n\n"

    "ALWAYS:\n"
    "- Output the COMPLETE updated artifact with fix applied\n"
    "- Preserve file structure and unchanged code\n"
    "- Use output_mode='artifact' in META block\n\n"

    "Triggers: 'fix', 'update', 'correct', 'change', 'modify', 'edit', 'revise', 'improve'\n"
    "Response: <META>{\"output_mode\":\"artifact\",...}</META> + full ```artifact block\n"
)


class _JinaEmbeddingProxy:
    """Thin wrapper providing .encode() that calls Jina API instead of local model."""
    def encode(self, text, **kwargs):
        result = _jina_embed_text(text)
        if result is not None:
            return result
        import numpy as np
        return np.zeros(1024, dtype=np.float32)


_embedding_proxy = _JinaEmbeddingProxy()


def get_embedding_model():
    """Return embedding proxy if Jina API is configured, else None."""
    if _embeddings_available():
        return _embedding_proxy
    return None


def _friendly_ai_error_message(error_payload: Dict[str, Any]) -> str:
    """Return a user-facing error message tailored to the model failure."""
    code = error_payload.get('error_code')
    provider = error_payload.get('provider')
    if provider and code:
        specific = PROVIDER_SPECIFIC_MESSAGES.get((provider, code))
        if specific:
            return specific
    if code == 'http_error':
        base = FRIENDLY_AI_ERROR_MESSAGES.get('http_error', generate_fallback_response())
        status_code = error_payload.get('status_code')
        if status_code:
            return f"{base} (HTTP {status_code}). Please try again shortly."
        return f"{base} Please try again shortly."
    return FRIENDLY_AI_ERROR_MESSAGES.get(code, generate_fallback_response())


def classify_user_intent(prompt: str, use_cache: bool = True) -> dict:
    """
    Use AI to classify user intent. This is a general-purpose classifier
    that understands what the user wants before any other processing.

    Returns dict with:
    - intent_type: primary intent (e.g., "financial_query", "creative_task", "question", etc.)
    - has_crypto_mention: bool - is user asking about cryptocurrency?
    - has_stock_mention: bool - is user asking about stocks?
    - asset_symbol: str or None - specific asset mentioned (e.g., "BTC", "AAPL")
    - wants_chart: bool - does user want a chart/graph?
    - wants_image: bool - does user want an image generated?
    - wants_code: bool - does user want code/diagram code?
    - confidence: float 0-1
    """
    if not prompt or len(prompt.strip()) < 3:
        return {"intent_type": "unknown", "confidence": 0}

    # Check cache
    cache_key = hashlib.md5(prompt.lower().strip().encode()).hexdigest()
    if use_cache and cache_key in _intent_cache:
        cached, timestamp = _intent_cache[cache_key]
        if time.time() - timestamp < _intent_cache_ttl:
            return cached

    # Classification prompt - designed to be fast and accurate
    classification_prompt = f"""Analyze this user message and classify their intent. Respond ONLY with a JSON object.

User message: "{prompt}"

Respond with this exact JSON format (no other text):
{{"intent_type": "<one of: financial_query, creative_task, question, recipe_request, code_request, image_request, general_chat>",
"has_crypto_mention": <true if asking about cryptocurrency prices/charts, false otherwise>,
"has_stock_mention": <true if asking about stock prices/charts, false otherwise>,
"asset_symbol": "<symbol like BTC, ETH, AAPL or null if none>",
"wants_chart": <true if wants price chart/graph, false otherwise>,
"wants_image": <true if wants AI-generated image, false otherwise>,
"wants_code": <true if wants code/mermaid/diagram code, false otherwise>}}

IMPORTANT: Words like "create", "make", "build" at the start usually mean creative_task, NOT cryptocurrency.
Only set has_crypto_mention=true if they're clearly asking about crypto PRICES or MARKET data."""

    try:
        # Use a fast model for classification
        import requests as req

        # Try to use Groq for fast classification (or fallback to current provider)
        groq_key = os.environ.get('GROQ_API_KEY')
        if groq_key:
            response = req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-8b-instant",  # Fast model
                    "messages": [{"role": "user", "content": classification_prompt}],
                    "max_tokens": 150,
                    "temperature": 0
                },
                timeout=3  # Fast timeout
            )

            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content'].strip()
                # Parse JSON from response
                # Find JSON in response (handle markdown code blocks)
                json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    result['confidence'] = 0.9
                    # Cache result
                    _intent_cache[cache_key] = (result, time.time())
                    logger.info(f"🧠 Intent classified: {result.get('intent_type')} for '{prompt[:50]}...'")
                    return result
    except Exception as e:
        logger.debug(f"Intent classification failed: {e}")

    # Fallback: return unknown intent (let old logic handle it)
    return {"intent_type": "unknown", "confidence": 0}


def detect_crypto_price_query(prompt: str) -> Optional[dict]:
    """
    Detect if the user is asking for crypto or stock price/chart.
    Returns dict with asset info if detected, None otherwise.
    """
    if not prompt:
        return None

    # Use AI intent classification first (general-purpose understanding)
    intent = classify_user_intent(prompt)
    if intent.get('confidence', 0) > 0.5:
        # If AI classified this and it's NOT a financial query, skip detection
        if intent.get('intent_type') in ['creative_task', 'code_request', 'image_request', 'recipe_request']:
            logger.debug(f"🧠 Skipping crypto detection - AI classified as: {intent.get('intent_type')}")
            return None

        # If AI found an asset symbol, use it directly
        if intent.get('asset_symbol') and (intent.get('has_crypto_mention') or intent.get('has_stock_mention')):
            symbol = intent['asset_symbol'].lower()
            if intent.get('has_crypto_mention'):
                coin_id = get_coin_id_dynamic(symbol) or CRYPTO_SYMBOLS.get(symbol)
                if coin_id:
                    logger.info(f"🧠 AI detected crypto: {symbol} -> {coin_id}")
                    return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}
            if intent.get('has_stock_mention'):
                if symbol.upper() in STOCK_SYMBOLS:
                    logger.info(f"🧠 AI detected stock: {symbol}")
                    return {'symbol': symbol.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}

    prompt_lower = prompt.lower()

    # Price query patterns - capture the asset name/symbol
    price_patterns = [
        r'(?:what(?:\'s| is| are)?|how much is|show me|get|check|give me)\s+(?:the\s+)?(?:current\s+)?(?:price|value|worth|chart|graph|stock)\s+(?:of|for)?\s*(\w+(?:\s+\w+)?)',
        r'(\w+(?:\s+\w+)?)\s+(?:price|chart|graph|value|stock)',
        r'(?:price|chart|graph|stock)\s+(?:of|for)\s+(\w+(?:\s+\w+)?)',
        r'how\s+much\s+(?:is|does)\s+(?:one\s+)?(\w+)\s+(?:cost|worth|stock)',
        r'(?:show|display|get)\s+(?:me\s+)?(?:the\s+)?(\w+(?:\s+\w+)?)\s+(?:price|chart|stock)',
    ]

    # Price-related words
    price_words = ['price', 'chart', 'graph', 'value', 'worth', 'cost', 'trading', 'market', 'stock', 'ticker']
    has_price_word = any(word in prompt_lower for word in price_words)

    # First, check for crypto symbols
    for pattern in price_patterns:
        match = re.search(pattern, prompt_lower)
        if match:
            query = match.group(1).strip()
            # Check if it's a crypto - fast path (top 10)
            if query in CRYPTO_SYMBOLS:
                coin_id = CRYPTO_SYMBOLS[query]
                logger.info(f"💰 Crypto price query detected: {query} -> {coin_id}")
                return {
                    'coin': coin_id,
                    'asset_type': 'crypto',
                    'currency': 'usd',
                    'range': '1D'
                }
            # Dynamic crypto lookup (all 24M+ coins)
            coin_id = get_coin_id_dynamic(query)
            if coin_id:
                logger.info(f"💰 Crypto price query detected (dynamic): {query} -> {coin_id}")
                return {
                    'coin': coin_id,
                    'asset_type': 'crypto',
                    'currency': 'usd',
                    'range': '1D'
                }
            # Check if it's a stock symbol (word boundary to avoid v matching vet)
            if query in STOCK_SYMBOLS:
                logger.info(f"📈 Stock price query detected: {query}")
                return {
                    'symbol': query.upper(),
                    'asset_type': 'stock',
                    'currency': 'usd',
                    'range': '1D'
                }
            # Check company names
            if query in COMPANY_TO_SYMBOL:
                symbol = COMPANY_TO_SYMBOL[query]
                logger.info(f"📈 Stock price query detected (company): {query} -> {symbol}")
                return {
                    'symbol': symbol.upper(),
                    'asset_type': 'stock',
                    'currency': 'usd',
                    'range': '1D'
                }

    # Direct detection with price context
    if has_price_word:
        # Words that are common English but also crypto symbols — require explicit price intent
        _ambiguous_crypto = {'link', 'dot', 'sol', 'ada', 'bnb'}
        # Common stop words to skip in dynamic lookup
        _stop_words = {
            'price', 'chart', 'graph', 'value', 'worth', 'cost', 'trading', 'market',
            'stock', 'ticker', 'the', 'for', 'of', 'is', 'what', 'show', 'me', 'get',
            'check', 'give', 'how', 'much', 'does', 'are', 'can', 'will', 'its', 'this',
            'that', 'with', 'from', 'have', 'has', 'about', 'would', 'could', 'should',
            'current', 'today', 'now', 'please', 'tell', 'display', 'my', 'your',
            'not', 'but', 'and', 'or', 'if', 'all', 'any', 'new', 'old', 'some',
            'file', 'text', 'code', 'html', 'page', 'resume', 'make', 'create',
            'write', 'read', 'help', 'need', 'want', 'like', 'just', 'here',
        }

        # Check crypto symbols - fast path (top 10)
        for symbol, coin_id in CRYPTO_SYMBOLS.items():
            if symbol in _ambiguous_crypto:
                # Ambiguous words need stronger signal: must appear next to a price word
                if not re.search(rf'\b{symbol}\b\s+(?:price|chart|value|stock|coin|crypto|token)', prompt_lower) and \
                   not re.search(rf'(?:price|chart|value|stock|coin|crypto|token)\s+(?:of\s+)?\b{symbol}\b', prompt_lower):
                    continue
            if re.search(rf'\b{symbol}\b', prompt_lower):
                logger.info(f"💰 Crypto detected with price context: {symbol} -> {coin_id}")
                return {
                    'coin': coin_id,
                    'asset_type': 'crypto',
                    'currency': 'usd',
                    'range': '1D'
                }

        # Dynamic crypto lookup - extract words and check against CoinGecko
        words = re.findall(r'\b([a-zA-Z]{2,10})\b', prompt_lower)
        for word in words:
            if word not in _stop_words and word not in _ambiguous_crypto:
                coin_id = get_coin_id_dynamic(word)
                if coin_id:
                    logger.info(f"💰 Crypto detected with price context (dynamic): {word} -> {coin_id}")
                    return {
                        'coin': coin_id,
                        'asset_type': 'crypto',
                        'currency': 'usd',
                        'range': '1D'
                    }

        # Check stock symbols
        for symbol in STOCK_SYMBOLS:
            if re.search(rf'\b{symbol}\b', prompt_lower):
                logger.info(f"📈 Stock detected with price context: {symbol}")
                return {
                    'symbol': symbol.upper(),
                    'asset_type': 'stock',
                    'currency': 'usd',
                    'range': '1D'
                }

        # Check company names
        for company, symbol in COMPANY_TO_SYMBOL.items():
            if company in prompt_lower:
                logger.info(f"📈 Stock company detected: {company} -> {symbol}")
                return {
                    'symbol': symbol.upper(),
                    'asset_type': 'stock',
                    'currency': 'usd',
                    'range': '1D'
                }

    return None


def build_prompt_fragment(prompt_text: str, limit: int = 3) -> str:
    """Return the first few words of a prompt for display purposes."""
    if not prompt_text or not isinstance(prompt_text, str):
        return ""
    words = prompt_text.strip().split()
    if not words:
        return ""
    return " ".join(words[:limit])


def load_or_create_session(user_id: str) -> Tuple[str, Dict[str, Any]]:
    """Load an existing session or create a new one if none is active."""
    session_id = session.get('current_session_id')
    session_doc = None
    logger.info(f"📥 SESSION DEBUG: Starting load_or_create_session, current_session_id from cookie: {session_id}")

    if session_id:
        session_doc = db.collection('users').document(user_id).collection('chat_sessions').document(session_id).get()
        if not session_doc.exists:
            # Stale session id; create a fresh one
            logger.warning(f"📥 SESSION DEBUG: Session {session_id} no longer exists in Firestore, creating new one")
            session_id = None
            session.pop('current_session_id', None)
        else:
            logger.info(f"📥 SESSION DEBUG: Found existing session {session_id} in Firestore")

    if not session_id:
        logger.info(f"📥 SESSION DEBUG: No active session for user {user_id}, creating a new one.")
        session_id = chat_session.create_session(user_id)
        session['current_session_id'] = session_id
        session_doc = db.collection('users').document(user_id).collection('chat_sessions').document(session_id).get()
    elif session_doc is None:
        session_doc = db.collection('users').document(user_id).collection('chat_sessions').document(session_id).get()
    session_data = session_doc.to_dict() if session_doc.exists else {}
    # Debug logging for message loading
    msg_count = len(session_data.get('messages', []))
    logger.info(f"📥 LOAD DEBUG: Loaded session {session_id} with {msg_count} messages from Firestore")
    return session_id, session_data


def _ensure_offline_session_state() -> Tuple[str, Dict[str, Any]]:
    """Provide a session placeholder stored in Flask session when Firestore is unavailable."""
    session_id = session.get('offline_session_id')
    if not session_id:
        session_id = f"offline_{uuid.uuid4().hex}"
        session['offline_session_id'] = session_id

    offline_messages = session.get('offline_messages')
    if not isinstance(offline_messages, list):
        offline_messages = []
    session['offline_messages'] = offline_messages

    offline_data = session.get('offline_session_data')
    if not isinstance(offline_data, dict):
        offline_data = {'session_name': 'Offline Session', 'messages': offline_messages}
    else:
        offline_data['session_name'] = offline_data.get('session_name', 'Offline Session')
        offline_data['messages'] = offline_messages

    session['offline_session_data'] = offline_data
    return session_id, offline_data


def _cache_offline_interaction(chat_data: Dict[str, Any], session_name: str) -> None:
    """Append chat history to the in-session cache when Firestore cannot be reached."""
    offline_messages = session.get('offline_messages')
    if not isinstance(offline_messages, list):
        offline_messages = []
    offline_messages.append(chat_data)
    session['offline_messages'] = offline_messages
    session['offline_session_data'] = {
        'session_name': session_name or 'Offline Session',
        'messages': offline_messages
    }
    session.setdefault('offline_session_id', f"offline_{uuid.uuid4().hex}")


def _clear_offline_cache() -> None:
    """Remove any cached offline chat history once persistence succeeds."""
    session.pop('offline_messages', None)
    session.pop('offline_session_data', None)
    session.pop('offline_session_id', None)


def load_user_and_session_preferences(
    user_id: str,
    session_data: Dict[str, Any],
    user_profile: Optional[Dict[str, Any]] = None
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Load user and session preferences."""
    session_preferences = session_data.get('preferences', [])
    user_doc_data: Dict[str, Any] = user_profile or {}
    if not user_doc_data:
        user_ref = db.collection('users').document(user_id)
        snapshot = user_ref.get()
        user_doc_data = snapshot.to_dict() if snapshot.exists else {}
    user_preferences = user_doc_data.get('preferences', [])
    return user_preferences, session_preferences, user_doc_data


def build_prompt(
    user_prompt: str,
    extracted_text: str,
    search_results: List[SearchResult],
    previous_messages: List[Dict[str, Any]],
    user_prefs: List[str],
    session_prefs: List[str],
    session_summary: Optional[str],
    relevant_messages: List[Dict[str, Any]],
    system_instructions: str,
    developer_instructions: str,
    long_term_memory: str,
    pending_steps: Optional[List[str]] = None,
    canonical_context: Optional[str] = None,
    encoding_name: str = "cl100k_base",
    max_tokens: int = 4096,
    response_token_buffer: int = 512
) -> str:
    """
    Build prompt using strict priority-based dynamic packing.

    Priority order (highest to lowest):
        1. System/developer instructions (required)
        2. Current user message (required)
        3. Long-term memory (relevant buckets)
        4. Canonical state (authoritative truth - code, solution, TODOs)
        5. Session state summary
        6. Pending steps from reflection
        7. Recent conversation (last 2-3 turns)
        8. Retrieved relevant messages
        9. Search results
        10. Extracted file content
        11. Preferences

    Stops adding when budget is exhausted. More consistent under long chats.
    """
    # Calculate available budget
    budget = max_tokens - response_token_buffer

    # Track what we're building
    sections = []  # List of (priority, label, text, tokens)
    used_tokens = 0

    def try_add(priority: int, label: str, text: str, required: bool = False) -> bool:
        """Try to add a section if budget allows."""
        nonlocal used_tokens
        if not text:
            return False
        tokens = count_tokens(text, encoding_name)
        if used_tokens + tokens <= budget:
            sections.append((priority, label, text, tokens))
            used_tokens += tokens
            return True
        elif required:
            raise TokenLimitError(f"{label} exceeds token budget")
        else:
            logger.debug(f"Skipped {label} ({tokens} tokens) - budget exhausted")
            return False

    # === PRIORITY 1: System instructions (required) ===
    if system_instructions:
        try_add(1, "system", system_instructions + "\n", required=True)

    # === PRIORITY 2: Developer instructions ===
    if developer_instructions:
        try_add(2, "developer", developer_instructions + "\n")

    # === PRIORITY 3: Current user message (required, added last in output) ===
    user_prompt_text = f"User: {user_prompt}\nAssistant:"
    user_prompt_tokens = count_tokens(user_prompt_text, encoding_name)
    if used_tokens + user_prompt_tokens > budget:
        raise TokenLimitError("User prompt exceeds the maximum token limit.")
    # Reserve space for user prompt (added at end)
    used_tokens += user_prompt_tokens

    # === PRIORITY 4: Long-term memory (relevant buckets) ===
    if long_term_memory:
        try_add(4, "memory", f"User memory:\n{long_term_memory}\n")

    # === PRIORITY 5: Canonical state (authoritative truth) ===
    # This is injected BEFORE chat context so AI sees "truth" first
    if canonical_context:
        try_add(5, "canonical", canonical_context)

    # === PRIORITY 6: Session state summary ===
    if session_summary:
        from .session_summary import get_summary_for_prompt
        summary_text = get_summary_for_prompt(session_summary)
        if summary_text:
            try_add(6, "summary", summary_text)

    # === PRIORITY 7: Pending steps from reflection ===
    if pending_steps:
        steps_text = "Pending steps:\n" + "\n".join(f"- {s}" for s in pending_steps[:5])
        try_add(7, "steps", steps_text + "\n")

    # === PRIORITY 8: Recent conversation (critical for continuity) ===
    recent_added = 0
    logger.info(f"🔍 HISTORY DEBUG: previous_messages count={len(previous_messages)}, last 4: {[m.get('prompt', '')[:30] for m in previous_messages[-4:]]}")
    for message in reversed(previous_messages[-4:]):  # Last 4 messages max
        prompt_part = message.get('prompt', '')
        response_part = message.get('response', '')
        if not prompt_part and not response_part:
            continue
        msg_text = ""
        if prompt_part:
            msg_text += f"User: {prompt_part}\n"
        if response_part:
            msg_text += f"Assistant: {response_part}\n"
        if try_add(8, f"recent_{recent_added}", msg_text):
            recent_added += 1
        else:
            logger.warning(f"🔍 HISTORY DEBUG: Could not add message, budget exhausted")
    logger.info(f"🔍 HISTORY DEBUG: Added {recent_added} recent messages to prompt")

    # === PRIORITY 9: Retrieved relevant messages (older context) ===
    relevant_added = 0
    for message in relevant_messages:
        # Skip if already in recent
        if message in previous_messages[-4:]:
            continue
        prompt_part = message.get('prompt') or message.get('content', '')
        response_part = message.get('response', '')
        if not prompt_part and not response_part:
            continue
        msg_text = f"[Relevant context]\nUser: {prompt_part}\nAssistant: {response_part}\n"
        if try_add(9, f"relevant_{relevant_added}", msg_text):
            relevant_added += 1

    # === PRIORITY 10: Search results ===
    if search_results:
        search_parts = []
        for r in search_results[:5]:  # Max 5 results
            search_parts.append(f"[{r.title}]({r.link})\n{r.snippet}")
        search_text = "Web search results:\n" + "\n\n".join(search_parts) + "\n"
        try_add(10, "search", search_text)

    # === PRIORITY 5.5: Extracted file content (HIGH priority for document analysis) ===
    # Moved up from priority 11 - file content is critical when user uploads a document
    if extracted_text:
        # Allow much more text for document analysis - up to 30k chars (~7.5k tokens)
        max_extract = min(len(extracted_text), 30000)
        extract_text = f"Uploaded file content:\n{extracted_text[:max_extract]}"
        if len(extracted_text) > max_extract:
            extract_text += f"\n[...truncated, showing first {max_extract} of {len(extracted_text)} chars]"
        # Use priority 5 to ensure file content is included before conversation history
        added = try_add(5, "files", extract_text + "\n")
        logger.info(f"File content: {max_extract} chars, added={added}, budget_used={used_tokens}/{budget}")

    # === PRIORITY 12: Preferences (lowest priority) ===
    # Handle user_prefs as either list (legacy) or dict (new category system)
    if isinstance(user_prefs, dict):
        # Extract category names sorted by count (most used first)
        categories = user_prefs.get('categories', {})
        user_prefs_list = sorted(categories.keys(), key=lambda x: categories[x], reverse=True)
    else:
        user_prefs_list = user_prefs if user_prefs else []
    preferences_text = " ".join(session_prefs + user_prefs_list).strip()
    if preferences_text:
        try_add(12, "prefs", f"Preferences: {preferences_text}\n")

    # === BUILD FINAL PROMPT ===
    # Sort by priority and assemble
    sections.sort(key=lambda x: x[0])

    # Group into logical sections for better prompt structure
    output_parts = []

    # Instructions first
    for priority, label, text, _ in sections:
        if label in ("system", "developer"):
            output_parts.append(text)

    # Context section (canonical first, then memory, summary, steps, prefs)
    context_parts = []
    for priority, label, text, _ in sections:
        if label in ("canonical", "memory", "summary", "steps", "prefs"):
            context_parts.append(text)
    if context_parts:
        output_parts.append("".join(context_parts))

    # Retrieved/search context (background)
    for priority, label, text, _ in sections:
        if label.startswith("relevant_") or label == "search" or label == "files":
            output_parts.append(text)

    # Recent conversation (most important context)
    recent_parts = []
    for priority, label, text, _ in sections:
        if label.startswith("recent_"):
            recent_parts.append(text)
    if recent_parts:
        # Reverse to get chronological order
        output_parts.extend(reversed(recent_parts))

    # Current user prompt (always last)
    output_parts.append(user_prompt_text)

    final_prompt = "".join(output_parts)

    logger.debug(f"Prompt built: {used_tokens}/{budget} tokens, {len(sections)} sections")

    return final_prompt


def get_ai_response(full_prompt: str, model: str, images: Optional[List[Dict[str, str]]] = None) -> Tuple[str, bool, Optional[str]]:
    """
    Send the full prompt to the AI model and return the response text, code flag, and optional reasoning.

    Args:
        full_prompt: The complete prompt text
        model: Model ID to use
        images: Optional list of image dicts with 'url' key for vision requests

    Returns:
        Tuple of (response_text, is_code_response, reasoning_content)
    """
    reasoning_content = None

    if images:
        # Vision model fallback chain
        vision_models = [
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
        ]
        response = None
        is_code_response = False

        for vision_model in vision_models:
            response, is_code_response = send_prompt_with_images(full_prompt, images, vision_model)
            # Check if response is an error
            if isinstance(response, dict) and response.get('error'):
                logger.warning(f"Vision model {vision_model} failed: {response.get('error')}, trying next...")
                continue
            else:
                logger.info(f"Used vision model {vision_model} for {len(images)} image(s)")
                break

        # If all vision models failed, response will be the last error
        if response is None:
            response = {'error': 'All vision models failed', 'error_code': 'vision_unavailable'}
    else:
        response, is_code_response = send_prompt(full_prompt, model)

    if isinstance(response, dict) and response.get('error'):
        logger.error(
            "Primary model failed (code=%s): %s",
            response.get('error_code') or 'unknown',
            response.get('error')
        )
        friendly_message = _friendly_ai_error_message(response)
        raise ModelProviderError(
            friendly_message,
            response.get('error_code') or 'model_failure',
            status_code=502
        )

    # Handle DeepSeek R1 response with reasoning content
    if isinstance(response, dict) and response.get('has_reasoning'):
        response_text = response.get('content', '')
        reasoning_content = response.get('reasoning', '')
        logger.info(f"DeepSeek R1 response with {len(reasoning_content)} chars of reasoning")
    elif isinstance(response, dict):
        response_text = response.get('text', '')
    else:
        response_text = response

    return response_text, is_code_response, reasoning_content


def _fallback_title_from_prompt(user_prompt: str, max_words: int = 6) -> str:
    """Derive a short title from the first few words of the user's prompt."""
    if not user_prompt:
        return ""
    words = user_prompt.strip().split()
    snippet = " ".join(words[:max_words]).strip()
    return snippet.capitalize() if snippet else ""


def _clean_user_prompt_for_title(text: str) -> str:
    if not text:
        return ""
    cleaned = _CODE_FENCE_PATTERN.sub(' ', text)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = _GREETINGS_PATTERN.sub('', cleaned.strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _is_meaningful_prompt(text: str) -> bool:
    if not text:
        return False
    alnum = re.sub(r'[^A-Za-z0-9]', '', text)
    if len(alnum) < 6:
        return False
    return len(text.split()) >= 2


def _assemble_intent_text(session_data: Dict[str, Any], current_prompt: str, max_prompts: int = 2, max_chars: int = 500) -> str:
    snippets: List[str] = []
    current_clean = _clean_user_prompt_for_title(current_prompt)
    include_current = _is_meaningful_prompt(current_clean)
    slots_for_history = max_prompts - (1 if include_current else 0)
    if slots_for_history < 0:
        slots_for_history = 0

    messages = []
    if session_data:
        stored = session_data.get('messages') or []
        messages = sorted(stored, key=lambda m: m.get('timestamp', ''))

    for message in messages:
        if slots_for_history and len(snippets) >= slots_for_history:
            break
        prompt_text = message.get('prompt') or ''
        cleaned = _clean_user_prompt_for_title(prompt_text)
        if _is_meaningful_prompt(cleaned):
            snippets.append(cleaned)

    if include_current:
        snippets.append(current_clean)

    snippets = [s for s in snippets if s]
    if not snippets:
        return ""

    intent_text = " ".join(snippets[:max_prompts]).strip()
    return intent_text[:max_chars]


def update_session_title_if_missing(
    user_id: str,
    session_id: str,
    session_data: Dict[str, Any],
    response_text: str,
    user_prompt: str
) -> str:
    """Generate and update a session title if none exists yet or if it's a placeholder."""
    title = session_data.get('session_name', '') if session_data else ''
    placeholder_titles = {'new chat', 'untitled session', ''}

    if title.strip().lower() in placeholder_titles:
        intent_text = _assemble_intent_text(session_data or {}, user_prompt, max_prompts=2, max_chars=500)
        if intent_text:
            new_title = generate_title(intent_text) or _fallback_title_from_prompt(intent_text)
            if new_title and new_title.lower() not in placeholder_titles:
                db.collection('users').document(user_id).collection('chat_sessions').document(session_id).update({
                    'session_name': new_title
                })
                logger.info(f"New title for session {session_id} of user {user_id}: {new_title}")
                title = new_title

    return title or "New Chat"


def save_interaction(
    user_id: str,
    session_id: str,
    user_prompt: str,
    response_text: str,
    sentiment: str,
    is_code_response: bool,
    search_results: List[SearchResult],
    user_attachments: Optional[List[Dict[str, Any]]] = None,
    persist: bool = True,
    response_id: Optional[str] = None,
    render_mode: str = "inline",
    artifact_reference: Optional[Dict[str, Any]] = None,
    reasoning: Optional[str] = None,
    crypto_chart: Optional[Dict[str, Any]] = None,
    display_prompt: Optional[str] = None,
    model_id: Optional[str] = None,
    task_type: Optional[str] = None,
    auto_selected: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """Save the current user-assistant interaction to the database.

    Args:
        display_prompt: The user-facing prompt to save (without system injections).
                       If None, uses user_prompt.

    Returns:
        Tuple of the chat payload and a boolean indicating whether persistence succeeded.
    """
    response_identifier = response_id or f"msg_{uuid.uuid4().hex}"
    chat_data = {
        'prompt': display_prompt if display_prompt is not None else user_prompt,
        'response': response_text,
        'timestamp': datetime.utcnow().isoformat(),
        'sentiment': sentiment,
        'is_code': is_code_response,
        'search_results': [],
        'response_id': response_identifier,
        'render_mode': render_mode
    }

    # ============================================================
    # Model provenance (only shown when user selected Auto)
    # ============================================================
    if model_id:
        provider_labels = {
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "groq": "Groq",
            "cerebras": "Cerebras",
            "moonshot": "Moonshot",
            "ollama": "Ollama",
        }
        provider = MODEL_PROVIDERS.get(model_id, "groq")
        chat_data['model_id'] = model_id
        chat_data['model_label'] = MODEL_LABELS.get(model_id, model_id)
        chat_data['model_provider'] = provider
        chat_data['model_provider_label'] = provider_labels.get(provider, provider)

    if task_type:
        chat_data['task_type'] = task_type

    if auto_selected:
        chat_data['auto_selected'] = True
    if reasoning:
        chat_data['reasoning'] = reasoning
        logger.info(f"Saving message with reasoning, length: {len(reasoning)}")
    if crypto_chart:
        chat_data['crypto_chart'] = crypto_chart
        logger.info(f"Saving message with crypto_chart: {crypto_chart.get('asset_type')} - {crypto_chart.get('symbol') or crypto_chart.get('coin')}")
    if artifact_reference:
        artifact_fields = {
            'artifact_id': artifact_reference.get('artifactId'),
            'artifact_title': artifact_reference.get('title'),
            'artifact_type': artifact_reference.get('type') or 'project',
            'artifact_language': artifact_reference.get('primaryLanguage'),
            'artifact_summary': artifact_reference.get('summary'),
            'artifact_is_active': True,
            'artifact_pending': artifact_reference.get('pending'),
            'artifact_version_id': artifact_reference.get('versionId'),
            'artifact_version_number': artifact_reference.get('versionNumber'),
            'artifact_entrypoint': artifact_reference.get('entrypoint'),
        }
        logger.info(f"📦 SAVE_INTERACTION: artifact_reference received, artifactId={artifact_reference.get('artifactId')}, pending={artifact_reference.get('pending')}")
        for key, value in artifact_fields.items():
            if value is not None:
                chat_data[key] = value
        logger.info(f"📦 SAVE_INTERACTION: artifact fields added to chat_data: {[k for k in artifact_fields if artifact_fields[k] is not None]}")
    if search_results:
        normalized_results = []
        for result in search_results:
            if result is None:
                continue
            if isinstance(result, SearchResult):
                normalized_results.append(result.to_dict())
                continue
            if isinstance(result, dict):
                normalized_results.append(result)
                continue
            if hasattr(result, '_asdict'):
                try:
                    normalized_results.append(result._asdict())
                    continue
                except Exception:
                    pass
            if hasattr(result, 'to_dict'):
                try:
                    normalized_results.append(result.to_dict())
                    continue
                except Exception:
                    pass
            if isinstance(result, (list, tuple)):
                normalized_results.append({'value': list(result)})
            else:
                normalized_results.append({'value': str(result)})
        if normalized_results:
            chat_data['search_results'] = normalized_results

    if user_attachments:
        chat_data['user_attachments'] = user_attachments

    if not persist:
        return chat_data, False

    try:
        # Check if this session is the active one for this browser/device
        # Gracefully handle missing request context (e.g., inside streaming generator)
        try:
            is_active_session = (session.get('current_session_id') == session_id)
        except RuntimeError:
            is_active_session = True  # Assume active when called from stream generator
        chat_session.save_messages(user_id, session_id, [chat_data], is_active_session=is_active_session)
        logger.info(f"Chat response generated for user {user_id}, session {session_id} (active={is_active_session})")
        return chat_data, True
    except Exception as exc:
        logger.warning(f"Failed to persist chat interaction for user {user_id}, session {session_id}: {exc}")
        return chat_data, False


def build_artifact_reference_payload(payload: Dict[str, Any], fallback_summary: str, pending: bool) -> Dict[str, Any]:
    active_version = payload.get('activeVersion') or {}
    summary = active_version.get('summary') or payload.get('summary') or fallback_summary or payload.get('title') or 'Artifact'
    entrypoint = active_version.get('entrypointPath') or payload.get('entrypoint') or payload.get('entrypointPath')
    return {
        'artifactId': payload.get('artifactId'),
        'versionId': active_version.get('versionId'),
        'versionNumber': active_version.get('versionNumber'),
        'title': payload.get('title', 'Artifact'),
        'primaryLanguage': active_version.get('primaryLanguage') or payload.get('primaryLanguage'),
        'summary': summary,
        'fileCount': active_version.get('fileCount') or payload.get('fileCount'),
        'entrypoint': entrypoint,
        'pending': pending,
        'type': payload.get('type') or 'project',
    }


def build_draft_artifact(payload: Dict[str, Any], message_id: str) -> Dict[str, Any]:
    draft_id = payload.get('artifactId') or f"draft_{message_id}"
    version_id = f"{draft_id}_v1"
    files = payload.get('files', [])
    version = {
        'versionId': version_id,
        'versionNumber': 1,
        'entrypointPath': payload.get('entrypoint'),
        'primaryLanguage': payload.get('primaryLanguage'),
        'summary': payload.get('summary'),
        'fileCount': len(files),
        'totalBytes': payload.get('totalBytes'),
        'files': files,
    }
    return {
        'artifactId': draft_id,
        'title': payload.get('title', 'Artifact'),
        'entrypoint': payload.get('entrypoint'),
        'type': payload.get('type') or 'project',
        'meta': payload.get('meta') or {},
        'versions': [version],
        'activeVersionId': version_id,
        'activeVersion': version,
        'primaryLanguage': payload.get('primaryLanguage'),
        'fileCount': len(files),
        'summary': payload.get('summary'),
    }


def _select_relevant_messages(
    current_prompt: str,
    messages: List[Dict[str, Any]],
    n_recent_turns: int = 2,
    n_relevant: int = 2,
    diversity_threshold: float = 0.85,
    min_relevance: float = 0.20
) -> List[Dict[str, Any]]:
    """
    Select messages using HYBRID retrieval: lexical + semantic + recency.

    Strategy:
        1. Include last N turns (user+assistant pairs) - prevents "forgot what you just said"
        2. Use hybrid search (BM25 + embeddings + recency) for older messages
        3. MMR-style diversity against ALL selected (recent + relevant)
        4. Minimum combined score threshold

    Hybrid advantages:
        - Lexical (BM25): Catches error codes, filenames, variable names
        - Semantic: Catches conceptual similarity, paraphrases
        - Recency: Boosts recent discussions

    Args:
        current_prompt: Current user message
        messages: All previous messages (each entry = one turn with prompt+response)
        n_recent_turns: Number of recent turns to always include (default 2)
        n_relevant: Number of hybrid matches to find (default 2)
        diversity_threshold: Max similarity to any selected message (default 0.85)
        min_relevance: Minimum combined score to include (default 0.20)

    Returns:
        List ordered for prompt: [relevant_background..., recent_turns...]
        This ordering ensures recent context isn't overridden by old retrievals.
    """
    if not messages:
        return []

    recent = []
    used_indices = set()

    # 1. RECENCY: Last N turns (each message entry = 1 turn with prompt+response)
    recent_candidates = []
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        has_prompt = bool(msg.get('prompt', '').strip())
        has_response = bool(msg.get('response', '').strip())
        if has_prompt or has_response:
            recent_candidates.append((i, msg))
            if len(recent_candidates) >= n_recent_turns:
                break

    # Add recent in chronological order
    for i, msg in reversed(recent_candidates):
        recent.append(msg)
        used_indices.add(i)

    # 2. HYBRID RETRIEVAL: BM25 + Semantic + Recency for older messages
    if not current_prompt or len(messages) <= n_recent_turns:
        return recent

    try:
        from .hybrid_retrieval import (
            HybridRetriever,
            filter_messages_by_recency,
            detect_search_all_intent,
            MAX_MESSAGES_PER_USER,
            MAX_MESSAGE_AGE_DAYS
        )

        # Filter to older messages only (exclude recent)
        older_messages = [msg for i, msg in enumerate(messages) if i not in used_indices]

        if not older_messages:
            return recent

        # Stage 1: Apply recency/count limits before indexing
        # This prevents unbounded growth in embedding compute
        search_all = detect_search_all_intent(current_prompt)
        filtered_messages = filter_messages_by_recency(
            older_messages,
            max_messages=MAX_MESSAGES_PER_USER,
            max_age_days=MAX_MESSAGE_AGE_DAYS,
            session_id=None,  # TODO: pass session_id for conversation filtering
            search_all=search_all
        )

        if not filtered_messages:
            return recent

        # Build hybrid index and search (Stage 2 happens inside search())
        retriever = HybridRetriever()
        retriever.index_messages(filtered_messages, compute_embeddings=True)

        # Get more candidates than needed for diversity filtering
        candidates = retriever.search(
            query=current_prompt,
            top_k=n_relevant * 3,
            min_score=min_relevance
        )

        if not candidates:
            return recent

        # 3. DIVERSITY: MMR-style - check against recent + already chosen
        from .hybrid_retrieval import normalize_stored_embedding
        model = get_embedding_model()
        relevant = []

        # Get embeddings for recent messages (for diversity check)
        recent_vecs = []
        if model:
            for msg in recent:
                text = f"{msg.get('prompt','')} {msg.get('response','')}".strip()
                if text:
                    # Handle both quantized and raw embedding formats
                    vec = normalize_stored_embedding(msg.get('embedding'))
                    if vec is None or len(vec) == 0:
                        vec = model.encode(text).tolist()
                    recent_vecs.append(vec)

        selected_vecs = list(recent_vecs)

        for result in candidates:
            if len(relevant) >= n_relevant:
                break

            msg = result.content

            # Diversity check using embeddings
            if model and selected_vecs:
                text = f"{msg.get('prompt','')} {msg.get('response','')}".strip()
                if text:
                    # Handle both quantized and raw embedding formats
                    vec = normalize_stored_embedding(msg.get('embedding'))
                    if vec is None or len(vec) == 0:
                        vec = model.encode(text).tolist()

                    is_diverse = True
                    for selected_vec in selected_vecs:
                        vec_sim = float(np.dot(vec, selected_vec) / (np.linalg.norm(vec) * np.linalg.norm(selected_vec) + 1e-9))
                        if vec_sim > diversity_threshold:
                            is_diverse = False
                            break

                    if not is_diverse:
                        continue

                    selected_vecs.append(vec)

            relevant.append(msg)
            logger.debug(f"Hybrid retrieved: lexical={result.lexical_score:.2f} semantic={result.semantic_score:.2f} combined={result.combined_score:.2f}")

        # 4. ORDERING: relevant (background) first, then recent (latest context)
        return relevant + recent

    except Exception as e:
        logger.warning(f"Hybrid retrieval failed: {e}, falling back to recent only")
        return recent


def _update_session_summary(user_id: str, session_id: str, session_data: Dict[str, Any], user_prompt: str, response_text: str):
    """
    Maintain a structured session state summary.
    Uses diff-based updates to prevent drift.

    Format:
        GOAL: Current objective
        DECISIONS: Key choices made
        CONSTRAINTS: Limitations
        DEFINITIONS: "X means Y"
        OPEN: Next steps, questions
    """
    from .session_summary import update_session_summary

    existing = (session_data or {}).get('session_summary', '')

    try:
        new_summary = update_session_summary(
            existing_summary=existing,
            user_prompt=user_prompt,
            assistant_response=response_text
        )

        if new_summary != existing:
            db.collection('users').document(user_id).collection('chat_sessions').document(session_id).update({
                'session_summary': new_summary
            })
    except Exception as e:
        logger.warning(f"Failed to update session summary for {session_id}: {e}")

