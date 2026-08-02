"""Calendar tool: Google/Apple Calendar via MCP (F021).

When no calendar service is connected, this tool nudges the user toward
Settings → Services. Once connected, it points the model at the service's
registered MCP tools (``mcp_google_calendar_*``).
"""

from __future__ import annotations

from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["CalendarTool"]

_CALENDAR_SERVICES = ("google-calendar", "outlook")


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "create", "find_free"],
            "description": "list events, create a new event, or find free time slots.",
        },
        "date_range": {
            "type": "string",
            "description": "Date range like 'today', 'this week', or '2026-07-18..2026-07-25'.",
        },
        "title": {
            "type": "string",
            "description": "For action=create: the event title.",
        },
        "start": {
            "type": "string",
            "description": "For action=create: ISO start datetime.",
        },
        "end": {
            "type": "string",
            "description": "For action=create: ISO end datetime.",
        },
        "duration_minutes": {
            "type": "integer",
            "description": "For action=find_free: minimum slot in minutes (default 30).",
        },
    },
    "required": ["action"],
})
class CalendarTool(Tool):
    """Manage calendar events (GCal / Apple Calendar via MCP)."""

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return (
            "Check your calendar, create events, or find free time slots. "
            "Connect Google Calendar or Apple Calendar in Settings → Services first."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        if action in {"list", "find_free"}:
            return PermissionRequest(
                action=f"calendar.{action}",
                resource="calendar",
                risk=Risk.READ,
                summary="Check your calendar",
                reversible=True,
            )
        return PermissionRequest(
            action="calendar.create",
            resource="calendar",
            risk=Risk.LOCAL_WRITE,
            summary="Create a calendar event",
            reversible=True,
            approval_free=True,
            approve_for_me=True,
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> "CalendarTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip()

        connected = connected_service_id(*_CALENDAR_SERVICES)
        if connected is not None:
            task = {
                "list": "list calendar events",
                "create": "create the event",
                "find_free": "find free time slots",
            }.get(action, "work with the calendar")
            return mcp_tools_hint(connected, task)

        if action == "list":
            date_range = str(kwargs.get("date_range") or "today")
            return (
                "I'd love to check your calendar for {date_range}, but you haven't "
                "connected a calendar yet! Head to **Settings → Services** and "
                "connect Google Calendar or Apple Calendar — it takes just a click."
            ).format(date_range=date_range)

        if action == "create":
            title = str(kwargs.get("title") or "Untitled event")
            return (
                "I'd love to add '{title}' to your calendar! But first, connect "
                "Google Calendar or Apple Calendar in **Settings → Services** — "
                "it's one click and I'll handle the rest."
            ).format(title=title)

        if action == "find_free":
            duration = int(kwargs.get("duration_minutes") or 30)
            return (
                "I can find {duration}-minute slots for you once you connect a "
                "calendar in **Settings → Services**. Google Calendar or Apple "
                "Calendar — just pick one!"
            ).format(duration=duration)

        return self.error(f"Not sure what to do with action '{action}'. Try list, create, or find_free.")
