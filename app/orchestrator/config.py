"""
Static configuration — fallback chains, free tier limits, provider endpoints.
All values verified against actual API docs.
Free tier limits set to 80% of actual to buffer Gunicorn worker divergence.
"""

# Provider health check endpoints
PROVIDER_ENDPOINTS = {
    "groq":     "https://api.groq.com/openai/v1/models",
    "cerebras": "https://api.cerebras.ai/v1/models",
    "openai":   "https://api.openai.com/v1/models",
    "deepseek": "https://api.deepseek.com/models",
    "gemini":   "https://generativelanguage.googleapis.com/v1beta/openai/models",
    # "ollama":   None,   # removed — no local LLM
}

# Known free tier limits (conservative — 80% of actual)
FREE_TIER_LIMITS = {
    "groq":     {"daily_requests": 11_520, "tokens_per_minute": 4_800},
    "cerebras": {"daily_tokens": 800_000},
    # Gemini free tier varies by model:
    #   gemini-3.1-flash-lite: 500 RPD (primary)
    #   gemma-4-26b: 1500 RPD, unlimited TPM (secondary)
    #   gemini-2.5-flash/3-flash: only 20 RPD (deep fallback)
    # Using conservative combined limit for the provider bucket
    "gemini":   {"daily_requests": 400, "daily_tokens": 200_000},
    # "ollama":   {"daily_tokens": 0},  # removed — no local LLM
    # deepseek/moonshot/openai = paid, no free tier limit to track
}

# Fallback chains per job type
# Format: (provider, model_id, quality_tier)
# tier0 = premium paid, tier1 = standard free/cheap, tier2 = degraded/local
FALLBACK_CHAINS = {
    "coding": [
        ("deepseek", "deepseek-chat",                          "tier1"),
        ("groq",     "qwen/qwen3-32b",                         "tier1"),
        ("cerebras", "qwen-3-235b-a22b-instruct-2507",         "tier1"),
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),  # 500 RPD
        ("gemini",   "gemini-2.5-flash",                       "tier1"),  # 20 RPD reasoning
        ("openai",   "gpt-4.1",                                "tier0"),
    ],
    "reasoning": [
        ("deepseek", "deepseek-reasoner",                      "tier1"),
        ("groq",     "qwen/qwen3-32b",                         "tier1"),
        ("cerebras", "qwen-3-235b-a22b-instruct-2507",         "tier1"),
        ("gemini",   "gemini-2.5-flash",                       "tier1"),  # 20 RPD but best for reasoning
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),  # 500 RPD fallback
        ("openai",   "gpt-4.1-mini",                           "tier0"),
    ],
    "general_chat": [
        ("groq",     "llama-3.3-70b-versatile",                "tier1"),
        ("cerebras", "qwen-3-235b-a22b-instruct-2507",         "tier1"),
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),  # 500 RPD
        ("gemini",   "gemma-4-26b-a4b-it",                    "tier1"),  # 1500 RPD
        ("deepseek", "deepseek-chat",                          "tier1"),
        ("openai",   "gpt-4.1-nano",                           "tier0"),
    ],
    "fast_chat": [
        ("groq",     "llama-3.1-8b-instant",                   "tier1"),
        ("cerebras", "llama3.1-8b",                            "tier1"),
        ("gemini",   "gemma-4-26b-a4b-it",                    "tier1"),  # 1500 RPD, unlimited TPM
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),  # 500 RPD
        ("openai",   "gpt-4.1-nano",                           "tier0"),
    ],
    "long_context": [
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),  # 500 RPD, 1M context
        ("deepseek", "deepseek-chat",                          "tier1"),
        ("groq",     "llama-3.3-70b-versatile",                "tier1"),
        ("openai",   "gpt-4.1",                                "tier0"),
    ],
    "vision": [
        ("groq",     "meta-llama/llama-4-scout-17b-16e-instruct", "tier1"),
        ("gemini",   "gemini-3.1-flash-lite-preview",          "tier1"),
        ("gemini",   "gemma-4-26b-a4b-it",                    "tier1"),
        ("openai",   "gpt-4.1-mini",                           "tier0"),
        ("openai",   "gpt-4.1",                                "tier0"),
    ],
    "uncensored": [
        ("deepseek", "deepseek-chat",                          "tier1"),
        ("groq",     "llama-3.3-70b-versatile",                "tier1"),
        ("openai",   "gpt-4.1",                                "tier0"),
    ],
    "internal_fast": [
        ("groq",     "llama-3.1-8b-instant",                   "tier1"),
        ("cerebras", "llama3.1-8b",                            "tier1"),
        ("gemini",   "gemma-4-26b-a4b-it",                    "tier1"),  # 1500 RPD
        ("openai",   "gpt-4.1-nano",                           "tier0"),
    ],
    "safety": [
        ("groq",     "meta-llama/llama-guard-4-12b",           "tier1"),
    ],
}

# Model family patterns for deprecation replacement matching
MODEL_FAMILIES = {
    "llama":    ["llama"],
    "qwen":     ["qwen"],
    "deepseek": ["deepseek"],
    "gemma":    ["gemma"],
    "gpt":      ["gpt"],
    "gemini":   ["gemini"],
    # "dolphin":  ["dolphin"],  # removed — no local LLM
}
