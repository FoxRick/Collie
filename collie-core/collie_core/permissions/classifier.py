"""Translate arbitrary tool calls into stable permission requests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from collie_core.permissions.defaults import HARD_APPROVAL_ACTIONS, SECRET_WORDS
from collie_core.permissions.models import PermissionRequest, Risk, Scope

_READ_PREFIXES = ("web_search", "web_fetch", "weather", "news")
_DELETE_WORDS = ("delete", "remove", "clear", "purge")
_SEND_WORDS = ("send", "publish", "post", "purchase", "buy", "pay")
_PATH_KEYS = ("path", "file", "filename", "directory", "folder")
_RESOURCE_KEYS = ("url", "path", "file", "folder", "recipient", "to", "service", "account")


def redact_parameters(params: Mapping[str, Any]) -> dict[str, Any]:
    def clean(key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(word in lowered for word in SECRET_WORDS):
            return "[redacted]"
        if isinstance(value, Mapping):
            return {str(k): clean(str(k), v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(key, item) for item in value[:20]]
        text = str(value)
        return text if len(text) <= 240 else text[:237] + "..."

    return {str(key): clean(str(key), value) for key, value in params.items()}


def _resource(name: str, params: Mapping[str, Any]) -> str:
    for key in _RESOURCE_KEYS:
        value = params.get(key)
        if value:
            if key in _PATH_KEYS:
                try:
                    return str(Path(str(value)).expanduser().resolve(strict=False))
                except OSError:
                    return str(value)
            return str(value)
    return name


def classify_tool(tool: Any, name: str, params: Mapping[str, Any]) -> PermissionRequest:
    custom = getattr(tool, "permission_request", None)
    if callable(custom):
        request = custom(dict(params))
        if isinstance(request, PermissionRequest):
            return request

    lowered = name.lower()
    operation = str(params.get("action") or "").lower()
    read_only = bool(getattr(tool, "read_only", False))
    if any(word in operation for word in _DELETE_WORDS):
        # Multi-action wrappers must not make delete-like operations look like
        # harmless reads just because their tool name is innocuous.
        action, risk = "delete.destructive", Risk.DESTRUCTIVE
    elif read_only or lowered.startswith(_READ_PREFIXES):
        action, risk = _read_action(lowered), Risk.READ
    elif lowered == "message":
        # Cross-channel/proactive message delivery (including attachments):
        # always a hard-approval external write.
        action, risk = "message.send", Risk.EXTERNAL_WRITE
    elif any(word in lowered for word in _DELETE_WORDS):
        action, risk = "delete.destructive", Risk.DESTRUCTIVE
    elif any(word in lowered for word in ("purchase", "buy", "pay")):
        action, risk = "financial.purchase", Risk.SENSITIVE
    elif any(word in lowered for word in ("send_email", "email_send")):
        action, risk = "email.send", Risk.EXTERNAL_WRITE
    elif any(word in lowered for word in _SEND_WORDS):
        action, risk = "external.publish", Risk.EXTERNAL_WRITE
    elif lowered.startswith("mcp_"):
        action, risk = f"mcp.{lowered[4:]}", Risk.EXTERNAL_WRITE
    else:
        action, risk = f"tool.{lowered}", Risk.LOCAL_WRITE

    hard = action in HARD_APPROVAL_ACTIONS or risk in {Risk.SENSITIVE, Risk.DESTRUCTIVE}
    return PermissionRequest(
        action=action,
        resource=_resource(name, params),
        risk=risk,
        summary=f"Use {name}",
        reversible=risk not in {Risk.SENSITIVE, Risk.DESTRUCTIVE},
        data_leaving_device=("*",) if risk == Risk.EXTERNAL_WRITE else (),
        suggested_scope=Scope.SERVICE if lowered.startswith("mcp_") else Scope.ONCE,
        redacted_parameters=redact_parameters(params),
        hard_approval=hard,
    )


def _read_action(name: str) -> str:
    if "weather" in name:
        return "weather.read"
    if name.startswith(("web_", "search_")):
        return "web.read"
    if "memory" in name:
        return "memory.read"
    if name.startswith("mcp_"):
        return "service.read"
    return "conversation.read"
