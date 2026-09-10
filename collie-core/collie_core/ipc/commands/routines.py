"""Routine and automation commands.

Handlers for the ``routines`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC

from websockets.asyncio.server import ServerConnection

from collie_core.ipc.command_support import _bounded_list_limit
from collie_core.routines.timezone import local_timezone


class RoutineCommands:
    """Routine and automation commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_list_automations(self, connection: ServerConnection, frame: dict) -> dict:
        return {"automations": self.db.list_automations()}

    async def _cmd_toggle_automation(self, connection: ServerConnection, frame: dict) -> dict:
        auto_id = str(frame.get("automation_id") or "")
        enabled = bool(frame.get("enabled"))
        self.db.toggle_automation(auto_id, enabled)
        return {"toggled": True}

    async def _cmd_create_automation(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.automations.custom import create_custom_automation

        row = create_custom_automation(
            self.db,
            str(frame.get("description") or ""),
            name=(str(frame["name"]) if frame.get("name") else None),
            timezone_name=str(frame.get("timezone") or local_timezone()),
        )
        return {"automation": row}

    async def _cmd_delete_automation(self, connection: ServerConnection, frame: dict) -> dict:
        auto_id = str(frame.get("automation_id") or "")
        if auto_id.startswith("collie-"):
            raise ValueError("That one's built in — flip it off instead of deleting it!")
        self.db.delete_automation(auto_id)
        return {"deleted": True}

    async def _cmd_update_automation(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.automations.custom import update_custom_automation

        row = update_custom_automation(
            self.db,
            str(frame.get("automation_id") or ""),
            str(frame.get("description") or ""),
            name=(str(frame["name"]) if frame.get("name") else None),
            timezone_name=str(frame["timezone"]) if frame.get("timezone") else None,
        )
        return {"automation": row}

    async def _cmd_list_routines(self, connection: ServerConnection, frame: dict) -> dict:
        return {"routines": self.db.list_automations()}

    async def _cmd_create_routine(self, connection: ServerConnection, frame: dict) -> dict:
        from datetime import datetime

        from collie_core.routines.schedule import next_occurrence, parse_schedule

        plan_id = str(frame.get("plan_id") or "")
        version = int(frame.get("version") or 0)
        plan = self.db.approve_plan(plan_id, version, str(frame.get("plan_hash") or ""))
        zone = str(frame.get("timezone") or local_timezone())
        schedule = parse_schedule(str(frame.get("schedule_description") or ""), zone)
        upcoming = next_occurrence(schedule, datetime.now(UTC))
        routine = self.db.add_automation(
            str(frame.get("name") or plan["title"]),
            description=str(plan["goal"]),
            schedule=schedule.time.strftime("%H:%M"),
            action_type="approved_plan",
            action_config={"plan_id": plan_id, "plan_version": version},
            enabled=True,
            timezone_name=zone,
            schedule_json=schedule.to_dict(),
            next_run_at=upcoming.isoformat(timespec="seconds") if upcoming else None,
            plan_id=plan_id,
            plan_version=version,
        )
        self.db.attach_plan_to_routine(plan_id, version, str(routine["id"]))
        await self.broadcast({"type": "routine_updated", "routine": routine})
        return {"routine": routine}

    async def _cmd_get_routine(self, connection: ServerConnection, frame: dict) -> dict:
        row = self.db.get_automation(str(frame.get("routine_id") or ""))
        if row is None:
            raise ValueError("routine not found")
        return {"routine": row}

    async def _cmd_update_routine(self, connection: ServerConnection, frame: dict) -> dict:
        from datetime import datetime

        from collie_core.routines.schedule import next_occurrence, parse_schedule

        routine_id = str(frame.get("routine_id") or "")
        updates = dict(frame.get("updates") or {})
        row = self.db.get_automation(routine_id)
        if row is None:
            raise ValueError("routine not found")
        description = updates.pop("schedule_description", None)
        if description is not None or "timezone" in updates:
            from dataclasses import replace

            from collie_core.automations.scheduler import AutomationScheduler

            zone = str(
                updates.get("timezone")
                or frame.get("timezone")
                or row.get("timezone")
                or local_timezone()
            )
            if description is not None:
                schedule = parse_schedule(str(description), zone)
            else:
                existing = AutomationScheduler._structured_schedule(row)
                if existing is None:
                    raise ValueError(
                        "This routine needs a valid schedule before changing timezone."
                    )
                schedule = replace(existing, timezone=zone)
            upcoming = next_occurrence(schedule, datetime.now(UTC))
            updates.update(
                {
                    "schedule_json": schedule.to_dict(),
                    "timezone": zone,
                    "next_run_at": (upcoming.isoformat(timespec="seconds") if upcoming else None),
                }
            )
        return {"routine": self.db.update_automation(routine_id, **updates)}

    async def _cmd_pause_routine(self, connection: ServerConnection, frame: dict) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        self.db.toggle_automation(routine_id, False)
        return {"routine": self.db.get_automation(routine_id)}

    async def _cmd_resume_routine(self, connection: ServerConnection, frame: dict) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        row = self.db.get_automation(routine_id)
        if row is None:
            raise ValueError("routine not found")
        if row.get("action_type") == "approved_plan" and (
            not row.get("plan_id") or not row.get("plan_version")
        ):
            raise ValueError("Review and approve this routine's plan before enabling it.")
        self.db.toggle_automation(routine_id, True)
        return {"routine": self.db.get_automation(routine_id)}

    async def _cmd_delete_routine(self, connection: ServerConnection, frame: dict) -> dict:
        return await self._cmd_delete_automation(
            connection, {"automation_id": frame.get("routine_id")}
        )

    async def _cmd_run_routine_now(self, connection: ServerConnection, frame: dict) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        row = self.db.get_automation(routine_id)
        if row is None:
            raise ValueError("routine not found")
        plan = None
        if row.get("action_type") == "approved_plan":
            if not row.get("plan_id") or not row.get("plan_version"):
                raise ValueError("This routine needs an approved plan first.")
            plan = self.db.get_plan(str(row["plan_id"]), int(row["plan_version"]))
            if plan is None or plan.get("status") != "approved":
                raise ValueError("This routine's plan changed and needs review.")
        conv_key = f"automations.{routine_id}.conversation_id"
        conv_id = str(self.db.get_setting(conv_key, "") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            conv_id = str(self.db.create_conversation(f"Routine: {row['name']}")["id"])
            self.db.set_setting(conv_key, conv_id)
        if conv_id in self._chat_tasks and not self._chat_tasks[conv_id].done():
            raise ValueError("This routine is already running.")
        run = self.db.create_run(
            trigger_type="manual",
            idempotency_key=f"manual:{routine_id}:{uuid.uuid4().hex}",
            plan_id=row.get("plan_id"),
            plan_version=row.get("plan_version"),
            routine_id=routine_id,
            conversation_id=conv_id,
        )
        if row.get("action_type") != "approved_plan":
            action_config = row.get("action_config")
            if isinstance(action_config, str):
                try:
                    action_config = json.loads(action_config)
                except (TypeError, json.JSONDecodeError):
                    action_config = {}
            instruction = str(
                action_config.get("prompt") if isinstance(action_config, dict) else ""
            ).strip()
            if not instruction:
                raise ValueError("This routine has no instruction to run.")
        else:
            instruction = (
                "Execute this approved routine plan sequentially. Do not take material "
                f"actions outside it. Verify the result.\n\n{plan['plan_json']}"
            )
        task = asyncio.create_task(
            self._run_chat_turn(
                conv_id,
                instruction,
                execution_mode="execute",
                run_id=str(run["id"]),
                plan_id=str(row["plan_id"]) if row.get("plan_id") else None,
                plan_version=int(row["plan_version"]) if row.get("plan_version") else None,
            )
        )
        self._chat_tasks[conv_id] = task
        task.add_done_callback(lambda _task, cid=conv_id: self._chat_tasks.pop(cid, None))
        return {"run": run}

    async def _cmd_test_routine(self, connection: ServerConnection, frame: dict) -> dict:
        row = self.db.get_automation(str(frame.get("routine_id") or ""))
        if row is None:
            raise ValueError("routine not found")
        return {
            "safe": bool(row.get("plan_id") and row.get("plan_version")),
            "services_available": True,
            "side_effects_performed": False,
        }

    async def _cmd_list_routine_runs(self, connection: ServerConnection, frame: dict) -> dict:
        limit = _bounded_list_limit(frame.get("limit"), default=100)
        return {
            "runs": self.db.list_runs(
                routine_id=str(frame.get("routine_id") or ""),
                limit=limit,
            )
        }

    async def _cmd_retry_routine_run(self, connection: ServerConnection, frame: dict) -> dict:
        previous = self.db.get_run(str(frame.get("run_id") or ""))
        if previous is None or previous.get("status") != "failed":
            raise ValueError("Only a failed run can be retried.")
        plan = self.db.get_plan(
            str(previous.get("plan_id") or ""),
            int(previous.get("plan_version") or 0),
        )
        if plan is None or plan.get("status") != "approved":
            raise ValueError("The plan changed and needs review before retrying.")
        conv_id = str(previous.get("conversation_id") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            conv_id = str(self.db.create_conversation("Retried routine")["id"])
        if conv_id in self._chat_tasks and not self._chat_tasks[conv_id].done():
            raise ValueError("This routine is already running.")
        run = self.db.create_run(
            trigger_type="retry",
            idempotency_key=f"retry:{previous['id']}:{uuid.uuid4().hex}",
            plan_id=previous.get("plan_id"),
            plan_version=previous.get("plan_version"),
            routine_id=previous.get("routine_id"),
            conversation_id=conv_id,
        )
        instruction = (
            "Retry this approved routine plan sequentially. Do not take material "
            f"actions outside it. Verify the result.\n\n{plan['plan_json']}"
        )
        task = asyncio.create_task(
            self._run_chat_turn(
                conv_id,
                instruction,
                execution_mode="execute",
                run_id=str(run["id"]),
                plan_id=str(previous["plan_id"]),
                plan_version=int(previous["plan_version"]),
            )
        )
        self._chat_tasks[conv_id] = task
        task.add_done_callback(lambda _task, cid=conv_id: self._chat_tasks.pop(cid, None))
        return {"run": run}
