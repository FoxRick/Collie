"""Plain-English parsing and timezone-safe next-run calculation."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from collie_core.routines.models import Schedule

_WEEKDAYS = {
    "monday": "MON",
    "mon": "MON",
    "tuesday": "TUE",
    "tue": "TUE",
    "tues": "TUE",
    "wednesday": "WED",
    "wed": "WED",
    "thursday": "THU",
    "thu": "THU",
    "thur": "THU",
    "friday": "FRI",
    "fri": "FRI",
    "saturday": "SAT",
    "sat": "SAT",
    "sunday": "SUN",
    "sun": "SUN",
}
_DAY_INDEX = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)
_MONTH_DAY_RE = re.compile(
    r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)(?:\s+day)?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DEFAULT_TIMES = {
    "morning": time(8),
    "noon": time(12),
    "afternoon": time(15),
    "evening": time(20),
    "night": time(21),
}


def _clock(text: str) -> time | None:
    for word, value in _DEFAULT_TIMES.items():
        if re.search(rf"\b{word}\b", text):
            explicit = _clock_numbers(text)
            return explicit or value
    return _clock_numbers(text)


def _clock_numbers(text: str) -> time | None:
    for match in _TIME_RE.finditer(text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").replace(".", "").lower()
        prefix = text[max(0, match.start() - 4) : match.start()]
        if not meridiem and match.group(2) is None and "at" not in prefix:
            continue
        if minute > 59 or hour > (12 if meridiem else 23) or hour == 0 and meridiem:
            continue
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return time(hour, minute)
    return None


def parse_schedule(description: str, timezone_name: str = "UTC") -> Schedule:
    """Parse an explicit MVP schedule or raise a clarification-friendly error."""
    text = (description or "").strip().lower()
    if not text:
        raise ValueError("Tell me when this routine should run.")
    clock = _clock(text)
    if clock is None:
        raise ValueError("What time should this routine run?")

    iso_date = _ISO_DATE_RE.search(text)
    if iso_date and re.search(r"\b(once|on)\b", text):
        return Schedule(
            kind="once",
            date=date.fromisoformat(iso_date.group(1)),
            time=clock,
            timezone=timezone_name,
        )

    if re.search(r"\b(?:weekdays?|monday\s+(?:through|to|-)\s+friday)\b", text):
        return Schedule(kind="weekdays", time=clock, timezone=timezone_name)

    selected = tuple(
        dict.fromkeys(code for word, code in _WEEKDAYS.items() if re.search(rf"\b{word}s?\b", text))
    )
    if selected:
        return Schedule(
            kind="weekly",
            days=selected,
            time=clock,
            timezone=timezone_name,
        )

    if re.search(r"\b(?:monthly|every\s+month|each\s+month)\b", text):
        if re.search(r"\bfirst\s+day\b", text):
            day = 1
        else:
            match = _MONTH_DAY_RE.search(text)
            if match is None:
                raise ValueError("Which numbered day of the month should this run?")
            day = int(match.group(1))
        if not 1 <= day <= 31:
            raise ValueError("The monthly day must be between 1 and 31.")
        return Schedule(kind="monthly", day=day, time=clock, timezone=timezone_name)

    if re.search(r"\b(?:daily|every\s+day|each\s+day)\b", text):
        return Schedule(kind="daily", time=clock, timezone=timezone_name)

    raise ValueError(
        "I found a time but not a clear frequency. Say once, daily, weekdays, "
        "a weekday, or a numbered day each month."
    )


def _local_candidate(day: date, clock: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, clock, tzinfo=zone)


def next_occurrence(schedule: Schedule, after: datetime | None = None) -> datetime | None:
    """Return the first occurrence after ``after`` as an aware UTC datetime."""
    instant = after or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    zone = ZoneInfo(schedule.timezone)
    local = instant.astimezone(zone)

    if schedule.kind == "once":
        candidate = _local_candidate(schedule.date, schedule.time, zone)  # type: ignore[arg-type]
        return candidate.astimezone(UTC) if candidate > local else None

    if schedule.kind in {"daily", "weekdays", "weekly"}:
        for offset in range(0, 15):
            day = local.date() + timedelta(days=offset)
            if schedule.kind == "weekdays" and day.weekday() > 4:
                continue
            if schedule.kind == "weekly":
                allowed = {_DAY_INDEX[item] for item in schedule.days}
                if day.weekday() not in allowed:
                    continue
            candidate = _local_candidate(day, schedule.time, zone)
            if candidate > local:
                return candidate.astimezone(UTC)
        return None

    for month_offset in range(0, 14):
        year = local.year + (local.month - 1 + month_offset) // 12
        month = (local.month - 1 + month_offset) % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        day = min(schedule.day or 1, last_day)
        candidate = _local_candidate(date(year, month, day), schedule.time, zone)
        if candidate > local:
            return candidate.astimezone(UTC)
    return None
