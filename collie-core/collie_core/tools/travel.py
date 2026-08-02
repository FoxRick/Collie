"""Travel tool: itineraries and packing lists (F030, Step 35).

Builds a structured day-by-day TravelCard from the plan the model assembles
(using web search and weather alongside), plus a sensible packing list.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["TravelTool"]

_ICONS = {
    "flight": "✈️", "train": "🚆", "drive": "🚗", "hotel": "🏨",
    "food": "🍽️", "sight": "📸", "activity": "🎟️", "walk": "🚶",
    "beach": "🏖️", "museum": "🏛️", "shopping": "🛍️", "other": "📍",
}

_PACKING_BASE = [
    "Passport / ID", "Phone + charger", "Toiletries", "Medications",
    "Comfortable shoes", "Underwear + socks per day",
]

_PACKING_EXTRAS = {
    "beach": ["Swimsuit", "Sunscreen", "Sunglasses", "Flip-flops"],
    "cold": ["Warm jacket", "Gloves", "Beanie", "Layers"],
    "rain": ["Rain jacket", "Compact umbrella"],
    "business": ["Business outfit", "Laptop + charger"],
    "hiking": ["Hiking boots", "Daypack", "Water bottle"],
    "city": ["Day bag", "Power bank"],
}


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["itinerary", "packing_list"],
            "description": "build a day-by-day itinerary card, or a packing list.",
        },
        "destination": {"type": "string", "description": "Where the trip goes."},
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "e.g. 'Day 1 — Sat'."},
                    "summary": {"type": "string", "description": "One-line plan."},
                    "activities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "time": {"type": "string"},
                                "title": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "description": "flight, train, drive, hotel, "
                                                   "food, sight, activity, walk, "
                                                   "beach, museum, shopping, other",
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["label"],
            },
            "description": "For itinerary: the day-by-day plan.",
        },
        "trip_type": {
            "type": "array",
            "items": {"type": "string"},
            "description": "For packing_list: tags like beach, cold, rain, "
                           "business, hiking, city.",
        },
        "nights": {
            "type": "integer",
            "description": "For packing_list: how many nights (default 3).",
        },
    },
    "required": ["action"],
})
class TravelTool(Tool):
    """Turn a trip plan into an itinerary card or packing list."""

    @property
    def name(self) -> str:
        return "travel"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        return PermissionRequest(
            action=f"travel.{action or 'plan'}",
            resource=str(params.get("destination") or "trip"),
            risk=Risk.READ,
            summary="Plan your trip",
            reversible=True,
        )

    @property
    def description(self) -> str:
        return (
            "Present travel plans: turn a researched plan into a day-by-day "
            "itinerary card, or generate a packing list for the trip type. "
            "Research flights/hotels/activities with web_search first, check "
            "the weather, then hand the plan to this tool."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> "TravelTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()

        if action == "itinerary":
            destination = str(kwargs.get("destination") or "").strip()
            raw_days = kwargs.get("days") or []
            if not destination:
                return self.error("Where are we headed? I need a destination.")
            if not isinstance(raw_days, list) or not raw_days:
                return self.error(
                    "Give me the day-by-day plan (label + activities) and I'll "
                    "lay it out."
                )
            days: list[dict[str, Any]] = []
            for entry in raw_days:
                if not isinstance(entry, dict):
                    continue
                activities = []
                for act in entry.get("activities") or []:
                    if not isinstance(act, dict) or not act.get("title"):
                        continue
                    kind = str(act.get("kind") or "other").strip().lower()
                    activities.append({
                        "icon": _ICONS.get(kind, _ICONS["other"]),
                        "time": str(act.get("time") or ""),
                        "title": str(act["title"]),
                    })
                days.append({
                    "label": str(entry.get("label") or f"Day {len(days) + 1}"),
                    "summary": str(entry.get("summary") or ""),
                    "activities": activities,
                })
            return json.dumps({
                "card_type": "travel",
                "destination": destination,
                "days": days,
            })

        if action == "packing_list":
            try:
                nights = max(1, int(kwargs.get("nights") or 3))
            except (TypeError, ValueError):
                nights = 3
            tags = [
                str(t).strip().lower()
                for t in (kwargs.get("trip_type") or [])
                if str(t).strip()
            ]
            items = list(_PACKING_BASE)
            items[items.index("Underwear + socks per day")] = (
                f"Underwear + socks × {nights + 1}"
            )
            for tag in tags:
                for extra in _PACKING_EXTRAS.get(tag, []):
                    if extra not in items:
                        items.append(extra)
            lines = [f"Packing list ({nights} nights"
                     + (f", {', '.join(tags)}" if tags else "") + "):"]
            lines += [f"  • {item}" for item in items]
            return "\n".join(lines)

        return self.error(
            f"Not sure what to do with action '{action}'. Try itinerary or "
            "packing_list."
        )
