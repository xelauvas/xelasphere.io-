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
    "moonshotai/kimi-k2-instruct-0905": {
        "label": "Kimi K2 (Long Context)",
        "provider": "groq",
        "selectable": False,  # Replaced by Moonshot-direct Kimi models
        "speed": "medium",
        "context": 262000,
        "long_context": True,
    },
    "openai/gpt-oss-120b": {
        "label": "GPT-OSS 120B",
        "provider": "groq",
        "selectable": True,
        "speed": "slow",
        "context": 128000,
    },

    # Vision models - Groq (internal use)
    "meta-llama/llama-4-maverick-17b-128e-instruct": {
        "label": "Llama 4 Maverick",
        "provider": "groq",
        "selectable": False,
        "vision": True,
        "context": 128000,
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "label": "Llama 4 Scout",
        "provider": "groq",
        "selectable": False,
        "vision": True,
        "context": 16000,
    },

    # Safety models - Groq (internal use)
    "meta-llama/llama-guard-4-12b": {
        "label": "Llama Guard 4",
        "provider": "groq",
        "selectable": False,
        "safety": True,
    },
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

    "llama-3.3-70b": {
        "label": "Llama 3.3 70B (Cerebras)",
        "provider": "cerebras",
        "selectable": False,  # Fallback only
        "speed": "fast",  # Cerebras is fast
        "context": 128000,
    },
    "llama3.1-8b": {
        "label": "Llama 3.1 8B (Cerebras)",
        "provider": "cerebras",
        "selectable": False,
        "speed": "fast",
        "context": 128000,
    },
    "qwen-3-32b": {
        "label": "Qwen 3 32B (Cerebras)",
        "provider": "cerebras",
        "selectable": False,
        "speed": "fast",
        "context": 32000,
        "reasoning": True,
    },
    "qwen-3-235b-a22b-instruct-2507": {
        "label": "Qwen 3 235B (Groq)",
        "provider": "groq",
        "selectable": True,  # User can select for complex tasks
        "speed": "slow",
        "context": 128000,
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
    # DEEPSEEK MODELS (Coding)
    # ============================================

    "deepseek-chat": {
        "label": "DeepSeek V3",
        "provider": "deepseek",
        "selectable": True,
        "speed": "medium",
        "coding": True,
        "context": 128000,
    },
    "deepseek-reasoner": {
        "label": "DeepSeek R1 (Reasoning)",
        "provider": "deepseek",
        "selectable": True,
        "speed": "slow",
        "coding": True,
        "reasoning": True,
        "context": 128000,
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
    "llama-3.3-70b-versatile": "llama-3.3-70b",
    "llama-3.1-8b-instant": "llama3.1-8b",
    "qwen/qwen3-32b": "qwen-3-32b",
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
    "safety": "meta-llama/llama-guard-4-12b",
    "prompt_guard": "meta-llama/llama-prompt-guard-2-86m",

    # Specialized tasks
    "vision": "meta-llama/llama-4-maverick-17b-128e-instruct",
    "coding": "deepseek-chat",
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
    "deepseek-v3": "deepseek-chat",
    "deepseek-r1": "deepseek-reasoner",
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


def get_model_for_task(task: str) -> str:
    """Get the optimal model for a specific task type."""
    return TASK_ROUTES.get(task, DEFAULT_MODEL)


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
