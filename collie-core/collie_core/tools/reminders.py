"""Reminders tool: in-app reminder engine backed by SQLite (F024).

Create, list, complete, snooze, and delete reminders. Data lives in the
``reminders`` table of collie.db.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from collie_core.db import CollieDB
from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["RemindersTool", "bind_reminders_db"]

_db: CollieDB | None = None


def bind_reminders_db(db: CollieDB) -> None:
    """Called once by the Collie runtime before tools are loaded."""
    global _db
    _db = db


def _store() -> CollieDB | None:
    return _db


def _normalize_due(value: str, *, label: str = "time") -> str:
    """Parse a due time and normalize it to aware UTC.

    Accepts strict ISO datetimes plus the natural-language forms the model
    tends to pass: ``tomorrow at 3pm``, ``in 2 hours``, ``next monday 9am``,
    ``3pm``, ``July 20 at 3pm``. Naive results are interpreted as the
    user's local time and converted to UTC so the firing loop and storage
    share one clock.
    """
    parsed = _parse_due(value, label)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(datetime.UTC).isoformat(timespec="seconds")


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_TIME_RE = re.compile(
    r"^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$|^(noon|midnight)$"
)
_UNIT_SECONDS = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2_592_000,  # 30 days — good enough for a reminder nudge
}


def _apply_clock(base, clock: str) -> datetime:
    """Resolve a clock token (``3pm`` / ``15:00`` / ``noon``) onto a date."""
    text = clock.strip().lower()
    if text == "noon":
        return base.replace(hour=12, minute=0, second=0, microsecond=0)
    if text == "midnight":
        return base.replace(hour=0, minute=0, second=0, microsecond=0)
    match = _TIME_RE.match(text)
    if not match:
        raise ValueError(f"'{clock}' is not a time I understand.")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"'{clock}' is not a time I understand.")
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_due(value: str, label: str) -> datetime:
    """Parse a due string into a local-aware datetime, or raise ValueError."""
    text = str(value).strip()
    lowered = text.lower()

    # Fast path: strict ISO (covers "2026-07-20T15:00:00" and the
    # space-separated "2026-07-20 15:00" form).
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    now = datetime.now().astimezone()

    # "in 2 hours", "in 30 minutes", "3 days from now"
    in_match = re.match(
        r"^in\s+(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks|month|months)$",
        lowered,
    )
    if in_match:
        seconds = int(in_match.group(1)) * _UNIT_SECONDS[
            in_match.group(2).rstrip("s")
        ]
        return now + timedelta(seconds=seconds)
    from_match = re.match(
        r"^(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks|month|months)\s+from\s+now$",
        lowered,
    )
    if from_match:
        seconds = int(from_match.group(1)) * _UNIT_SECONDS[
            from_match.group(2).rstrip("s")
        ]
        return now + timedelta(seconds=seconds)

    # "tomorrow at 3pm" / "tomorrow 3pm" / "today 6pm" / "tonight"
    day_shift: int | None = None
    rest = ""
    if lowered.startswith("tomorrow"):
        day_shift = 1
        rest = lowered[len("tomorrow") :]
    elif lowered.startswith("tonight"):
        day_shift = 0
        rest = lowered[len("tonight") :]
    elif lowered.startswith("today"):
        day_shift = 0
        rest = lowered[len("today") :]
    if day_shift is not None:
        base = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=day_shift
        )
        clock = rest.strip()
        if not clock:
            clock = "20:00" if lowered.startswith("tonight") else "09:00"
        return _apply_clock(base, clock)

    # "next monday 9am" / "monday at 5pm" / "friday 5pm"
    weekday_match = re.match(r"^(next\s+)?([a-z]+)(?:\s+at\s+|\s+)?(.*)$", lowered)
    if weekday_match and weekday_match.group(2) in _WEEKDAYS:
        is_next = bool(weekday_match.group(1))
        weekday = _WEEKDAYS[weekday_match.group(2)]
        clock = weekday_match.group(3).strip()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_ahead = (weekday - today.weekday()) % 7
        if is_next and days_ahead == 0:
            days_ahead = 7
        base = today + timedelta(days=days_ahead)
        if not is_next and days_ahead == 0 and not clock:
            clock = "09:00"
        result = _apply_clock(base, clock or "09:00")
        # A bare weekday with an explicit time still means the *coming*
        # occurrence: if that lands today but has already passed, roll to
        # next week rather than silently firing in the past.
        if not is_next and result.date() == now.date() and result <= now:
            result += timedelta(days=7)
        return result

    # Bare clock: "3pm", "15:00", "at 8:30 am" -> today, rolling to tomorrow
    # if that moment has already passed.
    bare_clock = text.strip()
    try:
        result = _apply_clock(now.replace(hour=0, minute=0, second=0, microsecond=0), bare_clock)
    except ValueError:
        result = None
    if result is not None:
        if result <= now:
            result += timedelta(days=1)
        return result

    # Last resort: dateutil's lenient parser for "July 20 at 3pm",
    # "20th of July 2026", etc. Date-only strings get a 9am default clock
    # so a reminder never fires at midnight.
    try:
        from dateutil import parser as _dateutil_parser
    except ImportError:  # pragma: no cover - dependency is declared
        raise ValueError(
            f"That {label} didn't parse. Use a date and time like 2026-07-20T15:00 "
            "or plain words like 'tomorrow at 3pm'."
        ) from None
    if not re.search(r"\d{1,2}:\d{2}|[ap]m\b|\bnoon\b|\bmidnight\b", lowered):
        text = f"{text} 09:00"
    default = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return _dateutil_parser.parse(text, default=default, fuzzy=False)
    except (ValueError, OverflowError):
        raise ValueError(
            f"That {label} didn't parse. Use a date and time like 2026-07-20T15:00 "
            "or plain words like 'tomorrow at 3pm'."
        ) from None


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "complete", "snooze", "delete"],
                "description": "What to do: create a reminder, list upcoming ones, mark complete, snooze, or delete.",
            },
            "text": {
                "type": "string",
                "description": "For action=create: what to remember. e.g. 'Call Mom at 3pm'.",
            },
            "due_at": {
                "type": "string",
                "description": "For action=create: when to fire. ISO datetime like '2026-07-20T15:00:00' or natural language: 'tomorrow at 3pm', 'in 2 hours', 'next monday 9am', '3pm'.",
            },
            "recurrence": {
                "type": "string",
                "description": "For action=create: optional recurrence rule. e.g. 'daily', 'weekly', 'weekdays', or a cron expression.",
            },
            "reminder_id": {
                "type": "string",
                "description": "For action=complete/snooze/delete: the ID of the reminder.",
            },
            "snooze_until": {
                "type": "string",
                "description": "For action=snooze: when to nudge again. ISO datetime like '2026-07-20T16:00:00' or natural language: 'tomorrow at 9am', 'in 1 hour'.",
            },
        },
        "required": ["action"],
    }
)
class RemindersTool(Tool):
    """Manage reminders — create, list, complete, snooze, delete."""

    @property
    def name(self) -> str:
        return "reminders"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        if action == "delete":
            return PermissionRequest(
                action="delete.destructive",
                resource=str(params.get("reminder_id") or "reminder"),
                risk=Risk.DESTRUCTIVE,
                summary="Delete this reminder",
                reversible=False,
                hard_approval=True,
            )
        recurring = action == "create" and bool(str(params.get("recurrence") or "").strip())
        return PermissionRequest(
            action=f"reminder.{action or 'manage'}",
            resource=str(params.get("reminder_id") or "local-reminders"),
            risk=Risk.LOCAL_WRITE,
            summary="Manage your reminders",
            reversible=True,
            approval_free=not recurring,
            approve_for_me=not recurring,
        )

    @property
    def description(self) -> str:
        return (
            "Manage reminders: create a new one (with an optional due time and "
            "recurrence rule like 'daily' or 'weekly'), list upcoming reminders, "
            "mark one as complete, snooze it, or delete it."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _store() is not None

    @classmethod
    def create(cls, ctx: Any) -> RemindersTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        db = _store()
        if db is None:
            return self.error("Reminders aren't available right now.")

        action = str(kwargs.get("action") or "").strip().lower()

        if action == "create":
            text = str(kwargs.get("text") or "").strip()
            due = str(kwargs.get("due_at") or "").strip()
            if not text:
                return self.error("I need to know what to remind you about!")
            if not due:
                import datetime as _dt

                due = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
            else:
                try:
                    due = _normalize_due(due)
                except ValueError as error:
                    return self.error(str(error))
            recurrence = str(kwargs.get("recurrence") or "") or None
            reminder = db.add_reminder(text, due, recurrence=recurrence)
            friendly = reminder.get("due_at", due)
            return (
                f"I'll remind you: {text}"
                + (f" (by {friendly})" if friendly else "")
                + (" — repeating!" if recurrence else "")
            )

        if action == "list":
            reminders = db.list_reminders()
            if not reminders:
                return "You don't have any reminders right now — you're all caught up!"
            lines = ["Here are your reminders:"]
            for r in reminders:
                status = ""
                if r.get("snoozed_until"):
                    status = f" [snoozed until {r['snoozed_until']}]"
                rec = f" — repeats {r['recurrence']}" if r.get("recurrence") else ""
                lines.append(f"  [{r['id'][:8]}] {r['text']} (due {r['due_at']}){rec}{status}")
            return "\n".join(lines)

        if action in ("complete", "delete"):
            rid = str(kwargs.get("reminder_id") or "").strip()
            if not rid:
                return self.error("Which reminder? I need the ID.")
            if action == "complete":
                if db.complete_reminder(rid):
                    return "Done — marked that one finished!"
                return self.error("I can't find that reminder — it may already be done.")
            if db.delete_reminder(rid):
                return "Gone — that reminder's been deleted."
            return self.error("I can't find that reminder — it may already be gone.")

        if action == "snooze":
            rid = str(kwargs.get("reminder_id") or "").strip()
            until = str(kwargs.get("snooze_until") or "").strip()
            if not rid:
                return self.error("Which reminder? I need the ID.")
            if not until:
                import datetime as _dt

                until = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(hours=1)).isoformat(
                    timespec="seconds"
                )
            else:
                try:
                    until = _normalize_due(until, label="snooze time")
                except ValueError as error:
                    return self.error(str(error))
            if not db.snooze_reminder(rid, until):
                return self.error("I can't find that reminder.")
            return f"Snoozed! I'll nudge you again at {until}."

        return self.error(
            f"Not sure what to do with action '{action}'. Try create, list, complete, snooze, or delete."
        )
