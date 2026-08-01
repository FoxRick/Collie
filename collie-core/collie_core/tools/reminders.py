"""Reminders tool: in-app reminder engine backed by SQLite.

Create, list, complete, snooze, and delete reminders. Data lives in the
``reminders`` table of collie.db.
"""

from __future__ import annotations

from typing import Any

from collie_core.db import CollieDB
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
    """Parse an ISO datetime and normalize it to aware UTC.

    Naive values (what an LLM usually writes, e.g. ``2026-07-20T15:00:00``)
    are interpreted as the user's local time and converted to UTC so the
    firing loop and storage share one clock.
    """
    import datetime as _dt

    try:
        parsed = _dt.datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(
            f"That {label} didn't parse. Use a date and time like 2026-07-20T15:00."
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return parsed.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


@tool_parameters({
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
            "description": "For action=create: ISO datetime string, e.g. '2026-07-20T15:00:00'. Collie will interpret natural-language dates and convert them.",
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
            "description": "For action=snooze: ISO datetime to snooze until, e.g. '2026-07-20T16:00:00'.",
        },
    },
    "required": ["action"],
})
class RemindersTool(Tool):
    """Manage reminders — create, list, complete, snooze, delete."""

    @property
    def name(self) -> str:
        return "reminders"

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
    def create(cls, ctx: Any) -> "RemindersTool":
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

                due = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
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
                lines.append(
                    f"  [{r['id'][:8]}] {r['text']} (due {r['due_at']}){rec}{status}"
                )
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

                until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).isoformat(
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
