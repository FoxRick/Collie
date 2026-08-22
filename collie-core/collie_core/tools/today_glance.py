"""Today-at-a-glance card data for the Morning Briefing routine (F023/F024).

Collects today's weather, upcoming reminders, and important dates from the
same local stores the chat tools use, and emits one ``today_glance`` card
payload so the morning briefing renders as a seeable summary instead of
prose-only. Every section degrades independently: no location or no
reminders simply drops that row. Calendar events stay out for now — the
calendar only speaks MCP, which needs a live service call the scheduler
should not make synchronously.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

__all__ = ["today_glance_card", "attach_today_glance"]

_MAX_REMINDERS = 3


def _weather_section(db: Any) -> dict[str, Any] | None:
    """Current conditions via the same open-meteo path the weather tool uses."""
    location = str(db.get_profile("location", "") or "").strip()
    if not location:
        return None

    # Reuse the weather tool's fetchers (blocking, so callers run this off
    # the event loop) instead of instantiating the tool class.
    from collie_core.tools.weather import _OPEN_METEO_FORECAST, _api_get, _geocode

    try:
        geo = _geocode(location)
        if geo is None:
            return None
        params = urlencode(
            {
                "latitude": geo["lat"],
                "longitude": geo["lon"],
                "current": "temperature_2m,apparent_temperature,weather_code,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": geo["timezone"],
                "forecast_days": "2",
            }
        )
        data = _api_get(f"{_OPEN_METEO_FORECAST}?{params}")
    except Exception:
        # Weather must never break the morning briefing.
        return None

    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict) or not current:
        return None

    from collie_core.tools.weather import _icon_from_code, _weather_description

    try:
        code = int(current.get("weather_code", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    daily = data.get("daily") if isinstance(data, dict) else None
    times = daily.get("time", []) if isinstance(daily, dict) else []
    highs = daily.get("temperature_2m_max", []) if isinstance(daily, dict) else []
    lows = daily.get("temperature_2m_min", []) if isinstance(daily, dict) else []
    rain = daily.get("precipitation_probability_max", []) if isinstance(daily, dict) else []

    section: dict[str, Any] = {
        "location": f"{geo['name']}, {geo['country']}".rstrip(", "),
        "icon": _icon_from_code(code, bool(current.get("is_day", 1))),
        "temp": current.get("temperature_2m"),
        "condition": _weather_description(code),
        "high": highs[0] if highs else None,
        "low": lows[0] if lows else None,
        "rain_chance": rain[0] if rain else None,
    }
    if times:
        section["date"] = str(times[0])
    return section


def _reminders_section(db: Any) -> list[dict[str, str]]:
    """Upcoming reminders due within the next 24 hours, soonest first."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    horizon = now + timedelta(days=1)
    items: list[dict[str, str]] = []
    for row in db.list_reminders():
        if len(items) >= _MAX_REMINDERS:
            break
        with contextlib.suppress(TypeError, ValueError):
            due = datetime.fromisoformat(str(row.get("due_at") or ""))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)  # noqa: UP017
            if now <= due <= horizon:
                items.append(
                    {"text": str(row.get("text") or ""), "due_at": str(row.get("due_at") or "")}
                )
    return items


def today_glance_card(db: Any) -> dict[str, Any] | None:
    """Build the ``today_glance`` payload, or None when there is nothing to show."""
    weather = _weather_section(db)
    reminders = _reminders_section(db)

    if weather is None and not reminders:
        return None

    payload: dict[str, Any] = {
        "card_type": "today_glance",
        "date": datetime.now().astimezone().strftime("%A, %B %d").replace(" 0", " "),
        "reminders": reminders,
    }
    if weather is not None:
        payload["weather"] = weather
    return payload


async def attach_today_glance(db: Any, conv_id: str, ipc: Any) -> None:
    """Persist + broadcast the card as its own quiet assistant message.

    Called by the runtime right after the morning-briefing text lands. The
    card rides *below* the streamed text (never instead of it), so it goes
    out as a separate message rather than mutating the briefing text. The
    weather fetch is blocking, so it runs on a worker thread.
    """
    import asyncio

    payload = await asyncio.to_thread(today_glance_card, db)
    if payload is None:
        return
    card_type = payload.pop("card_type")
    card = db.add_message(conv_id, "assistant", "", card_type=card_type, card_data=payload)
    await ipc.broadcast({"type": "message", "conversation_id": conv_id, "message": card})
