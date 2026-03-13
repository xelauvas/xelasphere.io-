# views.py

import atexit
import base64
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import random
import re
import shutil
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum  # <--- Added for backend enumeration

import firebase_admin
import numpy as np
import pytesseract
import tldextract
import requests
from PIL import Image, ImageOps, ImageFilter
from PyPDF2 import PdfReader
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DocxDocument = None
    DOCX_AVAILABLE = False
try:
    from openpyxl import load_workbook
    XLSX_AVAILABLE = True
except ImportError:
    load_workbook = None
    XLSX_AVAILABLE = False
try:
    from pptx import Presentation
    PPTX_AVAILABLE = True
except ImportError:
    Presentation = None
    PPTX_AVAILABLE = False
try:
    from striprtf.striprtf import rtf_to_text
    RTF_AVAILABLE = True
except ImportError:
    rtf_to_text = None
    RTF_AVAILABLE = False
try:
    from app.services.url_reader import get_url_reader, URLReader
    URL_READER_AVAILABLE = True
    logger_init_msg = "URL Reader service loaded successfully"
except ImportError as e:
    get_url_reader = None
    URLReader = None
    URL_READER_AVAILABLE = False
    logger_init_msg = f"URL Reader service unavailable: {e}"
from flask import (
    Blueprint,
    Flask,
    jsonify,
    render_template,
    request,
    Response,
    session,
    current_app,
    send_from_directory,
    abort,
    url_for,
    g
)
from flask_session import Session
from google.cloud import firestore as gc_firestore
from google.cloud.exceptions import NotFound
from .memory_v2.embeddings import embed_text as _jina_embed_text, embeddings_available as _embeddings_available
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .auth import auth_routes
from .config import Config, db
from .limiter import limiter, csrf_required
from .encryption import encrypt_api_key, decrypt_api_key, is_encrypted
from .grog_api import TaskGenerator
from .title_generator import generate_title
from .model_catalog import MODEL_LABELS, MODEL_PROVIDERS, DEFAULT_MODEL, AUTO_MODEL, resolve_model_id, get_selectable_models
from .smart_router import smart_route, is_refusal_response, get_uncensored_model, check_prompt_injection
from .utilities import (
    categorize_sessions,
    count_tokens,
    send_prompt,
    send_prompt_with_images,
    tokenize_sentences,
    is_image_request,
    generate_fallback_response
)
from .artifacts import (
    ArtifactPreference,
    ArtifactRouter,
    ArtifactStore,
    parse_structured_response,
)
# DEPRECATED: Old memory system (replaced by Memory V2)
# from .memory_gate import process_memory_gate
# from .memory_buckets import load_relevant_memory

# Memory V2: Single orchestration API
from .memory_v2 import (
    MemoryOrchestrator,
    SessionContext,
    ConversationState,
    MemoryFeatureFlags,
)

# Memory V3: Adaptive behavior layer (signals only, no storage)
from .memory_v3 import (
    get_v3_context, V3_ENABLED, reset_v3_state,
    should_inject_v3, get_v3_cohort, get_rollout_manager,
    generate_v3_signal, record_v3_metrics
)

from .system_rules import (
    get_system_rules,
    should_skip_memory,
    should_skip_reflection,
    should_skip_memory_write,
    get_message_mode
)
from .list_registry import (
    process_ai_response_for_lists,
    resolve_user_pointer
)
from .smart_guard import (
    smart_check_input,
    smart_tag_response,
    smart_validate_response
)
from .file_processor import FileProcessor
from .crypto_api import (
    get_crypto_ai_context, format_crypto_context_for_ai,
    get_stock_ai_context, format_stock_context_for_ai,
    get_coin_id as get_coin_id_dynamic  # Dynamic lookup with CoinGecko list
)
from .asset_config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, COMPANY_TO_SYMBOL,
    ASSET_NAMES, get_coin_id, get_display_name
)

from urllib.parse import urlparse
import asyncio
from cachetools import TTLCache
from contextlib import nullcontext
from pydantic import BaseModel, HttpUrl, Field
from prometheus_client import Counter, Histogram
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Optional EasyOCR fallback
try:
    import easyocr  # type: ignore
    EASY_OCR_AVAILABLE = True
except Exception:
    easyocr = None
    EASY_OCR_AVAILABLE = False

# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    firebase_admin.initialize_app()

# Global/system instructions and developer instructions used in prompt construction
SYSTEM_INSTRUCTIONS = (
    "You are an advanced conversational AI. "
    "Always follow system and safety policies. "
    "Keep reasoning internal and do not expose chain-of-thought.\n\n"

    "PRIORITY RULES (override all other behavior):\n"
    "A. AMBIGUITY: If unclear, DO NOT answer. Ask ONE clarifying question.\n"
    "B. FACT CONFIDENCE: If unsure, ask for clarification or state uncertainty.\n"
    "C. NO GUESSING: Pause → Clarify → Answer correctly.\n"
    "D. FACT CONSISTENCY: Never contradict a fact you stated earlier in this session.\n"
    "E. POINTER RESOLUTION: If user references 'the first one' or '#2', restate it before answering.\n\n"

    "⚠️ EXCEPTION - CREATIVE/WRITING/BUILD TASKS (OVERRIDES AMBIGUITY RULE):\n"
    "When user asks to WRITE, BUILD, CREATE, or MAKE something:\n"
    "- DO NOT ask clarifying questions - just BUILD IT immediately\n"
    "- Use sensible defaults for any missing details\n"
    "- If user says 'just do it', 'skip', 'go ahead' → PRODUCE OUTPUT NOW\n"
    "- This applies to: code, apps, games, tools, emails, stories, components\n"
    "- Better to produce a complete working result than ask questions\n"
    "- The user can always ask for changes after seeing the output\n\n"

    "BEHAVIOR RULES:\n"
    "1. STATELESS (factual): Answer directly, no memory/history\n"
    "2. STATEFUL (references past): Use relevant memory, favor user over memory\n"
    "3. Never inject irrelevant context\n"
    "4. Answer question first, match length to complexity"
)


def strip_html_from_response(text: str) -> str:
    """
    Strip common HTML tags from AI response while preserving code blocks.
    Some models (like Llama, Venice) may output HTML tags despite instructions.
    """
    if not text or not isinstance(text, str):
        return text

    original = text

    # Don't strip if it looks like intentional HTML in code blocks or artifacts
    if '```html' in text.lower() or '```htm' in text.lower():
        return text
    if '```artifact' in text.lower():
        return text

    # Check if response contains HTML tags
    has_html = bool(re.search(r'<(?:p|div|span|br|h[1-6]|strong|b|em|i|ul|ol|li|a)\b[^>]*>', text, re.IGNORECASE))

    if has_html:
        logging.info(f"Stripping HTML tags from AI response (first 100 chars): {text[:100]}")

    # Strip paragraph tags
    text = re.sub(r'<p\s*[^>]*>(.*?)</p>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip div tags
    text = re.sub(r'<div\s*[^>]*>(.*?)</div>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip span tags
    text = re.sub(r'<span\s*[^>]*>(.*?)</span>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip br tags
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    # Strip heading tags (h1-h6) - convert to markdown
    for i in range(1, 7):
        text = re.sub(rf'<h{i}\s*[^>]*>(.*?)</h{i}>', rf'{"#" * i} \1', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip strong/b tags - convert to markdown bold
    text = re.sub(r'<(?:strong|b)\s*[^>]*>(.*?)</(?:strong|b)>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip em/i tags - convert to markdown italic
    text = re.sub(r'<(?:em|i)\s*[^>]*>(.*?)</(?:em|i)>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip ul/ol/li tags - convert to markdown list
    text = re.sub(r'<li\s*[^>]*>(.*?)</li>', r'- \1', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?(?:ul|ol)\s*[^>]*>', '', text, flags=re.IGNORECASE)
    # Strip a tags - convert to markdown link
    text = re.sub(r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL | re.IGNORECASE)

    # Final cleanup: strip any remaining simple HTML tags as a catch-all
    # This catches tags that might not have closing tags or are malformed
    text = re.sub(r'</?(?:p|div|span)(?:\s[^>]*)?\s*/?>', '', text, flags=re.IGNORECASE)

    if has_html and text != original:
        logging.info(f"HTML stripped successfully. Result (first 100 chars): {text[:100]}")

    return text.strip()


def is_email_request(user_prompt: str) -> bool:
    """
    Detect if the user is asking for an email to be written.
    Checks for email-related keywords in multiple languages.
    """
    if not user_prompt or len(user_prompt) < 3:
        return False

    prompt_lower = user_prompt.lower()

    # Email keywords in various languages
    email_keywords = [
        # English
        r'\bemail\b', r'\be-mail\b', r'\bmail\b',
        # Action phrases
        r'\bwrite\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bdraft\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bcompose\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bsend\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        # Spanish
        r'\bcorreo\b', r'\bcorreo electr[oó]nico\b',
        r'\bescribe\s+(?:un\s+)?correo\b', r'\bredacta\s+(?:un\s+)?correo\b',
        # French
        r'\bcourriel\b', r'\bmél\b', r'\be-mail\b',
        r'\b[eé]crire?\s+(?:un\s+)?(?:courriel|e-mail|mail)\b',
        r'\br[eé]diger?\s+(?:un\s+)?(?:courriel|e-mail|mail)\b',
        # German
        r'\be-mail\b', r'\bemail\b',
        r'\bschreiben?\s+(?:eine?\s+)?e-?mail\b',
        # Portuguese
        r'\be-mail\b', r'\bemail\b',
        r'\bescrever?\s+(?:um\s+)?e-?mail\b',
        # Italian
        r'\be-mail\b', r'\bemail\b', r'\bposta elettronica\b',
        r'\bscrivere?\s+(?:una?\s+)?e-?mail\b',
        # Romanian
        r'\be-mail\b', r'\bemail\b',
        r'\bscrie\s+(?:un\s+)?e-?mail\b', r'\bredacteaz[aă]\s+(?:un\s+)?e-?mail\b',
        # Russian
        r'\bписьмо\b', r'\bэлектронн(?:ое|ая|ой)\s+письм[оа]\b',
        r'\bнаписать?\s+письмо\b',
        # Chinese (simplified)
        r'电子邮件', r'邮件', r'写邮件',
        # Japanese
        r'メール', r'電子メール',
        # Korean
        r'이메일', r'메일',
        # Arabic
        r'بريد إلكتروني', r'إيميل',
        # Hindi
        r'ईमेल',
    ]

    for pattern in email_keywords:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            logger.info(f"Email request detected: matched pattern '{pattern}'")
            return True

    return False


def looks_like_email(response_text: str) -> bool:
    """
    Quick check if the response actually looks like an email (has greeting + sign-off).
    This prevents false positives when AI asks clarifying questions instead of writing an email.
    """
    if not response_text or len(response_text) < 50:
        return False

    text_lower = response_text.lower()

    # Check for email greetings at the start
    # Allow word characters OR brackets for placeholders like [Name], [Hiring Manager]
    greetings = [
        r'(?:^|\n)\s*(?:dear|hi|hello|hey)\s+[\w\[\]]',
        r'(?:^|\n)\s*(?:estimad[oa]|querid[oa]|hola)\s+[\w\[\]]',  # Spanish
        r'(?:^|\n)\s*(?:cher|chère|bonjour)\s*[\w\[\]]?',  # French
        r'(?:^|\n)\s*(?:liebe[rs]?|sehr geehrte)\s*[\w\[\]]?',  # German
        r'(?:^|\n)\s*(?:stimate|stimată|dragă|bună)\s*[\w\[\]]?',  # Romanian
    ]

    # Check for sign-offs at the end
    sign_offs = [
        r'(?:best|kind|warm)?\s*regards',
        r'sincerely', r'thanks', r'cheers', r'best wishes',
        r'saludos', r'atentamente',  # Spanish
        r'cordialement',  # French
        r'cu stimă', r'cu respect',  # Romanian
        r'\[your name\]', r'\[name\]', r'\[tu nombre\]', r'\[numele tău\]',
    ]

    first_300 = text_lower[:300]
    last_300 = text_lower[-300:]

    has_greeting = any(re.search(p, first_300, re.IGNORECASE) for p in greetings)
    has_sign_off = any(re.search(p, last_300, re.IGNORECASE) for p in sign_offs)

    logger.info(f"📧 EMAIL CHECK: has_greeting={has_greeting}, has_sign_off={has_sign_off}, first_100={first_300[:100]}")

    return has_greeting and has_sign_off


def parse_email_from_response(response_text: str) -> dict:
    """
    Parse the AI response to extract email components (to, subject, body).
    Extracts only the actual email content, excluding AI explanatory text.
    """
    if not response_text:
        return None

    email_data = {
        'to': '',
        'subject': '',
        'body': ''
    }

    email_content = response_text.strip()

    # Look for Subject: line (supports multiple languages)
    subject_match = re.search(r'^(?:\*\*)?(?:subject|asunto|objet|betreff|oggetto|onderwerp|re|subj)[:\*]*\s*(.+?)$', email_content, re.IGNORECASE | re.MULTILINE)
    if subject_match:
        email_data['subject'] = subject_match.group(1).strip().strip('*')

    # Look for To: line
    to_match = re.search(r'^(?:to|para|à|an|a)[:\s]+(.+?)$', email_content, re.IGNORECASE | re.MULTILINE)
    if to_match:
        email_data['to'] = to_match.group(1).strip()

    # Find where the actual email body starts (greeting line)
    greeting_patterns = [
        r'^(?:dear|hi|hello|hey|good\s+(?:morning|afternoon|evening))\s+[\w\[\]]',
        r'^(?:estimad[oa]|querid[oa]|hola)\s+[\w\[\]]',  # Spanish
        r'^(?:cher|chère|bonjour|salut)\s+[\w\[\]]',  # French
        r'^(?:sehr\s+geehrt|liebe[rs]?|hallo)\s+[\w\[\]]',  # German
        r'^\[(?:recipient|name|client|hiring)',  # Placeholders
        r'^(?:i\s+hope\s+this)',  # Common email opener
    ]

    # Find where the actual email ends (sign-off line)
    signoff_patterns = [
        r'(?:best|kind|warm)\s*regards',
        r'(?:sincerely|cordially|respectfully)',
        r'(?:thank\s*you|thanks)',
        r'(?:yours\s+(?:truly|faithfully))',
        r'\[your\s+(?:name|full\s+name|signature)\]',
        r'(?:atentamente|saludos|cordialmente)',  # Spanish
        r'(?:cordialement|salutations)',  # French
    ]

    # Try to find email boundaries
    lines = email_content.split('\n')
    email_start = 0
    email_end = len(lines)

    # Find start of actual email (first greeting line)
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        for pattern in greeting_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                email_start = i
                break
        if email_start > 0:
            break

    # Find end of actual email (last sign-off line + a few lines for name/contact)
    for i in range(len(lines) - 1, -1, -1):
        line_lower = lines[i].lower().strip()
        for pattern in signoff_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                # Include up to 6 more lines for signature block
                email_end = min(i + 7, len(lines))
                break
        if email_end < len(lines):
            break

    # Extract the email body
    body_lines = lines[email_start:email_end]
    body = '\n'.join(body_lines)

    # Clean up - remove header lines from body
    body = re.sub(r'^(?:\*\*)?(?:subject|asunto|objet|betreff|oggetto|onderwerp|re|subj|to|para|à|an|from|de|cc|bcc)[:\*]*\s*.+?$', '', body, flags=re.IGNORECASE | re.MULTILINE)

    # Remove markdown separators (---, ===, ***)
    body = re.sub(r'^[-=\*]{3,}\s*$', '', body, flags=re.MULTILINE)

    # Clean up excessive blank lines
    body = re.sub(r'\n{3,}', '\n\n', body)

    email_data['body'] = body.strip()

    # If we couldn't find a subject, try to generate one from first line
    if not email_data['subject'] and body:
        first_line = body.split('\n')[0][:60]
        if first_line:
            email_data['subject'] = first_line + ('...' if len(body.split('\n')[0]) > 60 else '')

    return email_data


# ==================== AI Intent Classification ==================== #
# Uses the LLM to understand user intent before any hardcoded detection

# Cache for intent classification (avoid repeated LLM calls)
_intent_cache = {}
_intent_cache_ttl = 300  # 5 minutes

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


# ==================== Crypto & Stock Price Detection ==================== #
# CRYPTO_SYMBOLS, STOCK_SYMBOLS, COMPANY_TO_SYMBOL imported from asset_config


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

# Configure Tesseract path and languages gracefully (enable OCR when available)
tesseract_cmd = getattr(Config, 'TESSERACT_CMD', None)
OCR_LANG_RAW = getattr(Config, 'TESSERACT_LANG', 'eng')

def normalize_langs(raw: str) -> Tuple[str, List[str]]:
    if not raw:
        return 'eng', ['eng']
    # Allow comma, space, or plus separators
    parts = re.split(r'[,\s\+]+', raw.strip())
    parts = [p for p in parts if p]
    if not parts:
        parts = ['eng']
    # Deduplicate while preserving order
    seen = []
    for p in parts:
        lp = p.lower()
        if lp not in seen:
            seen.append(lp)
    return '+'.join(seen), seen

OCR_LANG, OCR_LANG_LIST = normalize_langs(OCR_LANG_RAW)

resolved_tesseract = None
if tesseract_cmd and shutil.which(tesseract_cmd):
    resolved_tesseract = shutil.which(tesseract_cmd)
else:
    fallback = shutil.which('tesseract')
    if fallback:
        resolved_tesseract = fallback

OCR_AVAILABLE = bool(resolved_tesseract)
if OCR_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = resolved_tesseract

easyocr_reader = None
def get_easyocr_reader():
    global easyocr_reader
    if not EASY_OCR_AVAILABLE:
        return None
    if easyocr_reader is None:
        try:
            easyocr_reader = easyocr.Reader(OCR_LANG_LIST, gpu=False)
        except Exception as e:
            logger.warning(f"Failed to initialize EasyOCR reader: {e}")
            easyocr_reader = None
    return easyocr_reader

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
if not OCR_AVAILABLE:
    logger.warning("Tesseract OCR not found on system PATH; image text extraction will be skipped.")
if URL_READER_AVAILABLE:
    logger.info("URL Reader service loaded successfully")
else:
    logger.warning(f"URL Reader service unavailable - link summarization disabled")

SUPPORTED_TTS_FORMATS = {
    'mp3': 'audio/mpeg',
    'wav': 'audio/wav',
    'opus': 'audio/opus'
}

# Define Flask Blueprint
app_routes = Blueprint('app_routes', __name__)

# Create an instance of TaskGenerator
task_generator = TaskGenerator()


def fetch_user_preferences(user_id: str) -> dict:
    """Fetch user's category preferences from Firebase."""
    try:
        if not user_id:
            return {}

        user_doc = db.collection('users').document(user_id).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            return data.get('preferences', {})
        return {}

    except Exception as e:
        logger.error(f"Error fetching user preferences for {user_id}: {e}")
        return {}


def update_user_preferences(user_id: str, message: str) -> None:
    """Update user's category preferences based on their message."""
    try:
        if not user_id or not message:
            return

        # Detect categories from the message
        detected = TaskGenerator.detect_categories(message)
        if not detected:
            return

        # Update category counts in Firebase
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            data = user_doc.to_dict()
            preferences = data.get('preferences', {})
            categories = preferences.get('categories', {})
        else:
            preferences = {}
            categories = {}

        # Increment counts for detected categories
        for category in detected:
            categories[category] = categories.get(category, 0) + 1

        preferences['categories'] = categories
        preferences['last_updated'] = datetime.utcnow().isoformat()

        # Save back to Firebase
        user_ref.set({'preferences': preferences}, merge=True)
        logger.debug(f"Updated preferences for {user_id}: {categories}")

    except Exception as e:
        logger.error(f"Error updating user preferences for {user_id}: {e}")


# Allowed file extensions for uploads
ALLOWED_EXTENSIONS = {
    # Images
    'png', 'jpg', 'jpeg', 'gif',
    # Documents
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    # Text & Data
    'txt', 'text', 'md', 'csv', 'json', 'rtf',
    # Config & Data formats
    'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'env',
    'xml', 'html', 'htm', 'svg',
    # Programming languages
    'py', 'pyw',                    # Python
    'js', 'mjs', 'cjs',             # JavaScript
    'ts', 'tsx', 'jsx',             # TypeScript/React
    'java', 'kt', 'kts', 'scala',   # JVM languages
    'c', 'h', 'cpp', 'hpp', 'cc',   # C/C++
    'cs',                           # C#
    'go',                           # Go
    'rs',                           # Rust
    'rb', 'erb',                    # Ruby
    'php',                          # PHP
    'swift',                        # Swift
    'dart',                         # Dart
    'lua',                          # Lua
    'pl', 'pm',                     # Perl
    'r',                            # R
    'sql',                          # SQL
    'sh', 'bash', 'zsh', 'fish',    # Shell scripts
    'ps1', 'psm1',                  # PowerShell
    # Web & Markup
    'css', 'scss', 'sass', 'less',  # Stylesheets
    'vue', 'svelte',                # Frontend frameworks
    # Other
    'hs', 'lhs',                    # Haskell
    'ex', 'exs',                    # Elixir
    'clj', 'cljs', 'cljc',          # Clojure
    'ml', 'mli',                    # OCaml
    'fs', 'fsx',                    # F#
    'erl', 'hrl',                   # Erlang
    'elm',                          # Elm
    'v', 'sv',                      # Verilog
    'vhd', 'vhdl',                  # VHDL
    'asm', 's',                     # Assembly
    'dockerfile',                   # Docker
    'makefile', 'mk',               # Make
    'gradle', 'cmake',              # Build tools
    'tf', 'tfvars',                 # Terraform
    'proto',                        # Protocol Buffers
    'graphql', 'gql',               # GraphQL
    'sol',                          # Solidity
    'zig',                          # Zig
    'nim',                          # Nim
    'cr',                           # Crystal
    'jl',                           # Julia
}

CREDITS_MIN_INCREMENT = 1


def _adjust_user_session_total(user_id: str, delta: int) -> None:
    """Increment or decrement a user's stored session count."""
    if not user_id or delta == 0:
        return
    try:
        db.collection('users').document(user_id).set(
            {'total_sessions': gc_firestore.Increment(delta)},
            merge=True
        )
    except Exception as exc:
        logger.warning(f"Failed to adjust total_sessions for {user_id}: {exc}")


def _increment_user_credits(user_id: str, amount: int) -> None:
    """Increment the stored credit usage for a user."""
    if not user_id or amount <= 0:
        return
    try:
        db.collection('users').document(user_id).set(
            {'credits_used': gc_firestore.Increment(amount)},
            merge=True
        )
    except Exception as exc:
        logger.warning(f"Failed to increment credits for {user_id}: {exc}")


def _calculate_credit_cost(user_prompt: str, response_text: str) -> int:
    """Estimate credit cost based on prompt/response token counts."""
    try:
        prompt_tokens = count_tokens(user_prompt or "")
        response_tokens = count_tokens(response_text or "")
    except Exception as exc:
        logger.debug(f"Token count failed, defaulting credit cost to 1: {exc}")
        return CREDITS_MIN_INCREMENT
    total_tokens = prompt_tokens + response_tokens
    return max(total_tokens, CREDITS_MIN_INCREMENT)


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

# Define maximum tokens and buffer for AI model responses
MAX_TOKENS = 4096  # Adjust based on AI model's specifications
RESPONSE_TOKEN_BUFFER = 500  # Reserve tokens for the AI's response

def login_required(f):
    """Decorator to ensure the user is logged in before accessing the route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'You need to be logged in to perform this action.'}), 403
        return f(*args, **kwargs)
    return decorated_function


# Guest mode constants
GUEST_MESSAGE_LIMIT = 5
GUEST_BANNER_THRESHOLD = 3


def is_guest():
    """Check if current session is a guest (not logged in)."""
    return 'guest' in session and 'user' not in session


def get_guest_message_count():
    """Get the current guest message count."""
    if is_guest():
        return session['guest'].get('message_count', 0)
    return 0


def guest_allowed(f):
    """Allow guests with limited features, or full access for logged-in users."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session and 'guest' not in session:
            # Create guest session
            session['guest'] = {
                'id': f'guest_{uuid.uuid4().hex[:12]}',
                'message_count': 0,
                'created_at': datetime.utcnow().isoformat()
            }
            session.modified = True
        return f(*args, **kwargs)
    return decorated_function


# ==================== Guest IP Tracking (Firebase) ====================
def get_client_ip():
    """Get the real client IP, handling proxies."""
    # Cloudflare header (most reliable when using Cloudflare)
    if request.headers.get('CF-Connecting-IP'):
        ip = request.headers.get('CF-Connecting-IP')
    # Check for forwarded headers (behind proxy/load balancer)
    elif request.headers.get('X-Forwarded-For'):
        # X-Forwarded-For can contain multiple IPs, first one is the client
        ip = request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        ip = request.headers.get('X-Real-IP')
    else:
        ip = request.remote_addr
    return ip or '0.0.0.0'


def hash_ip(ip: str) -> str:
    """Hash IP for privacy - we don't store raw IPs."""
    # Add a salt from config for extra security
    salt = Config.SECRET_KEY[:16] if hasattr(Config, 'SECRET_KEY') else 'xelasphere'
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def get_guest_ip_usage(ip_hash: str) -> dict:
    """Get guest usage data from Firebase by IP hash."""
    try:
        doc_ref = db.collection('guest_usage').document(ip_hash)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.warning(f"Failed to get guest IP usage: {e}")
        return None


def increment_guest_ip_usage(ip_hash: str) -> int:
    """Increment guest message count in Firebase. Returns new count."""
    try:
        doc_ref = db.collection('guest_usage').document(ip_hash)
        doc = doc_ref.get()

        now = datetime.utcnow()
        today = now.strftime('%Y-%m-%d')

        if doc.exists:
            data = doc.to_dict()
            # Reset count if it's a new day
            if data.get('last_date') != today:
                new_count = 1
                doc_ref.update({
                    'message_count': new_count,
                    'last_date': today,
                    'last_used': now.isoformat(),
                    'total_messages': gc_firestore.Increment(1)
                })
            else:
                new_count = data.get('message_count', 0) + 1
                doc_ref.update({
                    'message_count': new_count,
                    'last_used': now.isoformat(),
                    'total_messages': gc_firestore.Increment(1)
                })
            return new_count
        else:
            # First time seeing this IP
            doc_ref.set({
                'message_count': 1,
                'last_date': today,
                'first_seen': now.isoformat(),
                'last_used': now.isoformat(),
                'total_messages': 1
            })
            return 1
    except Exception as e:
        logger.warning(f"Failed to increment guest IP usage: {e}")
        # Fallback to session-based tracking if Firebase fails
        return -1


def check_guest_ip_limit(ip_hash: str) -> tuple[bool, int]:
    """Check if guest IP has exceeded daily limit. Returns (allowed, current_count)."""
    try:
        data = get_guest_ip_usage(ip_hash)
        if not data:
            return True, 0

        today = datetime.utcnow().strftime('%Y-%m-%d')
        # Reset count if it's a new day
        if data.get('last_date') != today:
            return True, 0

        current_count = data.get('message_count', 0)
        return current_count < GUEST_MESSAGE_LIMIT, current_count
    except Exception as e:
        logger.warning(f"Failed to check guest IP limit: {e}")
        # Allow on error, fallback to session tracking
        return True, 0


def make_etag_response(data, status_code=200):
    """Create a JSON response with ETag support for cache validation.

    Returns 304 Not Modified if client's If-None-Match header matches the ETag.
    """
    json_data = json.dumps(data, separators=(',', ':'), sort_keys=True)
    etag = hashlib.md5(json_data.encode()).hexdigest()

    # Check if client has matching ETag
    if_none_match = request.headers.get('If-None-Match')
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status=304)

    response = Response(json_data, status=status_code, mimetype='application/json')
    response.headers['ETag'] = f'"{etag}"'
    response.headers['Cache-Control'] = 'private, must-revalidate'
    return response


def error_handler(f):
    """Decorator to handle exceptions and provide consistent error responses."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except NotFound as e:
            logger.error(f"Firestore document not found: {str(e)}")
            return jsonify({'error': 'Resource not found.'}), 404
        except ValueError as e:
            logger.error(f"Invalid argument: {str(e)}")
            return jsonify({'error': 'Invalid operation.'}), 400
        except ModelProviderError as e:
            logger.error(f"Model provider error ({e.error_code}): {e.message}")
            payload = {'error': e.message}
            if e.error_code:
                payload['error_code'] = e.error_code
            return jsonify(payload), e.status_code
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            logger.debug(traceback.format_exc())
            return jsonify({'error': 'An unexpected error occurred.'}), 500
    return wrapper


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


class ModelProviderError(Exception):
    """Raised when an upstream AI provider fails and we want to surface a friendly message."""
    def __init__(self, message: str, error_code: Optional[str] = None, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class TokenLimitError(Exception):
    """Custom exception raised when the token limit is exceeded."""
    pass


class ChatSession:
    """Handle chat session operations in Firestore."""
    def __init__(self, db_client: gc_firestore.Client):
        self.db = db_client

    def create_session(self, user_id: str, session_name: Optional[str] = None, temporary: bool = False) -> str:
        session_id = f"session_{uuid.uuid4()}"
        session_ref = self._get_session_ref(user_id, session_id)
        now = datetime.utcnow()
        session_data = {
            'session_name': session_name or 'New Chat',
            'messages': [],
            'pages': [],
            'active_page_id': None,
            'created_at': now,
            'status': 'temporary' if temporary else 'active',
            'is_temporary': temporary,
            'last_updated': now,
            'is_pinned': False,
            'pinned_at': None,
            # Unread notification tracking for cross-device sync
            'unread_count': 0,
            'last_message_at': None,
            'last_read_at': now
        }
        session_ref.set(session_data)
        if not temporary:
            _adjust_user_session_total(user_id, 1)
        return session_id

    def compute_embedding(self, text: str) -> List[float]:
        model = get_embedding_model()
        if not model:
            return []
        return model.encode(text).tolist()

    def extract_key_info(self, text: str) -> List[str]:
        return [text]

    def update_session_preferences(self, user_id: str, session_id: str, preferences: List[str]) -> None:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if session_doc.exists:
            existing_preferences = session_doc.to_dict().get('preferences', [])
            updated_preferences = list(set(existing_preferences + preferences))
        else:
            updated_preferences = preferences
        session_ref.set({'preferences': updated_preferences}, merge=True)

    def save_messages(self, user_id: str, session_id: str, messages: List[Dict[str, Any]], is_active_session: bool = True) -> None:
        """Save messages to a session.

        Args:
            user_id: The user's ID
            session_id: The session ID to save to
            messages: List of message dictionaries to save
            is_active_session: If False, increment unread_count for cross-device notifications
        """
        if not messages:
            return

        session_ref = self._get_session_ref(user_id, session_id)

        # Process messages for Firestore storage
        processed_messages = []
        for message in messages:
            message_copy = message.copy()
            
            # Convert SearchResult objects to dictionaries
            if 'search_results' in message_copy:
                if isinstance(message_copy['search_results'], list):
                    message_copy['search_results'] = [
                        {
                            'title': result.title,
                            'link': str(result.link),
                            'snippet': result.snippet,
                            'source_domain': result.source_domain,
                            'website_name': result.website_name,
                            'favicon': result.favicon,
                            'relevance_score': float(result.relevance_score),
                            'timestamp': result.timestamp.isoformat()
                        } if isinstance(result, SearchResult) else result
                        for result in message_copy['search_results']
                    ]
            
            # Add quantized embedding when response text exists
            # Uses int8 quantization to reduce Firestore storage by 4x
            response_text = message_copy.get('response', '')
            if response_text:
                try:
                    from .embedding_utils import embedding_to_storage
                    raw_embedding = self.compute_embedding(response_text)
                    if raw_embedding:
                        message_copy['embedding'] = embedding_to_storage(raw_embedding)
                    else:
                        message_copy['embedding'] = {}
                except ImportError:
                    # Fallback to raw embedding if utils not available
                    message_copy['embedding'] = self.compute_embedding(response_text)
            else:
                message_copy['embedding'] = {}
            
            # Extract key info
            key_info = self.extract_key_info(message_copy.get('prompt', ''))
            self.update_session_preferences(user_id, session_id, key_info)
            
            processed_messages.append(message_copy)

        # Update Firestore with fallback for edge cases
        now = datetime.utcnow()
        try:
            update_data = {
                'messages': gc_firestore.ArrayUnion(processed_messages),
                'last_updated': now,
                'last_message_at': now
            }
            # Increment unread_count only if this isn't the active session (for cross-device notifications)
            if not is_active_session:
                update_data['unread_count'] = gc_firestore.Increment(1)

            session_ref.update(update_data)
            logger.info(f"📝 SAVE DEBUG: Successfully saved {len(processed_messages)} message(s) to session {session_id} via ArrayUnion (unread_increment={not is_active_session})")
        except Exception as update_err:
            logger.warning(f"📝 SAVE DEBUG: ArrayUnion update failed for session {session_id}: {update_err}, trying fallback...")
            # Fallback: read current messages and append
            try:
                doc = session_ref.get()
                if doc.exists:
                    current_data = doc.to_dict()
                    current_messages = current_data.get('messages', [])
                    current_messages.extend(processed_messages)
                    fallback_update = {
                        'messages': current_messages,
                        'last_updated': now,
                        'last_message_at': now
                    }
                    if not is_active_session:
                        current_unread = current_data.get('unread_count', 0)
                        fallback_update['unread_count'] = current_unread + 1
                    session_ref.update(fallback_update)
                    logger.info(f"📝 SAVE DEBUG: Fallback save succeeded - now {len(current_messages)} messages in session {session_id}")
                else:
                    # Document doesn't exist - create it
                    session_ref.set({
                        'messages': processed_messages,
                        'last_updated': now,
                        'created_at': now,
                        'session_name': 'Chat',
                        'status': 'active',
                        'last_message_at': now,
                        'unread_count': 0 if is_active_session else 1,
                        'last_read_at': now
                    })
                    logger.info(f"📝 SAVE DEBUG: Created new document with {len(processed_messages)} message(s)")
            except Exception as fallback_err:
                logger.error(f"📝 SAVE DEBUG: Fallback save also failed for session {session_id}: {fallback_err}")

    def get_history(self, user_id: str, page_size: int = 10, page_token: Optional[str] = None) -> Dict[str, Any]:
        sessions_query = self._get_sessions_query(user_id, page_token)
        sessions = sessions_query.limit(page_size + 1).stream()

        chat_history = []
        next_token = None

        for idx, session_doc in enumerate(sessions):
            if idx >= page_size:
                next_token = session_doc.id
                break

            session_data = session_doc.to_dict()
            if not isinstance(session_data, dict):
                logger.warning(f"Skipping session with unexpected data format: {session_data}")
                continue

            status = session_data.get('status', 'active')
            is_temporary = session_data.get('is_temporary', status == 'temporary')
            if status in ('archived', 'temporary'):
                continue

            session_name = session_data.get('session_name', f"Chat {idx + 1}")
            created_at = session_data.get('created_at')
            created_at_str = created_at.isoformat() if created_at and isinstance(created_at, datetime) else None
            pinned_at = session_data.get('pinned_at')
            if isinstance(pinned_at, datetime):
                pinned_at = pinned_at.isoformat()
            pages_raw = session_data.get('pages') or []
            normalized_pages = []
            for page in pages_raw:
                if not isinstance(page, dict):
                    continue
                normalized = page.copy()
                created_at = normalized.get('createdAt') or normalized.get('created_at')
                if isinstance(created_at, datetime):
                    normalized['createdAt'] = created_at.isoformat()
                    normalized.pop('created_at', None)
                normalized_pages.append(normalized)

            messages = session_data.get('messages', [])
            # Debug: log artifact fields in messages
            for i, msg in enumerate(messages):
                if isinstance(msg, dict) and msg.get('artifact_id'):
                    logger.info(f"📥 HISTORY DEBUG: Session {session_doc.id} msg[{i}] has artifact_id={msg.get('artifact_id')}, render_mode={msg.get('render_mode')}")

            # Get last_updated timestamp
            last_updated = session_data.get('last_updated')
            last_updated_str = last_updated.isoformat() if last_updated and isinstance(last_updated, datetime) else None

            chat_history.append({
                'session_id': session_doc.id,
                'session_name': session_name,
                'created_at': created_at_str,
                'last_updated': last_updated_str or created_at_str,
                'messages': messages,
                'pages': normalized_pages,
                'active_page_id': session_data.get('active_page_id'),
                'status': status,
                'pinned_at': pinned_at,
                'is_temporary': is_temporary
            })

        return {'chat_history': chat_history, 'next_page_token': next_token}

    def get_session(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        session_doc = self._get_session_ref(user_id, session_id).get()
        if not session_doc.exists:
            return None

        session_data = session_doc.to_dict() or {}
        created_at = session_data.get('created_at')
        pinned_at = session_data.get('pinned_at')

        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        if isinstance(pinned_at, datetime):
            pinned_at = pinned_at.isoformat()

        pages_raw = session_data.get('pages') or []
        normalized_pages = []
        for page in pages_raw:
            if not isinstance(page, dict):
                continue
            normalized = page.copy()
            created_at_page = normalized.get('createdAt') or normalized.get('created_at')
            if isinstance(created_at_page, datetime):
                normalized['createdAt'] = created_at_page.isoformat()
                normalized.pop('created_at', None)
            normalized_pages.append(normalized)

        return {
            'session_id': session_id,
            'session_name': session_data.get('session_name', 'Untitled Session'),
            'created_at': created_at,
            'messages': session_data.get('messages', []),
            'pages': normalized_pages,
            'active_page_id': session_data.get('active_page_id'),
            'status': session_data.get('status', 'active'),
            'pinned_at': pinned_at,
            'is_temporary': session_data.get('is_temporary', False),
            'model_label': session_data.get('model_label') or session_data.get('model') or ''
        }

    def archive_session(self, user_id: str, session_id: str) -> Dict[str, Any]:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            raise ValueError("Chat session not found.")

        archived_at = datetime.utcnow()
        session_ref.set({
            'status': 'archived',
            'archived_at': archived_at,
            'last_updated': archived_at
        }, merge=True)
        return {'archived_at': archived_at.isoformat()}

    def pin_session(self, user_id: str, session_id: str, pinned: bool = True) -> Dict[str, Any]:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            raise ValueError("Chat session not found.")

        pinned_at_value = datetime.utcnow() if pinned else None
        update_payload = {
            'is_pinned': pinned,
            'pinned_at': pinned_at_value,
            'last_updated': datetime.utcnow()
        }
        session_ref.set(update_payload, merge=True)
        return {
            'is_pinned': pinned,
            'pinned_at': pinned_at_value.isoformat() if pinned_at_value else None
        }

    def _get_session_ref(self, user_id: str, session_id: str) -> gc_firestore.DocumentReference:
        return self.db.collection('users').document(user_id).collection('chat_sessions').document(session_id)

    def _get_sessions_query(self, user_id: str, page_token: Optional[str] = None) -> gc_firestore.Query:
        # Order by last_updated so most recently active sessions appear first
        query = self.db.collection('users').document(user_id) \
            .collection('chat_sessions') \
            .order_by('last_updated', direction=gc_firestore.Query.DESCENDING)
        if page_token:
            last_doc = self._get_session_ref(user_id, page_token).get()
            if last_doc.exists:
                query = query.start_after(last_doc)
        return query


chat_session = ChatSession(db)
artifact_router = ArtifactRouter()
artifact_store = ArtifactStore(db)

executor = ThreadPoolExecutor(max_workers=3)
def cleanup_executor():
    executor.shutdown(wait=True)

atexit.register(cleanup_executor)


class FileValidator:
    # All code/text extensions for text/plain MIME type
    _CODE_EXTENSIONS = [
        '.py', '.pyw', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx',
        '.java', '.kt', '.kts', '.scala', '.c', '.h', '.cpp', '.hpp', '.cc',
        '.cs', '.go', '.rs', '.rb', '.erb', '.php', '.swift', '.dart', '.lua',
        '.pl', '.pm', '.r', '.sql', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.psm1',
        '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
        '.hs', '.lhs', '.ex', '.exs', '.clj', '.cljs', '.cljc', '.ml', '.mli',
        '.fs', '.fsx', '.erl', '.hrl', '.elm', '.v', '.sv', '.vhd', '.vhdl',
        '.asm', '.s', '.dockerfile', '.makefile', '.mk', '.gradle', '.cmake',
        '.tf', '.tfvars', '.proto', '.graphql', '.gql', '.sol', '.zig', '.nim',
        '.cr', '.jl', '.txt', '.text', '.md', '.ini', '.cfg', '.conf', '.env',
        '.yaml', '.yml', '.toml', '.log',
        # Markup files (no magic bytes, detected as octet-stream)
        '.html', '.htm', '.xml', '.svg', '.xhtml',
    ]

    ALLOWED_MIMETYPES = {
        # PDF
        'application/pdf': ['.pdf'],
        # Images
        'image/jpeg': ['.jpg', '.jpeg'],
        'image/png': ['.png'],
        'image/gif': ['.gif'],
        'image/svg+xml': ['.svg'],
        # Word
        'application/msword': ['.doc'],
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
        # Excel
        'application/vnd.ms-excel': ['.xls'],
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
        # PowerPoint
        'application/vnd.ms-powerpoint': ['.ppt'],
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
        # Text/Code - most code files are detected as text/plain
        'text/plain': _CODE_EXTENSIONS,
        'text/x-python': ['.py', '.pyw'],
        'text/javascript': ['.js', '.mjs', '.cjs'],
        'application/javascript': ['.js', '.mjs', '.cjs'],
        'text/typescript': ['.ts', '.tsx'],
        'text/x-java': ['.java'],
        'text/x-c': ['.c', '.h'],
        'text/x-c++': ['.cpp', '.hpp', '.cc'],
        'text/x-csharp': ['.cs'],
        'text/x-go': ['.go'],
        'text/x-rust': ['.rs'],
        'text/x-ruby': ['.rb', '.erb'],
        'text/x-php': ['.php'],
        'application/x-php': ['.php'],
        'text/x-swift': ['.swift'],
        'text/x-sql': ['.sql'],
        'text/x-shellscript': ['.sh', '.bash', '.zsh', '.fish'],
        'application/x-sh': ['.sh', '.bash'],
        'text/css': ['.css'],
        'text/x-scss': ['.scss'],
        'text/x-sass': ['.sass'],
        'text/x-less': ['.less'],
        # Data formats
        'text/csv': ['.csv'],
        'text/markdown': ['.md'],
        'application/json': ['.json'],
        'application/rtf': ['.rtf'],
        'text/rtf': ['.rtf'],
        'text/xml': ['.xml'],
        'application/xml': ['.xml'],
        'text/html': ['.html', '.htm'],
        'application/x-yaml': ['.yaml', '.yml'],
        'text/yaml': ['.yaml', '.yml'],
        'application/toml': ['.toml'],
        'text/x-toml': ['.toml'],
        # Office Open XML formats are ZIP archives
        'application/zip': ['.docx', '.xlsx', '.pptx'],
        # Catch-all for unrecognized but allowed text files
        'application/octet-stream': _CODE_EXTENSIONS,
    }
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    # Blocked extensions - only truly dangerous executables
    # Code files are allowed for analysis (read-only, never executed)
    BLOCKED_EXTENSIONS = {
        '.exe', '.dll', '.scr', '.com', '.pif',  # Windows executables
        '.bat', '.cmd', '.vbs', '.wsf', '.wsh',   # Windows scripts (dangerous)
        '.msi', '.msp',                            # Windows installers
        '.hta', '.mht', '.mhtml',                  # HTML applications
        '.jar',                                     # Java archives (executable)
        '.asp', '.aspx', '.jsp', '.cgi',           # Server-side scripts
    }

    @classmethod
    def validate(cls, file) -> Tuple[bool, str]:
        if not file:
            return False, "No file provided"

        # Check filename for blocked extensions
        filename = file.filename.lower() if file.filename else ''
        _, ext = os.path.splitext(filename)
        if ext in cls.BLOCKED_EXTENSIONS:
            logger.warning(f"Blocked file extension attempted: {ext}")
            return False, f"File type {ext} is not allowed"

        # Check file size
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > cls.MAX_FILE_SIZE:
            return False, f"File size exceeds {cls.MAX_FILE_SIZE // (1024*1024)}MB"

        # Validate using magic bytes (content-based detection)
        mime_type = get_file_type(file.filename, file)

        if mime_type not in cls.ALLOWED_MIMETYPES:
            logger.warning(f"Disallowed MIME type: {mime_type} for file {file.filename}")
            return False, f"File type {mime_type} not allowed"

        # Verify extension matches the detected content type
        allowed_extensions = cls.ALLOWED_MIMETYPES.get(mime_type, [])
        if ext and ext not in allowed_extensions:
            logger.warning(
                f"Extension mismatch: {ext} doesn't match detected type {mime_type} "
                f"(allowed: {allowed_extensions})"
            )
            return False, f"File extension {ext} doesn't match content type"

        return True, "File is valid"


class TextExtractorUtility:
    @staticmethod
    def extract_text(file_path: str) -> Tuple[str, Dict[str, Any]]:
        debug_info = {
            'file_type': None,
            'processing_steps': [],
            'error': None
        }

        try:
            with open(file_path, 'rb') as file:
                mime_type = get_file_type(file_path, file)
            debug_info['file_type'] = mime_type

            ext = os.path.splitext(file_path)[1].lower()

            # PDF
            if mime_type == 'application/pdf':
                return TextExtractorUtility._extract_from_pdf(file_path, debug_info)

            # Images (OCR)
            elif mime_type in ['image/jpeg', 'image/png', 'image/gif']:
                return TextExtractorUtility._extract_from_image(file_path, debug_info)

            # Plain text, markdown
            elif mime_type in ['text/plain', 'text/markdown']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # CSV
            elif mime_type == 'text/csv' or ext == '.csv':
                return TextExtractorUtility._extract_from_csv(file_path, debug_info)

            # JSON
            elif mime_type == 'application/json' or ext == '.json':
                return TextExtractorUtility._extract_from_json(file_path, debug_info)

            # RTF
            elif mime_type in ['application/rtf', 'text/rtf'] or ext == '.rtf':
                return TextExtractorUtility._extract_from_rtf(file_path, debug_info)

            # Office Open XML formats (detected as ZIP or specific MIME)
            elif mime_type == 'application/zip' or mime_type.startswith('application/vnd.openxmlformats'):
                if ext == '.docx':
                    return TextExtractorUtility._extract_from_docx(file_path, debug_info)
                elif ext == '.xlsx':
                    return TextExtractorUtility._extract_from_xlsx(file_path, debug_info)
                elif ext == '.pptx':
                    return TextExtractorUtility._extract_from_pptx(file_path, debug_info)
                else:
                    debug_info['error'] = f"Unsupported ZIP-based file type: {ext}"
                    return "", debug_info

            # Excel (specific MIME)
            elif mime_type == 'application/vnd.ms-excel' or ext in ['.xls', '.xlsx']:
                if ext == '.xlsx':
                    return TextExtractorUtility._extract_from_xlsx(file_path, debug_info)
                else:
                    debug_info['error'] = f"Old Excel format (.xls) not supported - please convert to .xlsx"
                    return "", debug_info

            # PowerPoint (specific MIME)
            elif mime_type == 'application/vnd.ms-powerpoint' or ext in ['.ppt', '.pptx']:
                if ext == '.pptx':
                    return TextExtractorUtility._extract_from_pptx(file_path, debug_info)
                else:
                    debug_info['error'] = f"Old PowerPoint format (.ppt) not supported - please convert to .pptx"
                    return "", debug_info

            # Word (specific MIME for old .doc format)
            elif mime_type == 'application/msword' or ext == '.doc':
                debug_info['error'] = f"Old Word format (.doc) not supported - please convert to .docx"
                return "", debug_info

            # XML/HTML/SVG - read as text
            elif mime_type in ['text/xml', 'application/xml', 'text/html', 'image/svg+xml'] or ext in ['.xml', '.html', '.htm', '.svg']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # YAML/TOML - read as text
            elif mime_type in ['application/x-yaml', 'text/yaml', 'application/toml', 'text/x-toml'] or ext in ['.yaml', '.yml', '.toml']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # Code files - all text-based, check by extension or MIME
            elif (mime_type.startswith('text/') or
                  mime_type in ['application/javascript', 'application/x-php', 'application/x-sh', 'application/octet-stream'] or
                  ext in FileValidator._CODE_EXTENSIONS):
                debug_info['processing_steps'].append(f'Processing as code/text file: {ext}')
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            else:
                debug_info['error'] = f"Unsupported mime type for text extraction: {mime_type}"
                return "", debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            logger.exception(f"Text extraction failed for {file_path}")
            return "", debug_info

    @staticmethod
    def _extract_from_pdf(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        try:
            reader = PdfReader(file_path)
            debug_info['processing_steps'].append('Opening PDF file')
            text = ""

            debug_info['processing_steps'].append(f'PDF pages found: {len(reader.pages)}')
            for i, page in enumerate(reader.pages):
                debug_info['processing_steps'].append(f'Processing page {i+1}')
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                except Exception as e:
                    debug_info['processing_steps'].append(f'Error on page {i+1}: {str(e)}')

            # Attempt OCR if no text extracted
            if not text.strip():
                debug_info['processing_steps'].append('No text extracted, attempting OCR')
                try:
                    for i, page in enumerate(reader.pages):
                        if page.images:
                            for image in page.images:
                                try:
                                    img_data = image.data
                                    img = Image.open(io.BytesIO(img_data))
                                    page_text = pytesseract.image_to_string(img)
                                    if page_text:
                                        text += page_text + "\n"
                                except Exception as img_e:
                                    debug_info['processing_steps'].append(f'OCR error on page {i+1}: {str(img_e)}')
                except Exception as e:
                    debug_info['processing_steps'].append(f'OCR processing error: {str(e)}')

            final_text = text.strip()
            if not final_text:
                debug_info['processing_steps'].append('No text could be extracted from the PDF')
            return final_text, debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"PDF extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_image(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        if not OCR_AVAILABLE:
            debug_info['error'] = 'OCR not available on server'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening image file')
            image = Image.open(file_path)
            ocr_config = '--oem 3 --psm 6'

            # Build language candidates (primary + english fallback)
            lang_candidates = []
            if OCR_LANG:
                lang_candidates.append(OCR_LANG)
            if 'eng' not in (OCR_LANG or ''):
                lang_candidates.append('eng')

            def try_tesseract(img, lang_label):
                try:
                    txt = pytesseract.image_to_string(img, lang=lang_label, config=ocr_config)
                    return (txt or '').strip()
                except pytesseract.TesseractError as e:
                    debug_info['processing_steps'].append(f'Tesseract error ({lang_label}): {e}')
                    return ""

            # Prepare preprocessed variants once
            gray = ImageOps.grayscale(image)
            boosted = ImageOps.autocontrast(gray).filter(ImageFilter.SHARPEN)
            scale_factor = 1.5
            upscaled = boosted.resize(
                (int(boosted.width * scale_factor), int(boosted.height * scale_factor)),
                Image.LANCZOS
            )
            thresholded = upscaled.point(lambda p: 255 if p > 150 else 0)

            for lang_label in lang_candidates:
                for variant_name, variant_img in [
                    ('native', image),
                    ('preprocessed', boosted),
                    ('upscale+threshold', thresholded),
                ]:
                    debug_info['processing_steps'].append(f'Performing OCR ({variant_name}, lang={lang_label})')
                    text = try_tesseract(variant_img, lang_label)
                    if text:
                        debug_info['engine'] = 'tesseract'
                        debug_info['lang_used'] = lang_label
                        return text, debug_info

            # EasyOCR fallback
            reader = get_easyocr_reader()
            if reader:
                debug_info['processing_steps'].append('Performing OCR (easyocr fallback)')
                try:
                    results = reader.readtext(file_path, detail=0, paragraph=True)
                    text = "\n".join(r for r in results if isinstance(r, str)).strip()
                    if text:
                        debug_info['engine'] = 'easyocr'
                        debug_info['lang_used'] = "+".join(OCR_LANG_LIST)
                        return text, debug_info
                except Exception as e:
                    debug_info['processing_steps'].append(f'EasyOCR error: {e}')

            debug_info['error'] = 'OCR returned no text'
            return "", debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            return "", debug_info

    @staticmethod
    def _extract_from_text(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        try:
            debug_info['processing_steps'].append('Reading text file')
            with open(file_path, 'r', encoding='utf-8') as file:
                text = file.read()
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            return "", debug_info

    @staticmethod
    def _extract_from_docx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from DOCX files using python-docx."""
        if not DOCX_AVAILABLE:
            debug_info['error'] = 'python-docx not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening DOCX file')
            doc = DocxDocument(file_path)

            paragraphs = []
            for para in doc.paragraphs:
                if para.text.strip():
                    paragraphs.append(para.text)

            # Also extract text from tables
            for table in doc.tables:
                for row in table.rows:
                    row_text = []
                    for cell in row.cells:
                        if cell.text.strip():
                            row_text.append(cell.text.strip())
                    if row_text:
                        paragraphs.append(' | '.join(row_text))

            text = '\n'.join(paragraphs)
            debug_info['processing_steps'].append(f'Extracted {len(paragraphs)} paragraphs')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"DOCX extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_xlsx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from Excel files using openpyxl."""
        if not XLSX_AVAILABLE:
            debug_info['error'] = 'openpyxl not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening Excel file')
            wb = load_workbook(file_path, read_only=True, data_only=True)

            all_text = []
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                all_text.append(f"=== Sheet: {sheet_name} ===")

                for row in sheet.iter_rows(values_only=True):
                    row_values = [str(cell) if cell is not None else '' for cell in row]
                    # Skip empty rows
                    if any(v.strip() for v in row_values):
                        all_text.append(' | '.join(row_values))

            wb.close()
            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(wb.sheetnames)} sheets')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"Excel extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_pptx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from PowerPoint files using python-pptx."""
        if not PPTX_AVAILABLE:
            debug_info['error'] = 'python-pptx not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening PowerPoint file')
            prs = Presentation(file_path)

            all_text = []
            for slide_num, slide in enumerate(prs.slides, 1):
                slide_text = [f"=== Slide {slide_num} ==="]
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_text.append(shape.text)
                    # Extract text from tables
                    if shape.has_table:
                        for row in shape.table.rows:
                            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                            if row_text:
                                slide_text.append(' | '.join(row_text))
                if len(slide_text) > 1:  # More than just the header
                    all_text.extend(slide_text)

            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(prs.slides)} slides')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"PowerPoint extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_csv(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from CSV files."""
        try:
            debug_info['processing_steps'].append('Reading CSV file')
            import csv

            all_text = []
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                for row in reader:
                    if any(cell.strip() for cell in row):
                        all_text.append(' | '.join(row))

            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(all_text)} rows')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"CSV extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_json(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from JSON files."""
        try:
            debug_info['processing_steps'].append('Reading JSON file')

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Pretty print the JSON for readability
            text = json.dumps(data, indent=2, ensure_ascii=False)
            debug_info['processing_steps'].append('Parsed JSON successfully')
            return text, debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"JSON extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_rtf(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from RTF files using striprtf."""
        if not RTF_AVAILABLE:
            debug_info['error'] = 'striprtf not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Reading RTF file')

            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                rtf_content = f.read()

            text = rtf_to_text(rtf_content)
            debug_info['processing_steps'].append('Extracted text from RTF')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"RTF extraction error: {str(e)}")
            return "", debug_info


def get_file_type(filename: str, file) -> str:
    """
    Determines the MIME type of a file using both extension and content inspection.
    This helps prevent mime-type spoofing attacks.
    Returns content-based MIME type for security.
    """
    # First check the extension
    ext_mime_type, _ = mimetypes.guess_type(filename)

    # Then check the actual content
    header = file.read(2048)
    file.seek(0)

    # Map of magic bytes to mime types (ordered by specificity)
    content_signatures = [
        # More specific signatures first
        (b'\x89PNG\r\n\x1a\n', 'image/png'),  # Complete PNG signature
        (b'\xFF\xD8\xFF\xE0', 'image/jpeg'),  # JPEG with JFIF
        (b'\xFF\xD8\xFF\xE1', 'image/jpeg'),  # JPEG with EXIF
        (b'\xFF\xD8\xFF\xDB', 'image/jpeg'),  # JPEG with DQT
        (b'\xFF\xD8\xFF', 'image/jpeg'),  # Generic JPEG
        (b'%PDF-', 'application/pdf'),  # PDF with version
        (b'%PDF', 'application/pdf'),  # PDF fallback
        (b'GIF87a', 'image/gif'),
        (b'GIF89a', 'image/gif'),
        (b'PK\x03\x04', 'application/zip'),  # ZIP files including docx/xlsx
        (b'RIFF', 'application/octet-stream'),  # RIFF container (could be WebP, AVI, etc.)
    ]

    # Determine content-based mime type
    content_mime_type = None
    for signature, mime in content_signatures:
        if header.startswith(signature):
            content_mime_type = mime
            break

    # For ZIP-based formats (docx, xlsx, etc.), check internal structure
    if content_mime_type == 'application/zip' and ext_mime_type:
        office_extensions = {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        }
        if ext_mime_type in office_extensions:
            content_mime_type = ext_mime_type

    # For text files, require extension match and validate content is safe
    if not content_mime_type:
        if ext_mime_type == 'text/plain':
            # Verify it's actually text by checking for non-printable chars
            try:
                decoded = header.decode('utf-8')
                # Check for dangerous patterns in "text" files
                dangerous_patterns = [b'<script', b'<?php', b'<%', b'#!/']
                if not any(pattern in header.lower() for pattern in dangerous_patterns):
                    content_mime_type = 'text/plain'
            except UnicodeDecodeError:
                pass

    # If we still couldn't determine, mark as unknown
    if not content_mime_type:
        content_mime_type = 'application/octet-stream'

    # Log mismatches for security monitoring
    if ext_mime_type and content_mime_type and ext_mime_type != content_mime_type:
        if content_mime_type != 'application/octet-stream':
            logger.warning(
                f"File type mismatch detected: extension={ext_mime_type}, content={content_mime_type}, "
                f"filename={filename}"
            )

    # Always trust content-based detection over extension
    return content_mime_type


def generate_unique_filename(original_filename: str, user_id: str) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_hash = hashlib.md5(f"{user_id}_{str(datetime.now())}".encode()).hexdigest()[:8]
    _, ext = os.path.splitext(original_filename)
    secure_name = secure_filename(original_filename)
    base_name = os.path.splitext(secure_name)[0]
    return f"{user_id}_{base_name}_{timestamp}_{file_hash}{ext}"


def extract_text_concurrently(file_path: str) -> Tuple[str, Dict[str, Any]]:
    text, debug_info = TextExtractorUtility.extract_text(file_path)
    if debug_info.get('error'):
        logger.error(f"Text extraction error: {debug_info['error']}")
    return text, debug_info


# Weather routes moved to app/blueprints/weather_routes.py
# - /weather-test
# - /weather

# Upload routes moved to app/blueprints/upload_routes.py
# - /upload
# - /url-metadata

# Media routes moved to app/blueprints/media_routes.py
# - /generate-image
# - /check-generation-status
# - /tts/groq
# - /speech-to-text/groq

# Chat management routes moved to app/blueprints/chat_routes.py
# - /chat/new
# - /chat/save/<session_id>
# - /chat/archive
# - /chat/pin


# ----------------------------------
# Custom exceptions
# ----------------------------------
class SearchException(Exception):
    """Custom exception for search-related errors."""
    pass


class SearchResult(BaseModel):
    """
    Represents a single search result.
    Provides convenient validation and serialization via Pydantic.
    """
    title: str
    link: HttpUrl
    snippet: str
    source_domain: str
    website_name: str
    favicon: Optional[str] = None
    relevance_score: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this SearchResult to a dict, ensuring the link is str
        and timestamp is ISO-formatted.
        """
        return {
            'title': self.title,
            'link': str(self.link),
            'snippet': self.snippet,
            'source_domain': self.source_domain,
            'website_name': self.website_name,
            'favicon': self.favicon,
            'relevance_score': self.relevance_score,
            'timestamp': self.timestamp.isoformat(),
        }


# ----------------------------------
# Search backends enum
# ----------------------------------
class SearchBackend(str, Enum):
    BRAVE = "brave"


def resolve_search_backend(raw_backend: Optional[str]) -> SearchBackend:
    """
    Map user or config provided backend strings to a supported enum.
    Defaults to Brave when the value is missing or invalid.
    """
    if not raw_backend:
        return SearchBackend.BRAVE
    raw_value = raw_backend.lower()
    if raw_value == SearchBackend.BRAVE.value:
        return SearchBackend.BRAVE
    return SearchBackend.BRAVE

# ----------------------------------------------------
# Search Client supporting multiple backends
# ----------------------------------------------------
class SearchClient:
    """
    A rate-limited, caching search client backed by the Brave Search API.
    """
    CALLS_PER_MINUTE = 30
    BACKOFF_FACTOR = 1.5

    _instance = None
    _metrics = {
        'counter': None,
        'latency': None,
        'errors': None
    }

    def __new__(cls, *args, **kwargs):
        """
        Ensure this is a singleton by keeping a single class-level _instance.
        """
        if cls._instance is None:
            cls._instance = super(SearchClient, cls).__new__(cls)
            cls._initialize_metrics()
        return cls._instance

    @classmethod
    def _initialize_metrics(cls):
        """
        Initialize Prometheus metrics if they are not already set.
        """
        if not cls._metrics['counter']:
            try:
                cls._metrics['counter'] = Counter('search_requests_total', 'Total searches')
                cls._metrics['latency'] = Histogram('search_latency_seconds', 'Search latency')
                cls._metrics['errors'] = Counter('search_errors_total', 'Total search errors')
            except ValueError:
                # If metrics are already registered in a long-running process, skip re-registering.
                pass

    def __init__(
        self, 
        timeout: int = 10, 
        max_retries: int = 3,
        backend: Optional[SearchBackend] = None
    ):
        """
        Initialize the SearchClient with a chosen backend and other settings.
        """
        # Only initialize once due to singleton pattern
        if not hasattr(self, 'initialized'):
            self.timeout = timeout
            self.max_retries = max_retries
            self.rate_limit_delay = 60 / self.CALLS_PER_MINUTE
            self.last_request_time = 0
            config_backend = resolve_search_backend(getattr(Config, 'SEARCH_DEFAULT_BACKEND', None))
            provided_backend = resolve_search_backend(backend.value if isinstance(backend, SearchBackend) else backend) if backend is not None else None
            self.backend = provided_backend or config_backend
            self.conversation_history: List[Dict[str, Any]] = []
            
            # Cache up to 1000 queries for 1 hour
            self.cache = TTLCache(maxsize=1000, ttl=3600)
            self.trusted_domains = ['bbc.com', 'nytimes.com', 'wikipedia.org']
            self.blocked_domains = [
                d.strip().lower() for d in getattr(Config, 'SEARCH_BLOCKED_DOMAINS', []) if isinstance(d, str) and d.strip()
            ]

            # Vectorizer for relevance scoring
            self.vectorizer = TfidfVectorizer(stop_words='english')

            self.initialized = True

    # ---------- Metrics Helpers ----------
    def _increment_counter(self):
        if self._metrics['counter']:
            self._metrics['counter'].inc()

    def _record_latency(self):
        if self._metrics['latency']:
            return self._metrics['latency'].time()
        return nullcontext()

    def _increment_error(self):
        if self._metrics['errors']:
            self._metrics['errors'].inc()

    # ---------- Text Helpers ----------
    @staticmethod
    def _clean_text(text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s.,!?-]', '', text)
        return text.strip()

    def _extract_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            return domain.replace('www.', '', 1)
        except Exception as e:
            logger.error(f"Domain extraction failed for URL {url}: {e}")
            return ""

    def _is_blocked_domain(self, domain: str) -> bool:
        """Return True if the domain (or its parent) is blocked."""
        if not domain:
            return False
        domain = domain.lower()
        return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in self.blocked_domains)

    @staticmethod
    def _is_latin_heavy(text: str, threshold: float = 0.6) -> bool:
        """Heuristic to skip mostly non-Latin text to reduce irrelevant locale results."""
        if not text:
            return True
        latin = sum(1 for ch in text if ord(ch) < 128 and ch.isalpha())
        total = sum(1 for ch in text if ch.isalpha())
        if total == 0:
            return True
        return (latin / total) >= threshold

    def _extract_website_name(self, url: str) -> str:
        try:
            domain_info = tldextract.extract(url)
            return domain_info.domain.capitalize() or "Unknown"
        except Exception as e:
            logger.error(f"Website name extraction failed for URL {url}: {e}")
            return "Unknown"

    def _generate_summary(self, text: str, max_length: int = 200) -> str:
        try:
            if not text:
                return "No snippet available."
            return text[:max_length]
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return (text or "")[:max_length]

    def _calculate_relevance_score(self, result: Dict[str, str], query: str) -> float:
        try:
            content = f"{result.get('title', '')} {result.get('body', '')}".strip()
            vectors = self.vectorizer.fit_transform([query, content])
            if vectors.shape[1] == 0:
                return 0.0
            tfidf_score = cosine_similarity(vectors[0], vectors[1])[0][0]

            # Bonus for trusted domains
            domain_score = 0.1 if self._extract_domain(result.get('href', '')) in self.trusted_domains else 0
            return min(tfidf_score + domain_score, 1.0)
        except Exception as e:
            logger.error(f"Relevance calculation failed: {e}")
            return 0.0

    def _validate_result(self, result: Dict[str, str]) -> bool:
        try:
            # Require title and href; body/snippet are optional
            if not result.get('title') or not result.get('href'):
                return False

            parsed = urlparse(result['href'])
            if not parsed.scheme or not parsed.netloc:
                return False
            domain = parsed.netloc.lower().replace('www.', '', 1)
            if self._is_blocked_domain(domain):
                return False
            return len(result['title']) >= 3 and self._is_latin_heavy(result.get('title', '') + " " + result.get('body', ''))
        except Exception:
            return False

    def _cache_key(self, query: str, max_results: int, min_relevance: float) -> str:
        backend_value = self.backend.value if isinstance(self.backend, SearchBackend) else str(self.backend)
        return f"{query}:{backend_value}:{self.max_retries}:{self.timeout}:{max_results}:{min_relevance}"

    def _get_backoff_time(self, attempt: int) -> float:
        return min(300, (self.BACKOFF_FACTOR ** attempt) + random.uniform(0, 1))

    # ------------------------------------------------------------------
    # Brave Search
    # ------------------------------------------------------------------
    def _brave_fetch_results(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """
        Perform Brave Search using the official API.
        """
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '')
        endpoint = getattr(Config, 'BRAVE_SEARCH_ENDPOINT', 'https://api.search.brave.com/res/v1/web/search')
        if not api_key:
            logger.warning("Brave Search API key not configured; skipping search.")
            return []

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key
        }
        params = {
            "q": query,
            "count": max_results,
            "country": "US",
            "search_lang": "en",
            "safesearch": "moderate"
        }

        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            items = data.get("web", {}).get("results", [])
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "href": item.get("url", ""),
                    "body": html.unescape(item.get("description") or ""),
                })
            return results
        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Master Async Method (delegates to each backend)
    # ------------------------------------------------------------------
    async def search_async(
        self,
        query: str,
        max_results: int = 7,
        min_relevance_score: float = 0.0
    ) -> List[SearchResult]:
        """
        Asynchronous search method that returns up to `max_results` results
        with a relevance score above `min_relevance_score`, using the chosen backend.
        """
        if not query or not isinstance(query, str):
            raise ValueError("Invalid query provided")

        cache_key = self._cache_key(query, max_results, min_relevance_score)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        # Prepare empty results
        final_results: List[SearchResult] = []
        seen_links = set()

        for attempt in range(self.max_retries):
            try:
                with self._record_latency():
                    self._increment_counter()

                    raw_results: List[Dict[str, Any]] = await asyncio.to_thread(
                        self._brave_fetch_results,
                        query,
                        max_results
                    )

                    for r in raw_results:
                        # Validate minimal fields
                        if not self._validate_result(r):
                            continue

                        # Calculate relevance
                        relevance_score = self._calculate_relevance_score(r, query)
                        if relevance_score < min_relevance_score:
                            continue

                        domain = self._extract_domain(r['href'])
                        body = r.get('body') or r.get('snippet') or r.get('description') or r.get('content') or ''
                        result_obj = SearchResult(
                            title=self._clean_text(r['title']),
                            link=r['href'],
                            snippet=self._generate_summary(body),
                            source_domain=domain,
                            website_name=self._extract_website_name(r['href']),
                            favicon=f"https://{domain}/favicon.ico" if domain else None,
                            relevance_score=relevance_score,
                        )
                        if result_obj.link in seen_links:
                            continue
                        seen_links.add(result_obj.link)
                        final_results.append(result_obj)

                    if final_results:
                        # If we found any valid results, break out of retry loop
                        break

            except Exception as e:
                self._increment_error()
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise SearchException(f"Search failed after {self.max_retries} attempts")
                # Exponential backoff
                await asyncio.sleep(self._get_backoff_time(attempt))

        # Sort by relevance score descending
        final_results.sort(key=lambda x: x.relevance_score, reverse=True)

        trimmed_results = final_results[:max_results]
        # Cache and return the top `max_results`
        self.cache[cache_key] = trimmed_results
        return trimmed_results

    # ------------------------------------------------------------------
    # Synchronous wrapper
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 7,
        min_relevance_score: float = 0.0
    ) -> List[SearchResult]:
        """
        Synchronous wrapper around the async search.
        """
        return asyncio.run(self.search_async(query, max_results, min_relevance_score))

    def explain_results(self, results: List[SearchResult]) -> List[str]:
        """
        Provide a textual explanation of the sources and relevance scores.
        """
        return [
            (
                f"Result from {result.source_domain} (score: {result.relevance_score:.2f})"
                + (" [trusted]" if result.source_domain in self.trusted_domains else "")
            )
            for result in results
        ]

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """
        Return any stored conversation or query/response history.
        """
        return self.conversation_history


# ----------------------------------------------------
# Higher-level helper that uses the SearchClient
# ----------------------------------------------------
def perform_web_search(
    user_id: str, 
    user_prompt: str, 
    backend: SearchBackend = SearchBackend.BRAVE
) -> List[SearchResult]:
    """
    Perform a web search on behalf of a given user, handling any exceptions.
    Returns a list of SearchResult objects.
    """
    # Create the search client with the desired backend
    resolved_backend = resolve_search_backend(backend.value if isinstance(backend, SearchBackend) else backend)
    search_client = SearchClient(backend=resolved_backend)

    try:
        search_results = search_client.search(user_prompt)
        logger.info(f"Search returned {len(search_results)} results for user {user_id}")
        return search_results
    except SearchException as e:
        logger.error(f"Search failed for user {user_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during search for user {user_id}: {e}")
        return []


def _generated_image_dir(user_id: str) -> str:
    sanitized_user_id = re.sub(r'[^\w\-]', '_', user_id or '')
    output_dir = os.path.join(Config.UPLOAD_FOLDER, sanitized_user_id, "generated_images")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def save_image_bytes_to_user_dir(
    image_bytes: bytes,
    user_id: str,
    prefix: str,
    seed: Optional[int] = None,
    extension: str = "png"
) -> Optional[str]:
    try:
        output_dir = _generated_image_dir(user_id)
        identifier = seed if seed is not None else uuid.uuid4().hex
        filename = f"{prefix}_{identifier}.{extension}"
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "wb") as handle:
            handle.write(image_bytes)
        with Image.open(file_path) as img:
            img.verify()
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to persist generated image bytes: {e}")
        try:
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        return None


def save_remote_image(image_url: str, user_id: str, prefix: str, seed: Optional[int] = None) -> Optional[str]:
    if not image_url:
        return None
    response = None
    file_path = None
    try:
        response = requests.get(image_url, timeout=30, stream=True)
        response.raise_for_status()
        output_dir = _generated_image_dir(user_id)
        identifier = seed if seed is not None else uuid.uuid4().hex
        ext = "png"
        content_type = response.headers.get('Content-Type', '')
        if "jpeg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"
        filename = f"{prefix}_{identifier}.{ext}"
        file_path = os.path.join(output_dir, filename)
        max_bytes = 10 * 1024 * 1024
        total = 0
        with open(file_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    os.remove(file_path)
                    logger.warning("Remote image exceeded size cap; download aborted.")
                    return None
                handle.write(chunk)
        with Image.open(file_path) as img:
            img.verify()
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to download remote image {image_url}: {e}")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return None


def _save_data_url_image(data_url: str, user_id: str, prefix: str, seed: Optional[int] = None) -> Optional[str]:
    if not data_url.startswith("data:"):
        return None
    try:
        header, encoded = data_url.split(",", 1)
    except ValueError:
        return None
    mime_match = re.match(r'data:(.*?);base64', header)
    mime_type = mime_match.group(1) if mime_match else "image/png"
    try:
        image_bytes = base64.b64decode(encoded)
    except Exception as e:
        logger.warning(f"Failed to decode image data URL: {e}")
        return None
    extension = mimetypes.guess_extension(mime_type) or ".png"
    extension = extension.lstrip(".")
    return save_image_bytes_to_user_dir(image_bytes, user_id, prefix, seed, extension=extension)


def pollinations_url(prompt: str, seed: int) -> str:
    suffix = "without watermark"
    full_prompt = f"{prompt}, {suffix}" if prompt else suffix
    encoded_prompt = requests.utils.quote(full_prompt)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width=1024&height=1024"


def save_pollinations_image(prompt: str, seed: int, user_id: str) -> Optional[str]:
    """Download and save a Pollinations image to the uploads folder; return relative path on success."""
    url = pollinations_url(prompt, seed)
    try:
        output_dir = _generated_image_dir(user_id)
        filename = f"pollinations_{seed}.png"
        file_path = os.path.join(output_dir, filename)
        if os.path.exists(file_path):
            return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
        response = requests.get(url, timeout=20, stream=True)
        response.raise_for_status()
        max_bytes = 10 * 1024 * 1024  # 10 MB cap
        total = 0
        with open(file_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    os.remove(file_path)
                    logger.warning("Pollinations image exceeded size cap; aborted download.")
                    return None
                handle.write(chunk)
        try:
            with Image.open(file_path) as img:
                img.verify()
        except Exception:
            os.remove(file_path)
            logger.warning("Pollinations download failed image verification; file removed.")
            return None
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to save Pollinations image locally: {e}")
        return None


def _infer_aspect_ratio(size_value: str) -> Optional[str]:
    if not size_value:
        return None
    size_value = size_value.strip().lower()
    if ":" in size_value:
        return size_value
    mapping = {
        "1024x1024": "1:1",
        "832x1248": "2:3",
        "1248x832": "3:2",
        "864x1184": "3:4",
        "1184x864": "4:3",
        "896x1152": "4:5",
        "1152x896": "5:4",
        "768x1344": "9:16",
        "1344x768": "16:9",
        "1536x672": "21:9",
    }
    return mapping.get(size_value)


def generate_image_with_openrouter(prompt: str, user_id: str, seed: int) -> Optional[Dict[str, Any]]:
    api_key = getattr(Config, 'OPENROUTER_API_KEY', None)
    if not api_key:
        logger.error("OpenRouter API key is not configured.")
        return None
    url = getattr(Config, 'OPENROUTER_IMAGE_URL', 'https://openrouter.ai/api/v1/chat/completions')
    model = getattr(Config, 'OPENROUTER_IMAGE_MODEL', 'google/gemini-2.5-flash-image')
    model_label = getattr(Config, 'OPENROUTER_IMAGE_MODEL_LABEL', model)
    size = getattr(Config, 'OPENROUTER_IMAGE_SIZE', '1024x1024')

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if getattr(Config, 'OPENROUTER_SITE_URL', None):
        headers["HTTP-Referer"] = Config.OPENROUTER_SITE_URL
    if getattr(Config, 'OPENROUTER_APP_NAME', None):
        headers["X-Title"] = Config.OPENROUTER_APP_NAME

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "modalities": ["image", "text"]
    }
    aspect_ratio = _infer_aspect_ratio(size)
    if aspect_ratio:
        payload["image_config"] = {"aspect_ratio": aspect_ratio}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            logger.error(f"OpenRouter image generation returned invalid JSON: {response.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"OpenRouter image generation failed: {e}")
        return None

    choices = data.get('choices') if isinstance(data, dict) else None
    if not choices:
        logger.warning("OpenRouter image API returned no choices.")
        return None

    message = choices[0].get("message", {})
    images = message.get("images") or []
    image_url = None
    local_path = None

    for entry in images:
        candidate = entry.get("image_url", {}).get("url")
        if not candidate:
            continue
        image_url = candidate
        if candidate.startswith("data:"):
            local_path = _save_data_url_image(candidate, user_id, prefix="openrouter", seed=seed)
        else:
            local_path = save_remote_image(candidate, user_id, prefix="openrouter", seed=seed)
        if local_path:
            break

    if not image_url:
        logger.warning("OpenRouter image payload missing usable URL.")
        return None

    return {
        'image_url': image_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'openrouter',
        'model': model,
        'model_label': model_label
    }


def generate_pollinations_image(prompt: str, user_id: str, seed: int) -> Optional[Dict[str, Any]]:
    image_url = pollinations_url(prompt, seed)
    local_path = save_pollinations_image(prompt, seed, user_id)
    return {
        'image_url': image_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'pollinations',
        'model': 'pollinations',
        'model_label': 'Pollinations'
    }


def generate_image_result(user_id: str, prompt: str) -> Optional[Dict[str, Any]]:
    seed = abs(hash(f"{user_id}:{prompt}")) % (2**31)
    result = generate_image_with_openrouter(prompt, user_id, seed)
    if result:
        return result
    logger.info("Falling back to Pollinations for image generation.")
    return generate_pollinations_image(prompt, user_id, seed)


def delete_local_attachments(messages: List[Dict[str, Any]]) -> None:
    """Remove any local files referenced in message attachments."""
    if not messages:
        return

    base_upload_path = os.path.abspath(Config.UPLOAD_FOLDER)
    for message in messages:
        for field_name in ('attachments', 'user_attachments'):
            attachments = message.get(field_name, [])
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                local_path = attachment.get('local_path')
                if not local_path:
                    continue
                try:
                    if os.path.isabs(local_path):
                        abs_path = os.path.abspath(local_path)
                    else:
                        abs_path = os.path.abspath(os.path.join(Config.UPLOAD_FOLDER, local_path))

                    if not abs_path.startswith(base_upload_path):
                        logger.warning(f"Skipping attachment outside upload dir: {abs_path}")
                        continue

                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                        logger.info(f"Deleted attachment file {abs_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete attachment {local_path}: {e}")


# ------------------ Helper Functions for Chat Endpoint ------------------ #


# =============================================================================
# ARTIFACT CONTEXT INJECTION (Claude-inspired)
# =============================================================================
# When user asks to fix/update an artifact, we inject the current artifact code
# into the prompt so the AI can see exactly what to modify.

ARTIFACT_UPDATE_PATTERNS = [
    # Direct action on artifact/code
    r"\b(?:fix|update|correct|change|modify|edit|revise|improve|refactor)\s+(?:the\s+)?(?:artifact|code|bug|error|issue|css|style|layout|design|app)\b",
    # "fix this/that/it"
    r"\b(?:fix|correct|resolve|update)\s+(?:this|that|it|the)\b",
    # "update the artifact/code"
    r"\bupdat\w*\s+(?:the\s+)?(?:artifact|code|version|my)\b",
    # Polite requests
    r"\b(?:can you|please|could you)\s+(?:fix|update|correct|change|modify)\b",
    # Technical fixes
    r"\bfix\s+(?:the\s+)?(?:syntax|babel|runtime|compile|css|style|render)\s*(?:error|issue)?\b",
    # "make it work/better"
    r"\bmake\s+(?:it|the\s+\w+)\s+(?:work|better|responsive|look)\b",
    # Keywords that imply artifact modification
    r"\b(?:still\s+(?:not|same|broken)|doesn.t\s+work|not\s+working|same\s+version)\b",
    # "add/remove X to/from the code/app"
    r"\b(?:add|remove|delete)\s+.{0,20}(?:to|from|in)\s+(?:the\s+)?(?:code|app|artifact|component)\b",
    # References to updating version
    r"\b(?:new|next|updated|fixed)\s+version\b",
]


def detect_artifact_update_intent(user_prompt: str) -> bool:
    """
    Detect if user is asking to fix/update an artifact.
    Claude-style: automatically detect intent to modify existing code.
    Uses broad patterns to catch typos and various phrasings.
    """
    if not user_prompt:
        return False
    prompt_lower = user_prompt.lower().strip()

    # Quick keyword check first (fast path)
    update_keywords = {'fix', 'update', 'change', 'modify', 'edit', 'correct', 'improve', 'refactor', 'revise'}
    words = set(prompt_lower.split())
    if not words & update_keywords:
        # Also check for typos of common words
        for word in words:
            # Simple typo detection: "uptade", "updat", "fixx", etc.
            for kw in update_keywords:
                if len(word) >= 3 and len(kw) >= 3:
                    # Check if word is close to keyword (1-2 char difference)
                    if word[:3] == kw[:3] or word[-3:] == kw[-3:]:
                        break
            else:
                continue
            break
        else:
            return False

    for pattern in ARTIFACT_UPDATE_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True

    # Fallback: if any update keyword is present and message is short (likely a command)
    if len(prompt_lower.split()) <= 10 and words & update_keywords:
        return True

    return False


def load_current_artifact_context(user_id: str, session_id: str) -> Optional[str]:
    """
    Load the current active artifact's code and format it as context.
    Claude-style: inject current artifact state so AI knows what to modify.

    Returns formatted context string or None if no active artifact.
    """
    try:
        # Get active artifact for this session
        artifacts_data = artifact_store.list_artifacts(user_id, session_id)
        artifacts = artifacts_data.get('artifacts', [])

        # Find the active artifact
        active_artifact = None
        for art in artifacts:
            if art.get('isActive'):
                active_artifact = art
                break

        # If no explicitly active, use most recent
        if not active_artifact and artifacts:
            active_artifact = artifacts[0]

        if not active_artifact:
            return None

        artifact_id = active_artifact.get('artifactId')
        if not artifact_id:
            return None

        # Load full artifact with files
        full_artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not full_artifact:
            return None

        active_version = full_artifact.get('activeVersion')
        if not active_version:
            return None

        files = active_version.get('files', [])
        if not files:
            return None

        # Format as context for the AI
        context_parts = [
            "═══════════════════════════════════════════════════════════════",
            "CURRENT ARTIFACT (for reference when fixing/updating)",
            "═══════════════════════════════════════════════════════════════",
            f"Title: {full_artifact.get('title', 'Untitled')}",
            f"Type: {full_artifact.get('type', 'project')}",
            f"Files: {len(files)}",
            "───────────────────────────────────────────────────────────────",
        ]

        for file in files:
            path = file.get('path', 'unknown')
            language = file.get('language', 'text')
            content = file.get('content', '')

            context_parts.append(f"\n📄 {path} ({language}):")
            context_parts.append(f"```{language}")
            context_parts.append(content)
            context_parts.append("```")

        context_parts.append("\n───────────────────────────────────────────────────────────────")
        context_parts.append("When updating this artifact, regenerate ALL files with changes applied.")
        context_parts.append("Output a complete ```artifact block with the fixed/updated code.")
        context_parts.append("═══════════════════════════════════════════════════════════════\n")

        return "\n".join(context_parts)

    except Exception as e:
        logger.warning(f"Failed to load artifact context: {e}")
        return None


def should_auto_enable_search(user_prompt: str) -> Tuple[bool, str]:
    """
    Smart detector to determine if web search would benefit the response.
    Returns (should_search, confidence) where confidence is 'high', 'medium', or 'low'.

    Uses pattern matching to detect:
    - Explicit search requests
    - Time-sensitive queries
    - Real-time data topics (weather, stocks, news, sports)
    - Questions implying current information needed
    """
    if not user_prompt:
        return False, 'low'

    normalized = user_prompt.lower()

    # === HIGH CONFIDENCE: Definitely needs search ===

    # Explicit search requests
    explicit_search = [
        r"\bsearch\b", r"\blook\s*up\b", r"\bfind\s+online\b",
        r"\bweb\s*search\b", r"\bgoogle\b", r"\bbing\b",
        r"\bsearch\s+(for|the|about)\b",
    ]

    # Time-sensitive keywords that strongly indicate need for current info
    time_sensitive = [
        r"\btoday\b", r"\btonight\b", r"\bright\s+now\b",
        r"\bcurrently\b", r"\bcurrent(ly)?\b", r"\blatest\b",
        r"\brecent(ly)?\b", r"\bjust\s+(happened|announced|released)\b",
        r"\bthis\s+(week|month|year|morning|afternoon|evening)\b",
        r"\byesterday\b", r"\blast\s+(week|month|night|hour)\b",
        r"\bbreaking\b", r"\blive\b", r"\bupdate[sd]?\b",
        r"\bnow\s+playing\b", r"\bhappening\s+now\b",
    ]

    # Topics that require real-time data
    realtime_topics = [
        # Weather
        r"\bweather\b", r"\bforecast\b", r"\btemperature\b",
        r"\brain(ing|y)?\b", r"\bsnow(ing|y)?\b", r"\bstorm\b",
        # Finance
        r"\bstock\s*(price|market|ticker)?\b", r"\bshare\s*price\b",
        r"\bmarket\s*(cap|value|price)\b", r"\btrading\b",
        r"\bexchange\s+rate\b", r"\bcrypto(currency)?\b",
        r"\bbitcoin\b", r"\bethereum\b", r"\b(USD|EUR|GBP)\s*to\b",
        # News & Events
        r"\bnews\b", r"\bheadlines?\b", r"\bannouncement\b",
        r"\belection\b", r"\bvot(e|ing|ed)\b", r"\bpoll(s|ing)?\b",
        # Sports
        r"\bscore[sd]?\b", r"\bgame\s+result\b", r"\bwho\s+won\b",
        r"\bmatch\s+result\b", r"\bstandings?\b", r"\bleague\s+table\b",
        r"\bplayoff\b", r"\bchampionship\b", r"\btournament\b",
        # Trending/Viral
        r"\btrending\b", r"\bviral\b", r"\bpopular\s+right\s+now\b",
    ]

    # Check high confidence patterns
    high_confidence_patterns = explicit_search + time_sensitive + realtime_topics
    if any(re.search(p, normalized) for p in high_confidence_patterns):
        return True, 'high'

    # === MEDIUM CONFIDENCE: Likely needs search ===

    medium_patterns = [
        # Questions about current state/status
        r"\bwhat('s|\s+is)\s+happening\b",
        r"\bwhat('s|\s+is)\s+going\s+on\b",
        r"\bwhat('s|\s+is)\s+the\s+(status|state)\b",
        r"\bis\s+.+\s+(open|closed|available|working)\b",
        r"\bwhen\s+(is|does|will|did)\b",
        r"\bwhere\s+can\s+i\s+(buy|find|get|watch)\b",
        # Price/availability queries
        r"\bhow\s+much\s+(does|is|are|do)\b",
        r"\bprice\s+(of|for)\b", r"\bcost\s+(of|for)\b",
        r"\bavailab(le|ility)\b",
        # Current leadership/roles
        r"\bwho\s+is\s+the\s+(current|new)?\s*(ceo|president|cto|leader|head|director)\b",
        r"\bwho\s+(runs|leads|owns)\b",
        # Latest versions/releases
        r"\blatest\s+(version|release|update|model)\b",
        r"\bnew(est)?\s+(iphone|android|mac|windows|ios|version)\b",
        r"\brelease\s+date\b",
        # Location-based queries
        r"\bnear\s+me\b", r"\bin\s+my\s+area\b",
        r"\brestaurants?\s+in\b", r"\bhotels?\s+in\b",
        # Specific years (recent/near future)
        r"\b202[4-9]\b", r"\b203[0-5]\b",
    ]

    if any(re.search(p, normalized) for p in medium_patterns):
        return True, 'medium'

    # === NO SEARCH indicators (override) ===

    # These patterns strongly suggest no search is needed
    no_search_patterns = [
        # Creative/generative tasks
        r"\bwrite\s+(a|me|an|the)\b", r"\bgenerate\b", r"\bcreate\b",
        r"\bcompose\b", r"\bdraft\b",
        # Coding tasks
        r"\bcode\b", r"\bprogram\b", r"\bscript\b", r"\bfunction\b",
        r"\bdebug\b", r"\bfix\s+(this|the|my)\b", r"\brefactor\b",
        r"\bimplement\b", r"\bbuild\s+(a|an|the)\b",
        # Explanation/help
        r"\bexplain\b", r"\bwhat\s+does\s+.+\s+mean\b",
        r"\bhow\s+(do|does|to)\s+.+\s+work\b",
        r"\bteach\s+me\b", r"\bhelp\s+me\s+understand\b",
        # Translation/conversion
        r"\btranslate\b", r"\bconvert\b",
        # Math/logic
        r"\bcalculate\b", r"\bsolve\b", r"\bcompute\b",
        # Summarization
        r"\bsummarize\b", r"\bsummary\b", r"\btl;?dr\b",
        # Analysis of provided content
        r"\banalyze\s+(this|the)\b", r"\breview\s+(this|the|my)\b",
    ]

    if any(re.search(p, normalized) for p in no_search_patterns):
        return False, 'high'

    # Default: don't search, low confidence
    return False, 'low'


def should_auto_enable_search_simple(user_prompt: str) -> bool:
    """Simple wrapper that returns just the boolean for backward compatibility."""
    should_search, _ = should_auto_enable_search(user_prompt)
    return should_search

def build_prompt_fragment(prompt_text: str, limit: int = 3) -> str:
    """Return the first few words of a prompt for display purposes."""
    if not prompt_text or not isinstance(prompt_text, str):
        return ""
    words = prompt_text.strip().split()
    if not words:
        return ""
    return " ".join(words[:limit])


def _safe_upload_relative_path(candidate: Optional[str]) -> Optional[str]:
    """Normalize a user-provided upload path so it stays within UPLOAD_FOLDER."""
    if not candidate or not isinstance(candidate, str):
        return None

    path = candidate.strip()
    if not path:
        return None

    prefixes = ['/uploads/', 'uploads/']
    for prefix in prefixes:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    path = path.lstrip('/').replace('\\', '/')
    if not path:
        return None

    normalized = os.path.normpath(path)
    if normalized.startswith('..'):
        return None

    return normalized.replace('\\', '/')


def _build_metadata_lookup() -> Dict[str, List[Dict[str, Any]]]:
    metadata_list = session.get('file_metadata_list') or []
    if not isinstance(metadata_list, list):
        metadata_list = [metadata_list]
    lookup: Dict[str, List[Dict[str, Any]]] = {}
    for meta in metadata_list:
        if not isinstance(meta, dict):
            continue
        original = meta.get('original_filename')
        if not original:
            continue
        lookup.setdefault(original, []).append(meta)
    return lookup


def _resolve_user_attachment_source(
    attachment: Dict[str, Any],
    metadata_lookup: Dict[str, List[Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    name = attachment.get('name') or attachment.get('original_filename')
    matched_meta = None
    if name:
        meta_bucket = metadata_lookup.get(name)
        if meta_bucket:
            matched_meta = meta_bucket.pop(0)

    if matched_meta:
        rel_path = matched_meta.get('relative_path')
        rel_path = _safe_upload_relative_path(rel_path)
        if rel_path:
            return {
                'url': url_for('app_routes.serve_uploaded_file', requested_path=rel_path, _external=False),
                'local_path': rel_path,
                'original_filename': matched_meta.get('original_filename') or name
            }

    preview_url = attachment.get('previewUrl')
    rel_path = _safe_upload_relative_path(preview_url)
    if rel_path:
        return {
            'url': url_for('app_routes.serve_uploaded_file', requested_path=rel_path, _external=False),
            'local_path': rel_path,
            'original_filename': name
        }

    # NOTE: We intentionally do NOT store base64 dataUrls in Firestore.
    # They are too large and cause display issues in chat history.
    # User-uploaded images should have been saved to disk during upload,
    # so we should have a local_path from file_metadata_list.
    # If we reach here without a local_path, the attachment won't be stored.

    return None


def build_user_attachment_records(user_prompt: str, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform raw attachment payloads so they can be persisted in chat history."""
    if not attachments:
        return []

    metadata_lookup = _build_metadata_lookup()
    prompt_fragment = build_prompt_fragment(user_prompt)
    records: List[Dict[str, Any]] = []

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        resolved = _resolve_user_attachment_source(attachment, metadata_lookup)
        if not resolved:
            continue

        record = {
            'name': attachment.get('name') or attachment.get('original_filename') or 'Attachment',
            'original_filename': resolved.get('original_filename'),
            'type': attachment.get('type'),
            'isImage': bool(attachment.get('isImage')),
            'url': resolved['url'],
            'local_path': resolved.get('local_path'),
            'prompt_fragment': prompt_fragment,
            'provider': 'user-upload',
            'uploaded_at': datetime.utcnow().isoformat()
        }
        cleaned = {k: v for k, v in record.items() if v is not None}
        records.append(cleaned)

    return records


def handle_image_request(user_id: str, user_prompt: str, page_id: Optional[str] = None):
    """Handle image generation requests."""
    logger.info(f"Detected image request from user {user_id}: {user_prompt}")
    try:
        result = generate_image_result(user_id, user_prompt)
        if not result:
            raise RuntimeError("Image generation failed")
        # Ensure a session exists and persist message with attachment
        session_id, _ = load_or_create_session(user_id)
        session['current_session_id'] = session_id
        local_path = result.get('local_path')
        public_url = result['image_url']
        data_url = public_url if isinstance(public_url, str) and public_url.startswith('data:') else None
        response_id = f"msg_{uuid.uuid4().hex}"
        filename = os.path.basename(local_path) if local_path else None
        prompt_fragment = build_prompt_fragment(user_prompt)
        model_name = result.get('model')
        model_label = result.get('model_label') or model_name
        attachments = [{
            'name': 'AI generated image',
            'original_filename': filename,
            'isImage': True,
            'url': public_url,
            'dataUrl': data_url,
            'seed': result['seed'],
            'provider': result.get('provider'),
            'model': model_name,
            'model_label': model_label,
            'local_path': local_path,
            'prompt_fragment': prompt_fragment
        }]
        storage_attachments = []
        for att in attachments:
            cleaned = att.copy()
            cleaned.pop('dataUrl', None)
            local_rel = cleaned.get('local_path')
            if local_rel:
                cleaned['url'] = url_for('app_routes.serve_uploaded_file', requested_path=local_rel, _external=False)
            else:
                url_value = cleaned.get('url')
                if isinstance(url_value, str) and url_value.startswith('data:'):
                    cleaned.pop('url', None)
            cleaned = {k: v for k, v in cleaned.items() if v is not None}
            storage_attachments.append(cleaned)
        chat_data = {
            'prompt': user_prompt,
            'response': '',
            'timestamp': datetime.utcnow().isoformat(),
            'sentiment': 'neutral',
            'is_code': False,
            'search_results': [],
            'attachments': storage_attachments,
            'response_id': response_id,
            'suppress_text': True,
            'sender': 'AI',
            'message_type': 'image'
        }
        # This session was just set as current, so it's always the active session
        chat_session.save_messages(user_id, session_id, [chat_data], is_active_session=True)

        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        session_doc = session_ref.get()
        session_data = session_doc.to_dict() if session_doc.exists else {}
        if page_id:
            try:
                session_ref.set({'active_page_id': page_id}, merge=True)
            except Exception as exc:
                logger.warning(f"Failed to set active page for image request: {exc}")
        try:
            session_title = update_session_title_if_missing(
                user_id=user_id,
                session_id=session_id,
                session_data=session_data or {},
                response_text='',
                user_prompt=user_prompt
            )
        except Exception as title_error:
            logger.warning(f"Failed to auto-title image session {session_id} for user {user_id}: {title_error}")
            session_title = (session_data or {}).get('session_name', 'New Chat')

        return jsonify({
            'message': 'Image URL generated successfully.',
            'image_url': result['image_url'],
            'local_path': result.get('local_path'),
            'seed': result['seed'],
            'prompt': user_prompt,
            'provider': result.get('provider'),
            'model': model_name,
            'model_label': model_label,
            'attachments': attachments,
            'session_id': session_id,
            'session_name': session_title,
            'message_id': response_id,
            'suppress_text': True
        }), 200
    except Exception as e:
        logger.error(f"Error generating image for user {user_id}: {e}")
        return jsonify({'error': 'Failed to generate image URL.'}), 500


@app_routes.route('/uploads/<path:requested_path>')
@login_required
def serve_uploaded_file(requested_path: str):
    """Serve user-uploaded or generated files with security protections."""
    base_dir = os.path.abspath(Config.UPLOAD_FOLDER)
    safe_path = os.path.abspath(os.path.join(base_dir, requested_path))
    if not safe_path.startswith(base_dir) or not os.path.exists(safe_path):
        current_app.logger.warning(f"Attempted access to missing or invalid upload path: {requested_path}")
        abort(404)

    directory = os.path.dirname(requested_path)
    filename = os.path.basename(requested_path)
    ext = os.path.splitext(filename)[1].lower()

    # Security: Force download for potentially dangerous file types
    # These could contain scripts that execute in browser
    DANGEROUS_EXTENSIONS = {
        '.html', '.htm', '.svg', '.xml', '.xhtml',
        '.js', '.mjs', '.cjs',  # JavaScript
        '.php', '.asp', '.aspx', '.jsp',  # Server scripts
        '.sh', '.bash', '.ps1', '.bat', '.cmd',  # Shell scripts
    }

    # Safe to display inline (images, PDFs)
    INLINE_SAFE = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf'}

    if ext in DANGEROUS_EXTENSIONS:
        # Force download - prevents XSS
        response = send_from_directory(
            os.path.join(base_dir, directory),
            filename,
            as_attachment=True,
            download_name=filename
        )
        # Extra security headers
        response.headers['Content-Type'] = 'application/octet-stream'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        return response
    elif ext in INLINE_SAFE:
        # Safe to display inline
        response = send_from_directory(os.path.join(base_dir, directory), filename)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    else:
        # Default: force download for unknown types
        response = send_from_directory(
            os.path.join(base_dir, directory),
            filename,
            as_attachment=True
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response


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


_GREETINGS_PATTERN = re.compile(r'^(hi|hello|hey|help|please)\b[\s,!.:-]*', re.IGNORECASE)
_CODE_FENCE_PATTERN = re.compile(r'```.*?```', re.DOTALL)


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


# File intent detection patterns
_FILE_INTENT_PATTERNS = [
    r'\b(document|file|pdf|uploaded|attachment|page\s+\d+)\b',
    r'\b(in\s+the\s+(document|file|pdf))\b',
    r'\b(from\s+(the\s+)?(document|file|pdf))\b',
    r'\b(what\s+does\s+(the\s+)?(document|file|pdf)\s+say)\b',
    r'\b(summarize|explain|analyze)\s+(the\s+)?(document|file|pdf)\b',
    r'\b(according\s+to\s+(the\s+)?(document|file|pdf))\b',
]


def _detect_file_intent(prompt: str, has_files: bool = False) -> bool:
    """
    Detect if the user's prompt references uploaded files/documents.

    Args:
        prompt: User's message
        has_files: Whether files are attached to the conversation

    Returns:
        True if file context should be retrieved
    """
    prompt_lower = prompt.lower()

    # Quick checks
    if has_files and any(word in prompt_lower for word in ['document', 'file', 'pdf', 'uploaded', 'attachment']):
        return True

    # Pattern matching
    for pattern in _FILE_INTENT_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True

    return False


def _get_file_chunk_context(
    user_id: str,
    query: str,
    conversation_id: Optional[str] = None,
    top_k: int = 5
) -> str:
    """
    Retrieve relevant file chunks for the query.

    Args:
        user_id: Owner user ID
        query: User's question
        conversation_id: Filter to conversation's files
        top_k: Number of chunks to retrieve

    Returns:
        Formatted context string with relevant file excerpts
    """
    try:
        from .hybrid_retrieval import FileChunkRetriever, format_file_chunk_context

        logger.info(f"RAG retrieval: query='{query[:50]}...', conversation_id={conversation_id}")

        retriever = FileChunkRetriever(db)
        results = retriever.search(
            user_id=user_id,
            query=query,
            conversation_id=conversation_id,
            top_k=top_k
        )

        if not results:
            logger.info("RAG retrieval: No chunks found")
            return ""

        logger.info(f"RAG retrieval: Found {len(results)} relevant chunks")
        return format_file_chunk_context(results)

    except Exception as e:
        logger.warning(f"Failed to retrieve file chunks: {e}")
        return ""


# ------------------ Streaming Chat Endpoint (DeepSeek R1) ------------------ #
@app_routes.route('/chat/stream', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def chat_stream():
    """
    Stream chat response with real-time reasoning content (DeepSeek R1).
    Returns Server-Sent Events (SSE) with reasoning and content chunks.
    At completion, processes content for artifacts and saves to history.
    """
    from .utilities import stream_deepseek_response

    user_id = None
    guest_mode = 'guest_id' in session and 'user' not in session

    if not guest_mode:
        user_data = session.get('user')
        if not user_data or 'uid' not in user_data:
            return jsonify({'error': 'Unauthorized'}), 401
        user_id = user_data['uid']
    else:
        user_id = session.get('guest', {}).get('id')

    prompt = request.form.get('prompt', '').strip()
    user_prompt_raw = prompt  # Keep raw user text for intent detection (before file content injection)
    model = request.form.get('model', 'deepseek-reasoner')
    page_id = request.form.get('page_id', '').strip() or None

    # Include extracted text from uploaded files
    extracted_text = ''
    attachments_json = request.form.get('attachments', '')
    if attachments_json:
        try:
            att_list = json.loads(attachments_json)
            for att in att_list:
                # Try extractedText sent from frontend first
                et = att.get('extractedText')
                if et:
                    extracted_text += et + '\n'
                    continue
                # Fallback: read file from disk using previewUrl
                preview_url = att.get('previewUrl', '')
                if preview_url and not att.get('isImage'):
                    # previewUrl is like /uploads/user_id/filename.txt
                    file_rel = preview_url.lstrip('/')
                    # Try relative to app root first, then Config.UPLOAD_FOLDER
                    app_root = os.path.dirname(os.path.dirname(__file__))
                    file_path = os.path.join(app_root, file_rel)
                    if not os.path.isfile(file_path):
                        # previewUrl may be /uploads/X, UPLOAD_FOLDER is 'uploads'
                        stripped = preview_url.replace('/uploads/', '', 1)
                        file_path = os.path.join(Config.UPLOAD_FOLDER, stripped)
                    if os.path.isfile(file_path):
                        try:
                            with open(file_path, 'r', errors='replace') as f:
                                et = f.read()
                            if et:
                                extracted_text += et + '\n'
                                logger.info(f"[Stream] Read {len(et)} chars from disk: {file_path}")
                        except Exception as e:
                            logger.warning(f"[Stream] Failed to read file {file_path}: {e}")
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback to session
    if not extracted_text:
        extracted_text = session.pop('extracted_text', '')
    if extracted_text:
        max_extract = min(len(extracted_text), 30000)
        file_context = f"Uploaded file content:\n{extracted_text[:max_extract]}"
        if len(extracted_text) > max_extract:
            file_context += f"\n[...truncated, showing first {max_extract} of {len(extracted_text)} chars]"
        prompt = f"{file_context}\n\nUser request: {prompt}"

    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400

    # Get or create session for saving history and context detection
    session_id = None
    previous_messages = []
    if not guest_mode and user_id:
        try:
            session_id, session_data = load_or_create_session(user_id)
            # Load previous messages for follow-up question context detection
            previous_messages = session_data.get('messages', [])
            if isinstance(previous_messages, list) and previous_messages:
                # Sort by timestamp
                try:
                    previous_messages = sorted(
                        previous_messages,
                        key=lambda m: m.get('timestamp', ''),
                    )
                except Exception:
                    pass
                # Cap messages in memory — only recent ones are needed for context
                if len(previous_messages) > 20:
                    logger.debug(f"[Stream] Trimming {len(previous_messages)} messages to last 20")
                    previous_messages = previous_messages[-20:]
                logger.debug(f"[Stream] Loaded {len(previous_messages)} previous messages for context")
        except Exception as e:
            logger.warning(f"Failed to load session for streaming: {e}")

    # ============================================================
    # Memory V2: Get memory context via orchestrator (streaming)
    # ============================================================
    memory_orchestrator_stream = None
    memory_eval_metadata_stream = None
    long_term_memory_stream = ""

    if not guest_mode and user_id:
        try:
            memory_session_ctx = SessionContext(
                user_id=user_id,
                device_id=session.get('_device_id'),
                is_new_device=session.get('_is_new_device', False)
            )
            conv_state = ConversationState(
                pending_confirmation_ids=session.get('_memory_pending_ids', []),
                last_retrieved_ids=session.get('_memory_last_ids', []),
                messages_in_session=len(previous_messages)
            )
            memory_orchestrator_stream = MemoryOrchestrator(user_id, memory_session_ctx)
            memory_context = memory_orchestrator_stream.get_context(prompt, conv_state)

            if not memory_context.memory_disabled:
                long_term_memory_stream = memory_context.context_block
                memory_eval_metadata_stream = memory_context.eval_metadata
                logger.info(f"🧠 [Stream] Memory V2: Retrieved {len(memory_context.eval_metadata.retrieved_ids)} memories")
        except Exception as e:
            logger.warning(f"[Stream] Memory V2 retrieval failed: {e}")
    # ============================================================

    # ============================================================
    # Memory V3: Add behavior signals (ephemeral, never stored)
    # ============================================================
    v3_injected = False
    v3_signal = None
    if V3_ENABLED and long_term_memory_stream:
        try:
            # Generate signal for metrics (always, even in shadow mode)
            v3_signal = generate_v3_signal(
                prompt,
                previous_messages[-5:] if previous_messages else None
            )

            # Check if this user/session should get V3 injection
            v3_should_inject = should_inject_v3(
                user_id=user_id,
                session_id=session_id
            )

            if v3_should_inject:
                long_term_memory_stream = get_v3_context(
                    user_message=prompt,
                    conversation_history=previous_messages[-5:] if previous_messages else None,
                    v2_context=long_term_memory_stream
                )
                v3_injected = True
                v3_cohort = "treatment"
                logger.info(f"🧠 [Stream] Memory V3: Behavior signals INJECTED (cohort={v3_cohort})")
            else:
                v3_cohort = "control"
                logger.info(f"🧠 [Stream] Memory V3: Shadow mode (cohort={v3_cohort})")

            # Record metrics for rollout analysis
            record_v3_metrics(v3_signal, injected=v3_injected)

            # Track conversation turn for KPIs
            rollout_mgr = get_rollout_manager()
            rollout_mgr.record_turn(
                prompt,
                previous_messages[-3:] if previous_messages else None
            )

        except Exception as e:
            logger.warning(f"[Stream] Memory V3 failed (using V2 only): {e}")
    # ============================================================

    # Inject memory context into streaming prompt (was missing — memories retrieved but never used)
    enhanced_prompt = prompt
    if long_term_memory_stream:
        enhanced_prompt = f"User memory (from previous sessions):\n{long_term_memory_stream}\n\n{enhanced_prompt}"
        logger.info(f"🧠 [Stream] Injected {len(long_term_memory_stream)} chars of memory context into prompt")

    # Inject recent chat history for conversation continuity
    if previous_messages:
        recent = previous_messages[-6:]  # Last 3 exchanges
        history_lines = []
        for msg in recent:
            role = "User" if msg.get('prompt') else "Assistant"
            if msg.get('prompt'):
                history_lines.append(f"User: {msg['prompt'][:300]}")
            if msg.get('response'):
                history_lines.append(f"Assistant: {msg['response'][:300]}")
        if history_lines:
            history_block = "\n".join(history_lines)
            enhanced_prompt = f"Recent conversation:\n{history_block}\n\n{enhanced_prompt}"
            logger.info(f"🔍 [Stream] Injected {len(recent)} recent messages into prompt")

    # Claude-inspired: Inject current artifact context when user asks to fix/update
    if session_id and user_id and detect_artifact_update_intent(prompt):
        logger.info(f"🔧 [Stream] Artifact update intent detected: '{prompt[:50]}...'")
        artifact_context = load_current_artifact_context(user_id, session_id)
        if artifact_context:
            logger.info(f"📦 [Stream] Injected {len(artifact_context)} chars of artifact context")
            # Prepend artifact context to prompt so AI sees current code
            enhanced_prompt = f"{artifact_context}\n\nUser request: {prompt}\n\nIMPORTANT: Apply the fix/update to the code shown above and output a complete ```artifact block with ALL files."
        else:
            logger.debug("[Stream] No active artifact found to inject")

    # Inject crypto/stock context ONLY when user explicitly asks for price/chart
    # Use raw user prompt (not file-enriched prompt) to avoid false positives from file content
    chart_query = detect_crypto_price_query(user_prompt_raw)
    if chart_query:
        asset_type = chart_query.get('asset_type')
        if asset_type == 'crypto':
            coin = chart_query.get('coin')
            if coin:
                crypto_context = get_crypto_ai_context(coin)
                if crypto_context:
                    context_str = format_crypto_context_for_ai(crypto_context)
                    logger.info(f"💰 [Stream] Injected crypto context for chart query: {coin}")
                    enhanced_prompt = f"{context_str}\n\nUser question: {enhanced_prompt}"
        elif asset_type == 'stock':
            symbol = chart_query.get('symbol')
            if symbol:
                stock_context = get_stock_ai_context(symbol)
                if stock_context:
                    context_str = format_stock_context_for_ai(stock_context)
                    logger.info(f"📈 [Stream] Injected stock context for chart query: {symbol}")
                    enhanced_prompt = f"{context_str}\n\nUser question: {enhanced_prompt}"

    def generate():
        import json
        reasoning_buffer = []
        content_buffer = []

        try:
            for chunk in stream_deepseek_response(enhanced_prompt, model):
                chunk_type = chunk.get('type')

                if chunk_type == 'reasoning':
                    text = chunk.get('text', '')
                    reasoning_buffer.append(text)
                    yield f"data: {json.dumps({'type': 'reasoning', 'text': text})}\n\n"

                elif chunk_type == 'content':
                    text = chunk.get('text', '')
                    content_buffer.append(text)
                    yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"

                elif chunk_type == 'done':
                    full_reasoning = ''.join(reasoning_buffer)
                    full_content = ''.join(content_buffer)
                    # Strip HTML tags that some models may output
                    full_content = strip_html_from_response(full_content)

                    # Process content for artifacts
                    artifact_data = None
                    render_mode = 'inline'
                    chat_summary = full_content
                    routing = None

                    try:
                        structured = parse_structured_response(full_content)
                        routing = artifact_router.route(structured, prompt, provider_flagged_code=False)
                        logger.info(f"Stream routing decision: render_mode={routing.render_mode}, has_payload={routing.artifact_payload is not None}")
                        logger.info(f"Stream chat_payload: {repr(routing.chat_payload[:200] if routing.chat_payload else None)}")
                        logger.info(f"Stream structured.chat: {repr(structured.chat[:200] if structured.chat else None)}")
                        logger.info(f"Stream structured.remainder: {repr(structured.remainder[:200] if structured.remainder else None)}")

                        if routing.render_mode and routing.render_mode != 'inline':
                            render_mode = routing.render_mode
                            chat_summary = routing.chat_payload or structured.remainder or ''
                            logger.info(f"Stream chat_summary set to: {repr(chat_summary[:200])}")

                            if routing.artifact_payload:
                                files = routing.artifact_payload.get('files', [])
                                primary_lang = files[0].get('language', 'code') if files else 'code'
                                artifact_id = f'artifact-{int(time.time())}'
                                version_id = f'version-{int(time.time())}'

                                # Structure expected by artifactPanel.js
                                artifact_data = {
                                    'artifactId': routing.artifact_payload.get('artifact_id', artifact_id),
                                    'title': routing.artifact_payload.get('title', 'Artifact'),
                                    'type': routing.artifact_payload.get('type', 'project'),
                                    'primaryLanguage': primary_lang,
                                    'activeVersion': {
                                        'versionId': version_id,
                                        'versionNumber': 1,
                                        'files': files,
                                        'primaryLanguage': primary_lang,
                                        'entrypointPath': files[0].get('path') if files else None
                                    },
                                    'versions': [{
                                        'versionId': version_id,
                                        'versionNumber': 1
                                    }]
                                }
                                logger.info(f"Stream artifact extracted: {len(files)} files")
                    except Exception as artifact_err:
                        logger.warning(f"Artifact processing error in stream: {artifact_err}")

                    # Generate response ID before done payload so frontend can use same ID
                    response_msg_id = f"msg_{uuid.uuid4().hex}"

                    # Check if response looks like an email (has greeting + sign-off)
                    is_email = looks_like_email(full_content)
                    email_data = None
                    if is_email:
                        email_data = parse_email_from_response(full_content)
                        logger.info(f"Stream: Email detected, parsed: subject='{email_data.get('subject', '')[:30]}...'")

                    # Check if user asked for crypto/stock price/chart
                    crypto_chart = detect_crypto_price_query(user_prompt_raw)
                    if crypto_chart:
                        asset_type = crypto_chart.get('asset_type', 'crypto')
                        asset = crypto_chart.get('symbol') if asset_type == 'stock' else crypto_chart.get('coin')
                        logger.info(f"Stream: {asset_type.upper()} chart detected for {asset}")

                    done_payload = {
                        'type': 'done',
                        'reasoning': full_reasoning,
                        'content': full_content,
                        'chat_summary': chat_summary,
                        'render_mode': render_mode,
                        'artifact': artifact_data,
                        'response_id': response_msg_id,
                        'is_email': is_email,
                        'email': email_data,
                        'crypto_chart': crypto_chart
                    }
                    logger.info(f"Stream done payload: render_mode={render_mode}, has_artifact={artifact_data is not None}, crypto_chart={crypto_chart is not None}")

                    # Save to history and persist artifact
                    if session_id and user_id and not guest_mode:
                        try:
                            # Update session title if missing
                            try:
                                session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
                                session_doc = session_ref.get()
                                session_data = session_doc.to_dict() if session_doc.exists else {}
                                update_session_title_if_missing(user_id, session_id, session_data, chat_summary, prompt)
                            except Exception as title_err:
                                logger.warning(f"Failed to update session title in streaming: {title_err}")

                            artifact_ref = None

                            # Persist artifact to Firestore if detected
                            if artifact_data and routing and routing.artifact_payload:
                                try:
                                    persisted = artifact_store.persist_decision(
                                        user_id=user_id,
                                        session_id=session_id,
                                        decision=routing,
                                        source_message_id=response_msg_id,
                                        created_by="assistant"
                                    )
                                    if persisted:
                                        logger.info(f"✅ Stream artifact persisted: {persisted.get('artifactId')}")
                                        # Update artifact_data with persisted IDs
                                        artifact_data['artifactId'] = persisted.get('artifactId', artifact_data.get('artifactId'))
                                        if persisted.get('activeVersion'):
                                            artifact_data['activeVersion']['versionId'] = persisted['activeVersion'].get('versionId')

                                        artifact_ref = {
                                            'artifactId': persisted.get('artifactId'),
                                            'title': persisted.get('title'),
                                            'type': persisted.get('type'),
                                            'primaryLanguage': persisted.get('primaryLanguage'),
                                            'summary': chat_summary[:200] if chat_summary else None,
                                            'versionId': persisted.get('activeVersion', {}).get('versionId'),
                                            'versionNumber': persisted.get('versionCount', 1)
                                        }
                                    else:
                                        # persist_decision returned None - use artifact_data as fallback
                                        logger.warning(f"⚠️ persist_decision returned None, using artifact_data fallback")
                                        artifact_ref = {
                                            'artifactId': artifact_data.get('artifactId'),
                                            'title': artifact_data.get('title'),
                                            'type': artifact_data.get('type'),
                                            'primaryLanguage': artifact_data.get('primaryLanguage'),
                                            'summary': chat_summary[:200] if chat_summary else None,
                                            'versionId': artifact_data.get('activeVersion', {}).get('versionId'),
                                            'versionNumber': 1,
                                            'pending': True
                                        }
                                except Exception as artifact_err:
                                    logger.warning(f"Failed to persist stream artifact: {artifact_err}")
                                    artifact_ref = {
                                        'artifactId': artifact_data.get('artifactId'),
                                        'title': artifact_data.get('title'),
                                        'type': artifact_data.get('type'),
                                        'primaryLanguage': artifact_data.get('primaryLanguage'),
                                        'summary': chat_summary[:200] if chat_summary else None,
                                        'pending': True
                                    }

                            logger.info(f"📦 Saving stream interaction: render_mode={render_mode}, artifact_ref={artifact_ref is not None}, artifact_id={artifact_ref.get('artifactId') if artifact_ref else None}, pending={artifact_ref.get('pending') if artifact_ref else 'N/A'}")
                            save_interaction(
                                user_id=user_id,
                                session_id=session_id,
                                user_prompt=prompt,
                                response_text=chat_summary,
                                sentiment='neutral',
                                is_code_response=(render_mode in ['artifact', 'code_window']),
                                search_results=[],
                                persist=True,
                                response_id=response_msg_id,
                                render_mode=render_mode,
                                artifact_reference=artifact_ref,
                                reasoning=full_reasoning if full_reasoning else None,
                                crypto_chart=crypto_chart
                            )
                            logger.info(f"Streaming response saved to history for session {session_id}")

                            # ============================================================
                            # Memory V2: Post-turn processing (streaming)
                            # ============================================================
                            stream_memory_updated = False
                            if memory_orchestrator_stream is not None:
                                try:
                                    # Use conv_state captured before generator (avoids request context issue)
                                    conv_state_for_write = ConversationState(
                                        pending_confirmation_ids=conv_state.pending_confirmation_ids if conv_state else [],
                                        last_retrieved_ids=conv_state.last_retrieved_ids if conv_state else [],
                                        messages_in_session=len(previous_messages)
                                    )
                                    memory_write_result = memory_orchestrator_stream.post_turn(
                                        prompt,
                                        full_content,
                                        conv_state_for_write,
                                        memory_eval_metadata_stream
                                    )
                                    # Note: session write skipped here (no request context in generator)
                                    # Pending IDs will be re-read from Firestore on next request
                                    # Check if anything was actually stored
                                    stream_memory_updated = (
                                        memory_write_result.stored > 0 or
                                        memory_write_result.promoted > 0 or
                                        memory_write_result.superseded > 0
                                    )
                                    logger.info(f"🧠 [Stream] Memory V2: stored={memory_write_result.stored}, promoted={memory_write_result.promoted}")
                                except Exception as mem_err:
                                    logger.warning(f"[Stream] Memory V2 post_turn failed: {mem_err}")

                            # Add memory_updated to done_payload
                            done_payload['memory_updated'] = stream_memory_updated
                            # ============================================================

                        except Exception as save_err:
                            logger.warning(f"Failed to save streaming response to history: {save_err}")

                    yield f"data: {json.dumps(done_payload)}\n\n"

                elif chunk_type == 'error':
                    yield f"data: {json.dumps({'type': 'error', 'text': chunk.get('text', 'Unknown error')})}\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


# ------------------ Edit Text API (for email toolbar) ------------------ #
@app_routes.route('/api/edit-text', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def edit_text():
    """
    AI-assisted text editing for email composer toolbar.
    Takes selected text and an instruction, returns edited text.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        text = data.get('text', '').strip()
        instruction = data.get('instruction', '').strip()

        logger.info(f"Edit text request - instruction: {instruction[:50]}...")

        if not text or not instruction:
            return jsonify({'error': 'Text and instruction are required'}), 400

        if len(text) > 2000:
            return jsonify({'error': 'Text too long (max 2000 characters)'}), 400

        # Use Groq API for quick edits (consistent with rest of app)
        groq_key = Config.GROQ_API_KEY
        if not groq_key:
            return jsonify({'error': 'API key not configured'}), 500

        edit_prompt = f"""Edit the following text according to the instruction. Return ONLY the edited text, nothing else.

Instruction: {instruction}

Text to edit:
{text}

Edited text:"""

        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "You are a helpful text editor. Edit text exactly as instructed. Return only the edited text without any explanations or additional formatting."},
                {"role": "user", "content": edit_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }

        response = requests.post(
            Config.API_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()

        result = response.json()
        edited_text = result['choices'][0]['message']['content'].strip()
        logger.info(f"Edit text success - result length: {len(edited_text)}")

        return jsonify({'edited_text': edited_text})

    except Exception as e:
        logger.error(f"Edit text error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ------------------ Chat Endpoint ------------------ #
@app_routes.route('/chat', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@guest_allowed
def chat():
    """
    Handle chat interactions with the AI model:
    - Extract user input and optional extracted text from uploaded files.
    - Optionally perform a web search and integrate the results as context.
    - Retrieve conversation history and preferences to provide context.
    - Generate an AI response and return it along with metadata.
    """
    try:
        # Guest mode handling
        guest_mode = is_guest()
        if guest_mode:
            # IP-based tracking (prevents cookie clearing bypass)
            client_ip = get_client_ip()
            ip_hash = hash_ip(client_ip)

            # Check IP-based limit first (stored in Firebase)
            ip_allowed, ip_count = check_guest_ip_limit(ip_hash)
            if not ip_allowed:
                logger.info(f"Guest IP limit reached for hash: {ip_hash[:8]}...")
                return jsonify({
                    'error': 'guest_limit_reached',
                    'message': 'Create a free account to continue chatting and unlock all features.',
                    'limit': GUEST_MESSAGE_LIMIT
                }), 403

            # Also check session-based limit as backup
            session_count = get_guest_message_count()
            current_count = max(ip_count, session_count)

            if current_count >= GUEST_MESSAGE_LIMIT:
                return jsonify({
                    'error': 'guest_limit_reached',
                    'message': 'Create a free account to continue chatting and unlock all features.',
                    'limit': GUEST_MESSAGE_LIMIT
                }), 403

            user_id = session['guest']['id']
            # Store IP hash in session for later increment
            session['_guest_ip_hash'] = ip_hash
        else:
            user_id = session['user']['uid']

        user_prompt = request.form.get('prompt', '').strip()

        # For guests: force default model and disable certain features
        if guest_mode:
            model = DEFAULT_MODEL
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)
            smart_routing_enabled = False
        else:
            requested_model = request.form.get('model', AUTO_MODEL)
            # Check if smart routing should be used
            smart_routing_enabled = (requested_model == AUTO_MODEL or requested_model == 'auto' or not requested_model)
            if smart_routing_enabled:
                # Will be routed by smart_route() after we have context
                model = DEFAULT_MODEL  # Placeholder, will be updated
            else:
                model = resolve_model_id(requested_model)
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)

        extracted_text = ''
        raw_attachments = request.form.get('attachments')
        attachments = []
        if raw_attachments:
            try:
                parsed = json.loads(raw_attachments)
                if isinstance(parsed, list):
                    attachments = parsed
            except Exception as e:
                logger.warning(f"Failed to parse attachments payload: {e}")

        # Extract text from attachments (sent from frontend or read from disk)
        for att in attachments:
            et = att.get('extractedText')
            if et:
                extracted_text += et + '\n'
                continue
            # Fallback: read file from disk using previewUrl
            preview_url = att.get('previewUrl', '')
            if preview_url and not att.get('isImage'):
                file_rel = preview_url.lstrip('/')
                app_root = os.path.dirname(os.path.dirname(__file__))
                file_path = os.path.join(app_root, file_rel)
                if not os.path.isfile(file_path):
                    stripped = preview_url.replace('/uploads/', '', 1)
                    file_path = os.path.join(Config.UPLOAD_FOLDER, stripped)
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, 'r', errors='replace') as f:
                            et = f.read()
                        if et:
                            extracted_text += et + '\n'
                    except Exception as e:
                        logger.warning(f"Failed to read file {file_path}: {e}")
        # Fallback to session
        if not extracted_text:
            extracted_text = session.pop('extracted_text', '')
        requested_page_id_raw = request.form.get('page_id')
        requested_page_id = requested_page_id_raw.strip() if requested_page_id_raw else None
        # Guests don't get web search
        if guest_mode:
            enable_search = False
            search_confidence = 'low'
        else:
            enable_search_param = request.form.get('enable_search')
            if enable_search_param is not None:
                # User explicitly set the toggle
                enable_search = enable_search_param.lower() == 'true'
                search_confidence = 'user'
            else:
                # Auto-detect if search is needed
                enable_search, search_confidence = should_auto_enable_search(user_prompt)

        backend_param = request.form.get('backend') or getattr(Config, 'SEARCH_DEFAULT_BACKEND', SearchBackend.BRAVE.value)
        backend = resolve_search_backend(backend_param)

        # Validate input
        if not user_prompt and not extracted_text:
            logger.warning("No user prompt or extracted text provided.")
            return jsonify({'error': 'No prompt provided.'}), 400

        # Validate prompt length to prevent resource exhaustion
        MAX_PROMPT_LENGTH = 50000  # ~12,500 tokens at 4 chars/token
        MAX_EXTRACTED_TEXT_LENGTH = 100000  # Allow more for documents
        if len(user_prompt) > MAX_PROMPT_LENGTH:
            logger.warning(f"Prompt too long: {len(user_prompt)} chars from user {user_id}")
            return jsonify({'error': f'Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters.'}), 400
        if len(extracted_text) > MAX_EXTRACTED_TEXT_LENGTH:
            logger.warning(f"Truncating extracted text from {len(extracted_text)} to {MAX_EXTRACTED_TEXT_LENGTH} chars")
            extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_LENGTH]

        # Prompt injection guard
        injection_check = check_prompt_injection(user_prompt)
        if not injection_check.is_safe:
            logger.warning(f"🛡️ Blocked prompt injection from user {user_id}: {injection_check.matched_patterns}")
            return jsonify({
                'error': 'security_blocked',
                'message': injection_check.message
            }), 400

        firestore_available = True
        user_profile: Dict[str, Any] = {}
        user_api_keys = {}

        # Skip Firestore user profile for guests
        if not guest_mode:
            # Track user preferences from their message for personalized suggestions
            update_user_preferences(user_id, user_prompt)

            try:
                user_ref = db.collection('users').document(user_id)
                user_snapshot = user_ref.get()
                if user_snapshot.exists:
                    user_profile = user_snapshot.to_dict() or {}
            except Exception as e:
                firestore_available = False
                logger.warning(f"Failed to load user profile for {user_id}: {e}")
                user_profile = {}

            if isinstance(user_profile, dict):
                encrypted_keys = user_profile.get('api_keys') or {}
                # Decrypt user API keys if they are encrypted
                for key_name, key_value in encrypted_keys.items():
                    if key_value and is_encrypted(key_value):
                        try:
                            user_api_keys[key_name] = decrypt_api_key(key_value)
                        except Exception as e:
                            logger.warning(f"Failed to decrypt {key_name} API key for user {user_id}: {e}")
                    else:
                        user_api_keys[key_name] = key_value

        g.api_keys = user_api_keys if user_api_keys else {}

        # NOTE: Image request check moved to after session/messages are loaded
        # so we can pass chat history for better detection accuracy

        # Perform optional web search
        search_results: List[SearchResult] = []
        if enable_search and user_prompt:
            search_results = perform_web_search(user_id, user_prompt, backend=backend)

        # Fetch content from URLs in the message (for summarization/analysis)
        url_content = ""
        url_fetch_results = []
        if URL_READER_AVAILABLE and user_prompt and not guest_mode:
            try:
                url_reader = get_url_reader(db)
                detected_urls = url_reader.detect_urls(user_prompt)
                if detected_urls:
                    logger.info(f"Detected {len(detected_urls)} URL(s) in message: {detected_urls}")
                    for url in detected_urls:
                        result = url_reader.fetch_url(url)
                        url_fetch_results.append(result)
                        if result.get('success'):
                            url_content += (
                                f"\n\n---\n"
                                f"[Content from: {result.get('title', url)}]\n"
                                f"Source: {url}\n"
                                f"Type: {result.get('type', 'article')}\n\n"
                                f"{result.get('content', '')}\n"
                                f"---\n"
                            )
                            logger.info(f"Fetched {len(result.get('content', ''))} chars from {url}")
                        else:
                            logger.warning(f"Failed to fetch URL {url}: {result.get('error')}")
            except Exception as e:
                logger.error(f"URL reader error: {e}")

        offline_reason = None

        # Guests use ephemeral in-memory session only
        if guest_mode:
            session_id = session['guest']['id']
            # Store guest messages in Flask session (ephemeral)
            if 'guest_messages' not in session:
                session['guest_messages'] = []
            session_data = {
                'messages': session['guest_messages'],
                'session_name': 'Guest Chat',
                'preferences': []
            }
        elif firestore_available:
            try:
                session_id, session_data = load_or_create_session(user_id)
            except Exception as e:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'session_unavailable'
                logger.warning(f"Falling back to offline session cache for user {user_id}: {e}")
                session_id, session_data = _ensure_offline_session_state()
        else:
            offline_reason = 'session_unavailable'
            session_id, session_data = _ensure_offline_session_state()
        # Note: We no longer override session_data['messages'] with page-specific messages.
        # The main messages array is the authoritative source for conversation history on the backend.
        # The pages feature is managed by the frontend for UI branching purposes.
        # Backend save_interaction() saves to session.messages, so we should read from there too.
        resolved_page_id = requested_page_id or (session_data.get('active_page_id') if isinstance(session_data, dict) else None)
        if resolved_page_id:
            logger.debug(f"📥 PAGE DEBUG: active_page_id={resolved_page_id} (using main messages array for history, not page-specific)")

        # Load user and session preferences (skip for guests)
        if guest_mode:
            user_preferences = []
            session_preferences = []
        elif firestore_available:
            try:
                user_preferences, session_preferences, user_profile = load_user_and_session_preferences(
                    user_id,
                    session_data,
                    user_profile
                )
            except Exception as e:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'preferences_unavailable'
                logger.warning(f"Failed to load preferences from Firestore for user {user_id}: {e}")
                user_preferences = []
                session_preferences = session_data.get('preferences', [])
        else:
            user_preferences = []
            session_preferences = session_data.get('preferences', [])
        # Use messages ordered by timestamp
        previous_messages = session_data.get('messages', [])
        if isinstance(previous_messages, list):
            try:
                previous_messages = sorted(
                    previous_messages,
                    key=lambda m: m.get('timestamp', ''),
                )
            except Exception:
                pass
            # Cap messages in memory — only recent ones are needed for context
            if len(previous_messages) > 20:
                logger.debug(f"Trimming {len(previous_messages)} messages to last 20")
                previous_messages = previous_messages[-20:]

        # Check if user requests an image (now with chat history for better accuracy)
        if is_image_request(user_prompt, previous_messages):
            return handle_image_request(user_id, user_prompt, requested_page_id)

        # Resolve pointer references (e.g., "tell me about #10" → explicit item)
        pointer_info = None
        original_prompt = user_prompt
        try:
            user_prompt, was_rewritten, pointer_info = resolve_user_pointer(
                user_id, session_id, user_prompt
            )
            if was_rewritten:
                logger.info(f"Pointer resolved: '{original_prompt[:50]}' → '{user_prompt[:80]}'")
            elif pointer_info and pointer_info.get("needs_clarification"):
                # Pointer detected but no list to resolve - will need clarification
                logger.debug(f"Pointer detected but needs clarification: {pointer_info}")
        except Exception as e:
            logger.debug(f"Pointer resolution failed: {e}")

        # Check if there's a recent crypto/stock chart in conversation
        # If so, skip ambiguity check - "tell me more" clearly refers to the chart
        has_recent_chart = False
        recent_chart_info = None
        logger.info(f"📊 CHART CHECK: Checking {len(previous_messages)} messages for crypto_chart")
        for i, msg in enumerate(reversed(previous_messages[-4:])):
            has_chart = msg.get('crypto_chart')
            logger.info(f"📊 CHART CHECK: msg[{i}] has crypto_chart: {has_chart}, keys: {list(msg.keys())[:10]}")
            if has_chart:
                has_recent_chart = True
                recent_chart_info = msg.get('crypto_chart')
                logger.info(f"📊 Skipping SmartGuard - recent chart context: {recent_chart_info.get('asset_type', 'crypto')} - {recent_chart_info.get('coin') or recent_chart_info.get('symbol')}")
                break
        if not has_recent_chart:
            logger.debug(f"📊 CHART CHECK: No recent chart found in messages")

        # Check for ambiguous input BEFORE calling LLM (LLM-powered smart check)
        # Skip if there's a recent chart context (follow-up questions have clear context)
        try:
            intercept_result = None
            if not has_recent_chart:
                intercept_result = smart_check_input(
                    user_id, session_id, user_prompt, previous_messages
                )
            if intercept_result and intercept_result.should_intercept:
                logger.info(f"Intercepting ambiguous input: {intercept_result.reason}")
                # Return clarification response directly without calling LLM
                clarification_response = intercept_result.clarification_prompt
                response_message_id = f"msg_{uuid.uuid4().hex}"
                chat_data, persisted = save_interaction(
                    user_id=user_id,
                    session_id=session_id,
                    user_prompt=user_prompt,
                    response_text=clarification_response,
                    sentiment='neutral',
                    is_code_response=False,
                    search_results=[],
                    user_attachments=[],
                    persist=firestore_available,
                    response_id=response_message_id,
                    render_mode='inline',
                    artifact_reference=None
                )
                return jsonify({
                    'response': clarification_response,
                    'sentiment': 'neutral',
                    'is_code_response': False,
                    'session_id': session_id,
                    'message_id': response_message_id,
                    'title': session_data.get('session_name') or "Chat",
                    'intercept_reason': intercept_result.reason,
                })
        except Exception as e:
            logger.debug(f"Ambiguity check failed: {e}")

        # Determine stateless vs stateful mode
        is_stateless = should_skip_memory(user_prompt)
        message_mode = get_message_mode(user_prompt)
        logger.debug(f"Message mode: {message_mode} for prompt: {user_prompt[:50]}...")

        # ============================================================
        # Memory V2: Get memory context via orchestrator
        # ============================================================
        logger.info(f"🧠 Memory V2: Starting retrieval for user {user_id}")
        memory_eval_metadata = None
        memory_orchestrator = None  # Kept for post_turn() call later
        try:
            # Build session context for Memory V2
            memory_session_ctx = SessionContext(
                user_id=user_id,
                device_id=session.get('_device_id'),
                is_new_device=session.get('_is_new_device', False)
            )

            # Get conversation state from session (for pending confirmations)
            conv_state = ConversationState(
                pending_confirmation_ids=session.get('_memory_pending_ids', []),
                last_retrieved_ids=session.get('_memory_last_ids', []),
                messages_in_session=len(previous_messages)
            )

            # Create orchestrator and get context
            memory_orchestrator = MemoryOrchestrator(user_id, memory_session_ctx)
            memory_context = memory_orchestrator.get_context(user_prompt, conv_state)

            # Use context_block as long_term_memory
            if memory_context.memory_disabled:
                long_term_memory = ""
                logger.debug(f"Memory disabled: {memory_context.disable_reason}")
            else:
                long_term_memory = memory_context.context_block
                memory_eval_metadata = memory_context.eval_metadata
                logger.info(f"🧠 Memory V2: Retrieved {len(memory_context.eval_metadata.retrieved_ids)} memories")

                # Check for action required (low-confidence confirmation)
                if memory_context.action_required:
                    logger.debug(f"Memory action required: {memory_context.action_required.action_type}")

        except Exception as e:
            logger.error(f"🧠 Memory V2 retrieval FAILED: {e}", exc_info=True)
            long_term_memory = ""
        # ============================================================

        # ============================================================
        # Memory V3: Add behavior signals (ephemeral, never stored)
        # ============================================================
        v3_injected = False
        v3_signal = None
        if V3_ENABLED and long_term_memory:
            try:
                # Generate signal for metrics (always, even in shadow mode)
                v3_signal = generate_v3_signal(
                    user_prompt,
                    previous_messages[-5:] if previous_messages else None
                )

                # Check if this user/session should get V3 injection
                v3_should_inject = should_inject_v3(
                    user_id=user_id,
                    session_id=session_id
                )

                if v3_should_inject:
                    long_term_memory = get_v3_context(
                        user_message=user_prompt,
                        conversation_history=previous_messages[-5:] if previous_messages else None,
                        v2_context=long_term_memory
                    )
                    v3_injected = True
                    v3_cohort = "treatment"
                    logger.info(f"🧠 Memory V3: Behavior signals INJECTED (cohort={v3_cohort})")
                else:
                    v3_cohort = "control"
                    logger.info(f"🧠 Memory V3: Shadow mode (cohort={v3_cohort})")

                # Record metrics for rollout analysis
                record_v3_metrics(v3_signal, injected=v3_injected)

                # Track conversation turn for KPIs
                rollout_mgr = get_rollout_manager()
                rollout_mgr.record_turn(
                    user_prompt,
                    previous_messages[-3:] if previous_messages else None
                )

            except Exception as e:
                logger.warning(f"Memory V3 failed (using V2 only): {e}")
        # ============================================================

        # Build attachment context (names/types) to keep the model aware of uploaded files
        attachment_lines = []
        if attachments:
            for att in attachments:
                name = att.get('name') or att.get('original_filename') or 'file'
                att_type = att.get('type') or ''
                attachment_lines.append(f"- {name}" + (f" ({att_type})" if att_type else ""))
        else:
            meta_list = session.get('file_metadata_list') or []
            for meta in meta_list:
                name = meta.get('original_filename') or meta.get('filename')
                if name:
                    att_type = meta.get('mime_type') or ''
                    attachment_lines.append(f"- {name}" + (f" ({att_type})" if att_type else ""))

        user_attachment_records = build_user_attachment_records(user_prompt, attachments)

        attachment_context = ""
        if attachment_lines:
            attachment_context = "Files attached or recently uploaded:\n" + "\n".join(attachment_lines)
            extracted_text = f"{attachment_context}\n\n{extracted_text}".strip()

        # RAG: Retrieve relevant file chunks if files exist in conversation
        has_files = bool(attachment_lines) or session_data.get('has_files', False)
        # Always try RAG retrieval when files exist (not just on explicit file mentions)
        # This allows follow-up questions like "summarize this" to work
        if has_files or _detect_file_intent(user_prompt, has_files=has_files):
            file_chunk_context = _get_file_chunk_context(
                user_id=user_id,
                query=user_prompt,
                conversation_id=session_id,
                top_k=8  # Increase for better context on follow-ups
            )
            if file_chunk_context:
                # Prepend file chunks to extracted text (higher priority than full text)
                extracted_text = f"{file_chunk_context}\n\n{extracted_text}".strip()
                logger.info(f"Added {len(file_chunk_context)} chars of file chunk context")

        # Add URL-fetched content to extracted_text
        if url_content:
            extracted_text = f"{extracted_text}\n\n{url_content}".strip()
            logger.info(f"Added {len(url_content)} chars of URL content")

        response_message_id = f"msg_{uuid.uuid4().hex}"

        # Build the prompt (skip context for stateless questions)
        try:
            # Check if there's existing conversation history
            # If we have history, treat as stateful even if message pattern looks "stateless"
            # because short follow-ups like "yes", "maintenance" ARE continuations
            has_conversation_history = len(previous_messages) > 1

            # For stateless questions WITHOUT history, skip session summary, relevant messages, pending steps
            if is_stateless and not has_conversation_history:
                session_summary_text = ""
                relevant_msgs = []
                pending_steps_list = None
                prev_msgs_for_prompt = previous_messages[-1:] if previous_messages else []
                logger.debug(f"Stateless mode (no history): limiting to 1 message")
            else:
                # Has conversation history OR is explicitly stateful - use full context
                session_summary_text = session_data.get('session_summary', '')
                relevant_msgs = _select_relevant_messages(user_prompt, previous_messages)
                pending_steps_list = session_data.get('pending_steps')
                prev_msgs_for_prompt = previous_messages
                if has_conversation_history and is_stateless:
                    logger.debug(f"Overriding stateless - has {len(previous_messages)} messages of history")

            # Increase token budget when there's file content to analyze
            prompt_max_tokens = 16000 if extracted_text else 4096
            is_recipe_request = False  # Default, updated below if recipe intent detected

            # Claude-inspired: Inject current artifact context when user asks to fix/update
            artifact_context = None
            if detect_artifact_update_intent(user_prompt):
                logger.info(f"🔧 Artifact update intent detected: '{user_prompt[:50]}...'")
                artifact_context = load_current_artifact_context(user_id, session_id)
                if artifact_context:
                    logger.info(f"📦 Injected {len(artifact_context)} chars of artifact context")
                    # Increase token budget for artifact context
                    prompt_max_tokens = max(prompt_max_tokens, 24000)
                else:
                    logger.debug("No active artifact found to inject")

            # Intent-based format enforcement for recipes
            # Classify intent and inject focused format reminder when recipe is detected
            user_intent = classify_user_intent(user_prompt)
            is_recipe_request = (
                user_intent.get('intent_type') == 'recipe_request' and
                user_intent.get('confidence', 0) > 0.5
            )

            # Preserve the user-facing prompt before any system injections
            display_prompt = user_prompt

            if is_recipe_request:
                recipe_format_reminder = (
                    "[SYSTEM: Recipe response format required]\n"
                    "When providing a recipe, you MUST:\n"
                    "1. Start with ## followed by the ACTUAL recipe name (e.g., '## Chicken Stir Fry')\n"
                    "2. Add meta line: ⏱ 20 min | 🍽 4 servings | 🔥 Easy\n"
                    "3. Add '### Ingredients' header\n"
                    "4. List EVERY ingredient with '- ' bullet and quantity (e.g., '- 1 lb chicken breast')\n"
                    "5. Add '### Instructions' header\n"
                    "6. List numbered steps\n\n"
                    "CRITICAL: Your ### Ingredients section must list ALL ingredients with measurements.\n"
                    "Every item used in cooking (proteins, vegetables, oils, sauces, spices) needs its own '- ' line.\n"
                    "Do NOT skip ingredients. Do NOT put ingredients inside instruction steps only.\n\n"
                )
                user_prompt = recipe_format_reminder + "User request: " + user_prompt
                logger.info(f"🍳 Injected recipe format reminder for: {user_prompt[:80]}...")

            full_prompt = build_prompt(
                user_prompt=user_prompt,
                extracted_text=extracted_text,
                search_results=search_results,
                previous_messages=prev_msgs_for_prompt,
                user_prefs=user_preferences,
                session_prefs=session_preferences,
                session_summary=session_summary_text,
                relevant_messages=relevant_msgs,
                system_instructions=SYSTEM_INSTRUCTIONS,
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                long_term_memory=long_term_memory,
                pending_steps=pending_steps_list,
                canonical_context=artifact_context,  # Claude-style artifact injection
                encoding_name="cl100k_base",
                max_tokens=prompt_max_tokens,
                response_token_buffer=512
            )
        except TokenLimitError:
            logger.warning("Input exceeds maximum token limit.")
            return jsonify({'error': 'Input exceeds maximum token limit.'}), 400

        # Inject crypto/stock context ONLY when user explicitly asks for price/chart
        chart_query = detect_crypto_price_query(user_prompt)
        if chart_query:
            asset_type = chart_query.get('asset_type')
            if asset_type == 'crypto':
                coin = chart_query.get('coin')
                if coin:
                    crypto_context = get_crypto_ai_context(coin)
                    if crypto_context:
                        context_str = format_crypto_context_for_ai(crypto_context)
                        logger.info(f"💰 Injected crypto context for chart query: {coin}")
                        full_prompt = f"{context_str}\n\n{full_prompt}"
            elif asset_type == 'stock':
                symbol = chart_query.get('symbol')
                if symbol:
                    stock_context = get_stock_ai_context(symbol)
                    if stock_context:
                        context_str = format_stock_context_for_ai(stock_context)
                        logger.info(f"📈 Injected stock context for chart query: {symbol}")
                        full_prompt = f"{context_str}\n\n{full_prompt}"

        # Extract image URLs from attachments for vision API
        # Include both current attachments AND recent images from conversation history
        image_attachments = []
        seen_image_paths = set()  # Avoid duplicates

        # Enrich attachments with local_path from file_metadata_list (uploaded files)
        metadata_lookup = _build_metadata_lookup()
        for att in attachments:
            if att.get('isImage') and not att.get('local_path'):
                resolved = _resolve_user_attachment_source(att, metadata_lookup)
                if resolved and resolved.get('local_path'):
                    att['local_path'] = resolved['local_path']

        # Handle image files sent as multipart form fields (for vision API)
        image_files = request.files.getlist('image_files')
        for img_file in image_files:
            if img_file and img_file.content_type and img_file.content_type.startswith('image/'):
                try:
                    img_data = base64.b64encode(img_file.read()).decode('utf-8')
                    data_url = f"data:{img_file.content_type};base64,{img_data}"
                    # Find matching attachment and set dataUrl, or add new one
                    matched = False
                    for att in attachments:
                        if att.get('isImage') and att.get('name') == img_file.filename and not att.get('dataUrl'):
                            att['dataUrl'] = data_url
                            matched = True
                            break
                    if not matched:
                        attachments.append({
                            'name': img_file.filename,
                            'type': img_file.content_type,
                            'isImage': True,
                            'dataUrl': data_url,
                        })
                except Exception as e:
                    logger.warning(f"Failed to read image file {img_file.filename}: {e}")


        def extract_image_url(att: Dict[str, Any]) -> Optional[str]:
            """Extract and convert image attachment to data URL."""
            if not att.get('isImage'):
                return None

            # Try dataUrl first (base64), then previewUrl, then url, then local_path
            img_url = att.get('dataUrl') or att.get('previewUrl') or att.get('url') or att.get('local_path')
            if not img_url:
                return None

            # If it's a local path, convert to base64 data URL
            if img_url.startswith('/uploads/') or (not img_url.startswith('data:') and not img_url.startswith('http')):
                try:
                    # Normalize path
                    rel_path = img_url.replace('/uploads/', '') if img_url.startswith('/uploads/') else img_url
                    local_path = os.path.join(Config.UPLOAD_FOLDER, rel_path)

                    # Skip if already processed
                    if local_path in seen_image_paths:
                        return None
                    seen_image_paths.add(local_path)

                    if os.path.exists(local_path):
                        mime_type, _ = mimetypes.guess_type(local_path)
                        if mime_type and mime_type.startswith('image/'):
                            with open(local_path, 'rb') as f:
                                img_data = base64.b64encode(f.read()).decode('utf-8')
                                return f"data:{mime_type};base64,{img_data}"
                except Exception as e:
                    logger.warning(f"Failed to load image for vision: {e}")
                return None
            elif img_url.startswith('data:image/'):
                return img_url
            elif img_url.startswith('http'):
                return img_url
            return None

        # 1. Current message attachments (highest priority)
        for att in attachments:
            img_url = extract_image_url(att)
            if img_url:
                image_attachments.append({'url': img_url})
                logger.info(f"Added current attachment image for vision")

        # 2. Recent conversation history images (last 5 messages with images, max 3 images total)
        MAX_HISTORY_IMAGES = 3
        if len(image_attachments) < MAX_HISTORY_IMAGES:
            for msg in reversed(previous_messages[-10:]):  # Check last 10 messages
                if len(image_attachments) >= MAX_HISTORY_IMAGES:
                    break

                # Check user_attachments field
                for att in msg.get('user_attachments', []):
                    if len(image_attachments) >= MAX_HISTORY_IMAGES:
                        break
                    img_url = extract_image_url(att)
                    if img_url:
                        image_attachments.append({'url': img_url})
                        logger.info(f"Added history image for vision context")

                # Also check attachments field
                for att in msg.get('attachments', []):
                    if len(image_attachments) >= MAX_HISTORY_IMAGES:
                        break
                    img_url = extract_image_url(att)
                    if img_url:
                        image_attachments.append({'url': img_url})
                        logger.info(f"Added history image for vision context")

        # Smart routing: determine optimal model based on task
        selected_task_type = None
        selected_model_id = None

        if smart_routing_enabled:
            has_images = bool(image_attachments)
            conversation_length = len(previous_messages) if previous_messages else 0

            routed_model, task_type = smart_route(
                message=user_prompt,
                user_selected_model=None,  # Auto mode
                has_images=has_images,
                conversation_length=conversation_length,
                previous_response=None,
                conversation_history=previous_messages,  # Pass history for context detection
            )
            model = routed_model
            selected_task_type = task_type
            selected_model_id = model
            logger.info(f"🔀 Smart routing: {task_type} -> {model}")
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)

        # Get AI response (with images if present)
        raw_model_response, provider_flagged_code, reasoning_content = get_ai_response(
            full_prompt, model, images=image_attachments if image_attachments else None
        )

        # Check for refusal and retry with uncensored model if needed
        refusal_detected = is_refusal_response(raw_model_response)
        logger.info(f"🔍 Refusal check: smart_routing={smart_routing_enabled}, refusal_detected={refusal_detected}")
        if smart_routing_enabled and refusal_detected:
            uncensored_model = get_uncensored_model()
            logger.info(f"🔄 Detected refusal, retrying with {uncensored_model}")
            raw_model_response, provider_flagged_code, reasoning_content = get_ai_response(
                full_prompt, uncensored_model, images=image_attachments if image_attachments else None
            )
            model = uncensored_model

        # Strip HTML tags that some models (like Llama) may output despite instructions
        raw_model_response = strip_html_from_response(raw_model_response)

        if not raw_model_response.strip():
            logger.error(f"AI model returned an empty response for user {user_id}.")
            return jsonify({'error': 'Empty response from AI model.'}), 500

        # Validate response for duplicates and contradictions (embedding-based)
        response_validation = None
        try:
            response_validation = smart_validate_response(
                user_id, session_id, raw_model_response, previous_messages
            )
            if not response_validation.is_valid:
                logger.warning(f"Response validation failed: {response_validation.issues}")
                if response_validation.is_duplicate:
                    # Response is too similar to previous - ask LLM to expand differently
                    logger.info(f"Duplicate detected ({response_validation.similarity_score:.0%} similar)")
                    # Append instruction to avoid repetition
                    expansion_prompt = (
                        f"{full_prompt}\n\n"
                        "[SYSTEM: Your previous response was too similar. "
                        "Please provide NEW information, different examples, or expand on a different aspect. "
                        "Do NOT repeat what you've already said.]"
                    )
                    raw_model_response, provider_flagged_code, _ = get_ai_response(expansion_prompt, model)
                    raw_model_response = strip_html_from_response(raw_model_response)
                    if not raw_model_response.strip():
                        raw_model_response = "I've already covered this topic. Would you like me to explore a different aspect or go deeper on a specific point?"
                if response_validation.contradictions:
                    for c in response_validation.contradictions:
                        logger.warning(f"Contradiction detected: {c['entity']} was '{c['old_value']}', now '{c['new_value']}'")
        except Exception as e:
            logger.debug(f"Response validation failed: {e}")

        structured_response = parse_structured_response(raw_model_response)
        routing_decision = artifact_router.route(
            structured_response,
            user_prompt,
            provider_flagged_code=provider_flagged_code
        )
        # Determine response text based on render mode
        chat_payload = routing_decision.chat_payload or ""
        remainder = structured_response.remainder or ""

        # For code_window mode: verify code actually exists
        has_code_anywhere = "```" in chat_payload or "```" in remainder or "```" in raw_model_response

        # Fallback: if AI said code_window but no code exists, switch to inline
        effective_render_mode = routing_decision.render_mode
        effective_contains_code = routing_decision.contains_code

        if routing_decision.render_mode == "code_window" and not has_code_anywhere:
            logger.warning("🔧 AI specified code_window but no code fences found - falling back to inline")
            effective_render_mode = "inline"
            effective_contains_code = False
        elif routing_decision.render_mode == "artifact" and not routing_decision.artifact_payload:
            logger.warning("🔧 AI specified artifact mode but no artifact payload - falling back to code_window")
            effective_render_mode = "code_window" if has_code_anywhere else "inline"
            effective_contains_code = has_code_anywhere
        elif routing_decision.render_mode == "code_window" and "```" not in chat_payload and "```" in remainder:
            # Use remainder if chat_payload doesn't have code fences but remainder does
            response_text = remainder.strip() or raw_model_response
            logger.info("🔧 Using remainder for code_window (chat_payload had no code fences)")

        if routing_decision.render_mode == "code_window" and has_code_anywhere:
            response_text = (remainder if "```" in remainder else chat_payload if "```" in chat_payload else raw_model_response).strip() or raw_model_response
        else:
            response_text = (chat_payload or remainder or raw_model_response).strip() or raw_model_response

        full_response_text = remainder or raw_model_response
        is_code_response = effective_contains_code

        # Extract and store any numbered lists from response (for pointer resolution)
        try:
            stored_list = process_ai_response_for_lists(
                user_id, session_id, response_text, response_message_id
            )
            if stored_list:
                logger.debug(f"Stored list with {len(stored_list.items)} items for pointer resolution")
        except Exception as e:
            logger.debug(f"List extraction failed: {e}")

        # Validate recipe response format
        if is_recipe_request and response_text:
            has_ingredients_section = '### Ingredients' in response_text or '**Ingredients' in response_text
            has_ingredient_bullets = bool(re.search(r'### Ingredients.*?^-\s+\S', response_text, re.MULTILINE | re.DOTALL))

            if has_ingredients_section and not has_ingredient_bullets:
                logger.warning(f"🍳 Recipe response has Ingredients header but NO bullet items!")
                # Log the ingredients section for debugging
                ing_match = re.search(r'(### Ingredients.*?)(?=###|$)', response_text, re.DOTALL)
                if ing_match:
                    logger.warning(f"🍳 Ingredients section content: {ing_match.group(1)[:200]}...")
            elif not has_ingredients_section:
                logger.warning(f"🍳 Recipe response missing ### Ingredients section entirely!")
            else:
                logger.info(f"🍳 Recipe format validation passed ✓")

        # Debug logging for artifact decision
        logger.info(f"🔍 ARTIFACT DEBUG: AI render_mode={routing_decision.render_mode}, effective={effective_render_mode}")
        logger.info(f"🔍 ARTIFACT DEBUG: AI contains_code={routing_decision.contains_code}, effective={effective_contains_code}")
        logger.info(f"🔍 ARTIFACT DEBUG: has_code_anywhere={has_code_anywhere}")
        logger.info(f"🔍 ARTIFACT DEBUG: meta={structured_response.meta}")
        logger.info(f"🔍 ARTIFACT DEBUG: response_text_preview={response_text[:200] if response_text else 'EMPTY'}...")

        artifact_payload = None
        artifact_reference = None
        artifact_status = None
        if routing_decision.render_mode != "inline" and routing_decision.artifact_payload:
            draft_payload = routing_decision.artifact_payload.copy()
            persisted_artifact = None
            logger.info(f"🎨 Artifact detected! Mode: {routing_decision.render_mode}, Action: {routing_decision.desired_action}")
            if firestore_available:
                try:
                    logger.info(f"🔄 Calling persist_decision for session {session_id}")
                    persisted_artifact = artifact_store.persist_decision(
                        user_id=user_id,
                        session_id=session_id,
                        decision=routing_decision,
                        source_message_id=response_message_id,
                        created_by="assistant"
                    )
                    if persisted_artifact:
                        logger.info(f"✅ Artifact persisted: {persisted_artifact.get('artifactId')} v{persisted_artifact.get('versionCount', 1)}")
                    else:
                        logger.warning("⚠️ persist_decision returned None")
                except Exception as exc:
                    logger.error(f"❌ Failed to persist artifact: {exc}", exc_info=True)
                    logger.warning("Failed to persist artifact for user %s session %s: %s", user_id, session_id, exc)
                    artifact_status = 'persist_failed'
                    if not offline_reason:
                        offline_reason = 'artifact_persist_failed'
            else:
                artifact_status = 'pending_persist'
                if not offline_reason:
                    offline_reason = 'artifact_persist_deferred'

            if persisted_artifact:
                artifact_payload = persisted_artifact
            else:
                artifact_payload = build_draft_artifact(draft_payload, response_message_id)

            is_pending = artifact_status in {'persist_failed', 'pending_persist', 'artifact_persist_failed', 'artifact_persist_deferred'}
            artifact_reference = build_artifact_reference_payload(artifact_payload, response_text, is_pending)
            artifact_payload['pending'] = is_pending

        sentiment = 'neutral'

        # Check if user asked for crypto/stock price/chart (needed for save_interaction)
        crypto_chart = detect_crypto_price_query(user_prompt)
        if crypto_chart:
            asset_type = crypto_chart.get('asset_type', 'crypto')
            asset = crypto_chart.get('symbol') if asset_type == 'stock' else crypto_chart.get('coin')
            logger.info(f"{asset_type.upper()} chart detected for {asset}")

        # Guest mode: store in session, skip all Firestore operations
        if guest_mode:
            title = "Guest Chat"
            # Store message in Flask session
            guest_message = {
                'prompt': user_prompt,
                'response': response_text,
                'timestamp': datetime.utcnow().isoformat()
            }
            session['guest_messages'].append(guest_message)
            # Increment guest message count (both session and Firebase)
            session['guest']['message_count'] = session['guest'].get('message_count', 0) + 1
            session.modified = True

            # Increment Firebase IP-based counter (prevents cookie clearing bypass)
            ip_hash = session.get('_guest_ip_hash')
            if ip_hash:
                increment_guest_ip_usage(ip_hash)
            chat_data = guest_message
            persisted = False
        else:
            # Update session title if missing
            if firestore_available:
                try:
                    title = update_session_title_if_missing(user_id, session_id, session_data, response_text, user_prompt)
                except Exception as e:
                    firestore_available = False
                    if not offline_reason:
                        offline_reason = 'title_update_failed'
                    logger.warning(f"Failed to update session title for user {user_id}: {e}")
                    title = session_data.get('session_name') or "Offline Session"
            else:
                title = session_data.get('session_name') or "Offline Session"

            # Save this interaction (offline cache if persistence fails)
            chat_data, persisted = save_interaction(
                user_id=user_id,
                session_id=session_id,
                user_prompt=user_prompt,
                response_text=response_text,
                sentiment=sentiment,
                is_code_response=is_code_response,
                search_results=search_results,
                user_attachments=user_attachment_records,
                persist=firestore_available,
                response_id=response_message_id,
                render_mode=effective_render_mode,
                artifact_reference=artifact_reference,
                reasoning=reasoning_content,
                crypto_chart=crypto_chart,
                display_prompt=display_prompt,
                model_id=selected_model_id or model,
                task_type=selected_task_type,
                auto_selected=bool(smart_routing_enabled) or (session.get('selected_model') == AUTO_MODEL),
            )
            if artifact_payload and not persisted:
                chat_data['artifact_draft'] = artifact_payload

            if persisted and firestore_available:
                _clear_offline_cache()
            else:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'persistence_failed'
                _cache_offline_interaction(chat_data, title)

            # Tag response for SmartGuard continuation detection
            # This enables "other one", "again", "next" to work naturally
            try:
                smart_tag_response(user_id, session_id, response_text, user_prompt)
            except Exception as e:
                logger.debug(f"Failed to tag response for SmartGuard: {e}")

        # Skip all post-processing for guests
        if not guest_mode:
            if resolved_page_id and firestore_available:
                try:
                    db.collection('users').document(user_id).collection('chat_sessions').document(session_id).set(
                        {'active_page_id': resolved_page_id},
                        merge=True
                    )
                except Exception as exc:
                    logger.warning(f"Failed to update active page for session {session_id}: {exc}")

            # Update rolling session summary when Firestore is reachable
            if firestore_available:
                try:
                    _update_session_summary(user_id, session_id, session_data, user_prompt, response_text)
                except Exception as e:
                    firestore_available = False
                    if not offline_reason:
                        offline_reason = 'summary_update_failed'
                    logger.warning(f"Failed to update session summary for user {user_id}: {e}")

            # Track credit usage only when persistence succeeded
            credit_cost = _calculate_credit_cost(user_prompt, full_response_text)
            if firestore_available:
                try:
                    _increment_user_credits(user_id, credit_cost)
                except Exception as e:
                    logger.warning(f"Failed to increment credits for user {user_id}: {e}")

        # Process reflection + memory in parallel (skip for stateless questions and guests)
        memory_updated = False
        next_steps = []

        # Skip reflection and memory writing for stateless questions
        skip_reflection = should_skip_reflection(user_prompt)
        skip_memory_write = should_skip_memory_write(user_prompt, response_text)
        logger.info(f"🧠 Memory V2: skip_reflection={skip_reflection}, skip_memory_write={skip_memory_write}, guest={guest_mode}, firestore={firestore_available}")

        if not guest_mode and firestore_available and not (skip_reflection and skip_memory_write):
            from .reflection import reflect_on_response, get_memory_candidates, get_next_steps_for_session

            try:
                # Run reflection and memory V2 post_turn in parallel (conditionally)
                reflection_future = None
                memory_future = None

                if not skip_reflection:
                    reflection_future = executor.submit(
                        reflect_on_response,
                        user_prompt,
                        response_text
                    )

                # ============================================================
                # Memory V2: Post-turn processing (extraction + storage)
                # ============================================================
                logger.info(f"🧠 Memory V2: Checking post_turn - skip_memory_write={skip_memory_write}, orchestrator={memory_orchestrator is not None}")
                if not skip_memory_write and memory_orchestrator is not None:
                    logger.info(f"🧠 Memory V2: Running post_turn extraction...")
                    # Build conversation state for post_turn
                    conv_state_for_write = ConversationState(
                        pending_confirmation_ids=session.get('_memory_pending_ids', []),
                        last_retrieved_ids=session.get('_memory_last_ids', []),
                        messages_in_session=len(previous_messages)
                    )
                    memory_future = executor.submit(
                        memory_orchestrator.post_turn,
                        user_prompt,
                        response_text,
                        conv_state_for_write,
                        memory_eval_metadata
                    )
                elif skip_memory_write:
                    logger.debug("Skipping memory write - stateless question")
                # ============================================================

                # Gather results (with timeouts)
                reflection_result = None
                if reflection_future:
                    try:
                        reflection_result = reflection_future.result(timeout=4)
                    except Exception:
                        pass

                memory_write_result = None
                if memory_future:
                    try:
                        memory_write_result = memory_future.result(timeout=4)
                        # Memory V2 returns MemoryWriteResult - check if anything was actually stored
                        memory_updated = (
                            memory_write_result.stored > 0 or
                            memory_write_result.promoted > 0 or
                            memory_write_result.superseded > 0
                        )
                        # Update session state for next turn
                        if memory_write_result.new_pending_confirmation_ids:
                            session['_memory_pending_ids'] = memory_write_result.new_pending_confirmation_ids
                        logger.info(f"🧠 Memory V2: stored={memory_write_result.stored}, promoted={memory_write_result.promoted}, superseded={memory_write_result.superseded}")
                    except Exception as e:
                        logger.error(f"🧠 Memory V2 post_turn FAILED: {e}", exc_info=True)

                # Process reflection results
                if reflection_result and reflection_result.has_actionable_items():
                    # NOTE: Reflection memory candidates are now handled by Memory V2's
                    # extraction in post_turn(). The old MemoryGate path is deprecated.
                    # If reflection finds unique memories not caught by extraction,
                    # consider routing them through orchestrator.add_external_candidates().

                    # Store next steps in session if any
                    next_steps = get_next_steps_for_session(reflection_result) or []
                    if next_steps and session_id:
                        try:
                            db.collection('users').document(user_id).collection('chat_sessions').document(session_id).update({
                                'pending_steps': next_steps
                            })
                        except Exception:
                            pass

            except Exception as e:
                logger.debug(f"Reflection/memory processing error: {e}")

        # Prepare and return response
        cached_unsynced = 0
        offline_active = False
        if not firestore_available:
            offline_messages = session.get('offline_messages')
            if isinstance(offline_messages, list):
                cached_unsynced = len(offline_messages)
            if not offline_reason:
                offline_reason = 'unknown'
            offline_active = cached_unsynced > 0

        # Check if response looks like an email (has greeting + sign-off)
        email_data = None
        is_email = looks_like_email(response_text)
        if is_email:
            email_data = parse_email_from_response(response_text)
            logger.info(f"Email detected, parsed: subject='{email_data.get('subject', '')[:30]}...'")

        response_data = {
            'response': response_text,
            'is_code': is_code_response,
            'sentiment': sentiment,
            'tokenized_response': tokenize_sentences(response_text) if not is_code_response else [],
            'search_results': [result.to_dict() for result in search_results],
            'render_mode': effective_render_mode,
            'artifact': artifact_payload,
            'artifact_reference': artifact_reference,
            'artifact_status': artifact_status,
            'artifact_error': routing_decision.error_message,  # User-friendly error message
            'message_id': response_message_id,
            'chat_summary': response_text,
            'raw_response': full_response_text,
            'session_id': session_id,
            'session_name': title,
            'active_page_id': resolved_page_id,
            'offline_mode': offline_active,
            'offline_reason': offline_reason if offline_active else None,
            'offline_cached_messages': cached_unsynced,
            'memory_updated': memory_updated,
            'next_steps': next_steps if next_steps else None,
            # Guest mode info
            'guest_mode': guest_mode,
            'guest_message_count': get_guest_message_count() if guest_mode else None,
            'guest_message_limit': GUEST_MESSAGE_LIMIT if guest_mode else None,
            'guest_show_banner': get_guest_message_count() >= GUEST_BANNER_THRESHOLD if guest_mode else False,
            # DeepSeek R1 reasoning content (chain-of-thought)
            'reasoning': reasoning_content if reasoning_content else None,
            # Email composition data
            'is_email': is_email,
            'email': email_data,
            # Crypto chart data
            'crypto_chart': crypto_chart
        }

        # Include per-message model provenance when available (for Auto details UI)
        if isinstance(chat_data, dict):
            for key in (
                'auto_selected',
                'model_id',
                'model_label',
                'model_provider',
                'model_provider_label',
                'task_type',
            ):
                if key in chat_data:
                    response_data[key] = chat_data.get(key)

        return jsonify(response_data)

    except RequestEntityTooLarge:
        logger.error("Request payload too large.")
        return jsonify({'error': 'Payload too large. Maximum allowed size is 16 MB.'}), 413
    except KeyError as e:
        logger.error(f"Missing key in data: {str(e)}")
        return jsonify({'error': f"Missing key: {str(e)}"}), 400
    except ModelProviderError:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error at /chat endpoint for user {session.get('user', {}).get('uid', 'unknown')}: {e}",
            exc_info=True
        )
        return jsonify({'error': 'An unexpected error occurred.'}), 500


# Artifact routes moved to app/blueprints/artifacts_routes.py
# Session routes moved to app/blueprints/session_routes.py
# Memory routes moved to app/blueprints/memory_routes.py


@app_routes.errorhandler(404)
def not_found_error(e):
    app.logger.warning("404 error occurred.")
    return render_template('404.html'), 404


# Routes moved to app/blueprints/:
# - Index (/) -> pages_routes.py
# - Open shared session (/c/<session_id>) -> pages_routes.py
# - More options (/more-options) -> pages_routes.py
# - Account manage (/account/manage) -> account_routes.py
# - Account API keys (/account/api-keys) -> account_routes.py
# - Upgrade (/upgrade) -> pages_routes.py
# - Favicon (/favicon.ico) -> pages_routes.py
# - Debug (/debug) -> pages_routes.py
# - Email routes -> email_routes.py

def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = os.urandom(24)

    app.register_blueprint(app_routes)
    app.register_blueprint(auth_routes)

    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_FILE_DIR'] = os.path.join(app.instance_path, 'sessions')
    os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)
    Session(app)

    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=Config.DEBUG_MODE)
