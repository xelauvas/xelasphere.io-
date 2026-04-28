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
from .model_catalog import MODEL_PROVIDERS, MODEL_CATALOG, resolve_model_id, DEFAULT_MODEL, FALLBACK_MODELS
from .artifacts import contains_code_block
import tiktoken
from datetime import datetime, timedelta
from typing import List, Set, Dict, Optional, Any
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


def classify_confirmation_response(user_response: str, pending_description: str) -> str:
    """Classify user's response to an image confirmation.
    Returns: 'confirm' | 'reject' | 'correction'"""
    prompt = f'''The user was asked to confirm generating this image:
"{pending_description}"

They responded: "{user_response}"

Classify their response as exactly one of:
- CONFIRM: they agree (yes, sure, go ahead, perfect, do it, exactly, looks good, etc.)
- REJECT: they decline (no, nevermind, cancel, stop, etc.)
- CORRECTION: they want a different image (they described something else or modified the description)

Reply with ONLY one word: CONFIRM, REJECT, or CORRECTION'''

    resp, _ = send_prompt(prompt, model='llama-3.1-8b-instant')
    answer = resp.strip().upper().split()[0] if isinstance(resp, str) and resp.strip() else 'REJECT'
    return answer.lower() if answer in ('CONFIRM', 'REJECT', 'CORRECTION') else 'reject'


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
    provider_labels = {"openrouter": "OpenRouter", "deepseek": "DeepSeek", "groq": "Groq", "cerebras": "Cerebras", "moonshot": "Moonshot", "openai": "OpenAI", "ollama": "Ollama", "gemini": "Gemini"}
    provider_label = provider_labels.get(provider, "Groq")

    if provider == "ollama":
        url = Config.OLLAMA_API_URL
        headers = {
            "Content-Type": "application/json"
        }
        timeout = 120  # Local models can be slower on first request
    elif provider == "openrouter":
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
    elif provider == "gemini":
        effective_key = (api_overrides.get('gemini') if api_overrides else None) or Config.GEMINI_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.GEMINI_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 30
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
    elif provider == "openai":
        effective_key = (api_overrides.get('openai') if api_overrides else None) or Config.OPENAI_API_KEY
        if not effective_key:
            raise ValueError(f'{provider_label} API key is not configured.')
        url = Config.OPENAI_API_URL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        timeout = 60
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
        "max_tokens": 8192,
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

    # Ollama fallback: any error triggers fallback to cloud model
    if provider == "ollama" and resolved_model in FALLBACK_MODELS:
        fallback_model = FALLBACK_MODELS[resolved_model]
        fallback_provider = MODEL_PROVIDERS.get(fallback_model, "groq")
        logger.info(f"Ollama failed, falling back to {fallback_provider} model: {fallback_model}")

        response_content, is_code_flag, error_payload, _ = _execute_prompt_request(
            prompt, fallback_model, fallback_provider, api_overrides
        )

        if error_payload is None:
            logger.info(f"{fallback_provider} fallback successful")
            return response_content, is_code_flag
        else:
            logger.warning(f"{fallback_provider} fallback also failed: {error_payload}")

    # If rate limited or payload too large and we have a Cerebras fallback, try it
    elif status_code in (429, 413) and provider == "groq" and resolved_model in FALLBACK_MODELS:
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


def send_metadata_prompt(
    user_msg: str,
    *,
    primary: Optional[str] = None,
    fallback: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout: int = 15,
) -> Optional[str]:
    """
    Internal metadata prompt with primary→fallback provider routing.

    Used by title generation, session summaries, and memory extraction —
    tasks where we want a specific two-model stack, structured output,
    and no HTML-stripping system instruction. Defaults come from
    Config.METADATA_MODEL_PRIMARY / METADATA_MODEL_FALLBACK.

    Returns the response content string on success. Returns None if both
    primary and fallback fail (callers decide their own degraded path).
    """
    primary = primary or getattr(Config, 'METADATA_MODEL_PRIMARY', 'gemini-2.5-flash')
    fallback = fallback or getattr(Config, 'METADATA_MODEL_FALLBACK', 'llama-3.3-70b-versatile')

    api_overrides: Dict[str, str] = {}
    if has_request_context():
        overrides = getattr(flask_g, 'api_keys', None)
        if isinstance(overrides, dict):
            api_overrides = overrides

    attempts = [("primary", primary), ("fallback", fallback)]
    for label, model in attempts:
        if not model:
            continue
        resolved = resolve_model_id(model)
        provider = MODEL_PROVIDERS.get(resolved, "groq")

        try:
            url, headers, _t, provider_label = _get_provider_config(provider, resolved, api_overrides)
        except ValueError as e:
            logger.warning(f"metadata[{label}] {provider} config error: {e}")
            continue

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_msg})

        payload: Dict[str, Any] = {
            "messages": messages,
            "model": resolved,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # Reasoning models (e.g. gemini-2.5-flash) burn the max_tokens budget
        # on thinking tokens and return empty content for tight budgets.
        # Metadata tasks don't need chain-of-thought, so disable it — but
        # only for models flagged as reasoning-capable. Sending it to a
        # plain model (e.g. Groq Llama) would be rejected with HTTP 400.
        if MODEL_CATALOG.get(resolved, {}).get("reasoning"):
            payload["reasoning_effort"] = "none"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code >= 400:
                logger.warning(
                    f"metadata[{label}] {provider_label} HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                continue
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                logger.warning(f"metadata[{label}] {provider_label}: empty choices")
                continue
            message = choices[0].get("message") or {}
            # Some reasoning models return both content and reasoning_content;
            # we only care about the final content for metadata tasks.
            content = (message.get("content") or "").strip()
            if not content:
                logger.warning(f"metadata[{label}] {provider_label}: empty content")
                continue
            if label == "fallback":
                logger.info(f"metadata fallback via {provider_label} ({model}) succeeded")
            return content
        except requests.Timeout:
            logger.warning(f"metadata[{label}] {provider_label}: timeout")
            continue
        except Exception as e:
            logger.warning(f"metadata[{label}] {provider_label}: {e}")
            continue

    logger.warning("metadata prompt: primary and fallback both failed")
    return None


def stream_deepseek_response(prompt: str, model: str = "deepseek-reasoner", system_prompt_override: str = None):
    """
    Stream response from DeepSeek, Moonshot, or OpenAI API with real-time content.

    Args:
        prompt: The user prompt (may include context from ContextBuilder)
        model: Model identifier
        system_prompt_override: If provided, replaces the default system prompt

    Yields:
        dict with 'type' ('reasoning', 'content', 'done', 'error') and 'text' or 'data'
    """
    from .config import Config
    from .model_catalog import resolve_model_id, MODEL_PROVIDERS
    from flask import g as flask_g, has_request_context
    import json

    resolved_model = resolve_model_id(model)
    provider = MODEL_PROVIDERS.get(resolved_model, "groq")

    # Streaming supported for OpenAI-compatible providers
    if provider not in ("deepseek", "moonshot", "openai", "groq", "ollama", "cerebras", "gemini"):
        yield {'type': 'error', 'text': 'Streaming not available for this provider'}
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

    # Use override system prompt if provided (from ContextBuilder), otherwise use default
    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
        system_prompt = "You are XelaSphere AI, a helpful assistant."

    # Some reasoning models (e.g. Kimi K2.5) only accept temperature=1
    temperature = 1.0 if resolved_model in ("kimi-k2.5",) else 0.7

    max_tokens = 16384 if provider == "openai" else 8192

    data = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "model": resolved_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True
    }

    try:
        response = requests.post(url, json=data, headers=headers, stream=True, timeout=(10, 90))
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


def send_prompt_with_images(prompt: str, images: List[Dict[str, str]], model: str = None):
    """
    Send a multimodal prompt with images to a vision-capable AI model.

    Args:
        prompt: Text prompt
        images: List of image dicts with 'url' (data URL or http URL) and optional 'detail' ('low'/'high'/'auto')
        model: Vision-capable model ID (default: meta-llama/llama-4-scout-17b-16e-instruct)

    Returns:
        Tuple of (response_text, is_code_response)
    """
    if not images:
        # No images, fall back to text-only
        return send_prompt(prompt, model)

    # Pick vision model via orchestrator if none specified; fall back to the
    # catalog resolver (which follows watcher-detected deprecations) so a
    # dead Groq vision model auto-heals to the live successor.
    if not model:
        try:
            from app.orchestrator import get_model as _orch_get
            model, _, _ = _orch_get("vision", {})
        except Exception:
            from app.model_catalog import get_model_for_task
            model = get_model_for_task("vision")
    resolved_model = model
    provider = MODEL_PROVIDERS.get(resolved_model, "groq")

    api_overrides: Dict[str, str] = {}
    if has_request_context():
        overrides = getattr(flask_g, 'api_keys', None)
        if isinstance(overrides, dict):
            api_overrides = overrides

    try:
        url, headers, timeout, provider_label = _get_provider_config(provider, resolved_model, api_overrides)
    except ValueError as e:
        return _model_error_payload(str(e), 'missing_api_key', provider=provider), False

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
        logging.info(f"Stripping HTML tags from AI response (len={len(text)})")

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
        logging.info(f"HTML stripped successfully (result_len={len(text)})")

    return text.strip()
