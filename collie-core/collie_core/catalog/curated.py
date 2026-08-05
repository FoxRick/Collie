"""Collie-owned curated layer over the models.dev catalogue.

This is the small, hand-maintained overlay the strategy doc requires: it
defines which providers Collie shows, the friendly display name, the auth
type, the protocol/adapter, the implied base URL when models.dev has none,
a sensible default model, the help link, key prefixes used for paste-time
auto-detection, and whether Collie has actually tested the provider.

The bundled snapshot (``snapshot.json``) is generated from the live
models.dev feed by ``tools/update_catalogue_snapshot.py``; this curated layer
is merged over it at generation time (and at refresh time, so a refresh never
loses the Collie-owned overrides).
"""

from __future__ import annotations

# id -> curated metadata. ``models`` is intentionally NOT here — it comes
# from the models.dev snapshot. Everything else is Collie-owned truth.
CURATED_PROVIDERS: dict[str, dict[str, object]] = {
    "openai": {
        "name": "OpenAI",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-5.5",
        "key_prefixes": ["sk-proj-", "sk-"],
        "tested": True,
    },
    "anthropic": {
        "name": "Anthropic",
        "auth_type": "api-key",
        "protocol": "anthropic",
        "api_base": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6",
        "key_prefixes": ["sk-ant-"],
        "tested": True,
    },
    "deepseek": {
        "name": "DeepSeek",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "key_prefixes": ["sk-"],
        "tested": True,
    },
    "openrouter": {
        "name": "OpenRouter",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://openrouter.ai/api/v1",
        "default_model": "~openai/gpt-mini-latest",
        "key_prefixes": ["sk-or-"],
        "tested": True,
    },
    "groq": {
        "name": "Groq",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_prefixes": ["gsk_"],
        "tested": True,
    },
    "google": {
        "name": "Google Gemini",
        "auth_type": "api-key",
        "protocol": "openai",
        # OpenAI-compatible endpoint Google exposes for Gemini API keys.
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-flash-latest",
        "key_prefixes": ["AIza"],
        "tested": False,
    },
    "xai": {
        "name": "xAI",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.x.ai/v1",
        "default_model": "grok-4.3",
        "key_prefixes": ["xai-"],
        "tested": False,
    },
    "perplexity": {
        "name": "Perplexity",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.perplexity.ai",
        "default_model": "sonar",
        "key_prefixes": ["pplx-"],
        "tested": False,
    },
    "mistral": {
        "name": "Mistral",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-2506",
        "key_prefixes": [],
        "tested": False,
    },
    "fireworks-ai": {
        "name": "Fireworks AI",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/deepseek-v4-flash",
        "key_prefixes": [],
        "tested": False,
    },
    "zhipuai": {
        "name": "Zhipu AI",
        "auth_type": "api-key",
        "protocol": "openai",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.7-flash",
        "key_prefixes": [],
        "tested": False,
    },
}

# Order shown in the picker (a stable, curated order beats dict order).
CURATED_ORDER: tuple[str, ...] = (
    "openai",
    "anthropic",
    "deepseek",
    "openrouter",
    "groq",
    "google",
    "xai",
    "perplexity",
    "mistral",
    "fireworks-ai",
    "zhipuai",
)
