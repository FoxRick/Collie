"""Agent entry to the same native controller used by the desktop UI."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from collie_core.native_control import NativeControl
from collie_core.permissions.models import ExecutionContext, PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context

_controller: NativeControl | None = None


def bind_native_control(controller: NativeControl) -> None:
    global _controller
    _controller = controller


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["observe", "focus", "click", "type", "scroll", "stop"],
            },
            "text": {
                "type": "string",
                "description": "Full replacement editor text; maximum 16000 characters.",
            },
            "direction": {"type": "string", "enum": ["up", "down"]},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
)
class NativeControlTool(Tool):
    name = "native_control"
    description = (
        "Interact with the unsaved classic Notepad window the user selected in this private desktop chat. "
        "Observe returns editor metadata only. Click means focus editor; type replaces all text. "
        "Each action asks approval. No save, menus, shortcuts, external actions or screenshots. Stop releases selection."
    )
    read_only = False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        stopping = params.get("operation") == "stop"
        return PermissionRequest(
            action="native.stop" if stopping else "native.dispatch",
            resource="selected editor",
            risk=Risk.READ if stopping else Risk.SENSITIVE,
            summary="Stop native control"
            if stopping
            else "Request an action in the selected editor",
            reversible=stopping,
            hard_approval=not stopping,
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> NativeControlTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        request = current_request_context()
        if not request or request.channel != "collie" or _controller is None:
            return self.error("Native control needs a private desktop chat with a selected window.")
        metadata = request.metadata.get("permission_context", {})
        if not isinstance(metadata, dict):
            return self.error("Native control needs a private desktop chat context.")
        context = ExecutionContext(
            **{f.name: metadata[f.name] for f in fields(ExecutionContext) if f.name in metadata}
        )
        if kwargs.get("operation") == "stop":
            _controller.stop(context.conversation_id or "")
            return json.dumps({"stopped": True})
        try:
            return json.dumps(await _controller.execute(context, **kwargs))
        except ValueError as error:
            return self.error(str(error))
