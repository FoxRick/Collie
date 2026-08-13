"""Optional weekly models.dev catalogue refresh.

The bundled snapshot (``collie_core/catalog/snapshot.json``) is the runtime
fallback and always works offline. This module implements the optional,
HTTPS-fetched, schema-validated refresh: it re-fetches models.dev api.json,
re-trims it with the curated layer, and only then persists it as a settings
overlay, retaining the accepted snapshot's version + sha256 so Settings can
show "catalogue updated X ago" and rollback can return to the last-good
bundled snapshot.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from collie_core.catalog.snapshot_util import trim_live_catalogue

CATALOGUE_VERSION_SETTING = "catalogue.refresh.version"
CATALOGUE_HASH_SETTING = "catalogue.refresh.sha256"
CATALOGUE_REFRESHED_AT_SETTING = "catalogue.refresh.updated_at"
CATALOGUE_SNAPSHOT_SETTING = "catalogue.snapshot"

MODELS_DEV_URL = "https://models.dev/api.json"
_FETCH_TIMEOUT_SECONDS = 20
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Collie/alpha (catalogue refresh)"},
    )
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
        data = response.read(_MAX_PAYLOAD_BYTES + 1)
    if len(data) > _MAX_PAYLOAD_BYTES:
        raise ValueError("models.dev payload exceeded the size limit")
    return data


async def fetch_and_trim_catalogue(
    settings: Any,
    url: str | None = None,
) -> dict[str, Any]:
    """Fetch, validate and persist a refreshed catalogue snapshot.

    On any failure the previous snapshot (bundled or last accepted) is left
    untouched and a human error is returned.
    """
    try:
        data = await _fetch_async(url or MODELS_DEV_URL)
        raw = json.loads(data.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return {
            "refreshed": False,
            "error": "I couldn't reach the provider catalogue right now. "
            "Collie keeps using the bundled one — try again later.",
            "detail": str(error),
        }

    trimmed = trim_live_catalogue(raw, generated_at=_now_iso())
    if trimmed is None:
        return {
            "refreshed": False,
            "error": "The provider catalogue came back in a shape I don't trust, "
            "so I kept the bundled one.",
            "detail": "schema validation failed",
        }

    serialized = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"))
    source = trimmed.get("source") or {}
    settings.set_setting(CATALOGUE_VERSION_SETTING, str(source.get("sha256") or "")[:16])
    settings.set_setting(CATALOGUE_HASH_SETTING, str(source.get("sha256") or ""))
    settings.set_setting(CATALOGUE_REFRESHED_AT_SETTING, str(trimmed.get("generated_at") or ""))
    settings.set_setting(CATALOGUE_SNAPSHOT_SETTING, serialized)
    return {
        "refreshed": True,
        "version": str(source.get("sha256") or "")[:16],
        "sha256": str(source.get("sha256") or ""),
        "refreshed_at": trimmed.get("generated_at"),
        "providers_count": len(trimmed.get("providers") or []),
    }


async def _fetch_async(url: str) -> bytes:
    """Run the blocking fetch off the event loop."""
    import asyncio

    return await asyncio.to_thread(_fetch, url)
