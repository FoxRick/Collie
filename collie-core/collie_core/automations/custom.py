"""Custom automations from plain English (Step 40, F062, F064).

"Every Friday at 5pm, ask me how my week went" → schedule ``Fri 17:00`` plus
a prompt the agent runs when it fires. Parsing is deterministic (no LLM
needed) so it works before a provider is even configured.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from collie_core.db import CollieDB
from collie_core.routines.schedule import next_occurrence
from collie_core.routines.schedule import parse_schedule as parse_structured_schedule

__all__ = ["create_custom_automation", "parse_schedule"]

_WEEKDAYS = {
    "monday": "Mon",
    "mon": "Mon",
    "tuesday": "Tue",
    "tue": "Tue",
    "tues": "Tue",
    "wednesday": "Wed",
    "wed": "Wed",
    "thursday": "Thu",
    "thu": "Thu",
    "thur": "Thu",
    "thurs": "Thu",
    "friday": "Fri",
    "fri": "Fri",
    "saturday": "Sat",
    "sat": "Sat",
    "sunday": "Sun",
    "sun": "Sun",
}

_DAYPART_DEFAULTS = {
    "morning": "08:00",
    "afternoon": "15:00",
    "evening": "20:00",
    "night": "21:00",
    "noon": "12:00",
    "midnight": "00:00",
    "lunchtime": "12:00",
    "lunch": "12:00",
    "bedtime": "22:00",
}

_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)
_MONTHDAY_RE = re.compile(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b")
_DAILY_RE = re.compile(
    r"\b(?:every\s+day|daily|each\s+day|every\s+(?:morning|afternoon|evening|night))\b"
)
_MONTHLY_RE = re.compile(r"\b(?:every\s+month|monthly|each\s+month)\b")


def _find_time(text: str) -> str | None:
    for word, default in _DAYPART_DEFAULTS.items():
        if re.search(rf"\b{word}\b", text):
            explicit = _find_clock_time(text)
            return explicit or default
    return _find_clock_time(text)


def _find_clock_time(text: str) -> str | None:
    for match in _TIME_RE.finditer(text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").replace(".", "").lower()
        if not meridiem and not match.group(2) and not _looks_like_time(match, text):
            continue
        if hour > 23 or minute > 59:
            continue
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"
    return None


def _looks_like_time(match: re.Match, text: str) -> bool:
    """A bare number only counts as a time when preceded by 'at'."""
    prefix = text[max(0, match.start() - 3) : match.start()]
    return prefix.strip().endswith("at")


def parse_schedule(description: str) -> str | None:
    """Turn plain English into a scheduler string, or None if unclear.

    Supported outputs (matching the scheduler):
    - ``HH:MM``      daily
    - ``Www HH:MM``  weekly (Mon..Sun)
    - ``DD HH:MM``   monthly
    """
    text = " " + (description or "").strip().lower() + " "
    if not text.strip():
        return None

    time = _find_time(text)

    for word, code in _WEEKDAYS.items():
        if re.search(rf"\b{word}s?\b", text):
            return f"{code} {time or '09:00'}"

    monthday = _MONTHDAY_RE.search(text)
    if monthday and (_MONTHLY_RE.search(text) or time):
        day = min(28, max(1, int(monthday.group(1))))
        return f"{day:02d} {time or '09:00'}"

    if _DAILY_RE.search(text) or time:
        return time or None

    return None


def create_custom_automation(
    db: CollieDB,
    description: str,
    *,
    name: str | None = None,
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    """Create an automation row from a plain-English description."""
    description = (description or "").strip()
    if not description:
        raise ValueError("Tell me what you'd like me to do, and when!")

    try:
        structured = parse_structured_schedule(description, timezone_name)
    except ValueError as exc:
        raise ValueError(
            "I couldn't work out a clear schedule. Try 'weekdays at 8am', "
            "'every Friday at 5pm', or 'the first day of every month at 9am'. "
            f"{exc}"
        ) from exc
    schedule = parse_schedule(description) or structured.time.strftime("%H:%M")
    next_run = next_occurrence(structured, datetime.now(UTC))

    label = (name or "").strip()
    if not label:
        label = re.sub(r"\s+", " ", description)
        label = (label[:47] + "…") if len(label) > 48 else label

    prompt = (
        f"Scheduled task from the user: {description}\n\n"
        "Carry this out now. Use whatever tools help (weather, calendar, "
        "reminders, news, web search). Speak in Collie's voice: warm, "
        "playful, first person, never corporate."
    )
    return db.add_automation(
        label,
        description=description,
        schedule=schedule,
        action_type="custom",
        action_config={"kind": "custom", "prompt": prompt},
        enabled=True,
        delivery_channels=["in_app"],
        timezone_name=timezone_name,
        schedule_json=structured.to_dict(),
        next_run_at=next_run.isoformat(timespec="seconds") if next_run else None,
    )
