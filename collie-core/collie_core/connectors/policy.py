"""Compile MCP hints and curated overrides into stable connector risk policy."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_READ_WORDS = ("search", "list", "get", "read", "find", "fetch", "lookup", "view")
_IMPORTANT_WORDS = ("send", "publish", "invite", "share", "pay", "purchase")
_DESTRUCTIVE_WORDS = ("delete", "revoke", "remove", "cancel", "purge")


def _annotation(annotations: Any, *names: str) -> bool:
    for name in names:
        value = (
            annotations.get(name)
            if isinstance(annotations, dict)
            else getattr(annotations, name, None)
        )
        if value is True:
            return True
    return False


def classify_connector_tool(
    name: str,
    annotations: Any = None,
    *,
    trusted: bool = False,
    overrides: dict[str, str] | None = None,
) -> str:
    """Return read, change, important, or destructive.

    Unknown tools from untrusted/custom servers remain changes and therefore
    require approval.
    """
    lowered = name.lower()
    override = (overrides or {}).get(name) or (overrides or {}).get(lowered)
    if override in {"read", "change", "important", "destructive"}:
        return override
    if _annotation(annotations, "destructiveHint", "destructive_hint"):
        return "destructive"
    if any(word in lowered for word in _DESTRUCTIVE_WORDS):
        return "destructive"
    if any(word in lowered for word in _IMPORTANT_WORDS):
        return "important"
    if trusted and _annotation(annotations, "readOnlyHint", "read_only_hint"):
        return "read"
    if trusted and any(lowered.startswith(word) for word in _READ_WORDS):
        return "read"
    return "change"


def cached_tool(tool: Any, *, trusted: bool, overrides: dict[str, str]) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or {}
    annotations = getattr(tool, "annotations", None)
    annotations_data = (
        annotations.model_dump(mode="json", exclude_none=True)
        if hasattr(annotations, "model_dump")
        else annotations
        if isinstance(annotations, dict)
        else {}
    )
    encoded = json.dumps(schema, sort_keys=True, default=str).encode("utf-8")
    return {
        "name": str(tool.name),
        "schema_hash": hashlib.sha256(encoded).hexdigest(),
        "annotations": annotations_data,
        "risk": classify_connector_tool(
            str(tool.name), annotations, trusted=trusted, overrides=overrides
        ),
    }
