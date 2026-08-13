"""Presentations tool: slides via MCP (F035, Step 39).

Points the model at the connected files service's MCP tools; presentation
creation composes an outline the user can drop into any slides app.
"""

from __future__ import annotations

from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["PresentationsTool"]

_SLIDE_SERVICES = ("google-drive",)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["outline", "create"],
                "description": "draft a slide outline, or create the deck in a connected service.",
            },
            "topic": {"type": "string", "description": "What the deck is about."},
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                },
                "description": "For outline: the slide-by-slide content.",
            },
        },
        "required": ["action"],
    }
)
class PresentationsTool(Tool):
    """Draft slide outlines; create decks via a connected service."""

    @property
    def name(self) -> str:
        return "presentations"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        return PermissionRequest(
            action=f"presentations.{action or 'outline'}",
            resource=str(params.get("topic") or "presentation"),
            risk=Risk.READ if action == "outline" else Risk.LOCAL_WRITE,
            summary="Draft a presentation outline"
            if action == "outline"
            else "Create a presentation",
            reversible=True,
        )

    @property
    def description(self) -> str:
        return (
            "Help with presentations: draft a slide-by-slide outline, or — "
            "with Google Drive connected in Settings → Services — create the "
            "deck there."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> PresentationsTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()

        if action == "outline":
            topic = str(kwargs.get("topic") or "").strip()
            slides = kwargs.get("slides") or []
            if not isinstance(slides, list) or not slides:
                return self.error(
                    "Give me the slide-by-slide content (title + bullets) and "
                    "I'll lay out the outline."
                )
            lines = [f"Presentation outline{': ' + topic if topic else ''}", ""]
            for i, slide in enumerate(slides, start=1):
                if not isinstance(slide, dict) or not slide.get("title"):
                    continue
                lines.append(f"Slide {i}: {slide['title']}")
                for bullet in slide.get("bullets") or []:
                    lines.append(f"  • {bullet}")
                lines.append("")
            return "\n".join(lines).rstrip()

        if action == "create":
            connected = connected_service_id(*_SLIDE_SERVICES)
            if connected is not None:
                return mcp_tools_hint(connected, "create the presentation")
            return (
                "I can draft the outline right now (action=outline), but to "
                "create the deck itself, connect Google Drive in "
                "**Settings → Services** first!"
            )

        return self.error(f"Not sure what to do with action '{action}'. Try outline or create.")
