"""Notes tool: Apple Notes, Notion, or Obsidian via MCP.

When no notes service is connected, this tool nudges the user toward
Settings → Services. Once connected, it points the model at the service's
registered MCP tools (``mcp_notion_*`` / ``mcp_apple_notes_*``).
"""

from __future__ import annotations

from typing import Any

from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["NotesTool"]

_NOTES_SERVICES = ("notion", "apple-notes")


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "search", "list_recent"],
            "description": "create a note, search notes, or list recent ones.",
        },
        "title": {
            "type": "string",
            "description": "For action=create: the note's title.",
        },
        "content": {
            "type": "string",
            "description": "For action=create: the note's content.",
        },
        "query": {
            "type": "string",
            "description": "For action=search: what to look for.",
        },
    },
    "required": ["action"],
})
class NotesTool(Tool):
    """Manage notes (Apple Notes / Notion / Obsidian via MCP)."""

    @property
    def name(self) -> str:
        return "notes"

    @property
    def description(self) -> str:
        return (
            "Create, search, or list your notes. Connect Apple Notes, Notion, "
            "or Obsidian in Settings → Services first."
        )

    @property
    def read_only(self) -> bool:
        return False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> "NotesTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()

        connected = connected_service_id(*_NOTES_SERVICES)
        if connected is not None:
            task = {
                "create": "create the note",
                "search": "search the notes",
                "list_recent": "list recent notes",
            }.get(action, "work with notes")
            return mcp_tools_hint(connected, task)

        if action == "create":
            title = str(kwargs.get("title") or "Untitled")
            return (
                "I'd love to save that note '{title}' for you, but you haven't "
                "connected a notes app yet! Go to **Settings → Services** and "
                "connect Apple Notes, Notion, or Obsidian."
            ).format(title=title)

        if action == "search":
            query = str(kwargs.get("query") or "")
            friendly = f' for "{query}"' if query else ""
            return (
                "I'd search through your notes{query_hint}, but you need to "
                "connect a notes app first. Head to **Settings → Services** — "
                "Apple Notes, Notion, or Obsidian, one click away!"
            ).format(query_hint=friendly)

        if action == "list_recent":
            return (
                "I'd pull up your recent notes, but you haven't connected a "
                "notes app yet! Connect Apple Notes, Notion, or Obsidian in "
                "**Settings → Services**."
            )

        return self.error(
            f"Not sure what to do with action '{action}'. Try create, search, or list_recent."
        )
