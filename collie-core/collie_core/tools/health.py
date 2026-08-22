"""Health tool: in-app logging for steps, sleep, water, weight (F032, Step 35).

One value per metric per day; a week view renders as a HealthCard.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from collie_core.tools.life_db import life_db
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["HealthTool"]

_METRICS = ("steps", "sleep_hours", "water_cups", "weight")


def _safe_float(value: Any) -> float:
    """Parse a DB value, clamping NaN/inf/absurd magnitudes to 0."""
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed) or parsed < 0 or parsed > 1_000_000_000:
        return 0.0
    return parsed


def _card(db: Any) -> str:
    today = date.today()
    week_ago = today - timedelta(days=6)
    rows = db.health_logs_since(week_ago.isoformat())
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        by_day.setdefault(str(row["logged_on"]), {})[str(row["metric"])] = _safe_float(row["value"])

    latest = by_day.get(today.isoformat(), {})
    grid: list[int] = []
    streak = 0
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        logged = len(by_day.get(day, {}))
        grid.append(min(4, logged))
    for i in range(0, 7):
        day = (today - timedelta(days=i)).isoformat()
        if by_day.get(day):
            streak += 1
        else:
            break

    # Per-habit dot grids for the streak view (HealthStreaks): one entry per
    # metric per day, oldest → newest; None = nothing logged that day.
    habit_specs = [
        ("steps", "Steps", "👟", "#e8913a"),
        ("water_cups", "Water", "💧", "#6baed6"),
        ("sleep_hours", "Sleep", "😴", "#8b7ec8"),
    ]
    habits: list[dict[str, Any]] = []
    for key, label, icon, color in habit_specs:
        days: list[float | None] = []
        for i in range(6, -1, -1):
            day = (today - timedelta(days=i)).isoformat()
            value = by_day.get(day, {}).get(key)
            days.append(value if value else None)
        habits.append({"key": key, "label": label, "icon": icon, "color": color, "days": days})

    weight_row = db.health_latest("weight")
    payload: dict[str, Any] = {
        "card_type": "health",
        "streak_days": streak,
        "steps": int(_safe_float(latest.get("steps", 0))),
        "sleep_hours": _safe_float(latest.get("sleep_hours", 0)),
        "water_cups": int(_safe_float(latest.get("water_cups", 0))),
        "grid": grid,
        "habits": habits,
    }
    if weight_row is not None:
        payload["weight"] = _safe_float(weight_row["value"])
        payload["weight_date"] = weight_row["logged_on"]
    return json.dumps(payload)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["log", "summary"],
                "description": "log a health value for a day, or show the week view.",
            },
            "metric": {
                "type": "string",
                "enum": list(_METRICS),
                "description": "For log: which metric.",
            },
            "value": {
                "type": "number",
                "description": "For log: the value (steps count, hours, cups, kg).",
            },
            "date": {
                "type": "string",
                "description": "For log: date YYYY-MM-DD (default today).",
            },
            "note": {"type": "string", "description": "For log: optional note."},
        },
        "required": ["action"],
    }
)
class HealthTool(Tool):
    """Log health metrics and show weekly trends."""

    @property
    def name(self) -> str:
        return "health"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        return PermissionRequest(
            action=f"health.{action or 'manage'}",
            resource="health-log",
            risk=Risk.READ if action == "summary" else Risk.LOCAL_WRITE,
            summary="Review your health log" if action == "summary" else "Update your health log",
            reversible=True,
        )

    @property
    def description(self) -> str:
        return (
            "Log the user's health values (steps, sleep_hours, water_cups, "
            "weight) — one value per metric per day — and show a weekly "
            "summary with a streak."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return life_db() is not None

    @classmethod
    def create(cls, ctx: Any) -> HealthTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        db = life_db()
        if db is None:
            return self.error("Health logging isn't available right now.")

        action = str(kwargs.get("action") or "").strip().lower()

        if action == "log":
            metric = str(kwargs.get("metric") or "").strip().lower()
            if metric not in _METRICS:
                return self.error(f"I can track {', '.join(_METRICS)} — which one is this?")
            try:
                value = float(kwargs.get("value"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return self.error("What's the value? I need a number.")
            if not math.isfinite(value) or value < 0 or value > 1_000_000_000:
                return self.error("That value doesn't look right — try a sensible number.")
            db.log_health(
                metric,
                value,
                logged_on=str(kwargs.get("date") or "") or None,
                note=str(kwargs.get("note") or "") or None,
            )
            return _card(db)

        if action == "summary":
            return _card(db)

        return self.error(f"Not sure what to do with action '{action}'. Try log or summary.")
