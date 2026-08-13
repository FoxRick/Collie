"""Bundled provider catalogue (models.dev snapshot + Collie curated layer).

``CatalogueStore`` is the single access point for onboarding: it serves the
provider list for the picker, the default model per provider, the base URL,
key-prefix hints for paste-time auto-detection, and friendly model names for
the "Connected — using …" confirmation. It also owns the optional weekly
refresh (``refresh.py``) with version/hash retention and rollback to the
last-good bundled snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from collie_core.catalog.curated import CURATED_ORDER, CURATED_PROVIDERS
from collie_core.catalog.refresh import (
    CATALOGUE_HASH_SETTING,
    CATALOGUE_REFRESHED_AT_SETTING,
    CATALOGUE_VERSION_SETTING,
    fetch_and_trim_catalogue,
)
from collie_core.catalog.snapshot_util import (
    SCHEMA_VERSION,
    validate_catalogue_schema,
)

__all__ = ["CatalogueStore", "BUNDLED_SNAPSHOT_PATH", "SCHEMA_VERSION"]

BUNDLED_SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshot.json"


class CatalogueStore:
    """Load, validate and query the provider catalogue snapshot."""

    def __init__(self, settings: Any | None = None, snapshot_path: Path | None = None):
        self._settings = settings
        self._snapshot_path = snapshot_path or BUNDLED_SNAPSHOT_PATH
        self._snapshot: dict[str, Any] | None = None
        self._providers: dict[str, dict[str, Any]] = {}
        self._model_names: dict[str, dict[str, str]] = {}
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        """Load the bundled snapshot, then merge the live-refreshed overlay."""
        # Reset first: when both the bundled file and the overlay are gone
        # (rollback), a stale snapshot must not survive.
        self._snapshot = None
        if self._snapshot_path.is_file():
            try:
                self._snapshot = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._snapshot = None
        if self._snapshot and not validate_catalogue_schema(self._snapshot):
            self._snapshot = None
        if self._settings is not None:
            refreshed = str(self._settings.get_setting("catalogue.snapshot", "") or "")
            if refreshed:
                try:
                    candidate = json.loads(refreshed)
                except (TypeError, ValueError):
                    candidate = None
                if candidate is not None and validate_catalogue_schema(candidate):
                    self._snapshot = candidate
        self._index()

    def _index(self) -> None:
        providers: dict[str, dict[str, Any]] = {}
        model_names: dict[str, dict[str, str]] = {}
        for entry in (self._snapshot or {}).get("providers", []):
            pid = str(entry.get("id") or "")
            if not pid:
                continue
            models = entry.get("models") or {}
            if isinstance(models, dict):
                model_names[pid] = {
                    str(mid): str(meta.get("name") or mid)
                    for mid, meta in models.items()
                    if isinstance(meta, dict)
                }
            providers[pid] = entry
        self._providers = providers
        self._model_names = model_names

    # -- public queries ----------------------------------------------------

    def snapshot_metadata(self) -> dict[str, Any]:
        source = (self._snapshot or {}).get("source") or {}
        return {
            "schema_version": (self._snapshot or {}).get("schema_version"),
            "generated_at": (self._snapshot or {}).get("generated_at"),
            "source_url": source.get("url"),
            "source_sha256": source.get("sha256"),
            "source_providers_count": source.get("providers_count"),
        }

    def refresh_state(self) -> dict[str, Any]:
        """Quiet Settings display: when the live catalogue was last accepted."""
        if self._settings is None:
            return {"available": False}
        return {
            "available": True,
            "version": str(self._settings.get_setting(CATALOGUE_VERSION_SETTING, "") or ""),
            "sha256": str(self._settings.get_setting(CATALOGUE_HASH_SETTING, "") or ""),
            "refreshed_at": str(
                self._settings.get_setting(CATALOGUE_REFRESHED_AT_SETTING, "") or ""
            ),
        }

    def providers(self) -> list[dict[str, Any]]:
        """Curated providers in picker order with merged snapshot data."""
        out: list[dict[str, Any]] = []
        ordered = list(CURATED_ORDER) + [
            pid for pid in sorted(self._providers) if pid not in CURATED_ORDER
        ]
        for pid in ordered:
            curated = dict(CURATED_PROVIDERS.get(pid) or {})
            entry = self._providers.get(pid)
            if entry is None:
                continue
            merged = {
                "id": pid,
                "name": curated.get("name") or entry.get("name") or pid,
                "auth_type": curated.get("auth_type") or "api-key",
                "protocol": curated.get("protocol") or "openai",
                "api_base": curated.get("api_base") or entry.get("api_base"),
                "default_model": curated.get("default_model") or entry.get("default_model"),
                "key_prefixes": curated.get("key_prefixes") or [],
                "tested": bool(curated.get("tested", False)),
                "help_url": entry.get("help_url") or entry.get("doc_url"),
                "models": [
                    {"id": mid, "name": self._model_names.get(pid, {}).get(mid, mid)}
                    for mid in (entry.get("models") or {})
                ],
            }
            out.append(merged)
        return out

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return next(
            (p for p in self.providers() if p["id"] == provider_id),
            None,
        )

    def default_model(self, provider_id: str) -> str | None:
        provider = self.get_provider(provider_id)
        if provider is None:
            return None
        return str(provider.get("default_model") or "") or None

    def api_base(self, provider_id: str) -> str | None:
        provider = self.get_provider(provider_id)
        if provider is None:
            return None
        return str(provider.get("api_base") or "") or None

    def model_label(self, provider_id: str, model_id: str | None) -> str | None:
        """Friendly model name for the connect confirmation copy."""
        if not model_id:
            return None
        names = self._model_names.get(provider_id, {})
        return names.get(model_id) or model_id

    # -- paste-time key detection -------------------------------------------

    def detect_provider_for_key(self, api_key: str) -> list[str]:
        """Ordered candidate provider ids for a pasted key (prefix hints only).

        Ambiguous prefixes return every matching provider; the caller resolves
        ambiguity with a live probe (the probe is the authority, not prefixes).
        """
        key = (api_key or "").strip()
        if not key:
            return []
        matches: list[tuple[int, str]] = []
        for provider in self.providers():
            for prefix in provider.get("key_prefixes") or []:
                prefix = str(prefix)
                if prefix and key.startswith(prefix):
                    matches.append((len(prefix), str(provider["id"])))
                    break
        if not matches:
            return []
        matches.sort(key=lambda item: item[0], reverse=True)
        longest = matches[0][0]
        # Only the longest matching prefix group counts: a key starting
        # ``sk-ant-`` is an Anthropic key, not an ambiguous ``sk-`` key.
        return [pid for length, pid in matches if length == longest]

    # -- refresh / rollback --------------------------------------------------

    async def refresh(self, url: str | None = None) -> dict[str, Any]:
        """Fetch the live models.dev feed, validate, trim, and persist.

        The previous snapshot stays available: the accepted snapshot (with its
        version + sha256) is retained in settings, and ``rollback()`` restores
        the bundled file as the last-good source.
        """
        if self._settings is None:
            return {"refreshed": False, "error": "Catalogue refresh needs a settings store."}
        result = await fetch_and_trim_catalogue(settings=self._settings, url=url)
        if result.get("refreshed"):
            self._load()
        return result

    def rollback(self) -> dict[str, Any]:
        """Drop the live-refreshed overlay and return to the bundled snapshot."""
        if self._settings is None:
            return {"rolled_back": False, "error": "Catalogue refresh needs a settings store."}
        self._settings.delete_setting("catalogue.snapshot")
        self._settings.delete_setting(CATALOGUE_VERSION_SETTING)
        self._settings.delete_setting(CATALOGUE_HASH_SETTING)
        self._settings.delete_setting(CATALOGUE_REFRESHED_AT_SETTING)
        self._load()
        return {"rolled_back": True}
