"""Email tool: Gmail or Outlook via MCP.

When no email service is connected, this tool nudges the user toward
Settings → Services. Once connected, it points the model at the service's
registered MCP tools (``mcp_gmail_*`` / ``mcp_outlook_*``).
"""

from __future__ import annotations

from typing import Any

from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["EmailTool"]

_EMAIL_SERVICES = ("gmail", "outlook")


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "read", "search", "draft"],
            "description": "list recent emails, read one, search, or draft a reply.",
        },
        "query": {
            "type": "string",
            "description": "For action=search: what to look for.",
        },
        "email_id": {
            "type": "string",
            "description": "For action=read: the email's ID.",
        },
        "reply_text": {
            "type": "string",
            "description": "For action=draft: the draft reply text.",
        },
    },
    "required": ["action"],
})
class EmailTool(Tool):
    """Manage email (Gmail / Outlook via MCP)."""

    @property
    def name(self) -> str:
        return "email"

    @property
    def description(self) -> str:
        return (
            "Check your inbox, read emails, search, or draft replies. "
            "Connect Gmail or Outlook in Settings → Services first."
        )

    @property
    def read_only(self) -> bool:
        return False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> "EmailTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()

        connected = connected_service_id(*_EMAIL_SERVICES)
        if connected is not None:
            task = {
                "list": "list recent emails",
                "read": "read the email",
                "search": "search the mailbox",
                "draft": "draft the reply",
            }.get(action, "work with email")
            return mcp_tools_hint(connected, task)

        if action == "list":
            return (
                "I'd love to sort through your inbox, but you haven't connected "
                "an email account yet! Go to **Settings → Services** and connect "
                "Gmail or Outlook — it takes one click."
            )

        if action == "search":
            query = str(kwargs.get("query") or "")
            friendly = f' for "{query}"' if query else ""
            return (
                "I'd dig through your mail{query_hint}, but you need to connect "
                "an email account first. Head to **Settings → Services** — Gmail "
                "or Outlook, one click and I'll do the rest!"
            ).format(query_hint=friendly)

        if action == "read":
            return (
                "I'd pull up that email, but you haven't connected an email "
                "account yet! Connect Gmail or Outlook in **Settings → Services** "
                "and I'll fetch it right away."
            )

        if action == "draft":
            return (
                "I'd draft that reply for you, but first connect your email in "
                "**Settings → Services**. Gmail or Outlook — just pick one!"
            )

        return self.error(
            f"Not sure what to do with action '{action}'. Try list, read, search, or draft."
        )
