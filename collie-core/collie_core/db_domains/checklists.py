"""Task checklist storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``task_checklists``, ``task_checklist_steps`` and
``conversation_review_gates``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ChecklistsDomain:
    """Task checklist storage. Mixed into :class:`CollieDB` by composition, never a domain to
    domain call."""

    def require_conversation_review(
        self, conversation_id: str, reasons: list[str]
    ) -> dict[str, Any]:
        """Persist a review-first gate until an approved plan is claimed."""
        normalized = list(
            dict.fromkeys(
                str(reason or "").strip() for reason in reasons if str(reason or "").strip()
            )
        )
        if not normalized:
            raise ValueError("A conversation review gate needs at least one reason.")
        declared_at = utc_now()
        with self._write_immediate() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("This conversation no longer exists.")
            conn.execute(
                "INSERT INTO conversation_review_gates "
                "(conversation_id, reasons_json, declared_at) VALUES (?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "reasons_json = excluded.reasons_json, declared_at = excluded.declared_at",
                (conversation_id, json.dumps(normalized), declared_at),
            )
        return {
            "conversation_id": conversation_id,
            "reasons": normalized,
            "declared_at": declared_at,
        }

    def get_conversation_review_gate(self, conversation_id: str) -> dict[str, Any] | None:
        row = self._row(
            "SELECT conversation_id, reasons_json, declared_at "
            "FROM conversation_review_gates WHERE conversation_id = ?",
            (conversation_id,),
        )
        if row is None:
            return None
        try:
            reasons = json.loads(str(row["reasons_json"]))
        except (TypeError, json.JSONDecodeError):
            reasons = []
        return {
            "conversation_id": str(row["conversation_id"]),
            "reasons": reasons if isinstance(reasons, list) else [],
            "declared_at": str(row["declared_at"]),
        }

    @staticmethod
    def _checklist_task_with(conn: sqlite3.Connection, checklist: sqlite3.Row) -> dict[str, Any]:
        steps = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM task_checklist_steps WHERE checklist_id = ? ORDER BY ordinal",
                (checklist["id"],),
            ).fetchall()
        ]
        return {
            "id": str(checklist["id"]),
            "conversation_id": str(checklist["conversation_id"]),
            "source": "checklist",
            "status": str(checklist["status"]),
            "revision": int(checklist["revision"]),
            "title": str(checklist["goal"]),
            "completed_count": sum(1 for step in steps if step["status"] == "completed"),
            "total_count": len(steps),
            "current_step_key": checklist["current_step_key"],
            "steps": [
                {
                    "key": str(step["step_key"]),
                    "title": str(step["title"]),
                    "status": str(step["status"]),
                    "summary": step["summary"],
                    "error_message": step["error_message"],
                }
                for step in steps
            ],
            "created_at": checklist["created_at"],
            "completed_at": checklist["completed_at"],
        }

    def create_task_checklist(
        self,
        *,
        conversation_id: str,
        goal: str,
        steps: list[dict[str, str]],
        review_plan_id: str | None = None,
        review_plan_version: int | None = None,
    ) -> dict[str, Any]:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("A task checklist needs a goal.")
        if not isinstance(steps, list) or not 3 <= len(steps) <= 7:
            raise ValueError("A task checklist needs 3 to 7 steps.")

        normalized: list[tuple[str, str]] = []
        keys: set[str] = set()
        for ordinal, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"Checklist step {ordinal + 1} must be an object.")
            key = str(step.get("key") or "").strip()
            title = str(step.get("title") or "").strip()
            if not key or not title:
                raise ValueError(f"Checklist step {ordinal + 1} needs a stable key and title.")
            if key in keys:
                raise ValueError(f"Checklist step key '{key}' is duplicated.")
            keys.add(key)
            normalized.append((key, title))

        checklist_id = new_id()
        now = utc_now()
        try:
            with self._write_immediate() as conn:
                if (
                    conn.execute(
                        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("This conversation no longer exists.")
                conn.execute(
                    "INSERT INTO task_checklists (id, conversation_id, goal, status, "
                    "current_step_key, review_plan_id, review_plan_version, revision, "
                    "created_at, updated_at) VALUES (?, ?, ?, 'active', NULL, ?, ?, 1, ?, ?)",
                    (
                        checklist_id,
                        conversation_id,
                        goal,
                        review_plan_id,
                        review_plan_version,
                        now,
                        now,
                    ),
                )
                for ordinal, (key, title) in enumerate(normalized):
                    conn.execute(
                        "INSERT INTO task_checklist_steps (id, checklist_id, step_key, "
                        "ordinal, title, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                        (new_id(), checklist_id, key, ordinal, title),
                    )
                row = conn.execute(
                    "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
                ).fetchone()
                assert row is not None
                result = self._checklist_task_with(conn, row)
        except sqlite3.IntegrityError as exc:
            if "task_checklists.conversation_id" in str(exc):
                raise ValueError("This conversation already has an active checklist.") from exc
            raise
        return result

    def get_task_checklist(self, checklist_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
            ).fetchone()
            return self._checklist_task_with(self._conn, row) if row is not None else None

    @staticmethod
    def _require_checklist_revision(
        checklist: sqlite3.Row | None, expected_revision: int
    ) -> sqlite3.Row:
        if checklist is None:
            raise ValueError("Task checklist not found.")
        if int(checklist["revision"]) != int(expected_revision):
            raise ValueError("This task checklist changed; use its latest revision.")
        if str(checklist["status"]) != "active":
            raise ValueError("This task checklist is already finished.")
        return checklist

    def update_task_checklist(
        self,
        checklist_id: str,
        *,
        expected_revision: int,
        step_key: str,
        status: str,
        summary: str | None = None,
        error_message: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in self._CHECKLIST_STEP_STATUSES:
            raise ValueError(f"Unknown checklist step status: {status}")
        summary = str(summary).strip() if summary is not None else None
        error_message = str(error_message).strip() if error_message is not None else None
        title = str(title).strip() if title is not None else None
        if status in {"blocked", "failed"} and not error_message:
            raise ValueError(f"A {status} checklist step needs an error message.")
        if title is not None and not title:
            raise ValueError("A checklist step title must not be blank.")

        now = utc_now()
        with self._write_immediate() as conn:
            self._require_checklist_revision(
                conn.execute(
                    "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
                ).fetchone(),
                expected_revision,
            )
            step = conn.execute(
                "SELECT * FROM task_checklist_steps WHERE checklist_id = ? AND step_key = ?",
                (checklist_id, step_key),
            ).fetchone()
            if step is None:
                raise ValueError("Checklist step not found.")
            old_status = str(step["status"])
            if old_status == "completed" and status != "completed":
                raise ValueError("A completed checklist step cannot move backwards.")
            if old_status in {"blocked", "failed", "skipped"} and status != old_status:
                raise ValueError("A finished checklist step cannot move backwards.")
            if title is not None and (old_status != "pending" or status != "pending"):
                raise ValueError("Only a pending checklist step can be renamed.")

            other_current = conn.execute(
                "SELECT step_key FROM task_checklist_steps WHERE checklist_id = ? "
                "AND status = 'in_progress' AND step_key != ? LIMIT 1",
                (checklist_id, step_key),
            ).fetchone()
            if status == "in_progress" and other_current is not None:
                raise ValueError("Only one checklist step can be in progress.")
            if status in {"blocked", "failed"} and other_current is not None:
                raise ValueError("Finish the current checklist step before ending another one.")

            started_at = step["started_at"]
            if status == "in_progress" and not started_at:
                started_at = now
            finished_at = now if status in {"completed", "blocked", "skipped", "failed"} else None
            conn.execute(
                "UPDATE task_checklist_steps SET title = COALESCE(?, title), status = ?, "
                "summary = COALESCE(?, summary), "
                "error_message = COALESCE(?, error_message), "
                "started_at = ?, finished_at = ? "
                "WHERE checklist_id = ? AND step_key = ?",
                (
                    title,
                    status,
                    summary,
                    error_message,
                    started_at,
                    finished_at,
                    checklist_id,
                    step_key,
                ),
            )
            parent_status = status if status in {"blocked", "failed"} else "active"
            current_row = conn.execute(
                "SELECT step_key FROM task_checklist_steps WHERE checklist_id = ? "
                "AND status = 'in_progress' ORDER BY ordinal LIMIT 1",
                (checklist_id,),
            ).fetchone()
            current_step_key = str(current_row["step_key"]) if current_row is not None else None
            completed_at = now if parent_status != "active" else None
            cursor = conn.execute(
                "UPDATE task_checklists SET status = ?, current_step_key = ?, "
                "revision = revision + 1, updated_at = ?, completed_at = ? "
                "WHERE id = ? AND revision = ?",
                (
                    parent_status,
                    current_step_key,
                    now,
                    completed_at,
                    checklist_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("This task checklist changed; use its latest revision.")
            refreshed = conn.execute(
                "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
            ).fetchone()
            assert refreshed is not None
            return self._checklist_task_with(conn, refreshed)

    def complete_task_checklist(
        self, checklist_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        now = utc_now()
        with self._write_immediate() as conn:
            self._require_checklist_revision(
                conn.execute(
                    "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
                ).fetchone(),
                expected_revision,
            )
            unfinished = conn.execute(
                "SELECT 1 FROM task_checklist_steps WHERE checklist_id = ? "
                "AND status NOT IN ('completed', 'skipped') LIMIT 1",
                (checklist_id,),
            ).fetchone()
            if unfinished is not None:
                raise ValueError("Complete or skip every checklist step before finishing the task.")
            cursor = conn.execute(
                "UPDATE task_checklists SET status = 'completed', current_step_key = NULL, "
                "revision = revision + 1, updated_at = ?, completed_at = ? "
                "WHERE id = ? AND revision = ?",
                (now, now, checklist_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("This task checklist changed; use its latest revision.")
            row = conn.execute(
                "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
            ).fetchone()
            assert row is not None
            return self._checklist_task_with(conn, row)

    def cancel_task_checklist(
        self,
        checklist_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        reason = str(reason).strip() if reason is not None else None
        with self._write_immediate() as conn:
            self._require_checklist_revision(
                conn.execute(
                    "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
                ).fetchone(),
                expected_revision,
            )
            conn.execute(
                "UPDATE task_checklist_steps SET status = 'skipped', error_message = ?, "
                "finished_at = ? WHERE checklist_id = ? AND status = 'in_progress'",
                (reason, now, checklist_id),
            )
            cursor = conn.execute(
                "UPDATE task_checklists SET status = 'cancelled', current_step_key = NULL, "
                "revision = revision + 1, updated_at = ?, completed_at = ? "
                "WHERE id = ? AND revision = ?",
                (now, now, checklist_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("This task checklist changed; use its latest revision.")
            row = conn.execute(
                "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
            ).fetchone()
            assert row is not None
            return self._checklist_task_with(conn, row)
