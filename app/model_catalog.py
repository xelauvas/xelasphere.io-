"""
Model Catalog - All available models with routing configuration.

Models are organized by:
- Provider (groq, cerebras, deepseek, openrouter)
- Purpose (chat, vision, safety, guard, long-context)
- User visibility (selectable vs internal-only)
"""

MODEL_CATALOG = {
    # ============================================
    # GROQ MODELS
    # ============================================

    # Chat models - Groq (user selectable)
    "llama-3.3-70b-versatile": {
        "label": "Llama 3.3 70B",
        "provider": "groq",
        "selectable": True,
        "speed": "medium",
        "context": 128000,
    },
    "llama-3.1-8b-instant": {
        "label": "Llama 3.1 8B",
        "provider": "groq",
        "selectable": False,  # Internal fast model
        "speed": "fast",
        "context": 128000,
    },
    "qwen/qwen3-32b": {
        "label": "Qwen 3 32B",
        "provider": "groq",
        "selectable": True,
        "speed": "medium",
        "context": 32000,
        "reasoning": True,
    },
    "openai/gpt-oss-120b": {
        "label": "GPT-OSS 120B",
        "provider": "groq",
        "selectable": True,
        "speed": "slow",
        "context": 128000,
    },

    # Vision models - Groq (internal use)
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "label": "Llama 4 Scout (Vision)",
        "provider": "groq",
        "selectable": False,
        "vision": True,
        "context": 16000,
    },

    # Safety models - Groq (internal use)
    "openai/gpt-oss-safeguard-20b": {
        "label": "GPT-OSS Safeguard",
        "provider": "groq",
        "selectable": False,
        "safety": True,
    },

    # Prompt guard models - Groq (internal use)
    "meta-llama/llama-prompt-guard-2-86m": {
        "label": "Prompt Guard 86M",
        "provider": "groq",
        "selectable": False,
        "prompt_guard": True,
        "speed": "instant",
    },
    "meta-llama/llama-prompt-guard-2-22m": {
        "label": "Prompt Guard 22M",
        "provider": "groq",
        "selectable": False,
        "prompt_guard": True,
        "speed": "instant",
    },

    # ============================================
    # CEREBRAS MODELS (Fallback)
    # ============================================

    "llama3.1-8b": {
        "label": "Llama 3.1 8B (Cerebras)",
        "provider": "cerebras",
        "selectable": False,
        "speed": "fast",
        "context": 128000,
    },
    "qwen-3-235b-a22b-instruct-2507": {
        "label": "Qwen 3 235B (Cerebras)",
        "provider": "cerebras",
        "selectable": True,
        "speed": "fast",
        "context": 32000,
        "reasoning": True,
    },
    "gpt-oss-120b": {
        "label": "GPT-OSS 120B (Cerebras)",
        "provider": "cerebras",
        "selectable": False,
        "speed": "medium",
        "context": 128000,
    },

    # ============================================
    # GEMINI MODELS (Google AI Studio — free tier)
    # Free tier RPD: 3.1 Flash Lite=500, Gemma 4=1500, Flash 2.5/3=20
    # ============================================

    "gemini-3.1-flash-lite-preview": {
        "label": "Gemini 3.1 Flash Lite",
        "provider": "gemini",
        "selectable": True,
        "speed": "fast",
        "context": 1048576,
        "long_context": True,
    },
    "gemma-4-26b-a4b-it": {
        "label": "Gemma 4 26B (Gemini)",
        "provider": "gemini",
        "selectable": True,
        "speed": "fast",
        "context": 128000,
    },
    "gemini-2.5-flash": {
        "label": "Gemini 2.5 Flash (Reasoning)",
        "provider": "gemini",
        "selectable": True,
        "speed": "medium",
        "context": 1048576,
        "reasoning": True,
        "long_context": True,
    },
    "gemini-2.0-flash": {
        "label": "Gemini 2.0 Flash",
        "provider": "gemini",
        "selectable": False,  # Low free tier (20 RPD)
        "speed": "fast",
        "context": 1048576,
        "long_context": True,
    },

    # ============================================
    # DEEPSEEK MODELS (Coding)
    # V4 launched 2026-04 — 1M context, thinking mode default.
    # Old IDs (deepseek-chat / deepseek-reasoner) are deprecated by DeepSeek;
    # kept as back-compat aliases below in MODEL_ALIASES.
    # ============================================

    "deepseek-v4-flash": {
        "label": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "selectable": True,
        "speed": "medium",
        "coding": True,
        "reasoning": True,  # thinking mode default
        "context": 1048576,  # 1M
        "long_context": True,
    },
    "deepseek-v4-pro": {
        "label": "DeepSeek V4 Pro (Best Coding)",
        "provider": "deepseek",
        "selectable": True,
        "speed": "slow",
        "coding": True,
        "reasoning": True,  # thinking mode default
        "context": 1048576,  # 1M
        "long_context": True,
    },

    # ============================================
    # MOONSHOT MODELS (Kimi)
    # ============================================

    "kimi-k2.5": {
        "label": "Kimi K2.5 (Reasoning)",
        "provider": "moonshot",
        "selectable": True,
        "speed": "slow",
        "context": 131072,
        "reasoning": True,
    },
    "kimi-latest": {
        "label": "Kimi Latest",
        "provider": "moonshot",
        "selectable": True,
        "speed": "medium",
        "context": 131072,
    },

    # ============================================
    # OPENAI MODELS
    # ============================================

    "gpt-4.1": {
        "label": "GPT-4.1 (Best Coding)",
        "provider": "openai",
        "selectable": True,
        "speed": "medium",
        "coding": True,
        "context": 1047576,
    },
    "gpt-4.1-mini": {
        "label": "GPT-4.1 Mini (Fast)",
        "provider": "openai",
        "selectable": True,
        "speed": "fast",
        "coding": True,
        "context": 1047576,
    },
    "gpt-4.1-nano": {
        "label": "GPT-4.1 Nano",
        "provider": "openai",
        "selectable": True,
        "speed": "fast",
        "context": 1047576,
    },

    # ============================================
    # OLLAMA MODELS (Local)
    # ============================================

    "gemma4:e2b": {
        "label": "Gemma 4 E2B (Local)",
        "provider": "ollama",
        "selectable": True,
        "speed": "fast",
        "context": 32000,
    },

    "dolphin3": {
        "label": "Dolphin 3 (Uncensored)",
        "provider": "ollama",
        "selectable": True,
        "uncensored": True,
        "speed": "fast",
        "context": 32000,
    },

}

# ============================================
# FALLBACK MAPPINGS (Groq -> Cerebras)
# ============================================

FALLBACK_MODELS = {
    "llama-3.3-70b-versatile": "qwen-3-235b-a22b-instruct-2507",
    "llama-3.1-8b-instant": "llama3.1-8b",
    "qwen/qwen3-32b": "qwen-3-235b-a22b-instruct-2507",
    "openai/gpt-oss-120b": "gpt-oss-120b",
    "gemma4:e2b": "llama-3.3-70b-versatile",  # Ollama -> Groq fallback
    "dolphin3": "llama-3.3-70b-versatile",  # Ollama -> Groq fallback
}

# ============================================
# TASK ROUTING CONFIGURATION
# ============================================

TASK_ROUTES = {
    # Fast tasks (< 200ms target)
    "intent": "llama-3.1-8b-instant",
    "autocomplete": "llama-3.1-8b-instant",
    "title": "llama-3.1-8b-instant",
    "memory_gate": "llama-3.1-8b-instant",
    "memory_relevance": "llama-3.1-8b-instant",
    "session_summary": "llama-3.1-8b-instant",

    # Accurate tasks
    "memory_extraction": "qwen/qwen3-32b",
    "reflection": "qwen/qwen3-32b",

    # Safety tasks
    "safety": "openai/gpt-oss-safeguard-20b",
    "prompt_guard": "meta-llama/llama-prompt-guard-2-86m",

    # Specialized tasks
    "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
    "coding": "deepseek-v4-pro",
    "reasoning": "qwen/qwen3-32b",
    "uncensored": "dolphin3",
    "long_context": "kimi-latest",

    # Default chat
    "chat": "llama-3.3-70b-versatile",
}

# ============================================
# USER-SELECTABLE MODELS (for UI dropdown)
# ============================================

def get_selectable_models():
    """Return models that users can select in the UI."""
    return {
        "auto": "Auto (Recommended)",
        **{k: v["label"] for k, v in MODEL_CATALOG.items() if v.get("selectable", False)}
    }

# ============================================
# ALIASES
# ============================================

MODEL_ALIASES = {
    "gemma4": "gemma4:e2b",
    "gemma": "gemma4:e2b",
    "dolphin": "dolphin3",
    "uncensored": "dolphin3",
    "venice": "dolphin3",
    # DeepSeek V4 (current)
    "deepseek": "deepseek-v4-pro",
    "deepseek-v4": "deepseek-v4-pro",
    "deepseek-flash": "deepseek-v4-flash",
    "deepseek-pro": "deepseek-v4-pro",
    # Deprecated by DeepSeek — kept so existing code/configs keep working.
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
    "deepseek-v3": "deepseek-v4-flash",
    "deepseek-r1": "deepseek-v4-pro",
    "kimi": "kimi-latest",
    "kimi-k2": "kimi-latest",
    "kimi-k2.5": "kimi-k2.5",
    "kimi-latest": "kimi-latest",
    "qwen-235b": "qwen-3-235b-a22b-instruct-2507",
    "gpt4": "gpt-4.1",
    "gpt-4": "gpt-4.1",
    "gpt": "gpt-4.1",
    "openai": "gpt-4.1",
    "auto": "auto",  # Special case for smart routing
}

# ============================================
# DERIVED LOOKUPS
# ============================================

MODEL_LABELS = {key: value["label"] for key, value in MODEL_CATALOG.items()}
MODEL_PROVIDERS = {key: value.get("provider", "groq") for key, value in MODEL_CATALOG.items()}

DEFAULT_MODEL = "llama-3.3-70b-versatile"
AUTO_MODEL = "auto"  # Triggers smart routing

# ============================================
# HELPER FUNCTIONS
# ============================================

def resolve_model_id(model: str) -> str:
    """Resolve model alias to canonical model ID."""
    if not model or model == "auto":
        return DEFAULT_MODEL
    canonical = MODEL_ALIASES.get(model, model)
    if canonical not in MODEL_CATALOG:
        return DEFAULT_MODEL
    return canonical


def resolve_live_model_id(model: str, task: str = None) -> str:
    """Resolve `model` to a live, non-deprecated equivalent.

    The orchestrator registry records `is_deprecated` / `replacement_id` on
    any model the watcher has detected as disappeared from its provider's
    /models endpoint. This helper follows that chain so callers reading
    static config (TASK_ROUTES, hard-coded fallback lists, etc.) transparently
    hop to the live successor without needing a code change.

    Unlike ``resolve_model_id``, this function does **not** coerce unknown
    IDs to ``DEFAULT_MODEL`` up-front — that would mask the registry's
    knowledge of a deprecated ID we've since purged from the catalog.
    Instead we walk the registry first and only fall back to DEFAULT_MODEL
    if nothing in the registry or fallback chain can serve the request.

    Resolution order:
      1. Normalize aliases (MODEL_ALIASES).
      2. Walk the registry's deprecation chain (cycle-safe) until a healthy
         model is reached.
      3. If the chain dead-ends on a deprecated model, walk
         ``FALLBACK_CHAINS[task]`` (when a task is given) for the first live,
         non-deprecated candidate.
      4. Otherwise return the catalog-resolved ID (or DEFAULT_MODEL if the
         input is truly unknown).
    """
    if not model or model == "auto":
        return DEFAULT_MODEL

    canonical = MODEL_ALIASES.get(model, model)

    try:
        from app.orchestrator import registry
    except Exception:
        # Orchestrator not importable yet (e.g. at module-load time) —
        # degrade to the catalog-only behavior.
        return canonical if canonical in MODEL_CATALOG else DEFAULT_MODEL

    visited = set()
    current = canonical
    terminal = None  # last model we examined (for fall-through return)
    while current and current not in visited:
        visited.add(current)
        m = registry.get(current)
        terminal = current
        if m is None:
            # Unknown to registry — if the catalog knows it, trust it;
            # otherwise break and let the fallback chain / DEFAULT_MODEL
            # kick in below.
            if current in MODEL_CATALOG:
                return current
            break
        if not m.is_deprecated and m.status != "down":
            return current
        if m.replacement_id and m.replacement_id not in visited:
            current = m.replacement_id
            continue
        break

    # Dead-ended on a deprecated / unknown model. Try the task-level chain.
    if task:
        try:
            from app.orchestrator.config import FALLBACK_CHAINS
        except Exception:
            FALLBACK_CHAINS = {}
        for _prov, chain_model, _tier in FALLBACK_CHAINS.get(task, []):
            fm = registry.get(chain_model)
            if fm and fm.is_available():
                return chain_model

    # Final fallback: the catalog-resolved ID if valid, else DEFAULT_MODEL.
    if terminal and terminal in MODEL_CATALOG:
        return terminal
    if canonical in MODEL_CATALOG:
        return canonical
    return DEFAULT_MODEL


def get_model_for_task(task: str) -> str:
    """Get the optimal *live* model for a specific task type.

    Resolves through the orchestrator registry so deprecated TASK_ROUTES
    entries auto-heal to their replacement_id (or the next live candidate in
    FALLBACK_CHAINS[task]).
    """
    return resolve_live_model_id(TASK_ROUTES.get(task, DEFAULT_MODEL), task=task)


def get_fallback_model(model: str) -> str:
    """Get Cerebras fallback for a Groq model."""
    return FALLBACK_MODELS.get(model)


def is_vision_model(model: str) -> bool:
    """Check if model supports vision/images."""
    return MODEL_CATALOG.get(model, {}).get("vision", False)


def is_coding_model(model: str) -> bool:
    """Check if model is optimized for coding."""
    return MODEL_CATALOG.get(model, {}).get("coding", False)


def is_reasoning_model(model: str) -> bool:
    """Check if model is optimized for reasoning."""
    return MODEL_CATALOG.get(model, {}).get("reasoning", False)


def get_model_context_limit(model: str) -> int:
    """Get context window size for a model."""
    return MODEL_CATALOG.get(model, {}).get("context", 8000)
