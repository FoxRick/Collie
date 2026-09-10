"""Automation storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``automations``.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class AutomationsDomain:
    """Automation storage. Mixed into :class:`CollieDB` by composition, never a domain to
    domain call."""

    def add_automation(
        self,
        name: str,
        *,
        description: str = "",
        schedule: str = "",
        action_type: str = "briefing",
        action_config: Any = None,
        enabled: bool = True,
        delivery_channels: Any = None,
        automation_id: str | None = None,
        timezone_name: str = "UTC",
        schedule_json: dict[str, Any] | None = None,
        next_run_at: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
    ) -> dict[str, Any]:
        aid = automation_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO automations (id, name, description, schedule, action_type, "
                "action_config, enabled, delivery_channels, created_at, timezone, "
                "schedule_json, next_run_at, plan_id, plan_version, routine_status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aid,
                    name,
                    description,
                    schedule,
                    action_type,
                    json.dumps(action_config) if action_config is not None else None,
                    1 if enabled else 0,
                    json.dumps(delivery_channels) if delivery_channels is not None else None,
                    now,
                    timezone_name,
                    json.dumps(schedule_json) if schedule_json is not None else None,
                    next_run_at,
                    plan_id,
                    plan_version,
                    "enabled" if enabled else "paused",
                    now,
                ),
            )
        return self._row("SELECT * FROM automations WHERE id = ?", (aid,))  # type: ignore[return-value]

    def list_automations(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automations"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self._rows(sql + " ORDER BY created_at")

    def toggle_automation(self, automation_id: str, enabled: bool) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE automations SET enabled = ?, routine_status = ?, updated_at = ?, "
                # Re-enabling schedules from now: a stale next_run_at must not
                # fire (or skip) instantly on resume.
                "next_run_at = CASE WHEN ? = 1 THEN NULL ELSE next_run_at END "
                "WHERE id = ?",
                (
                    1 if enabled else 0,
                    "enabled" if enabled else "paused",
                    utc_now(),
                    1 if enabled else 0,
                    automation_id,
                ),
            )

    def mark_routine_result(
        self, automation_id: str, *, success: bool, error: str | None = None
    ) -> None:
        now = utc_now()
        with self._write() as conn:
            self._mark_routine_result_with(conn, automation_id, success=success, now=now)

    @staticmethod
    def _mark_routine_result_with(conn, automation_id: str, *, success: bool, now: str) -> None:
        if success:
            conn.execute(
                "UPDATE automations SET last_run = ?, last_success_at = ?, "
                "consecutive_failures = 0, updated_at = ? WHERE id = ?",
                (now, now, now, automation_id),
            )
        else:
            conn.execute(
                "UPDATE automations SET last_failure_at = ?, "
                "consecutive_failures = consecutive_failures + 1, updated_at = ? WHERE id = ?",
                (now, now, automation_id),
            )

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM automations WHERE id = ?", (automation_id,))

    def update_automation(self, automation_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "name",
            "description",
            "schedule",
            "timezone",
            "schedule_json",
            "next_run_at",
            "missed_run_policy",
            "plan_id",
            "plan_version",
            "routine_status",
            "enabled",
            "action_config",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            row = self.get_automation(automation_id)
            if row is None:
                raise ValueError("routine not found")
            return row
        for key in ("schedule_json", "action_config"):
            if key in updates and not isinstance(updates[key], str):
                updates[key] = json.dumps(updates[key])
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._write() as conn:
            cursor = conn.execute(
                f"UPDATE automations SET {assignments} WHERE id = ?",
                (*updates.values(), automation_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("routine not found")
        return self.get_automation(automation_id)  # type: ignore[return-value]

    def set_routine_shared_delivery(
        self, automation_id: str, delivery: dict[str, Any] | None
    ) -> dict[str, Any]:
        with self._write() as conn:
            cursor = conn.execute(
                """UPDATE automations SET shared_delivery=?, shared_delivery_status=?,
                shared_delivery_event_id=NULL, shared_delivery_error=NULL, updated_at=? WHERE id=?""",
                (
                    json.dumps(delivery, sort_keys=True) if delivery else None,
                    "ready" if delivery else None,
                    utc_now(),
                    automation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("routine not found")
        return self.get_automation(automation_id)  # type: ignore[return-value]

    def record_routine_shared_delivery(
        self, automation_id: str, *, status: str, event_id: str | None, error: str | None
    ) -> None:
        if status not in {"ready", "pending", "acknowledged", "rejected"}:
            raise ValueError("invalid shared delivery status")
        with self._write() as conn:
            conn.execute(
                """UPDATE automations SET shared_delivery_status=?,
                shared_delivery_event_id=?, shared_delivery_error=?, updated_at=? WHERE id=?""",
                (status, event_id, error, utc_now(), automation_id),
            )

    def delete_automation(self, automation_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
