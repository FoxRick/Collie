"""Smart Home tool: Philips Hue / HomeKit / Google Home via MCP (F033, Step 35).

Points the model at the connected home service's MCP tools; otherwise nudges
the user toward Settings → Services.
"""

from __future__ import annotations

from typing import Any

from collie_core.tools.services_bridge import connected_service_id, mcp_tools_hint
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["SmartHomeTool"]

_HOME_SERVICES = ("philips-hue", "homekit", "google-home")


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["lights", "scene", "thermostat", "locks", "status"],
            "description": "control lights, activate a scene, adjust the "
                           "thermostat, check locks, or get device status.",
        },
        "request": {
            "type": "string",
            "description": "What to do, e.g. 'turn off the living room lights'.",
        },
    },
    "required": ["action"],
})
class SmartHomeTool(Tool):
    """Control the smart home (Hue / HomeKit / Google Home via MCP)."""

    @property
    def name(self) -> str:
        return "smart_home"

    @property
    def description(self) -> str:
        return (
            "Control smart home devices — lights, scenes, thermostat, locks. "
            "Connect Philips Hue (or another home service) in "
            "Settings → Services first."
        )

    @property
    def read_only(self) -> bool:
        return False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        # Smart-home integrations are hidden from the Windows weekend alpha.
        return False

    @classmethod
    def create(cls, ctx: Any) -> "SmartHomeTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()
        connected = connected_service_id(*_HOME_SERVICES)
        if connected is not None:
            task = {
                "lights": "control the lights",
                "scene": "activate the scene",
                "thermostat": "adjust the thermostat",
                "locks": "check the locks",
                "status": "get device status",
            }.get(action, "control the home")
            return mcp_tools_hint(connected, task)
        return (
            "I'd love to run the house, but no smart home service is "
            "connected yet! Head to **Settings → Services** and connect "
            "Philips Hue — one click and I've got the lights."
        )
