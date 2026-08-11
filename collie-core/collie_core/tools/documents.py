"""Documents tool: docs and files via MCP (F034, Step 39).

Points the model at the connected files service's MCP tools (Google Drive /
Dropbox); otherwise nudges the user toward Settings → Services.
"""

from __future__ import annotations

from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["DocumentsTool"]

_FILE_SERVICES = ("google-drive", "dropbox")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["find", "read", "summarize"],
                "description": "find a document, read one, or summarize it.",
            },
            "query": {
                "type": "string",
                "description": "The document name or what it's about.",
            },
        },
        "required": ["action"],
    }
)
class DocumentsTool(Tool):
    """Find and read documents (Google Drive / Dropbox via MCP)."""

    @property
    def name(self) -> str:
        return "documents"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        return PermissionRequest(
            action=f"documents.{action or 'read'}",
            resource=str(params.get("query") or "documents"),
            risk=Risk.READ,
            summary="Read your documents",
            reversible=True,
        )

    @property
    def description(self) -> str:
        return (
            "Find, read, or summarize the user's documents. Connect Google "
            "Drive or Dropbox in Settings → Services first."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> DocumentsTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()
        connected = connected_service_id(*_FILE_SERVICES)
        if connected is not None:
            task = {
                "find": "find the document",
                "read": "read the document",
                "summarize": "fetch the document to summarize",
            }.get(action, "work with documents")
            return mcp_tools_hint(connected, task)
        return (
            "I'd dig through your documents, but no files service is "
            "connected yet! Connect Google Drive or Dropbox in "
            "**Settings → Services** and I'll fetch anything you need."
        )
