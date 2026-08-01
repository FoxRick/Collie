"""Collie settings: build the engine ``Config`` from SQLite, not JSON files.

Collie never asks the user to edit a config file. All user-facing settings
live in ``collie.db`` (settings table), and secrets live in the OS keychain
(delivered to the Python core by the Electron shell via IPC or environment).

This module translates those settings into the Pydantic ``Config`` object the
engine (stripped nanobot) expects at construction time.

Recognized settings keys (all optional; sensible defaults otherwise):
- ``provider.name``       e.g. "openai", "anthropic", "openrouter", "custom"
- ``provider.model``      default model id
- ``provider.api_base``   custom base URL (OpenAI-compatible endpoints)
- ``provider.secret_name`` keychain entry used by the active connection
- ``agent.timezone``      IANA timezone name
- ``agent.bot_name``      display name (default "Collie")
- ``agent.max_tool_iterations``

Secrets are injected at runtime by the shell (never persisted in SQLite):
- env ``COLLIE_PROVIDER_API_KEY`` or ``set_api_key()`` before ``build_config``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from collie_core.db import CollieDB, collie_home

__all__ = [
    "build_config",
    "delete_api_key",
    "ensure_workspace",
    "get_api_key",
    "restore_api_key",
    "set_api_key",
    "workspace_path",
]

_runtime_api_keys: dict[str, str] = {}

_DEFAULT_VISION = """# Collie's Personality

You are Collie, a helpful personal assistant. You are direct, warm, and a
bit playful — a smart, loyal dog who can type. You remember what matters to
your user and proactively help.

You never use jargon or technical terms unless the user does first. You
explain things simply. You speak in first person and keep a light touch of
dog humor ("I dug up some results") without overdoing it.

You never use corporate language. Never say "processing your request",
"engaging with your query", or "How may I assist you today?".

When the user asks you to do something that affects their accounts or data,
confirm before acting.
"""

_DEFAULT_AGENTS = """# About Me

Tell Collie about your life, work, and preferences here — or just chat, and
Collie will learn as you go. You can edit this any time in
Settings → Context.
"""


def set_api_key(provider: str, key: str) -> None:
    """Inject a provider API key at runtime (from keychain via IPC)."""
    _runtime_api_keys[provider.lower()] = key


def delete_api_key(provider: str) -> None:
    """Forget a provider API key from the running process."""
    _runtime_api_keys.pop(provider.lower(), None)


def get_api_key(provider: str) -> str | None:
    """Return a transient key for transactional runtime configuration.

    This accessor is core-internal.  IPC responses must never serialize its
    return value; it exists so a failed provider replacement can put the
    previous in-memory credential back exactly as it was.
    """
    return _runtime_api_keys.get(provider.lower())


def restore_api_key(provider: str, key: str | None) -> None:
    """Restore a prior transient key, clearing it when none existed."""
    if key:
        set_api_key(provider, key)
    else:
        delete_api_key(provider)


def workspace_path() -> Path:
    return collie_home() / "workspace"


def ensure_workspace() -> Path:
    """Create ``~/.collie/workspace`` with default VISION.md / AGENTS.md."""
    ws = workspace_path()
    (ws / "subagents").mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    vision = ws / "VISION.md"
    if not vision.exists():
        vision.write_text(_DEFAULT_VISION, encoding="utf-8")
    agents = ws / "AGENTS.md"
    if not agents.exists():
        agents.write_text(_DEFAULT_AGENTS, encoding="utf-8")
    return ws


def _api_key_for(provider: str) -> str:
    key = _runtime_api_keys.get(provider.lower())
    if key:
        return key
    env_specific = os.environ.get(f"COLLIE_{provider.upper()}_API_KEY")
    if env_specific:
        return env_specific
    return os.environ.get("COLLIE_PROVIDER_API_KEY", "")


# Default model per provider when the user hasn't picked one. The engine's
# own default is an Anthropic model, which other APIs reject outright.
_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-v4-pro",
    "openrouter": "anthropic/claude-sonnet-4-6",
    "moonshot": "kimi-k3",
    "groq": "llama-4-maverick",
}


def default_model_for(provider: str) -> str | None:
    return _DEFAULT_MODELS.get(provider.lower())


def build_config(db: CollieDB, *, mcp_servers: dict[str, Any] | None = None) -> Any:
    """Build the engine ``Config`` from SQLite settings.

    Returns a ``nanobot.config.schema.Config`` instance configured for the
    Collie workspace with the user's chosen provider and model.
    ``mcp_servers`` carries the connected-service MCP configs from the
    ``ServiceManager`` (Settings → Services).
    """
    from nanobot.config.schema import Config

    settings = db.all_settings()
    ws = ensure_workspace()

    provider_name = str(settings.get("provider.name") or "openai").lower()
    secret_name = str(settings.get("provider.secret_name") or provider_name)
    model = settings.get("provider.model") or default_model_for(provider_name)
    api_base = settings.get("provider.api_base") or None

    provider_section: dict[str, Any] = {"apiKey": _api_key_for(secret_name)}
    if api_base:
        provider_section["apiBase"] = api_base

    data: dict[str, Any] = {
        "agents": {
            "defaults": {
                "workspace": str(ws),
                "provider": provider_name,
                "botName": str(settings.get("agent.bot_name") or "Collie"),
            }
        },
        "providers": {provider_name: provider_section},
        "channels": {},
        "tools": {
            "mcpServers": dict(mcp_servers or {}),
            "restrictToWorkspace": True,
        },
    }
    if model:
        data["agents"]["defaults"]["model"] = model
    if settings.get("agent.timezone"):
        data["agents"]["defaults"]["timezone"] = settings["agent.timezone"]
    if settings.get("agent.max_tool_iterations"):
        try:
            iterations = int(settings["agent.max_tool_iterations"])
        except (TypeError, ValueError):
            iterations = 0
        # A sane bound: garbage, zero, or negatives must not reach the engine
        # (where 0/negative breaks the tool loop entirely).
        data["agents"]["defaults"]["maxToolIterations"] = min(2000, max(1, iterations))

    return Config.model_validate(data)
