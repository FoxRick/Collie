"""Life tool storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``people``, ``important_dates``, ``memory_journal``, ``reminders``,
``shopping_items``, ``expenses``, ``budgets`` and ``health_logs``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class LifeDomain:
    """Life tool storage. Mixed into :class:`CollieDB` by composition, never a domain to domain
    call."""

    def add_person(
        self,
        name: str,
        *,
        relationship: str | None = None,
        birthday: str | None = None,
        allergies: str | None = None,
        preferences: str | None = None,
        gift_ideas: str | None = None,
        notes: str | None = None,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        pid = person_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO people (id, name, relationship, birthday, allergies, "
                "preferences, gift_ideas, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    name,
                    relationship,
                    birthday,
                    allergies,
                    preferences,
                    gift_ideas,
                    notes,
                    now,
                    now,
                ),
            )
        return self.get_person(pid)  # type: ignore[return-value]

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM people WHERE id = ?", (person_id,))

    def find_person(self, name: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM people WHERE lower(name) = lower(?) LIMIT 1", (name,))

    def list_people(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM people ORDER BY name COLLATE NOCASE")

    def update_person(self, person_id: str, **fields: Any) -> None:
        allowed = {
            "name",
            "relationship",
            "birthday",
            "allergies",
            "preferences",
            "gift_ideas",
            "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = (*updates.values(), utc_now(), person_id)
        with self._write() as conn:
            conn.execute(f"UPDATE people SET {sets}, updated_at = ? WHERE id = ?", params)

    def delete_person(self, person_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.execute("DELETE FROM important_dates WHERE person_id = ?", (person_id,))

    def add_date(
        self,
        date: str,
        label: str,
        *,
        recurring: bool = False,
        reminder_days_before: int = 7,
        person_id: str | None = None,
        date_id: str | None = None,
    ) -> dict[str, Any]:
        did = date_id or new_id()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO important_dates (id, date, label, recurring, "
                "reminder_days_before, person_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    did,
                    date,
                    label,
                    1 if recurring else 0,
                    reminder_days_before,
                    person_id,
                    utc_now(),
                ),
            )
        return self._row("SELECT * FROM important_dates WHERE id = ?", (did,))  # type: ignore[return-value]

    def list_dates(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM important_dates ORDER BY date")

    def get_date(self, date_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM important_dates WHERE id = ?", (date_id,))

    def update_date(self, date_id: str, **fields: Any) -> None:
        allowed = {"date", "label", "recurring", "reminder_days_before", "person_id"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "recurring" in updates:
            updates["recurring"] = 1 if updates["recurring"] else 0
        if not updates:
            return
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._write() as conn:
            conn.execute(
                f"UPDATE important_dates SET {sets} WHERE id = ?",
                (*updates.values(), date_id),
            )

    def delete_date(self, date_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM important_dates WHERE id = ?", (date_id,))

    def log_memory_journal(
        self,
        kind: str,
        subject: str,
        action: str,
        value: Any = None,
    ) -> None:
        """Append one memory mutation to the journal (add|update|delete)."""
        snapshot = json.dumps(value, ensure_ascii=False) if value is not None else None
        with self._write() as conn:
            conn.execute(
                "INSERT INTO memory_journal (kind, subject, action, value, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, subject, action, snapshot, utc_now()),
            )

    def list_memory_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        """Most recent journal entries first, newest on top."""
        rows = self._rows("SELECT * FROM memory_journal ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            if row.get("value"):
                with suppress(TypeError, ValueError):
                    row["value"] = json.loads(row["value"])
        return rows

    def add_reminder(
        self,
        text: str,
        due_at: str,
        *,
        recurrence: str | None = None,
        reminder_id: str | None = None,
    ) -> dict[str, Any]:
        rid = reminder_id or new_id()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO reminders (id, text, due_at, recurrence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rid, text, due_at, recurrence, utc_now()),
            )
        return self._row("SELECT * FROM reminders WHERE id = ?", (rid,))  # type: ignore[return-value]

    def list_reminders(self, include_completed: bool = False) -> list[dict[str, Any]]:
        if include_completed:
            return self._rows("SELECT * FROM reminders ORDER BY due_at")
        return self._rows("SELECT * FROM reminders WHERE completed = 0 ORDER BY due_at")

    def due_reminders(self, now: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM reminders WHERE completed = 0 AND due_at <= ? "
            "AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY due_at",
            (now, now),
        )

    @staticmethod
    def _resolve_reminder_id(conn: sqlite3.Connection, reminder_id: str) -> str | None:
        """Resolve an exact ID or one unambiguous displayed 8-character prefix."""
        exact = conn.execute("SELECT id FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if exact is not None:
            return str(exact["id"])
        if len(reminder_id) != 8 or any(
            character not in "0123456789abcdefABCDEF" for character in reminder_id
        ):
            return None
        matches = conn.execute(
            "SELECT id FROM reminders WHERE substr(id, 1, 8) = ? LIMIT 2",
            (reminder_id.lower(),),
        ).fetchall()
        return str(matches[0]["id"]) if len(matches) == 1 else None

    def complete_reminder(self, reminder_id: str) -> bool:
        with self._write() as conn:
            resolved_id = self._resolve_reminder_id(conn, reminder_id)
            if resolved_id is None:
                return False
            cursor = conn.execute(
                "UPDATE reminders SET completed = 1 WHERE id = ?",
                (resolved_id,),
            )
            return cursor.rowcount > 0

    def snooze_reminder(self, reminder_id: str, until: str) -> bool:
        with self._write() as conn:
            resolved_id = self._resolve_reminder_id(conn, reminder_id)
            if resolved_id is None:
                return False
            cursor = conn.execute(
                "UPDATE reminders SET snoozed_until = ? WHERE id = ?",
                (until, resolved_id),
            )
            return cursor.rowcount > 0

    def delete_reminder(self, reminder_id: str) -> bool:
        with self._write() as conn:
            resolved_id = self._resolve_reminder_id(conn, reminder_id)
            if resolved_id is None:
                return False
            cursor = conn.execute("DELETE FROM reminders WHERE id = ?", (resolved_id,))
            return cursor.rowcount > 0

    def add_shopping_item(
        self,
        item: str,
        *,
        category: str = "Other",
        quantity: str | None = None,
        list_name: str = "Groceries",
    ) -> dict[str, Any]:
        iid = new_id()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO shopping_items (id, list_name, item, category, quantity, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (iid, list_name, item, category or "Other", quantity, utc_now()),
            )
        return self._row("SELECT * FROM shopping_items WHERE id = ?", (iid,))  # type: ignore[return-value]

    def list_shopping_items(
        self, list_name: str = "Groceries", include_checked: bool = True
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM shopping_items WHERE list_name = ?"
        if not include_checked:
            sql += " AND checked = 0"
        return self._rows(sql + " ORDER BY category, created_at", (list_name,))

    def find_shopping_item(self, item: str, list_name: str = "Groceries") -> dict[str, Any] | None:
        return self._row(
            "SELECT * FROM shopping_items WHERE list_name = ? AND lower(item) = lower(?) LIMIT 1",
            (list_name, item),
        )

    def check_shopping_item_by_name(self, item: str, list_name: str, checked: bool = True) -> int:
        """Check/uncheck every row with the given name (duplicates included)."""
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE shopping_items SET checked = ? "
                "WHERE list_name = ? AND lower(item) = lower(?)",
                (1 if checked else 0, list_name, item),
            )
            return cur.rowcount

    def delete_shopping_item_by_name(self, item: str, list_name: str) -> int:
        """Delete every row with the given name (duplicates included)."""
        with self._write() as conn:
            cur = conn.execute(
                "DELETE FROM shopping_items WHERE list_name = ? AND lower(item) = lower(?)",
                (list_name, item),
            )
            return cur.rowcount

    def clear_checked_shopping_items(self, list_name: str = "Groceries") -> int:
        with self._write() as conn:
            cur = conn.execute(
                "DELETE FROM shopping_items WHERE list_name = ? AND checked = 1",
                (list_name,),
            )
            return cur.rowcount

    def add_expense(
        self,
        amount: float,
        *,
        category: str = "Other",
        description: str | None = None,
        spent_at: str | None = None,
    ) -> dict[str, Any]:
        eid = new_id()
        day = spent_at or self._local_today()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO expenses (id, amount, category, description, spent_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    eid,
                    amount,
                    category or "Other",
                    description,
                    day,
                    utc_now(),
                ),
            )
        return self._row("SELECT * FROM expenses WHERE id = ?", (eid,))  # type: ignore[return-value]

    def expenses_by_category(self, month: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT category, SUM(amount) AS spent FROM expenses "
            "WHERE spent_at LIKE ? GROUP BY category ORDER BY spent DESC",
            (f"{month}%",),
        )

    def set_budget(self, category: str, monthly_limit: float) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO budgets (category, monthly_limit, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(category) DO UPDATE SET "
                "monthly_limit = excluded.monthly_limit, updated_at = excluded.updated_at",
                (category, monthly_limit, utc_now()),
            )

    def list_budgets(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM budgets ORDER BY category")

    def log_health(
        self,
        metric: str,
        value: float,
        *,
        logged_on: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        # User-visible "today"/"this month" windows use the local calendar,
        # so the default date must be local (not UTC) to stay consistent.
        day = logged_on or self._local_today()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO health_logs (id, metric, value, logged_on, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(metric, logged_on) DO UPDATE SET "
                "value = excluded.value, note = excluded.note",
                (new_id(), metric, value, day, note, utc_now()),
            )
        return self._row(  # type: ignore[return-value]
            "SELECT * FROM health_logs WHERE metric = ? AND logged_on = ?",
            (metric, day),
        )

    def health_logs_since(self, since_day: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM health_logs WHERE logged_on >= ? ORDER BY logged_on",
            (since_day,),
        )

    def health_latest(self, metric: str) -> dict[str, Any] | None:
        return self._row(
            "SELECT * FROM health_logs WHERE metric = ? ORDER BY logged_on DESC LIMIT 1",
            (metric,),
        )
