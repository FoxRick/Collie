"""Chat entry point for the same browser implementation used by the UI."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from collie_core.browser.service import OPERATIONS, BrowserService
from collie_core.permissions.models import ExecutionContext, PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context

__all__ = ["BrowserTool", "bind_browser_service"]
_service: BrowserService | None = None


def bind_browser_service(service: BrowserService) -> None:
    global _service
    _service = service


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": sorted(OPERATIONS)},
            "session_id": {"type": "string", "maxLength": 64},
            "url": {"type": "string", "maxLength": 2048},
            "selector": {"type": "string", "maxLength": 512},
            "text": {"type": "string", "maxLength": 4000},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
)
class BrowserTool(Tool):
    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Use Collie's separate visible browser: open URL, observe untrusted page text, "
            "navigate within the approved origin, fill non-secret fields, click a visible CSS selector, "
            "capture screenshot, list or close. Every page action asks for explicit approval. "
            "Use handoff for sign-in and final submissions; automation then stops until closed. "
            "Never enter passwords, payment details or secrets. No Enter/JS/uploads/downloads. "
            "Browser sessions belong to this task; no personal profile or shared-session access."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        # Dispatch itself does nothing. BrowserService gates the real action,
        # equally for IPC and tools, using the resolved origin/session/task.
        return PermissionRequest(
            action="browser.dispatch",
            resource="browser",
            risk=Risk.READ,
            summary="Check browser request",
            reversible=True,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        request = current_request_context()
        if _service is None or request is None:
            return ToolResult.error("Browser is not available in this runtime.")
        metadata = request.metadata.get("permission_context", {})
        if not isinstance(metadata, dict) or not metadata.get("conversation_id"):
            return ToolResult.error("Browser needs an authorized local task context.")
        context = ExecutionContext(
            **{f.name: metadata[f.name] for f in fields(ExecutionContext) if f.name in metadata}
        )
        try:
            result = await _service.execute(context, kwargs)
            if "screenshot" in result:
                result = {
                    "session_id": result["session_id"],
                    "message": "Capture verified. View it in this chat's Browser panel.",
                }
            return ToolResult(json.dumps(result))
        except ValueError as exc:
            return ToolResult.error(str(exc))
