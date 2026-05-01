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


MAX_TOKENS = 16384  # Models on Groq/Cerebras support 8K-128K context
RESPONSE_TOKEN_BUFFER = 1024  # Reserve tokens for the AI's response


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


# Ordered list of models to try for artifact generation.
# When one fails to produce valid artifact JSON, try the next.
ARTIFACT_MODEL_CASCADE = [
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "gpt-4.1",
    "qwen/qwen3-32b",
    "kimi-k2.5",
    "llama-3.3-70b-versatile",
]

# Models capable of producing well-formed artifact JSON.
# Small/fast models should only get chat instructions — they can't reliably
# follow the ACTION protocol with artifact fences.
ARTIFACT_CAPABLE_MODELS = {
    "deepseek-v4-flash", "deepseek-v4-pro",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "qwen/qwen3-32b", "qwen/qwq-32b",
    "kimi-k2.5", "kimi-latest",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
}


def _get_model_family(model: str) -> str:
    """Detect model family from model ID for prompt tuning."""
    m = model.lower()
    if 'deepseek' in m:
        return 'deepseek'
    if 'llama' in m:
        return 'llama'
    if 'qwen' in m or 'qwq' in m:
        return 'qwen'
    if 'kimi' in m:
        return 'kimi'
    if 'gpt' in m:
        return 'openai'
    if 'gemma' in m or 'dolphin' in m:
        return 'small'
    return 'default'


def _model_search_prompt_variant(family: str) -> str:
    """Return model-family-specific web search instructions.

    Different models need different prompt styles:
    - DeepSeek: follows well but needs explicit \\n reminder
    - Llama/Qwen: need shorter, simpler examples
    - Kimi: tends to add preamble before ACTION
    - Small/local: very concise, one example only
    """
    if family == 'deepseek':
        return (
            WEB_SEARCH_INSTRUCTIONS +
            "\nIMPORTANT: Each header field (query, search_type, render_hint, reason) "
            "must be on its own line. End the ACTION block with a blank line before "
            "writing your response text.\n"
        )
    if family in ('llama', 'qwen'):
        # Shorter prompt — these models get confused by long blocks
        return (
            "\n===============================================================\n"
            "WEB SEARCH\n"
            "===============================================================\n\n"
            "To search the web, output EXACTLY this format (no extra text before it):\n"
            "ACTION: web_search\n"
            "query: <your search query>\n"
            "search_type: general | images | video | news\n"
            "render_hint: news_cards | image_grid | video_primary | text_only\n"
            "reason: <why>\n\n"
            "Then write your response using [1], [2], [3] to cite sources.\n\n"
            "Use web_search for: news, current events, prices, weather, music, "
            "anything you're not 100% sure about.\n"
            "Do NOT search for: creative tasks, coding, greetings.\n\n"
            "EXAMPLE:\n"
            "ACTION: web_search\n"
            "query: latest news today\n"
            "search_type: news\n"
            "render_hint: news_cards\n"
            "reason: user wants current news\n\n"
        )
    if family == 'kimi':
        return (
            WEB_SEARCH_INSTRUCTIONS +
            "\nCRITICAL: Start your response IMMEDIATELY with ACTION: web_search "
            "on the very first line. Do NOT write any text before the ACTION line.\n"
        )
    if family == 'small':
        # Minimal prompt for small local models
        return (
            "\nWEB SEARCH: To search, output:\n"
            "ACTION: web_search\n"
            "query: <search terms>\n"
            "search_type: general\n"
            "render_hint: text_only\n"
            "reason: <why>\n\n"
            "Then write your answer citing sources as [1], [2], [3].\n"
            "Search for: news, prices, current events, songs, facts you're unsure about.\n\n"
        )
    return WEB_SEARCH_INSTRUCTIONS


def get_developer_instructions(model: str = "") -> str:
    """Return DEVELOPER_INSTRUCTIONS, stripping artifact/patch sections for incapable models.

    Web search instructions are ALWAYS included, tuned per model family.
    """
    from app.model_catalog import resolve_model_id
    resolved = resolve_model_id(model) if model else ""
    family = _get_model_family(resolved or model)
    search_block = _model_search_prompt_variant(family)

    if resolved in ARTIFACT_CAPABLE_MODELS or model in ARTIFACT_CAPABLE_MODELS:
        return DEVELOPER_INSTRUCTIONS + search_block
    # Strip everything from ACTION PROTOCOL onward — small models just get chat rules
    # But always include web search so all models can search
    marker = "RESPONSE FORMAT - ACTION PROTOCOL"
    idx = DEVELOPER_INSTRUCTIONS.find(marker)
    if idx > 0:
        return DEVELOPER_INSTRUCTIONS[:idx] + (
            "RESPONSE FORMAT\n"
            "===============\n"
            "Respond in plain Markdown. Use code blocks (```) for any code.\n"
            "Do NOT use ACTION: lines or artifact blocks.\n"
            "The ONLY ACTION you may use is ACTION: web_search (see below).\n"
        ) + search_block
    return DEVELOPER_INSTRUCTIONS + search_block


DEVELOPER_INSTRUCTIONS = (
    "Act as a helpful backend assistant for this application. "
    "Follow the UI tone, keep reasoning internal, and never leak secrets.\n\n"
    "CRITICAL: NEVER use HTML tags like <p>, <div>, <span>, <br>, etc. in your responses. "
    "Use plain text and Markdown formatting ONLY. No HTML.\n\n"

    """
═══════════════════════════════════════════════════════════════
RESPONSE FORMATTING
═══════════════════════════════════════════════════════════════

### Core principle
Make answers SCANNABLE. Use rich Markdown structure — headings, bullets, bold, code blocks, tables, emojis — wherever they help the reader navigate or absorb the answer faster than flowing prose would. Default to STRUCTURED, not plain prose.

### Markdown — use it generously
- **Headers (## ###):** Use for any multi-section answer. `##` for top-level sections, `###` for sub-sections. Most non-trivial answers benefit from at least one heading. Skip headers only for one-or-two-sentence replies.
- **Bold:** Use freely to highlight key terms, takeaways, names of things, important concepts, the answer to "what is X". Better to over-bold than to leave the reader scanning for the point.
- **Italics:** For emphasis, technical terms on first mention, titles of works, foreign phrases.
- **Lists:** Use bullets for any set of 2+ parallel items. Numbered lists for ordered steps. Nested lists are fine when the structure earns it. Prefer bullets over "first… then… also…" prose.
- **Code blocks:** Always for code, commands, file paths, technical strings. Use language tags (```python, ```js, ```bash, etc.).
- **Inline code:** For short technical strings (`variable_names`, `--flag`, `path/to/file`).
- **Tables:** Use when comparing items across attributes — even 2 items if the comparison helps.
- **Blockquotes:** For verbatim quotations, definitions you want to set apart, key insights worth highlighting.

### Emojis — use them
Emojis add warmth and visual hierarchy. They make answers feel less robotic and help the eye anchor on key sections.

- ✅ confirmations, "do this", completed steps
- ❌ "don't do this", warnings about wrong patterns
- 💡 tips, insights, recommendations
- ⚠️ caveats, gotchas, warnings
- 📝 notes, side comments
- 🚀 getting-started, shipping, performance wins
- 🔧 tools, configuration, setup
- 📦 packages, dependencies, installs
- 🎯 goals, objectives
- 📚 background, references
- ⚡ quick start, fast paths
- 🎉 celebrations, completions

Lead headings with a topic emoji when it fits naturally (`## 🎯 Goals`, `## ⚡ Quick Start`, `## 🔧 Setup`). Use them generously in conversational and informational responses. Skip inside pure code blocks; tone them down inside error or refusal messages.

### Length and density
Match structure to information richness, not raw word count. A short answer can still use **bold** for the key term. A 5-step process should be a numbered list, not a wall of text. A comparison should be a table.

### Answer first
The first sentence answers the question. Then expand with structured detail. Never preamble. Never describe what you're about to say.

### Follow-up messages
In ongoing conversations, respond to what was just said — not to the entire conversation history. Don't recap, don't re-establish context.

### Technical and coding contexts
Lead with the code block solution. Use sub-headings (`### Setup`, `### Usage`, `### Edge cases`) when the answer has multiple distinct parts. Annotate non-obvious code with comments.

### Recommendations and comparisons
**Verdict first, in bold.** Then a comparison table or pros/cons list. Never make the reader assemble the answer from pros and cons.

### Corrections
State the correction first — bold it if it matters. Then explain why.

### Uncertainty
Say it once, briefly. Don't hedge every sentence.

### Tone
Warm and structured. Confident, knowledgeable, scannable. Like a well-formatted technical doc that a friend wrote.

### Never
- Open with "Certainly!", "Great question!", "Of course!" or any affirmation
- End with "I hope this helps!" or similar
- Repeat the user's question before answering
- Greet back with a name and stop — never ask "how can I help you today?" after a greeting

### Concrete example — match this format

User asks: "Give me tips on improving time management."

Bad response (flat prose, no structure):
> To improve time management, start by prioritizing tasks using the Eisenhower Matrix, which categorizes tasks into urgent vs. important. Focus on the most critical tasks first, and break them down into smaller, manageable chunks. Minimize distractions by creating a conducive work environment...

Good response (the format you should produce):

## ⏰ Time Management Essentials

The biggest single win: **prioritize ruthlessly**. Most people work on what's urgent, not what's important.

### 🎯 Core techniques

- **Eisenhower Matrix** — sort every task by urgent × important. Do urgent+important *now*; schedule important+not-urgent.
- **Time blocking** — assign each task a calendar slot. Defends focus better than open-ended to-do lists.
- **Pomodoro** — 25-minute focused sprints, 5-minute breaks. Keeps energy steady through the day.

### 💡 What actually moves the needle

1. Pick **one task** that, if done, makes the day a win. Do it first.
2. Kill notifications during deep work — every interruption costs ~20 minutes of context recovery.
3. End each day by writing tomorrow's top 3 items. Decisions are easier in advance.

### ⚠️ Common traps

- ❌ Tools won't fix it — habits will.
- ❌ "Productive procrastination" (organizing, planning) feels like work but isn't.

**Bottom line:** pick one technique, run it for two weeks, then stack another. Don't try everything at once.

---

Note in the good response: heading with topic emoji, sub-headings for sections, **bold** for key terms, mixed bullet + numbered lists, ⚠️/❌/✅ emojis for visual anchoring, a `---` rule before a final takeaway, **Bottom line:** in bold. THAT is the level of structure to produce. Use this density of formatting whenever the answer has any internal structure at all.

"""


    "===============================================================\n"
    "RESPONSE FORMAT - ACTION PROTOCOL\n"
    "===============================================================\n\n"

    "Your FIRST LINE must be an ACTION declaration. Choose exactly one:\n\n"

    "ACTION: chat\n"
    "  Use when: answering questions, explaining concepts, discussing approaches,\n"
    "  giving feedback, or when the user's request does not produce a runnable\n"
    "  deliverable. Short code snippets (under 12 lines) that illustrate a point\n"
    "  belong in chat, not an artifact.\n\n"

    "ACTION: create_artifact\n"
    "  Use when the user asks to BUILD, CREATE, or MAKE a complete application.\n"
    "  ALWAYS use create_artifact for:\n"
    "  - React/JSX apps, HTML/CSS/JS pages, interactive web apps\n"
    "  - Python apps, games, visualizations, tools (we have a Python runtime)\n"
    "  - Any request like 'build me X', 'create X app', 'make X in Y language'\n"
    "  Do NOT use for: short code snippets, explanations with examples,\n"
    "  partial code, or utility functions. Those belong in chat.\n\n"

    "ACTION: patch_artifact\n"
    "  Use when: the user asks you to FIX, UPDATE, CHANGE, MODIFY, or IMPROVE\n"
    "  an existing artifact. You will receive the current file contents below.\n"
    "  Respond with only the patches needed - do not regenerate unchanged files.\n\n"

    "ACTION: show_chart <asset_name>\n"
    "  Use when: the user asks about the PRICE, VALUE, CHART, or MARKET DATA of\n"
    "  a specific cryptocurrency or stock. Put the asset identifier after show_chart.\n"
    "  Examples:\n"
    "    ACTION: show_chart bitcoin\n"
    "    ACTION: show_chart AAPL stock\n"
    "    ACTION: show_chart solana\n"
    "    ACTION: show_chart ethereum\n"
    "  IMPORTANT: Only use show_chart when the user explicitly wants to see\n"
    "  price/market data. Do NOT use it for general mentions of crypto or stocks\n"
    "  in conversation (e.g. 'explain how bitcoin works' = chat, NOT show_chart).\n"
    "  For follow-up questions like 'what is the price for it', resolve the pronoun\n"
    "  from conversation context and use the actual asset name.\n\n"

    "ACTION: generate_image\n"
    "  description: <detailed, standalone description for the image generator>\n"
    "  source: explicit | inferred\n\n"
    "  Use when: the user wants you to CREATE/GENERATE an actual image.\n\n"
    "  source values:\n"
    "  - explicit: user's message contains a clear image request — ANY of these patterns:\n"
    "    'draw me X', 'draw a X', 'image of X', 'create a picture of X', 'photo of X',\n"
    "    'paint X', 'sketch X', 'generate an image of X', 'make me an image of X'.\n"
    "    If the user describes WHAT they want drawn, it is ALWAYS explicit.\n"
    "  - inferred: ONLY when the user references prior context with pronouns and you must\n"
    "    guess what they mean ('make one of those', 'create it', 'do that but as an image').\n"
    "    If the message itself describes the subject, use explicit, NOT inferred.\n\n"
    "  The description MUST be a complete, standalone prompt for an image generator.\n"
    "  Resolve ALL pronouns — 'it', 'that', 'this' — into concrete descriptions using\n"
    "  conversation context. Never pass pronouns to the image generator.\n\n"
    "  NEVER use generate_image for:\n"
    "  - Code, artifacts, or app creation ('create a React app' = create_artifact)\n"
    "  - Charts or financial data ('show me bitcoin' = show_chart)\n"
    "  - Explanations about images ('explain how DALL-E works' = chat)\n"
    "  - Figurative language ('picture this scenario', 'draw a conclusion' = chat)\n"
    "  - Discussing existing images ('the image shows...' = chat)\n\n"

    "After the ACTION line, write your response.\n\n"

    "EXAMPLE for chat responses:\n"
    "ACTION: chat\n"
    "[your markdown response]\n\n"

    "EXAMPLE for creating artifacts:\n"
    "ACTION: create_artifact\n"
    "[brief explanation]\n\n"
    "```artifact-meta\n"
    "{\"title\":\"App Name\",\"type\":\"project\",\"primaryLanguage\":\"jsx\"}\n"
    "```\n\n"
    "```artifact-file:App.jsx:jsx\n"
    "import React from 'react';\n"
    "export default function App() {\n"
    "  return <div>Hello World</div>;\n"
    "}\n"
    "```\n\n"
    "```artifact-file:style.css:css\n"
    "body { margin: 0; font-family: sans-serif; }\n"
    "```\n\n"
    "IMPORTANT: Each file goes in its own ```artifact-file:path:language block.\n"
    "  Code goes DIRECTLY in the block — NO JSON wrapping, NO escaping.\n"
    "  The ```artifact-meta block has ONLY the title/type/language metadata as JSON.\n\n"
    "For React apps: Use App.jsx as entrypoint + style.css. DO NOT create index.html.\n"
    "  The preview system auto-loads React 18 + Babel. Just write JSX directly.\n"
    "  IMPORTANT: return() MUST have a SINGLE root element.\n"
    "  CRITICAL: Inside artifact code, NEVER use markdown like **bold**, *italic*.\n"
    "  NEVER mix index.html with .jsx files.\n"
    "For plain HTML apps: Create index.html, style.css, script.js.\n"
    "React artifacts MUST have AT LEAST 2 files: App.jsx + style.css.\n\n"

    "EXAMPLE for patching artifacts:\n"
    "ACTION: patch_artifact\n"
    "[brief explanation]\n"
    "```artifact_patch\n"
    "{\"patches\":[{\"file\":\"App.jsx\",\"old_string\":\"exact text to find\",\"new_string\":\"replacement\"}]}\n"
    "```\n\n"
    "Rules for patches:\n"
    "- old_string must be EXACT text from the current code (include 2-4 surrounding lines)\n"
    "- new_string is the replacement (empty string for deletions)\n"
    "- Multiple patches allowed in one block\n"
    "- Write a brief summary of what you changed before the patch block\n\n"

    "Triggers for patch: 'fix', 'update', 'correct', 'change', 'modify', 'edit', 'improve'\n\n"

    "EXAMPLE for generating images:\n"
    "ACTION: generate_image\n"
    "description: a vibrant sunset over snow-capped mountains with a mirror-like lake reflecting orange and purple sky colors\n"
    "source: explicit\n\n"
    "Here's a sunset with warm golden light washing over snow-capped peaks, reflected in a still mountain lake.\n\n"

    "EXAMPLE (inferred context):\n"
    "ACTION: generate_image\n"
    "description: traditional Japanese pagoda temple surrounded by cherry blossom trees with Mount Fuji in the background\n"
    "source: inferred\n\n"
    "Based on our discussion, here's a traditional Japanese scene with cherry blossoms and Mount Fuji.\n\n"

    "IMPORTANT for generate_image chat text:\n"
    "  - Write a SHORT, natural sentence presenting the image (1-2 sentences max)\n"
    "  - Describe what's IN the image, not the process of making it\n"
    "  - NEVER say 'I created a description', 'I sent to an image generator', or mention the pipeline\n"
    "  - Good: 'Here's a golden sunset over the mountains.'\n"
    "  - Bad: 'I've created a description for an image generator to produce a sunset.'\n\n"
)

# Web search instructions — included for ALL models (not just artifact-capable)
WEB_SEARCH_INSTRUCTIONS = (
    "\n===============================================================\n"
    "WEB SEARCH\n"
    "===============================================================\n\n"
    "ACTION: web_search\n"
    "  query: <search query, resolve all pronouns from context>\n"
    "  search_type: general | images | video | news\n"
    "  render_hint: news_cards | image_grid | video_primary | text_only\n"
    "  reason: <why search is needed, logged server-side only>\n"
    "  embed_video: <YouTube video ID> (optional, model picks most relevant)\n\n"
    "  render_hint values:\n"
    "  - news_cards: use when results are news articles with images (news, current events, 'what happened')\n"
    "  - image_grid: use when user wants to SEE something ('show me', 'what does X look like', 'images of')\n"
    "  - video_primary: use when a video is the best answer ('how to', 'tutorial', 'watch', 'play')\n"
    "  - text_only: use for factual queries, prices, weather, definitions\n\n"
    "  Use when: user asks about current events, real-time data, facts you're\n"
    "  not confident about, prices, weather, news, 'search for', 'look up',\n"
    "  or any question where up-to-date information improves your answer.\n"
    "  ALWAYS search for: music/song requests ('play X', 'find song'),\n"
    "  specific media lookups, product lookups, or any proper noun / title\n"
    "  you are not 100% certain about — search first, don't guess.\n\n"
    "  NEVER use web_search for:\n"
    "  - Creative tasks (writing, coding, brainstorming)\n"
    "  - Explaining well-known concepts\n"
    "  - Conversational responses ('how are you', 'thank you')\n\n"
    "  After search results are injected, you MUST cite sources inline using\n"
    "  [1], [2], [3] notation. Every factual claim from search results must\n"
    "  have a citation.\n\n"
    "EXAMPLES for web search:\n"
    "ACTION: web_search\n"
    "query: what happened in the news today\n"
    "search_type: news\n"
    "render_hint: news_cards\n"
    "reason: user wants current news\n\n"
    "ACTION: web_search\n"
    "query: modern kitchen designs\n"
    "search_type: images\n"
    "render_hint: image_grid\n"
    "reason: user wants to see kitchen designs\n\n"
    "ACTION: web_search\n"
    "query: how to tie a bowline knot\n"
    "search_type: video\n"
    "render_hint: video_primary\n"
    "reason: tutorial best shown as video\n\n"
    "ACTION: web_search\n"
    "query: bitcoin price today\n"
    "search_type: general\n"
    "render_hint: text_only\n"
    "reason: user needs current price data\n\n"
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
"wants_code": <true if wants code/mermaid/diagram code, false otherwise>,
"style": "<one of: editorial, modern_blog, technical, chat, comparison, creative>"}}

IMPORTANT:
- Words like "create", "make", "build" at the start usually mean creative_task, NOT cryptocurrency.
- Only set has_crypto_mention=true if they're clearly asking about crypto PRICES or MARKET data.
- For "style": pick `editorial` for longform explainers/opinion ("tell me about X"); `modern_blog` for tips/how-tos/listicles ("ways to Y"); `technical` for code questions/debugging; `chat` for greetings/thanks/short factual; `comparison` for "X vs Y"/"which is better"; `creative` for stories/poems/fiction. Prefer `modern_blog` when uncertain."""

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
                    logger.info(f"🧠 Intent classified: {result.get('intent_type')} (prompt_len={len(prompt)})")
                    return result
    except Exception as e:
        logger.debug(f"Intent classification failed: {e}")

    # Fallback: return unknown intent (let old logic handle it)
    return {"intent_type": "unknown", "confidence": 0}


def detect_crypto_price_query(prompt: str) -> Optional[dict]:
    """
    Detect if the user is asking for crypto or stock price/chart.
    Returns dict with asset info if detected, None otherwise.

    Strategy: REQUIRE explicit financial intent signals. Never scan every word
    against CoinGecko — there are millions of tokens with common English names.

    Detection layers (all deterministic, no AI call):
      1. Unambiguous ticker symbols (btc, eth, doge, xrp, ltc) — match anywhere
      2. Explicit price-query structure ("X price", "price of X", "how much is X")
         → resolve the captured asset name via known lists + CoinGecko
      3. Ambiguous symbols (sol, ada, dot, link) — only match when adjacent to
         a financial keyword (price, chart, coin, crypto, token, stock, trading)
    """
    if not prompt:
        return None

    prompt_lower = prompt.lower().strip()

    # Short prompts (< 5 words) with a known asset are likely price queries
    word_count = len(prompt_lower.split())

    # ── 1. Unambiguous top-coin symbols — safe to match anywhere ────────
    # These are never normal English words.
    _unambiguous = {
        'btc': 'bitcoin', 'eth': 'ethereum', 'doge': 'dogecoin',
        'xrp': 'ripple', 'ltc': 'litecoin',
    }
    for symbol, coin_id in _unambiguous.items():
        if re.search(rf'\b{symbol}\b', prompt_lower):
            logger.info(f"💰 Unambiguous crypto symbol: {symbol} -> {coin_id}")
            return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

    # Full coin names — also unambiguous (allow plural "bitcoins", "ethereums" etc.)
    _coin_names = {
        'bitcoin': 'bitcoin', 'ethereum': 'ethereum', 'dogecoin': 'dogecoin',
        'litecoin': 'litecoin', 'solana': 'solana', 'cardano': 'cardano',
        'polkadot': 'polkadot', 'chainlink': 'chainlink',
    }
    for name, coin_id in _coin_names.items():
        if re.search(rf'\b{name}s?\b', prompt_lower):
            logger.info(f"💰 Crypto name detected: {name} -> {coin_id}")
            return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

    # ── 2. Explicit price-query patterns ────────────────────────────────
    # Only fire when the sentence structurally asks for a price/chart.
    # The captured group is the asset name — resolve it against known lists.
    price_patterns = [
        # "what's the price of X", "show me X chart", "check X value"
        r'(?:what(?:\'s| is)?|how much is|show me|get|check|give me)\s+(?:the\s+)?(?:current\s+)?(?:price|value|worth|chart|graph)\s+(?:of|for)\s+(\w+(?:\s+\w+)?)',
        # "X price", "X chart" (only at end of prompt to avoid mid-sentence matches)
        r'\b(\w+(?:\s+\w+)?)\s+(?:price|chart|stock)\s*[\?\.\!]?\s*$',
        # "price of X", "chart of X"
        r'\b(?:price|chart|graph|stock)\s+(?:of|for)\s+(\w+(?:\s+\w+)?)',
        # "how much is X", "how much does X cost"
        r'how\s+much\s+(?:is|does)\s+(?:one\s+)?(\w+)\s+(?:cost|worth)',
    ]

    for pattern in price_patterns:
        match = re.search(pattern, prompt_lower)
        if match:
            query = match.group(1).strip()
            result = _resolve_asset(query)
            if result:
                return result

    # ── 3. Ambiguous symbols — only with adjacent financial keyword ─────
    _financial_context = r'(?:price|chart|value|coin|crypto|token|stock|trading|market|ticker|worth)'
    _ambiguous = {
        'sol': 'solana', 'ada': 'cardano', 'dot': 'polkadot',
        'link': 'chainlink', 'bnb': 'binancecoin',
        'solana': 'solana', 'cardano': 'cardano', 'polkadot': 'polkadot',
        'chainlink': 'chainlink', 'ripple': 'ripple',
    }
    for symbol, coin_id in _ambiguous.items():
        # Must appear adjacent to a financial keyword (within ~3 words)
        if re.search(rf'\b{symbol}\b\s+{_financial_context}', prompt_lower) or \
           re.search(rf'{_financial_context}\s+(?:of\s+)?\b{symbol}\b', prompt_lower):
            logger.info(f"💰 Ambiguous crypto with financial context: {symbol} -> {coin_id}")
            return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

    # ── 4. Stock symbols & company names (only in price-query context) ──
    has_price_word = bool(re.search(
        r'\b(?:price|chart|graph|stock|ticker|trading|market\s+cap|shares?)\b',
        prompt_lower
    ))
    if has_price_word:
        for symbol in STOCK_SYMBOLS:
            if re.search(rf'\b{re.escape(symbol)}\b', prompt_lower):
                logger.info(f"📈 Stock detected with price context: {symbol}")
                return {'symbol': symbol.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}
        for company, symbol in COMPANY_TO_SYMBOL.items():
            if company in prompt_lower:
                logger.info(f"📈 Stock company detected: {company} -> {symbol}")
                return {'symbol': symbol.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}

    # ── 5. Short direct queries (≤4 words) — likely just an asset name ──
    # e.g. "solana", "bitcoin", "AAPL", "apple". Curated lists only; do NOT
    # fall through to the dynamic CoinGecko lookup here — without explicit
    # financial intent it routinely hijacks English greetings ("hello" →
    # "hello-labs", "ok" → random token). Explicit-intent layer 2 above is
    # the right place for dynamic resolution.
    if word_count <= 4:
        result = _resolve_asset(prompt_lower, allow_dynamic=False)
        if result:
            return result

    return None


def _resolve_asset(query: str, allow_dynamic: bool = True) -> Optional[dict]:
    """
    Try to resolve a query string to a known crypto or stock asset.

    allow_dynamic=True: use get_coin_id_dynamic (CoinGecko full catalog) as a
    fallback. Safe only when the query already carries explicit financial
    intent ("price of X", "X chart"), because CoinGecko lists thousands of
    obscure tokens with English-word tickers ("hello-labs" for "hello",
    "ok" for "ok", etc.) that would otherwise hijack greetings and short
    replies.

    allow_dynamic=False: match only against curated lists (CRYPTO_SYMBOLS,
    STOCK_SYMBOLS, COMPANY_TO_SYMBOL). Use this from the short-query layer
    where the user hasn't expressed financial intent.
    """
    query = query.lower().strip()
    # Reject pronouns, articles, and common non-asset words
    _non_assets = {
        'it', 'this', 'that', 'them', 'they', 'he', 'she', 'we', 'us', 'me',
        'my', 'your', 'our', 'their', 'its', 'his', 'her', 'mine', 'yours',
        'the', 'a', 'an', 'one', 'some', 'any', 'each', 'every', 'all',
        'what', 'which', 'who', 'whom', 'how', 'when', 'where', 'why',
        'something', 'anything', 'everything', 'nothing', 'thing', 'things',
        'current', 'today', 'now', 'please', 'here', 'there',
    }
    if query in _non_assets or len(query) < 2:
        return None
    # Normalize: try singular form too (e.g. "bitcoins" -> "bitcoin")
    candidates = [query]
    if query.endswith('s') and len(query) > 3:
        candidates.append(query[:-1])

    for q in candidates:
        # All curated lists run BEFORE dynamic CoinGecko — CoinGecko's
        # catalog contains thousands of scam/lookalike tokens that would
        # hijack queries for legitimate stocks ("apple" → scam token
        # instead of AAPL). Curated → safe; dynamic → only as last resort.

        # 1. Curated crypto fast path (top coins).
        if q in CRYPTO_SYMBOLS:
            coin_id = CRYPTO_SYMBOLS[q]
            logger.info(f"💰 Crypto price query: {q} -> {coin_id}")
            return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}
        # 2. Curated stock symbol. STOCK_SYMBOLS is a lowercase set so
        # we match q.lower() against it; the case-mismatch bug
        # (q.upper() in lowercase set → always False) used to let TSLA
        # / NVDA / etc. fall through to dynamic CoinGecko and hit
        # scam tokens with the same ticker.
        if q.lower() in STOCK_SYMBOLS:
            logger.info(f"📈 Stock price query: {q}")
            return {'symbol': q.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}
        # 3. Curated company name.
        if q in COMPANY_TO_SYMBOL:
            symbol = COMPANY_TO_SYMBOL[q]
            logger.info(f"📈 Stock price query (company): {q} -> {symbol}")
            return {'symbol': symbol.upper(), 'asset_type': 'stock', 'currency': 'usd', 'range': '1D'}
        # 4. Dynamic CoinGecko lookup — gated by explicit financial intent.
        #    Last resort: only fires for queries that didn't match any
        #    curated list above. Catches legitimate alt-coins ("vechain",
        #    "polkadot") without hijacking company names.
        if allow_dynamic:
            coin_id = get_coin_id_dynamic(q)
            if coin_id:
                logger.info(f"💰 Crypto price query (dynamic): {q} -> {coin_id}")
                return {'coin': coin_id, 'asset_type': 'crypto', 'currency': 'usd', 'range': '1D'}

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
    max_tokens: int = MAX_TOKENS,
    response_token_buffer: int = RESPONSE_TOKEN_BUFFER
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

    # === PRIORITY 2: Developer instructions (required — contains artifact format rules) ===
    if developer_instructions:
        try_add(2, "developer", developer_instructions + "\n", required=True)

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
    logger.info(f"🔍 HISTORY DEBUG: previous_messages count={len(previous_messages)}, last 4 prompt_lens={[len(m.get('prompt', '')) for m in previous_messages[-4:]]}")
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
        # Vision model fallback chain — resolves through the orchestrator
        # registry so a deprecated vision model auto-heals to its watcher-
        # supplied replacement (or the next live entry in FALLBACK_CHAINS).
        from app.model_catalog import resolve_live_model_id, get_model_for_task
        vision_models = [
            get_model_for_task("vision"),
            resolve_live_model_id(
                "meta-llama/llama-4-scout-17b-16e-instruct", task="vision"
            ),
        ]
        # Drop duplicates while preserving order (resolver may return the
        # same live model for both entries).
        seen = set()
        vision_models = [m for m in vision_models if m and not (m in seen or seen.add(m))]
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
    turn_metadata: Optional[Dict[str, Any]] = None,
    ai_attachments: Optional[List[Dict[str, Any]]] = None,
    message_type: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Save the current user-assistant interaction to the database.

    Args:
        display_prompt: The user-facing prompt to save (without system injections).
                       If None, uses user_prompt.

    Returns:
        Tuple of the chat payload and a boolean indicating whether persistence succeeded.
    """
    response_identifier = response_id or f"msg_{uuid.uuid4().hex}"
    # Serialize search results for Firestore
    serialized_search = []
    if search_results and isinstance(search_results, dict):
        # _search_done_data format: {web: [...], images: [...], videos: [...], ...}
        serialized_search = search_results
    elif search_results and isinstance(search_results, list):
        serialized_search = search_results

    chat_data = {
        'prompt': display_prompt if display_prompt is not None else user_prompt,
        'response': response_text,
        'timestamp': datetime.utcnow().isoformat(),
        'sentiment': sentiment,
        'is_code': is_code_response,
        'search_results': serialized_search,
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
    if turn_metadata:
        chat_data['turn_metadata'] = turn_metadata
    if search_results:
        if isinstance(search_results, dict):
            # _search_done_data format: {web: [...], images: [...], videos: [...], ...}
            # Already set in chat_data above via serialized_search — no normalization needed
            chat_data['search_results'] = search_results
        elif isinstance(search_results, list):
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
    if ai_attachments:
        chat_data['attachments'] = ai_attachments
    if message_type:
        chat_data['message_type'] = message_type

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

