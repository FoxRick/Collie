"""Lightweight automation scheduler for Collie.

Adapted from nanobot's cron service but simplified for Collie's needs:
- Seeds 5 built-in automations into the DB if missing
- Polls the `automations` table every 60 seconds
- Fires enabled automations whose schedule matches the current time
- Broadcasts automation events via the IPC server
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Any

from loguru import logger

from collie_core.db import CollieDB, utc_now
from collie_core.routines.models import Schedule
from collie_core.routines.schedule import next_occurrence

__all__ = [
    "AutomationScheduler",
    "seed_builtin_automations",
    "seed_gardener_automations",
]

BUILTIN_AUTOMATIONS = [
    {
        "id": "collie-morning-briefing",
        "name": "Morning Briefing",
        "description": "Weather + calendar + reminders to start the day",
        "schedule": "07:00",
        "action_type": "briefing",
        "action_config": {
            "kind": "morning",
            "prompt": (
                "Good morning! Give a friendly morning briefing. Include today's date, "
                "the current weather if the weather tool is available, and any upcoming "
                "calendar events or reminders. Be upbeat and encouraging. Use Collie's "
                "dog-themed voice: warm, playful, never corporate."
            ),
        },
        "enabled": True,
        "delivery_channels": ["in_app"],
    },
    {
        "id": "collie-evening-wind-down",
        "name": "Evening Wind-Down",
        "description": "Tomorrow's calendar, bedtime reminder",
        "schedule": "21:00",
        "action_type": "briefing",
        "action_config": {
            "kind": "evening",
            "prompt": (
                "Time to wind down! Give a friendly evening check-in. Summarize tomorrow's "
                "schedule if calendar is available, suggest a reasonable bedtime based on "
                "the user's wake time if known, and end with a warm good night. Collie "
                "voice: warm, caring, a bit sleepy."
            ),
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
    {
        "id": "collie-weekly-review",
        "name": "Weekly Review",
        "description": "Week summary, next week preview",
        "schedule": "Sun 18:00",
        "action_type": "briefing",
        "action_config": {
            "kind": "weekly",
            "prompt": (
                "It's the end of the week! Give a warm weekly review. List any accomplishments "
                "from the week if you can infer them. Preview next week's important events. "
                "Suggest one small thing to look forward to. Collie voice: reflective and "
                "encouraging, like a dog proud of their human."
            ),
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
    {
        "id": "collie-bill-reminders",
        "name": "Bill Reminders",
        "description": "Upcoming bills this week",
        "schedule": "Fri 10:00",
        "action_type": "reminder",
        "action_config": {
            "kind": "bills",
            "prompt": (
                "Check if there are any important dates marked for the coming week. "
                "If any relate to bills or payments, give a friendly reminder. If none, "
                "say it's all clear. Collie voice: helpful, not anxious."
            ),
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
    {
        "id": "collie-birthday-reminders",
        "name": "Birthday Reminders",
        "description": "Reminder based on people memory",
        "schedule": "09:00",
        "action_type": "reminder",
        "action_config": {
            "kind": "birthdays",
            "prompt": (
                "Check the important dates and people in memory. If anyone has a birthday "
                "coming up within the next 7 days, give a friendly heads-up. Include gift "
                "ideas if stored. Collie voice: excited for celebrations!"
            ),
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
]


def seed_builtin_automations(db: CollieDB) -> None:
    """Seed the built-in automations once, without resurrecting deletions."""
    if db.get_setting("automations.builtins_seeded", False):
        return
    existing = {a["id"] for a in db.list_automations()}
    for auto in BUILTIN_AUTOMATIONS:
        if auto["id"] in existing:
            continue
        db.add_automation(
            auto["name"],
            automation_id=auto["id"],
            description=auto["description"],
            schedule=auto["schedule"],
            action_type=auto["action_type"],
            action_config=auto["action_config"],
            enabled=bool(auto.get("enabled", False)),
            delivery_channels=auto["delivery_channels"],
        )
        logger.info("Seeded automation: {}", auto["name"])
    db.set_setting("automations.builtins_seeded", True)


# Gardener-family built-ins (PR 3 + PR 4 of the Gardener Foundations plan).
# Seeded under their own flag so installs that already ran
# ``seed_builtin_automations`` still get them — and, like all built-ins,
# a user deletion is never resurrected.
GARDENER_AUTOMATIONS = [
    {
        "id": "collie-memory-maintenance",
        "name": "Memory maintenance",
        "description": "Weekly memory consolidation (Dream)",
        "schedule": "Sun 09:00",
        "action_type": "memory_maintenance",
        "action_config": {
            "kind": "dream",
            "prompt": "",
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
    {
        "id": "collie-gardener-suggestions",
        "name": "Improvement suggestions",
        "description": "Weekly improvement suggestions from run records",
        "schedule": "Sun 10:00",
        "action_type": "gardener",
        "action_config": {
            "kind": "gardener",
            "prompt": "",
        },
        "enabled": False,
        "delivery_channels": ["in_app"],
    },
]


def seed_gardener_automations(db: CollieDB) -> None:
    """Seed the Gardener automations once (never resurrect deletions)."""
    if db.get_setting("automations.gardener_seeded", False):
        return
    existing = {a["id"] for a in db.list_automations()}
    for auto in GARDENER_AUTOMATIONS:
        if auto["id"] in existing:
            continue
        db.add_automation(
            auto["name"],
            automation_id=auto["id"],
            description=auto["description"],
            schedule=auto["schedule"],
            action_type=auto["action_type"],
            action_config=auto["action_config"],
            enabled=bool(auto.get("enabled", False)),
            delivery_channels=auto["delivery_channels"],
        )
        logger.info("Seeded automation: {}", auto["name"])
    db.set_setting("automations.gardener_seeded", True)


def _match_schedule(schedule_str: str, now: datetime) -> bool:
    """Check if ``now`` matches a schedule string.

    Formats:
    - ``HH:MM``                    — daily at that time
    - ``Mon|Tue|...|Sun HH:MM``    — weekly on that day
    - ``DD HH:MM``                 — monthly on that day
    """
    schedule_str = schedule_str.strip() if schedule_str else ""
    if not schedule_str:
        return False

    now_str = now.strftime("%H:%M")
    weekday = now.strftime("%a")
    day = now.strftime("%d")

    parts = schedule_str.split()
    if len(parts) == 1:
        return parts[0] == now_str
    if len(parts) == 2:
        if parts[0] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            return parts[0] == weekday and parts[1] == now_str
        if parts[0].isdigit():
            return parts[0] == day and parts[1] == now_str
    return False


class AutomationScheduler:
    """Background task that fires automations on schedule."""

    def __init__(
        self,
        db: CollieDB,
        broadcaster: callable | None = None,
        *,
        runner: callable | None = None,
        poll_seconds: int = 60,
    ) -> None:
        self.db = db
        self._broadcaster = broadcaster  # async (payload: dict) -> None
        self._runner = runner  # async (automation row: dict) -> None
        self._poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(3)
        self._run_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        seed_builtin_automations(self.db)
        seed_gardener_automations(self.db)
        stale_before = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
        recovered = self.db.recover_stale_runs(stale_before)
        if recovered:
            logger.warning("Recovered {} interrupted routine run(s)", recovered)
        self._backfill_next_runs()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for task in list(self._run_tasks):
            task.cancel()
        if self._run_tasks:
            await asyncio.gather(*self._run_tasks, return_exceptions=True)
        self._run_tasks.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Automation scheduler tick failed")
            await asyncio.sleep(self._poll_seconds)

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        automations = self.db.list_automations(enabled_only=True)

        for auto in automations:
            auto_id = str(auto["id"] or "")
            if auto.get("action_type") == "approved_plan" and (
                not auto.get("plan_id") or not auto.get("plan_version")
            ):
                self.db.update_automation(auto_id, routine_status="needs_attention")
                continue
            due_raw = str(auto.get("next_run_at") or "")
            if not due_raw:
                # An enabled routine without a next run (fresh, or re-enabled)
                # gets one computed from now — never left silent forever.
                schedule = self._structured_schedule(auto)
                if schedule is None:
                    self.db.update_automation(auto_id, routine_status="needs_attention")
                    continue
                upcoming = next_occurrence(schedule, now)
                self.db.update_automation(
                    auto_id,
                    next_run_at=(upcoming.isoformat(timespec="seconds") if upcoming else None),
                )
                continue
            try:
                due = datetime.fromisoformat(due_raw)
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if due > now:
                continue
            schedule = self._structured_schedule(auto)
            if schedule is None:
                self.db.update_automation(auto_id, routine_status="needs_attention")
                continue
            stale = due < now - timedelta(hours=6)
            following = next_occurrence(schedule, now if stale else due)
            run = self.db.claim_scheduled_run(
                auto_id,
                scheduled_for=due.isoformat(timespec="seconds"),
                next_run_at=following.isoformat(timespec="seconds") if following else None,
                plan_id=auto.get("plan_id"),
                plan_version=auto.get("plan_version"),
            )
            if run is None:
                continue
            if stale:
                self.db.transition_run(
                    str(run["id"]),
                    "skipped",
                    error_code="missed_window",
                    error_message="The scheduled time was outside the recent missed-run window.",
                )
                continue
            task = asyncio.create_task(self._execute_claimed(auto, run))
            self._run_tasks.add(task)
            task.add_done_callback(self._run_tasks.discard)

    async def _execute_claimed(self, auto: dict[str, Any], run: dict[str, Any]) -> None:
        async with self._semaphore:
            run_id = str(run["id"])
            self.db.transition_run(run_id, "running")
            await self._emit({"type": "run_started", "run": self.db.get_run(run_id)})
            try:
                await self._fire(auto, mark_result=False)
            except asyncio.CancelledError:
                self.db.transition_run(run_id, "interrupted", error_code="shutdown")
                raise
            except Exception as exc:
                logger.exception("Routine run failed: {}", auto.get("id"))
                self.db.transition_run(
                    run_id,
                    "failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                )
                self.db.mark_routine_result(str(auto["id"]), success=False, error=str(exc))
                steps = self.db.list_run_steps(run_id)
                current = self.db.get_current_run_step(run_id)
                target = current or next(
                    (
                        step
                        for step in steps
                        if step.get("status") not in {"completed", "failed", "skipped", "blocked"}
                    ),
                    None,
                )
                if target is not None:
                    self.db.upsert_run_step(
                        run_id,
                        str(target["step_key"]),
                        ordinal=int(target["ordinal"]),
                        title=str(target["title"]),
                        status="failed",
                        error_message=str(exc)[:1000],
                    )
                await self._emit({"type": "run_failed", "run": self.db.get_run(run_id)})
                return
            for step in self.db.list_run_steps(run_id):
                if step.get("status") != "queued":
                    continue
                self.db.upsert_run_step(
                    run_id,
                    str(step["step_key"]),
                    ordinal=int(step["ordinal"]),
                    title=str(step["title"]),
                    status="skipped",
                    output_summary="Not reached during this run.",
                )
            self.db.transition_run(run_id, "completed")
            self.db.mark_routine_result(str(auto["id"]), success=True)
            await self._emit({"type": "run_completed", "run": self.db.get_run(run_id)})

    async def _fire(self, auto: dict[str, Any], *, mark_result: bool = True) -> None:
        auto_id = str(auto["id"] or "")
        logger.info("Firing automation: {} ({})", auto.get("name"), auto_id)

        if self._runner is not None:
            await self._runner(auto)
            if mark_result:
                self.db.mark_routine_result(auto_id, success=True)
            return

        if self._broadcaster is None:
            return

        action_config = auto.get("action_config")
        if isinstance(action_config, str):
            try:
                action_config = json.loads(action_config)
            except (TypeError, json.JSONDecodeError):
                action_config = {}

        prompt = ""
        if isinstance(action_config, dict):
            prompt = str(action_config.get("prompt") or "")

        await self._broadcaster(
            {
                "type": "automation",
                "automation_id": auto_id,
                "name": auto.get("name"),
                "action_type": auto.get("action_type"),
                "prompt": prompt,
                "timestamp": utc_now(),
            }
        )
        if mark_result:
            self.db.mark_routine_result(auto_id, success=True)

    async def _emit(self, payload: dict[str, Any]) -> None:
        if self._broadcaster is not None:
            await self._broadcaster(payload)

    def _backfill_next_runs(self) -> None:
        now = datetime.now(UTC)
        for row in self.db.list_automations():
            if row.get("next_run_at"):
                continue
            schedule = self._structured_schedule(row)
            if schedule is None:
                continue
            upcoming = next_occurrence(schedule, now)
            self.db.update_automation(
                str(row["id"]),
                timezone=schedule.timezone,
                schedule_json=schedule.to_dict(),
                next_run_at=upcoming.isoformat(timespec="seconds") if upcoming else None,
            )

    @staticmethod
    def _structured_schedule(row: dict[str, Any]) -> Schedule | None:
        raw = row.get("schedule_json")
        if isinstance(raw, str) and raw:
            try:
                return Schedule.from_dict(json.loads(raw))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                return None
        legacy = str(row.get("schedule") or "").strip()
        parts = legacy.split()
        try:
            zone = str(row.get("timezone") or "UTC")
            if len(parts) == 1:
                return Schedule(kind="daily", time=time.fromisoformat(parts[0]), timezone=zone)
            if len(parts) == 2 and parts[0].isdigit():
                return Schedule(
                    kind="monthly",
                    day=int(parts[0]),
                    time=time.fromisoformat(parts[1]),
                    timezone=zone,
                )
            if len(parts) == 2:
                return Schedule(
                    kind="weekly",
                    days=(parts[0].upper(),),
                    time=time.fromisoformat(parts[1]),
                    timezone=zone,
                )
        except ValueError:
            return None
        return None
