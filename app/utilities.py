import os
import logging
import requests
import nltk
from nltk.tokenize import sent_tokenize
from PIL import Image
import pytesseract
import PyPDF2
import re
from .config import Config
from .model_catalog import MODEL_PROVIDERS, resolve_model_id, DEFAULT_MODEL, FALLBACK_MODELS
from .artifacts import contains_code_block
import tiktoken
from datetime import datetime, timedelta
from typing import List, Set, Dict
from flask import g as flask_g, has_request_context

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NLTKSetup:
    """Utility class to manage NLTK resource setup and verification."""
    
    RESOURCE_PATHS = {
        'vader_lexicon': 'sentiment/vader_lexicon',
        'punkt': 'tokenizers/punkt',
        'punkt_tab': 'tokenizers/punkt_tab',
    }
    
    def __init__(self, base_dir: str = None, required_resources: List[str] = None):
        """
        Initialize NLTK setup utility.
        
        Args:
            base_dir: Base directory for NLTK data. If None, uses the parent directory of current file
            required_resources: List of required NLTK resources. If None, uses default set
        """
        self.logger = logging.getLogger(__name__)
        self.base_dir = base_dir or os.path.dirname(__file__)
        self.nltk_data_dir = os.path.abspath(os.path.join(self.base_dir, '..', 'nltk_data'))
        default_resources = ['punkt', 'punkt_tab', 'vader_lexicon']
        self.required_resources = set(required_resources or default_resources)
        
    def setup_data_directory(self) -> None:
        """Create NLTK data directory if it doesn't exist and add to NLTK path."""
        try:
            os.makedirs(self.nltk_data_dir, exist_ok=True)
            
            # Add to NLTK path if not already present
            if self.nltk_data_dir not in nltk.data.path:
                nltk.data.path.append(self.nltk_data_dir)
                self.logger.info(f"Added NLTK data directory: {self.nltk_data_dir}")
            
            self.logger.debug(f"NLTK data paths: {nltk.data.path}")
            
        except Exception as e:
            self.logger.error(f"Failed to setup NLTK data directory: {str(e)}")
            raise
            
    def verify_resource(self, resource: str) -> bool:
        """
        Verify if a specific NLTK resource is available.
        
        Args:
            resource: Name of the NLTK resource to verify
            
        Returns:
            bool: True if resource is available, False otherwise
        """
        try:
            resource_path = self.RESOURCE_PATHS.get(resource)
            if not resource_path:
                # Fallback: assume tokenizer resource
                resource_path = f'tokenizers/{resource}'
            nltk.data.find(resource_path)
            return True
        except LookupError:
            return False
            
    def download_missing_resources(self) -> Set[str]:
        """
        Download any missing required NLTK resources.
        
        Returns:
            Set[str]: Set of resources that were downloaded
        """
        downloaded = set()
        
        for resource in self.required_resources:
            if not self.verify_resource(resource):
                try:
                    self.logger.info(f"Downloading NLTK resource: {resource}")
                    nltk.download(resource, download_dir=self.nltk_data_dir, quiet=True)
                    downloaded.add(resource)
                except Exception as e:
                    self.logger.error(f"Failed to download resource {resource}: {str(e)}")
                    raise
                    
        return downloaded
        
    def verify_all_resources(self) -> bool:
        """
        Verify all required resources are available.
        
        Returns:
            bool: True if all resources are available, False otherwise
        """
        missing = set()
        
        for resource in self.required_resources:
            if not self.verify_resource(resource):
                missing.add(resource)
                
        if missing:
            self.logger.warning(f"Missing NLTK resources: {missing}")
            return False
            
        self.logger.info("All required NLTK resources are available")
        return True
        
    def setup(self) -> bool:
        """
        Perform complete NLTK setup including directory creation and resource downloads.
        
        Returns:
            bool: True if setup completed successfully, False otherwise
        """
        try:
            # Setup data directory
            self.setup_data_directory()
            
            # Download any missing resources
            downloaded = self.download_missing_resources()
            if downloaded:
                self.logger.info(f"Downloaded resources: {downloaded}")
                
            # Verify everything is ready
            return self.verify_all_resources()
            
        except Exception as e:
            self.logger.error(f"NLTK setup failed: {str(e)}")
            return False


def _strong_image_rules(user_input: str) -> bool:
    """Fast path: obvious image intent via commands or strong keywords."""
    if not user_input:
        return False
    text = user_input.strip().lower()
    # Explicitly avoid when user asks for a prompt (crafting text, not the image)
    prompt_guard_patterns = [
        "prompt for an image",
        "image prompt",
        "prompt for a picture",
        "prompt for art",
        "write a prompt",
        "create a prompt",
        "create me a prompt",
        "generate a prompt",
    ]
    if any(pat in text for pat in prompt_guard_patterns):
        return False
    # Explicitly avoid artifact-related requests (from composer panel)
    artifact_guard_patterns = [
        "update the artifact",
        "revise the artifact",
        "edit the artifact",
        "```artifact",
        "artifact block",
        "current artifact code",
        "complete updated artifact",
        "current code to revise",
        "please provide the complete",
    ]
    if any(pat in text for pat in artifact_guard_patterns):
        return False
    # Command prefixes
    if text.startswith('/image') or text.startswith('img:') or text.startswith('/img'):
        return True

    verbs = ["draw", "render", "create", "generate", "illustrate", "visualize", "paint", "make"]
    nouns = ["image", "picture", "photo", "art", "visual", "illustration", "rendering"]
    modifiers = ["4k", "high res", "photorealistic", "cartoon", "anime", "concept art", "digital painting"]

    has_verb = any(v in text for v in verbs)
    has_noun = any(n in text for n in nouns)
    has_mod = any(m in text for m in modifiers)

    # Pattern: "picture of X", "image of X", "photo of X" (noun phrase without verb)
    noun_phrase_pattern = r'\b(picture|image|photo|illustration|art)\s+(of|showing|depicting|with)\s+\w+'
    if re.search(noun_phrase_pattern, text):
        return True

    return (has_verb and has_noun) or (has_verb and has_mod)


def classify_image_intent_llm(user_input: str, chat_history: list = None, model: str = None) -> bool:
    """
    AI-powered intent classifier with high accuracy (~99%).
    Analyzes user input AND chat history context to determine if user wants an image generated.
    """
    if not user_input:
        return False

    try:
        chosen_model = model or getattr(Config, "INTENT_MODEL", None) or "llama-3.3-70b-versatile"

        # Build context from chat history (last 3 messages for context)
        history_context = ""
        if chat_history and isinstance(chat_history, list):
            recent = chat_history[-3:] if len(chat_history) > 3 else chat_history
            history_lines = []
            for msg in recent:
                role = msg.get('role', 'user')
                content = msg.get('content', msg.get('prompt', ''))[:150]
                if content:
                    history_lines.append(f"{role}: {content}")
            if history_lines:
                history_context = "Recent conversation:\n" + "\n".join(history_lines) + "\n\n"

        prompt = f"""You are a precise intent classifier. Determine if the user wants to GENERATE an actual image file.

{history_context}Current message: "{user_input}"

NOT IMAGE (return TEXT):
- "write a prompt for an image" → wants prompt text, not image
- "explain how to create images" → wants explanation
- "picture this scenario" → figurative language
- "draw a conclusion" → figurative
- "create a function/app/code" → code request
- "update the artifact" → code request
- "the image shows..." → talking about existing image
- "in the picture I see..." → describing existing image

IMAGE (return IMAGE):
- "generate an image of X" → wants image
- "draw me a cat" → wants image
- "create a picture of mountains" → wants image
- "/image sunset" → wants image
- "picture of a sunset" → wants image (simple noun phrase = generation request)
- "image of a dog" → wants image
- "photo of a beach" → wants image
- "sunset picture" → wants image
- Short descriptions like "a cat sitting" → likely wants image

RULE: If the message is just describing something visual (e.g., "picture of X", "image of Y", "a beautiful sunset"), assume the user wants an image generated.

Reply with ONLY: IMAGE or TEXT"""

        resp, _ = send_prompt(prompt, model=chosen_model)

        if isinstance(resp, dict) and resp.get("error"):
            return False
        if not isinstance(resp, str):
            return False

        answer = resp.strip().upper().split()[0] if resp.strip() else "TEXT"
        is_image = answer == "IMAGE"

        if is_image:
            logger.info(f"AI classified as IMAGE: '{user_input[:50]}...'")

        return is_image

    except Exception as e:
        logger.warning(f"LLM intent check failed: {e}")
        return False


def is_image_request(user_input: str, chat_history: list = None) -> bool:
    """
    High-accuracy image request detection (~99%).

    Strategy:
    1) Quick rejection of obvious non-image requests
    2) Fast path for explicit commands (/image, img:)
    3) AI analysis with chat context for ambiguous cases
    """
    if not user_input:
        return False

    text = user_input.strip().lower()

    # === QUICK REJECTION: Definitely NOT image requests ===
    rejection_patterns = [
        # Code/programming
        "write code", "write a function", "create a function", "build an app",
        "make an app", "implement", "```", "function(", "def ", "class ",
        # Prompt crafting
        "prompt for", "write a prompt", "create a prompt", "image prompt",
        # Artifacts
        "artifact", "update the", "revise the", "edit the",
        # Figurative language
        "picture this", "draw a conclusion", "draw attention",
        # Existing images
        "can't see the image", "the image shows", "in this image",
        # Explanations
        "explain how", "how to create", "how do i", "tell me about",
    ]

    if any(pat in text for pat in rejection_patterns):
        return False

    # === FAST PATH: Explicit commands ===
    if text.startswith('/image ') or text.startswith('img:') or text.startswith('/img '):
        return True

    # === STRONG RULES + AI VERIFICATION ===
    if _strong_image_rules(user_input):
        return classify_image_intent_llm(user_input, chat_history)

    # === AI FALLBACK: Only if there's a hint ===
    image_hints = ["image", "picture", "photo", "draw", "render", "visual",
                   "illustration", "generate", "create"]

    if any(hint in text for hint in image_hints):
        return classify_image_intent_llm(user_input, chat_history)

    return False


def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Count the number of tokens, allowing `<|endoftext|>` as a special token."""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text, allowed_special={'<|endoftext|>'}))


def is_code(content):
    """
    Lightweight code detection used only for formatting decisions.
    Shares the same heuristics as the artifact router to avoid mismatches.
    """
    if not content or not isinstance(content, str):
        return False

    candidate = content.strip()
    if not candidate:
        return False

    if contains_code_block(candidate):
        return True

    lines = [line for line in candidate.splitlines() if line.strip()]
    if not lines:
        return False

    keyword_pattern = re.compile(
        r'\b(def|class|function|fn|return|import|console\.log|var|let|const|SELECT|INSERT|UPDATE|DELETE)\b',
        re.IGNORECASE
    )
    keyword_hits = sum(1 for line in lines if keyword_pattern.search(line))
    if keyword_hits >= max(3, len(lines) // 2):
        return True

    symbol_lines = 0
    for line in lines:
        stripped = line.strip()
        symbols = sum(1 for ch in stripped if ch in "{}[]();=<>")
        if symbols >= 3:
            symbol_lines += 1
    return symbol_lines >= 3


def categorize_sessions(sessions):
    """
    Categorize sessions by LAST INTERACTION time (last_updated), not creation time.
    Sessions are sorted within each category by most recent first.
    """
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    last_7_days = today - timedelta(days=7)

    categorized_sessions = {
        "Today": [],
        "Yesterday": [],
        "Last 7 Days": [],
        "Older": []
    }

    for session in sessions:
        # Skip any session that is not a dictionary
        if not isinstance(session, dict):
            print("Skipping invalid session:", session)  # Debug print
            continue

        # Use last_updated for categorization (fallback to created_at)
        activity_str = session.get("last_updated") or session.get("created_at")
        if not activity_str:
            continue  # Skip if no timestamp available

        # Convert to date
        try:
            activity_date = datetime.fromisoformat(activity_str).date()
        except ValueError:
            print("Invalid date format for session:", session)  # Debug print
            continue

        # Categorize based on last activity date
        if activity_date == today:
            categorized_sessions["Today"].append(session)
        elif activity_date == yesterday:
            categorized_sessions["Yesterday"].append(session)
        elif activity_date >= last_7_days:
            categorized_sessions["Last 7 Days"].append(session)
        else:
            categorized_sessions["Older"].append(session)

    # Sort each category by last_updated (most recent first)
    def sort_key(s):
        ts = s.get("last_updated") or s.get("created_at") or ""
        return ts

    for category in categorized_sessions:
        categorized_sessions[category].sort(key=sort_key, reverse=True)

    return categorized_sessions


def _model_error_payload(message: str, code: str, **details) -> dict:
    """
    Build a consistent error payload for downstream callers.
    """
    payload = {'error': message, 'error_code': code}
    payload.update({k: v for k, v in details.items() if v is not None})
    return payload


def _get_provider_config(provider: str, resolved_model: str, api_overrides: Dict[str, str]):
    """
    Get API configuration for a provider.
    Returns (url, headers, timeout, provider_label) or raises ValueError if not configured.
    """
    provider_labels = {"openrouter": "OpenRouter", "deepseek": "DeepSeek", "groq": "Groq", "cerebras": "Cerebras", "moonshot": "Moonshot"}
    provider_label = provider_labels.get(provider, "Groq")

    if provider == "openrouter":
        effective_key = (api_overrides.get('openrouter') if api_overrides else None) or Config.OPENROUTER_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.OPENROUTER_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }
        if Config.OPENROUTER_SITE_URL:
            headers["HTTP-Referer"] = Config.OPENROUTER_SITE_URL
        if Config.OPENROUTER_APP_NAME:
            headers["X-Title"] = Config.OPENROUTER_APP_NAME
        timeout = 30
    elif provider == "deepseek":
        effective_key = (api_overrides.get('deepseek') if api_overrides else None) or Config.DEEPSEEK_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.DEEPSEEK_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 60  # DeepSeek R1 needs longer for reasoning
    elif provider == "cerebras":
        effective_key = (api_overrides.get('cerebras') if api_overrides else None) or Config.CEREBRAS_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.CEREBRAS_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 15
    elif provider == "moonshot":
        effective_key = (api_overrides.get('moonshot') if api_overrides else None) or Config.MOONSHOT_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.MOONSHOT_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 60  # Kimi models may need longer for reasoning
    else:  # groq
        effective_key = (api_overrides.get('groq') if api_overrides else None) or Config.GROQ_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 15

    return url, headers, timeout, provider_label


def _execute_prompt_request(prompt: str, resolved_model: str, provider: str, api_overrides: Dict[str, str]):
    """
    Execute a single prompt request to the specified provider.
    Returns (response_content, is_code, error_payload, status_code).
    error_payload is None on success.
    """
    try:
        url, headers, timeout, provider_label = _get_provider_config(provider, resolved_model, api_overrides)
    except ValueError as e:
        logger.error(str(e))
        return None, False, _model_error_payload(str(e), 'missing_api_key', provider=provider), None

    # Core system instruction for output formatting
    system_instruction = (
        "You are a helpful AI assistant. "
        "CRITICAL: NEVER output HTML tags like <p>, <div>, <span>, <br>, <h1>, etc. in your responses. "
        "Use plain text and Markdown formatting ONLY. No HTML whatsoever."
    )

    # Some reasoning models (e.g. Kimi K2.5) only accept temperature=1
    temperature = 1.0 if resolved_model in ("kimi-k2.5",) else 0.7

    data = {
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "model": resolved_model,
        "max_tokens": 2048,
        "temperature": temperature
    }

    logger.debug(f"Preparing to send prompt to {url} with model {resolved_model}")

    try:
        response = requests.post(url, json=data, headers=headers, timeout=timeout)
        status_code = response.status_code

        # Check for rate limit before raise_for_status
        if status_code == 429:
            logger.warning(f"{provider_label} rate limited (429)")
            return None, False, _model_error_payload('Rate limited', 'rate_limited', provider=provider_label), 429

        response.raise_for_status()

        model_response = response.json()
        logger.debug(f"AI model response received: {model_response}")

        if 'choices' in model_response and len(model_response['choices']) > 0:
            message = model_response['choices'][0].get('message', {})
            message_content = message.get('content', '').strip()
            reasoning_content = message.get('reasoning_content', '').strip()

            if not message_content:
                logger.error("AI model returned an empty or invalid content.")
                return None, False, _model_error_payload('AI model returned an empty response.', 'empty_response'), status_code

            # Return reasoning content if present (for DeepSeek R1)
            if reasoning_content:
                return {
                    'content': message_content,
                    'reasoning': reasoning_content,
                    'has_reasoning': True
                }, is_code(message_content), None, status_code

            return message_content, is_code(message_content), None, status_code
        else:
            logger.error(f"Unexpected API response format: {model_response}")
            return None, False, _model_error_payload('Unexpected response format from AI model.', 'unexpected_response'), status_code

    except requests.Timeout:
        logger.error("The request to the AI model API timed out.")
        return None, False, _model_error_payload('Request timed out', 'timeout', provider=provider), None

    except requests.exceptions.HTTPError as e:
        response_text = response.text if 'response' in locals() else ''
        status_code = response.status_code if 'response' in locals() else None
        logger.error(f"HTTP error occurred ({provider}): {str(e)} - Response: {response_text}")
        error_code = 'http_error'
        message = f"{provider} responded with HTTP error: {str(e)}"
        if status_code == 401 and response_text and 'invalid api key' in response_text.lower():
            error_code = 'invalid_api_key'
            message = f"{provider} rejected the configured API key. Please update it to restore service."
        return None, False, _model_error_payload(message, error_code, status_code=status_code, response_text=response_text, provider=provider), status_code

    except requests.RequestException as e:
        logger.error(f"Request to {provider} API failed: {str(e)}")
        return None, False, _model_error_payload(f"Request failed: {str(e)}", 'request_exception', provider=provider), None

    except ValueError as e:
        logger.error(f"Failed to parse {provider} response: {str(e)}")
        return None, False, _model_error_payload('Invalid response from AI model.', 'invalid_response', provider=provider), None


def send_prompt(prompt, model="llama-3.1-70b-versatile"):
    """
    Send a prompt to the specified AI model API and handle the response.
    Automatically falls back to Cerebras if Groq returns a rate limit (429) error.
    """
    resolved_model = resolve_model_id(model or DEFAULT_MODEL)
    provider = MODEL_PROVIDERS.get(resolved_model, "groq")

    api_overrides: Dict[str, str] = {}
    if has_request_context():
        overrides = getattr(flask_g, 'api_keys', None)
        if isinstance(overrides, dict):
            api_overrides = overrides

    # Try primary provider
    response_content, is_code_flag, error_payload, status_code = _execute_prompt_request(
        prompt, resolved_model, provider, api_overrides
    )

    # If successful, return
    if error_payload is None:
        return response_content, is_code_flag

    # If rate limited and we have a Cerebras fallback, try it
    if status_code == 429 and provider == "groq" and resolved_model in FALLBACK_MODELS:
        fallback_model = FALLBACK_MODELS[resolved_model]
        logger.info(f"Groq rate limited, falling back to Cerebras model: {fallback_model}")

        response_content, is_code_flag, error_payload, _ = _execute_prompt_request(
            prompt, fallback_model, "cerebras", api_overrides
        )

        if error_payload is None:
            logger.info(f"Cerebras fallback successful")
            return response_content, is_code_flag
        else:
            logger.warning(f"Cerebras fallback also failed: {error_payload}")

    # Return original error if no fallback or fallback failed
    return error_payload, False


def stream_deepseek_response(prompt: str, model: str = "deepseek-reasoner"):
    """
    Stream response from DeepSeek or Moonshot API with real-time content.

    Yields:
        dict with 'type' ('reasoning', 'content', 'done', 'error') and 'text' or 'data'
    """
    from .config import Config
    from .model_catalog import resolve_model_id, MODEL_PROVIDERS
    from flask import g as flask_g, has_request_context
    import json

    resolved_model = resolve_model_id(model)
    provider = MODEL_PROVIDERS.get(resolved_model, "groq")

    # Only DeepSeek and Moonshot models support streaming
    if provider not in ("deepseek", "moonshot"):
        yield {'type': 'error', 'text': 'Streaming only available for DeepSeek and Moonshot models'}
        return

    api_overrides = {}
    if has_request_context():
        overrides = getattr(flask_g, 'api_keys', None)
        if isinstance(overrides, dict):
            api_overrides = overrides

    try:
        url, headers, timeout, provider_label = _get_provider_config(provider, resolved_model, api_overrides)
    except ValueError as e:
        yield {'type': 'error', 'text': str(e)}
        return

    # System prompt to guide artifact formatting
    system_prompt = """You are XelaSphere AI, a helpful coding assistant.

CRITICAL OUTPUT FORMAT RULE:
NEVER use HTML tags like <p>, <div>, <span>, <br>, <h1>, etc. in your responses.
Use plain text and Markdown formatting ONLY. No HTML whatsoever.

WHEN TO USE ARTIFACTS (MANDATORY):
When the user asks you to BUILD, CREATE, or MAKE any of the following, you MUST output a ```artifact block:
- Apps, games, tools, calculators, visualizations
- Interactive components (React, HTML+JS, etc.)
- Websites, landing pages, dashboards
- Diagrams (Mermaid), SVG graphics
- Any code that is meant to be RUN or PREVIEWED

This includes SINGLE-FILE projects. Even a single React component or HTML page gets an artifact.

ARTIFACT FORMAT (EXACT):
```artifact
{
  "title": "Project Title",
  "type": "project",
  "primaryLanguage": "javascript",
  "files": [
    {"path": "index.html", "language": "html", "content": "<!DOCTYPE html>\\n<html>..."},
    {"path": "style.css", "language": "css", "content": "body { ... }"},
    {"path": "App.jsx", "language": "jsx", "content": "function App() { ... }"}
  ]
}
```

RULES for artifact blocks:
- The entire artifact must be valid JSON inside the code fence
- Use "files" array with objects containing "path", "language", and "content"
- Escape newlines as \\n and quotes as \\" in the content strings
- Keep each file's content as a single-line escaped string
- Always close the artifact block with ```
- For React apps: DO NOT create index.html. ALWAYS create BOTH App.jsx AND style.css as separate files.
  The preview system auto-loads React 18 + Babel. Just write the JSX component directly.
  IMPORTANT: The component's return statement MUST have a SINGLE root element. Wrap in <div> or use React Fragment (<>...</>). NEVER return adjacent sibling elements without a wrapper.
  IMPORTANT: NEVER put CSS inline in JSX. ALL styles MUST go in style.css as a separate file.
  IMPORTANT: App.jsx MUST use `export default function App()` or `export default` at the end.
  IMPORTANT: JSX must use proper nested structure with className attributes that match the CSS selectors. For example, if CSS has `.calculator-container`, `.display`, `.buttons`, then JSX MUST have `<div className="calculator-container"><div className="display">...</div><div className="buttons">...</div></div>`. NEVER render a flat list of elements without container divs.
  CRITICAL: Inside artifact code content, NEVER use markdown formatting like **bold**, *italic*, # headings, or bullet points (•). Write ONLY valid JavaScript/JSX code. Button text should be plain text like "AC" not "**AC**". Headings in JSX must use <h1>, <h2> etc, NOT # markdown syntax.
  Example: {"title":"My App","type":"project","primaryLanguage":"jsx","files":[{"path":"App.jsx","language":"jsx","content":"import React, { useState } from 'react';\\nimport './style.css';\\n\\nexport default function App() {\\n  return <div>Hello</div>;\\n}"},{"path":"style.css","language":"css","content":"body { margin: 0; }"}]}
- For HTML apps (no React): split into index.html, style.css, script.js when >30 lines
- NEVER mix index.html with .jsx files - use EITHER React (jsx only) OR plain HTML

WHEN NOT TO USE ARTIFACTS:
- Explaining a concept with a code snippet
- Answering "how do I..." with a small example
- Code reviews, debugging explanations
- Any non-runnable code (config files, pseudocode)

🔧 ARTIFACT UPDATES & BUG FIXES (CRITICAL):
When you see "CURRENT ARTIFACT" in the user's message, it contains code that needs fixing/updating.
1. READ the provided artifact code carefully
2. IDENTIFY and FIX the issue
3. OUTPUT a complete ```artifact block with ALL files (including the fix)
4. DO NOT ask for more information - the code is already provided
5. DO NOT just explain how to fix - actually provide the fixed code

⚠️ CRITICAL: DO NOT ASK EXCESSIVE QUESTIONS
When user asks you to BUILD or CREATE something:
- DO NOT ask clarifying questions - just BUILD IT immediately
- Use sensible defaults for missing details
- If user says "just do it", "skip", or "go ahead" → PRODUCE OUTPUT NOW
- It's better to produce a complete working result than to ask questions
- The user can always ask for changes after seeing the output

For simple questions or explanations, just respond normally without artifacts."""

    # Some reasoning models (e.g. Kimi K2.5) only accept temperature=1
    temperature = 1.0 if resolved_model in ("kimi-k2.5",) else 0.7

    data = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "model": resolved_model,
        "max_tokens": 8192,
        "temperature": temperature,
        "stream": True
    }

    try:
        response = requests.post(url, json=data, headers=headers, stream=True, timeout=120)
        response.raise_for_status()

        for line in response.iter_lines():
            if not line:
                continue

            line_text = line.decode('utf-8')
            if not line_text.startswith('data: '):
                continue

            json_str = line_text[6:]  # Remove 'data: ' prefix
            if json_str.strip() == '[DONE]':
                yield {'type': 'done'}
                break

            try:
                chunk = json.loads(json_str)
                delta = chunk.get('choices', [{}])[0].get('delta', {})

                # DeepSeek R1 sends reasoning_content in delta
                if 'reasoning_content' in delta and delta['reasoning_content']:
                    yield {'type': 'reasoning', 'text': delta['reasoning_content']}

                # Regular content
                if 'content' in delta and delta['content']:
                    yield {'type': 'content', 'text': delta['content']}

            except json.JSONDecodeError:
                continue

    except requests.Timeout:
        yield {'type': 'error', 'text': 'Request timed out'}
    except requests.RequestException as e:
        yield {'type': 'error', 'text': f'Request failed: {str(e)}'}
    except Exception as e:
        yield {'type': 'error', 'text': f'Unexpected error: {str(e)}'}


def send_prompt_with_images(prompt: str, images: List[Dict[str, str]], model: str = "meta-llama/llama-4-maverick-17b-128e-instruct"):
    """
    Send a multimodal prompt with images to a vision-capable AI model.

    Args:
        prompt: Text prompt
        images: List of image dicts with 'url' (data URL or http URL) and optional 'detail' ('low'/'high'/'auto')
        model: Vision-capable model ID (default: meta-llama/llama-4-maverick-17b-128e-instruct)

    Returns:
        Tuple of (response_text, is_code_response)
    """
    if not images:
        # No images, fall back to text-only
        return send_prompt(prompt, model)

    # For vision models, use the model as-is (don't resolve to default)
    resolved_model = model or "meta-llama/llama-4-maverick-17b-128e-instruct"
    provider = MODEL_PROVIDERS.get(resolved_model, "groq")
    provider_label = "OpenRouter" if provider == "openrouter" else "Groq"

    api_overrides: Dict[str, str] = {}
    if has_request_context():
        overrides = getattr(flask_g, 'api_keys', None)
        if isinstance(overrides, dict):
            api_overrides = overrides

    if provider == "openrouter":
        effective_key = (api_overrides.get('openrouter') if api_overrides else None) or Config.OPENROUTER_API_KEY
        if not effective_key:
            logger.error("OpenRouter API key is not configured.")
            return _model_error_payload(f'{provider_label} API key is not configured.', 'missing_api_key',
                                        provider=provider_label), False
        url = Config.OPENROUTER_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }
        if Config.OPENROUTER_SITE_URL:
            headers["HTTP-Referer"] = Config.OPENROUTER_SITE_URL
        if Config.OPENROUTER_APP_NAME:
            headers["X-Title"] = Config.OPENROUTER_APP_NAME
    else:
        effective_key = (api_overrides.get('groq') if api_overrides else None) or Config.GROQ_API_KEY
        if not effective_key:
            logger.error("Groq API key is not configured.")
            return _model_error_payload(f'{provider_label} API key is not configured.', 'missing_api_key',
                                        provider=provider_label), False
        url = Config.API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }

    # Build multimodal content array
    content = [{"type": "text", "text": prompt}]

    for img in images:
        img_url = img.get('url', '')
        if not img_url:
            continue

        # Format for OpenAI-compatible API
        image_content = {
            "type": "image_url",
            "image_url": {
                "url": img_url
            }
        }

        # Optional detail level (for OpenAI/OpenRouter)
        if img.get('detail'):
            image_content["image_url"]["detail"] = img.get('detail')

        content.append(image_content)

    # Core system instruction for output formatting - sent as system role so all models respect it
    system_instruction = (
        "You are a helpful AI assistant. "
        "CRITICAL: NEVER output HTML tags like <p>, <div>, <span>, <br>, <h1>, etc. in your responses. "
        "Use plain text and Markdown formatting ONLY. No HTML whatsoever."
    )

    data = {
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": content}
        ],
        "model": resolved_model,
        "max_tokens": 2048,
        "temperature": 0.7
    }

    logger.debug(f"Sending multimodal prompt with {len(images)} image(s) to {url} with model {resolved_model}")

    try:
        response = requests.post(url, json=data, headers=headers, timeout=60)  # Longer timeout for vision
        response.raise_for_status()

        model_response = response.json()
        logger.debug(f"Vision model response received")

        if 'choices' in model_response and len(model_response['choices']) > 0:
            message_content = model_response['choices'][0].get('message', {}).get('content', '').strip()
            if not message_content:
                logger.error("Vision model returned an empty or invalid content.")
                return _model_error_payload('Vision model returned an empty response.', 'empty_response'), False
            return message_content, is_code(message_content)
        else:
            logger.error(f"Unexpected vision API response format: {model_response}")
            return _model_error_payload('Unexpected response format from vision model.', 'unexpected_response'), False

    except requests.Timeout:
        logger.error("Vision request timed out.")
        return _model_error_payload('Vision request timed out', 'timeout', provider=provider_label), False

    except requests.exceptions.HTTPError as e:
        response_text = ''
        status_code = None
        if 'response' in locals() and isinstance(response, requests.Response):
            response_text = response.text
            status_code = response.status_code
        logger.error(f"Vision HTTP error ({provider_label}): {str(e)} - Response: {response_text}")
        return _model_error_payload(f"{provider_label} vision error: {str(e)}", 'http_error',
                                    status_code=status_code, response_text=response_text,
                                    provider=provider_label), False

    except requests.RequestException as e:
        logger.error(f"Vision request to {provider_label} failed: {str(e)}")
        return _model_error_payload(f"Vision request failed: {str(e)}", 'request_exception', provider=provider_label), False

    except ValueError as e:
        logger.error(f"Failed to parse {provider_label} vision response: {str(e)}")
        return _model_error_payload('Invalid vision response from AI model.', 'invalid_response', provider=provider_label), False


def tokenize_sentences(text):
    """
    Tokenize the input text into a list of sentences using NLTK's sent_tokenize.
    """
    if not isinstance(text, str):
        logger.error("Invalid input: not a string.")
        raise ValueError("Input must be a string.")

    if not text.strip():  # Handle empty or whitespace-only strings
        logger.warning("Input string is empty or whitespace-only.")
        return []  # Return an empty list for empty input

    try:
        NLTKSetup(required_resources=['punkt', 'punkt_tab']).setup()
    except Exception as setup_err:
        logger.warning(f"NLTK setup warning while preparing tokenizers: {setup_err}")

    try:
        # Tokenize using NLTK's sent_tokenize
        sentences = sent_tokenize(text)
        logger.debug(f"Tokenized {len(sentences)} sentences: {sentences}")
        return sentences
    except Exception as e:
        logger.error(f"Error during tokenization: {str(e)}")
        raise


def generate_fallback_response():
    """
    Generate a default fallback response if all models fail.
    """
    return "I'm sorry, I'm unable to generate a response at the moment. Please try again later."
