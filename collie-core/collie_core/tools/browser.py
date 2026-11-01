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
            "operation": {"type": "string", "enum": sorted(OPERATIONS - {"upload", "download"})},
            "session_id": {"type": "string", "maxLength": 64},
            "url": {"type": "string", "maxLength": 2048},
            "ref": {"type": "string", "maxLength": 64},
            "observation_id": {"type": "string", "maxLength": 64},
            "tab_id": {"type": "string", "maxLength": 64},
            "checked": {"type": "boolean"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "key": {
                "type": "string",
                "enum": [
                    "Tab",
                    "Shift+Tab",
                    "ArrowDown",
                    "ArrowUp",
                    "ArrowLeft",
                    "ArrowRight",
                    "Escape",
                ],
            },
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
            "Use Collie's local visible Edge browser. Open, observe bounded untrusted text and "
            "exposed controls, then act using ref AND observation_id from the latest result. "
            "No CSS/JS. References expire after 60 seconds and after each observation/action. "
            "Fill ordinary fields, select option by text label, check with checked boolean, "
            "key for navigation only, scroll up/down, new_tab, tab by tab_id, close_tab. "
            "Approve_destination by URL before navigating to a new origin. Every action "
            "requires central explicit approval; submit separately authorizes a final action. "
            "Use handoff for user sign-in, then resume with the visible destination URL and "
            "explicit reauthorization; never type secrets. Upload/download need user file picker "
            "in the Browser panel. Screenshot is for human inspection only; images are not sent "
            "to your model. No personal profiles, arbitrary code, or shared-session access. "
            "Inspect fresh results to verify outcomes; never replay an uncertain write."
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
                    "message": "Capture is for the user in the Browser panel. Your model has not inspected this image.",
                }
            return ToolResult(json.dumps(result))
        except ValueError as exc:
            return ToolResult.error(str(exc))
