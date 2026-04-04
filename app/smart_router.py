"""
Smart Router - Intelligent model selection based on task type.

Routes requests to optimal models based on:
- Task type detection (coding, reasoning, vision, etc.)
- Conversation length (switch to long-context models)
- Content safety (detect refusals, route to Venice)
- User override (respect manual model selection)
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple

from .config import Config
from .model_catalog import (
    DEFAULT_MODEL,
    AUTO_MODEL,
    get_model_for_task,
    MODEL_PROVIDERS,
)

# Optional token estimation (tiktoken gives better estimates; fall back safely)
try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None

logger = logging.getLogger(__name__)

# ============================================
# PROVIDER AVAILABILITY (stability)
# ============================================

def _has_provider_key(provider: str) -> bool:
    """Return True if the provider has an API key configured."""
    provider = (provider or "").lower()
    if provider == "ollama":
        return bool(getattr(Config, "OLLAMA_API_URL", None))
    if provider == "deepseek":
        return bool(getattr(Config, "DEEPSEEK_API_KEY", None))
    if provider == "moonshot":
        return bool(getattr(Config, "MOONSHOT_API_KEY", None))
    if provider == "openrouter":
        return bool(getattr(Config, "OPENROUTER_API_KEY", None))
    if provider == "cerebras":
        return bool(getattr(Config, "CEREBRAS_API_KEY", None))
    # groq is default
    return bool(getattr(Config, "GROQ_API_KEY", None))


def _fallback_model_for_task(task_type: str) -> str:
    """Reasonable fallbacks that keep the UX working."""
    if task_type == "coding":
        # Prefer Kimi K2.5 when Moonshot is available (often stronger at complex coding).
        if _has_provider_key("moonshot"):
            return "kimi-k2.5"
        # Otherwise fall back to Groq.
        return "qwen/qwen3-32b"
    if task_type == "long_context":
        # Large context and decent quality.
        return "llama-3.3-70b-versatile"
    if task_type == "vision":
        return "meta-llama/llama-4-maverick-17b-128e-instruct"
    if task_type == "reasoning":
        return "qwen/qwen3-32b"
    return DEFAULT_MODEL


# ============================================
# TOKEN ESTIMATION (accuracy)
# ============================================

def _estimate_tokens(text: str, model_hint: str = "") -> int:
    """Best-effort token estimate. Uses tiktoken if available, otherwise a safe heuristic."""
    if not text:
        return 0
    # tiktoken isn't perfect for non-OpenAI models, but it's a much better estimate than len/4.
    if tiktoken is not None:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback heuristic
    return max(1, len(text) // 4)


def _estimate_conversation_tokens(
    message: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    lookback: int = 12,
) -> int:
    """Estimate tokens for recent history + current message."""
    total = _estimate_tokens(message)
    if not conversation_history:
        return total

    recent = conversation_history[-lookback:] if len(conversation_history) > lookback else conversation_history
    for msg in recent:
        content = msg.get("content", "") or msg.get("text", "") or msg.get("response", "") or ""
        total += _estimate_tokens(content)
    return total

# ============================================
# TASK DETECTION PATTERNS
# ============================================

CODING_PATTERNS = [
    r'\b(code|coding|program|function|class|method|api|debug|fix\s+(?:the\s+)?bug)\b',
    r'\b(python|javascript|typescript|java|rust|go|c\+\+|html|css|sql|react|node)\b',
    r'\b(implement|refactor|optimize|write\s+(?:a\s+)?(?:script|program|function))\b',
    r'```\w*\n',  # Code block in input
    r'\b(error|exception|traceback|stack\s*trace)\b',
]

REASONING_PATTERNS = [
    r'\b(explain|analyze|compare|evaluate|think\s+through|step\s+by\s+step)\b',
    r'\b(why|how\s+does|what\s+(?:is|are)\s+the\s+(?:reason|cause|difference))\b',
    r'\b(math|calculate|equation|formula|proof|logic)\b',
    r'\b(pros?\s+and\s+cons?|trade-?offs?|advantages?\s+(?:and|vs)\s+disadvantages?)\b',
]

IMAGE_GEN_PATTERNS = [
    r'\b(generate|create|make|draw)\s+(?:an?\s+)?(?:image|picture|photo|illustration|art)\b',
    r'\b(image\s+of|picture\s+of|show\s+me)\b',
    r'\b(visualize|render|design)\b',
]

UNCENSORED_PATTERNS = [
    r'\b(uncensored|unfiltered|no\s+restrictions?|without\s+limits?)\b',
    r'\b(roleplay|fiction|creative\s+writing|story)\b',
]

# Patterns that indicate a previous response was refused
REFUSAL_INDICATORS = [
    r"i can't help with",
    r"i cannot assist",
    r"i'm not able to",
    r"i'm unable to",
    r"i am unable to",
    r"unable to provide",
    r"against my guidelines",
    r"i must decline",
    r"i can't provide",
    r"cannot provide",
    r"inappropriate",
    r"harmful content",
    r"illegal activit",
    r"i cannot help",
    r"i won't help",
    r"i will not help",
    r"i'm not going to",
    r"i am not going to",
    r"not going to provide",
    r"against my programming",
    r"not able to assist",
    r"against the law",
    r"i can't do that",
    r"i cannot do that",
]

# ============================================
# TASK CLASSIFIER
# ============================================

def detect_task_type(
    message: str,
    has_images: bool = False,
    conversation_length: int = 0,
    previous_response: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Detect the task type from user message and context.

    Returns one of: 'coding', 'reasoning', 'vision', 'image_gen',
                    'uncensored', 'long_context', 'chat'
    """
    message_lower = message.lower()

    # Priority 1: Vision (has images attached)
    if has_images:
        logger.info("🎯 Task detected: vision (images attached)")
        return "vision"

    # Priority 2: Check if previous response was refused
    if previous_response:
        for pattern in REFUSAL_INDICATORS:
            if re.search(pattern, previous_response.lower()):
                logger.info("🎯 Task detected: uncensored (previous refusal)")
                return "uncensored"

    # Priority 3: Explicit uncensored request
    for pattern in UNCENSORED_PATTERNS:
        if re.search(pattern, message_lower):
            logger.info("🎯 Task detected: uncensored (explicit request)")
            return "uncensored"

    # Priority 4: Image generation
    for pattern in IMAGE_GEN_PATTERNS:
        if re.search(pattern, message_lower):
            logger.info("🎯 Task detected: image_gen")
            return "image_gen"

    # Priority 5: Coding - check current message
    coding_score = sum(1 for p in CODING_PATTERNS if re.search(p, message_lower, re.IGNORECASE))
    if coding_score >= 2:
        logger.info(f"🎯 Task detected: coding (score: {coding_score})")
        return "coding"

    # Priority 5b: Coding - check conversation history for code context
    # If recent messages contain code, continue as coding task
    if conversation_history and len(conversation_history) > 0:
        history_coding_score = _analyze_history_for_code(conversation_history)
        if history_coding_score >= 2:
            logger.info(f"🎯 Task detected: coding (history context, score: {history_coding_score})")
            return "coding"

    # Priority 6: Complex reasoning
    reasoning_score = sum(1 for p in REASONING_PATTERNS if re.search(p, message_lower, re.IGNORECASE))
    if reasoning_score >= 2:
        logger.info(f"🎯 Task detected: reasoning (score: {reasoning_score})")
        return "reasoning"

    # Priority 7: Long conversation (switch to long-context model)
    if conversation_length >= 20:
        logger.info(f"🎯 Task detected: long_context (msgs: {conversation_length})")
        return "long_context"

    # Default: general chat
    logger.info("🎯 Task detected: chat (default)")
    return "chat"


def _analyze_history_for_code(history: List[Dict[str, Any]], lookback: int = 4) -> int:
    """
    Analyze recent conversation history for coding context.

    Looks at recent messages to detect if conversation is about coding.
    Returns a score (0-5) indicating coding context strength.
    """
    score = 0
    recent = history[-lookback:] if len(history) > lookback else history

    logger.info(f"🔍 History analysis: checking {len(recent)} recent messages")

    for i, msg in enumerate(recent):
        # Check multiple possible content fields (user msgs use 'content', AI msgs use 'response')
        content = msg.get("content", "") or msg.get("text", "") or msg.get("response", "") or ""
        content_lower = content.lower()

        # Debug: log what we're analyzing
        content_preview = content[:100] if content else "(empty)"
        logger.info(f"🔍 History msg[{i}]: content_len={len(content)}, preview='{content_preview}...'")

        # Check for code blocks (strong indicator)
        if "```" in content:
            score += 2
            logger.info(f"🔍 History msg[{i}]: Found code block, score +2")
            continue

        # Check for coding keywords
        for pattern in CODING_PATTERNS:
            if re.search(pattern, content_lower, re.IGNORECASE):
                score += 1
                logger.info(f"🔍 History msg[{i}]: Matched coding pattern, score +1")
                break  # Only count once per message

    logger.info(f"🔍 History analysis complete: total score = {score}")
    return score


# ============================================
# SMART ROUTER
# ============================================

def smart_route(
    message: str,
    user_selected_model: Optional[str] = None,
    has_images: bool = False,
    conversation_length: int = 0,
    previous_response: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """
    Route request to optimal model based on task detection.

    Args:
        message: User's message
        user_selected_model: Model explicitly selected by user (None or 'auto' = smart routing)
        has_images: Whether message includes images
        conversation_length: Number of messages in conversation
        previous_response: Previous AI response (to detect refusals)
        context: Additional context dict
        conversation_history: Recent conversation messages for context detection

    Returns:
        Tuple of (model_id, task_type)
    """
    # If user explicitly selected a model (not 'auto'), respect it
    if user_selected_model and user_selected_model != AUTO_MODEL and user_selected_model != "auto":
        logger.info(f"🔀 Router: User override -> {user_selected_model}")
        return user_selected_model, "user_override"

    # Detect task type
    task_type = detect_task_type(
        message=message,
        has_images=has_images,
        conversation_length=conversation_length,
        previous_response=previous_response,
        conversation_history=conversation_history,
    )

    # Get optimal model for task
    model = get_model_for_task(task_type)

    # Accuracy: use token pressure rather than just message count for long-context routing
    total_tokens = _estimate_conversation_tokens(message, conversation_history)
    if task_type != "vision" and total_tokens >= 50000:
        # Force long-context when conversation is large
        task_type = "long_context"
        model = get_model_for_task(task_type)

    # Cost: for simple chat, prefer the fast small model
    if task_type == "chat":
        # Lightweight heuristic: short messages in short conversations are usually fine on 8B
        if len(message) <= 220 and (conversation_length or 0) <= 10 and total_tokens <= 8000:
            model = "llama-3.1-8b-instant"

    # Stability: provider-aware fallback if the chosen provider isn't configured
    provider = MODEL_PROVIDERS.get(model, "groq")
    if not _has_provider_key(provider):
        fallback = _fallback_model_for_task(task_type)
        logger.warning(
            "🔀 Router: provider key missing for %s (%s). Falling back to %s",
            model,
            provider,
            fallback,
        )
        model = fallback

    logger.info(f"🔀 Router: {task_type} -> {model}")

    return model, task_type


def should_use_long_context_model(
    conversation_length: int,
    total_tokens: int = 0,
    threshold_messages: int = 20,
    threshold_tokens: int = 50000,
) -> bool:
    """Determine if conversation should switch to a long-context model."""
    return (conversation_length >= threshold_messages) or (total_tokens >= threshold_tokens)


def estimate_conversation_tokens(messages: List[Dict[str, str]]) -> int:
    """Rough estimate of token count in conversation.

    Kept intentionally stable (chars//4) because it is used in tests and UI heuristics.
    The router uses a separate best-effort estimator for routing decisions.
    """
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    return total_chars // 4


# ============================================
# REFUSAL DETECTION
# ============================================

def is_refusal_response(response: str) -> bool:
    """
    Check if AI response indicates a refusal.
    """
    if not response:
        return False

    response_lower = response.lower()
    for pattern in REFUSAL_INDICATORS:
        if re.search(pattern, response_lower):
            return True
    return False


def get_uncensored_model() -> str:
    """
    Get the uncensored model for retry after refusal.
    """
    return get_model_for_task("uncensored")


# ============================================
# CONTEXT LENGTH MANAGEMENT
# ============================================

def check_context_overflow(
    model: str,
    messages: List[Dict[str, str]],
    buffer: int = 4000,
) -> Tuple[bool, Optional[str]]:
    """
    Check if conversation exceeds model's context limit.

    Returns:
        Tuple of (is_overflow, suggested_model)
    """
    model_limit = get_model_context_limit(model)
    estimated_tokens = estimate_conversation_tokens(messages)

    if estimated_tokens + buffer > model_limit:
        # Suggest long-context model
        long_context_model = get_model_for_task("long_context")
        logger.warning(
            f"⚠️ Context overflow: {estimated_tokens} tokens > {model_limit} limit. "
            f"Suggesting: {long_context_model}"
        )
        return True, long_context_model

    return False, None


# ============================================
# ROUTING METRICS (for logging/analytics)
# ============================================

class RoutingMetrics:
    """Track routing decisions for analytics."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._reset()
        return cls._instance

    def _reset(self):
        self.routing_counts = {}
        self.fallback_counts = {}
        self.refusal_retries = 0

    def record_route(self, task_type: str, model: str):
        key = f"{task_type}:{model}"
        self.routing_counts[key] = self.routing_counts.get(key, 0) + 1

    def record_fallback(self, from_model: str, to_model: str):
        key = f"{from_model}->{to_model}"
        self.fallback_counts[key] = self.fallback_counts.get(key, 0) + 1

    def record_refusal_retry(self):
        self.refusal_retries += 1

    def get_stats(self) -> Dict[str, Any]:
        return {
            "routing": self.routing_counts,
            "fallbacks": self.fallback_counts,
            "refusal_retries": self.refusal_retries,
        }


def get_routing_metrics() -> RoutingMetrics:
    """Get singleton routing metrics instance."""
    return RoutingMetrics()


# ============================================
# PROMPT INJECTION GUARD
# ============================================

# Common prompt injection patterns
INJECTION_PATTERNS = [
    # Direct instruction overrides
    r'ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?',
    r'disregard\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?',
    r'forget\s+(?:all\s+)?(?:previous|above|your)\s+instructions?',
    r'new\s+instructions?:\s*',
    r'override\s+(?:system|your)\s+(?:prompt|instructions?)',
    r'you\s+are\s+now\s+(?:a\s+)?(?:different|new)',
    r'pretend\s+(?:you\s+are|to\s+be)\s+(?:a\s+)?(?:different|new)',

    # System prompt extraction
    r'(?:show|reveal|display|print|output)\s+(?:your\s+)?(?:system|initial)\s+(?:prompt|instructions?)',
    r'what\s+(?:are|is)\s+your\s+(?:system|initial)\s+(?:prompt|instructions?)',
    r'repeat\s+(?:your\s+)?(?:system|initial)\s+(?:prompt|instructions?)',

    # Role manipulation
    r'you\s+(?:must|should|will)\s+(?:now\s+)?(?:act|behave)\s+as',
    r'from\s+now\s+on\s+you\s+(?:are|will)',
    r'switch\s+(?:to\s+)?(?:a\s+)?(?:different|new)\s+(?:mode|role|persona)',

    # Jailbreak attempts
    r'(?:dan|developer|admin)\s+mode',
    r'(?:enable|activate)\s+(?:jailbreak|unrestricted)',
    r'bypass\s+(?:all\s+)?(?:safety|content|ethical)\s+(?:filters?|restrictions?)',

    # Encoded/obfuscated instructions
    r'base64\s*:\s*[a-zA-Z0-9+/=]{20,}',
    r'decode\s+(?:and\s+)?(?:execute|follow|run)',
]

# Suspicious patterns (lower confidence, may be legitimate)
SUSPICIOUS_PATTERNS = [
    r'\]\s*\}\s*\]\s*\}\s*$',  # JSON injection attempt
    r'<\/?(?:script|system|admin|root)>',  # HTML/XML injection
    r'```(?:system|admin|root)',  # Code block injection
]


class PromptGuardResult:
    """Result of prompt injection check."""
    def __init__(
        self,
        is_safe: bool,
        risk_level: str,  # 'safe', 'suspicious', 'blocked'
        matched_patterns: List[str],
        message: str = ""
    ):
        self.is_safe = is_safe
        self.risk_level = risk_level
        self.matched_patterns = matched_patterns
        self.message = message


def check_prompt_injection(
    message: str,
    strict_mode: bool = False,
) -> PromptGuardResult:
    """
    Check message for prompt injection attempts.

    Args:
        message: User message to check
        strict_mode: If True, also flag suspicious patterns

    Returns:
        PromptGuardResult with safety assessment
    """
    if not message:
        return PromptGuardResult(True, 'safe', [], '')

    message_lower = message.lower()
    matched = []

    # Check definite injection patterns
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            matched.append(pattern)

    if matched:
        logger.warning(f"🛡️ Prompt injection detected: {len(matched)} patterns matched")
        return PromptGuardResult(
            is_safe=False,
            risk_level='blocked',
            matched_patterns=matched,
            message="Potential prompt injection detected. Please rephrase your request."
        )

    # Check suspicious patterns (only in strict mode)
    if strict_mode:
        suspicious = []
        for pattern in SUSPICIOUS_PATTERNS:
            if re.search(pattern, message_lower, re.IGNORECASE):
                suspicious.append(pattern)

        if suspicious:
            logger.info(f"⚠️ Suspicious patterns detected: {suspicious}")
            return PromptGuardResult(
                is_safe=True,  # Allow but flag
                risk_level='suspicious',
                matched_patterns=suspicious,
                message="Message contains potentially suspicious patterns."
            )

    return PromptGuardResult(True, 'safe', [], '')


def sanitize_prompt(message: str) -> str:
    """
    Sanitize potentially dangerous content from prompt.

    Note: This is a lightweight sanitizer. For production,
    consider using the dedicated prompt guard model.
    """
    if not message:
        return message

    # Remove potential JSON/code injection at boundaries
    sanitized = re.sub(r'^\s*[\[\{]', '', message)
    sanitized = re.sub(r'[\]\}]\s*$', '', sanitized)

    # Remove potential system prompt markers
    sanitized = re.sub(r'<\/?system[^>]*>', '', sanitized, flags=re.IGNORECASE)

    return sanitized.strip()


async def check_with_guard_model(
    message: str,
    api_key: Optional[str] = None,
) -> PromptGuardResult:
    """
    Check message using the dedicated prompt guard model.

    Uses meta-llama/llama-prompt-guard-2-86m for fast,
    accurate prompt injection detection.

    Note: This is async and makes an API call.
    Use check_prompt_injection() for synchronous regex-based check.
    """
    import os
    import aiohttp

    key = api_key or os.environ.get('GROQ_API_KEY')
    if not key:
        # Fall back to regex check
        return check_prompt_injection(message)

    model = get_model_for_task("prompt_guard")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": message}
                    ],
                    "max_tokens": 10,
                },
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("choices", [{}])[0].get("message", {}).get("content", "")

                    # Guard model returns classification
                    if "unsafe" in result.lower() or "injection" in result.lower():
                        return PromptGuardResult(
                            is_safe=False,
                            risk_level='blocked',
                            matched_patterns=['guard_model_detection'],
                            message="Message flagged by security model."
                        )

                    return PromptGuardResult(True, 'safe', [], '')

    except Exception as e:
        logger.warning(f"Guard model check failed: {e}, falling back to regex")

    # Fallback to regex
    return check_prompt_injection(message)
