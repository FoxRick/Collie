"""Snapshot trimming + schema validation shared by the builder, loader and refresh.

Kept out of ``catalog/__init__.py`` so ``refresh`` can import it without a
circular import (``__init__`` imports ``refresh`` for the store API).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from collie_core.catalog.curated import CURATED_ORDER, CURATED_PROVIDERS

SCHEMA_VERSION = 1


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_catalogue_schema(snapshot: Any) -> bool:
    """Schema-validate a catalogue snapshot before it is trusted.

    Catches truncated fetches, HTML error pages, and wrong-shaped payloads
    before they can replace the good snapshot.
    """
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        return False
    if not isinstance(snapshot.get("providers"), list) or not snapshot["providers"]:
        return False
    if not isinstance(snapshot.get("source"), dict):
        return False
    for entry in snapshot["providers"]:
        if not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            return False
        models = entry.get("models")
        if not isinstance(models, dict):
            return False
    return True


def trim_live_catalogue(raw: dict[str, Any], generated_at: str) -> dict[str, Any] | None:
    """Trim a raw models.dev api.json payload into a Collie snapshot.

    Keeps only curated providers and the fields Collie needs. Returns None
    when the payload is not a usable models.dev document.
    """
    if not isinstance(raw, dict):
        return None
    raw_providers = raw.get("providers")
    if not isinstance(raw_providers, dict):
        # models.dev api.json keys ARE the provider slugs (no wrapper key).
        raw_providers = raw
    if not raw_providers:
        return None
    providers_out: list[dict[str, Any]] = []
    for pid in CURATED_ORDER:
        raw_entry = raw_providers.get(pid)
        if not isinstance(raw_entry, dict):
            continue
        raw_models = raw_entry.get("models")
        if not isinstance(raw_models, dict):
            continue
        curated = CURATED_PROVIDERS.get(pid) or {}
        models_out: dict[str, dict[str, str]] = {}
        for mid, meta in raw_models.items():
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or mid)
            models_out[str(mid)] = {"name": name}
        if not models_out:
            continue
        entry: dict[str, Any] = {
            "id": pid,
            "name": str(curated.get("name") or raw_entry.get("name") or pid),
            "auth_type": curated.get("auth_type") or "api-key",
            "protocol": curated.get("protocol") or "openai",
            "api_base": curated.get("api_base") or raw_entry.get("api"),
            "default_model": curated.get("default_model") or next(iter(models_out)),
            "key_prefixes": curated.get("key_prefixes") or [],
            "tested": bool(curated.get("tested", False)),
            "doc_url": raw_entry.get("doc"),
            "help_url": curated.get("help_url"),
            "models": models_out,
        }
        providers_out.append(entry)
    if not providers_out:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": {
            "url": "https://models.dev/api.json",
            "sha256": sha256_hex(json.dumps(raw, sort_keys=True).encode("utf-8")),
            "providers_count": len(raw_providers),
        },
        "providers": providers_out,
    }
