"""Stable prompt/tool-schema/config hashing for run telemetry.

Pure, stdlib-only helpers that fingerprint exactly what a turn used:

- ``hash_system_prompt`` — the rendered system prompt text the model
  received (hashed at the point messages are final, never reconstructed).
- ``hash_tool_schema`` — the tool definitions presented to the model
  (``loop.tools.get_definitions()`` after registration).
- ``hash_config`` — model + provider + generation settings + limits from
  the ``build_config`` output, not raw SQLite rows.

All hashes are ``sha256:<hex>`` strings over stable JSON (sorted keys, no
whitespace) so identical inputs always hash identically across runs and
machines. Hashes are not secrets and need no redaction, but the values fed
to them (rendered prompts) must never be logged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "bind_prompt_hash_sources",
    "current_config_hash",
    "current_tool_schema_hash",
    "hash_config",
    "hash_system_prompt",
    "hash_tool_schema",
    "sha256_hex",
]


def sha256_hex(text: str) -> str:
    """Return ``sha256:<hex>`` for a text payload."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def hash_system_prompt(system_messages: list[str]) -> str:
    """Hash the rendered system prompt(s) for a turn.

    ``system_messages`` is the assembled system-role text the model
    actually received (typically one entry; multi-system turns join with
    a stable separator). Hashing the final rendered text — not a
    reconstruction from parts — keeps the fingerprint exact.
    """
    return sha256_hex("\n\n---\n\n".join(system_messages))


def hash_tool_schema(schemas: list[dict[str, Any]]) -> str:
    """Hash the tool schemas presented to the model.

    Stable ordering (``sort_keys``) means the same registry always hashes
    the same, regardless of insertion order.
    """
    return sha256_hex(_stable_json(schemas))


def hash_config(
    model: str,
    provider: str,
    generation: dict[str, Any],
    limits: dict[str, Any],
) -> str:
    """Hash the model/provider/generation/limits a turn ran under."""
    return sha256_hex(
        _stable_json(
            {
                "model": model,
                "provider": provider,
                "generation": generation,
                "limits": limits,
            }
        )
    )


# -- live loop sources -------------------------------------------------------
#
# The telemetry hook is per-turn and has no loop reference, so the runtime
# binds the *current* loop's tool schemas + config here after every
# ``_build_loop()`` (the module-binding pattern used by ``bind_life_db``).
# Hashes are computed lazily at read time so a stale reference can never
# leak a previous loop's fingerprint.

_current_tool_schemas: list[dict[str, Any]] | None = None
_current_model: str | None = None
_current_provider: str | None = None
_current_generation: dict[str, Any] | None = None
_current_limits: dict[str, Any] | None = None


def bind_prompt_hash_sources(
    *,
    tool_schemas: list[dict[str, Any]],
    model: str,
    provider: str,
    generation: dict[str, Any],
    limits: dict[str, Any],
) -> None:
    """Point the telemetry hook at the live loop's schemas and config."""
    global _current_tool_schemas, _current_model, _current_provider
    global _current_generation, _current_limits
    _current_tool_schemas = list(tool_schemas)
    _current_model = model
    _current_provider = provider
    _current_generation = dict(generation)
    _current_limits = dict(limits)


def current_tool_schema_hash() -> str | None:
    """Hash of the live loop's tool schemas, or ``None`` before any build."""
    if _current_tool_schemas is None:
        return None
    return hash_tool_schema(_current_tool_schemas)


def current_config_hash() -> str | None:
    """Hash of the live loop's config, or ``None`` before any build."""
    if _current_model is None or _current_provider is None:
        return None
    return hash_config(
        _current_model,
        _current_provider,
        _current_generation or {},
        _current_limits or {},
    )
