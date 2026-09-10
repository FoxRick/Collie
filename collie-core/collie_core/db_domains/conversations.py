"""Conversation and message storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``conversations`` and ``messages``.

Known reach outside this domain: ``delete_conversation`` cascades into plans,
runs, run_steps, plan_change_requests, task_checklists, task_checklist_steps,
conversation_review_gates and approval_requests. That cascade is declared in
``tests/collie/test_db_domain_split.py`` and is why the run/plan split will have
to touch this module too.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ConversationsDomain:
    """Conversation and message storage. Mixed into :class:`CollieDB` by composition, never a
    domain to domain call."""

    def create_conversation(
        self,
        title: str = "New chat",
        conv_id: str | None = None,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        cid = conv_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO conversations "
                "(id, title, created_at, updated_at, project_path) VALUES (?, ?, ?, ?, ?)",
                (cid, title, now, now, project_path),
            )
        return {
            "id": cid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "archived": 0,
            "project_path": project_path,
        }

    def get_conversation(self, conv_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM conversations WHERE id = ?", (conv_id,))

    def set_conversation_mode(self, conv_id: str, mode: str) -> None:
        if mode not in {"plan", "execute"}:
            raise ValueError("execution mode must be 'plan' or 'execute'")
        with self._write() as conn:
            conn.execute(
                "UPDATE conversations SET execution_mode = ?, updated_at = ? WHERE id = ?",
                (mode, utc_now(), conv_id),
            )

    def set_conversation_project(self, conv_id: str, project_path: str | None) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE conversations SET project_path = ?, updated_at = ? WHERE id = ?",
                (project_path, utc_now(), conv_id),
            )

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        if include_archived:
            return self._rows("SELECT * FROM conversations ORDER BY updated_at DESC")
        return self._rows("SELECT * FROM conversations WHERE archived = 0 ORDER BY updated_at DESC")

    def rename_conversation(self, conv_id: str, title: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), conv_id),
            )

    def delete_conversation(self, conv_id: str) -> None:
        with self._write() as conn:
            # Children first (messages has an FK to conversations; run_steps
            # cascades from runs, approval_requests is orphaned otherwise).
            conn.execute(
                "DELETE FROM task_checklist_steps WHERE checklist_id IN "
                "(SELECT id FROM task_checklists WHERE conversation_id = ?)",
                (conv_id,),
            )
            conn.execute("DELETE FROM task_checklists WHERE conversation_id = ?", (conv_id,))
            conn.execute(
                "DELETE FROM conversation_review_gates WHERE conversation_id = ?", (conv_id,)
            )
            conn.execute("DELETE FROM plan_change_requests WHERE conversation_id = ?", (conv_id,))
            conn.execute(
                "DELETE FROM run_steps WHERE run_id IN "
                "(SELECT id FROM runs WHERE conversation_id = ?)",
                (conv_id,),
            )
            conn.execute("DELETE FROM runs WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM plans WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM approval_requests WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        tool_calls: Any = None,
        tool_results: Any = None,
        card_type: str | None = None,
        card_data: Any = None,
        task_state: dict[str, Any] | None = None,
        attachments: Any = None,
        token_count: int | None = None,
        msg_id: str | None = None,
    ) -> dict[str, Any]:
        mid = msg_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, tool_calls, "
                "tool_results, card_type, card_data, task_state, attachments, token_count, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mid,
                    conversation_id,
                    role,
                    content,
                    json.dumps(tool_calls) if tool_calls is not None else None,
                    json.dumps(tool_results) if tool_results is not None else None,
                    card_type,
                    json.dumps(card_data) if card_data is not None else None,
                    json.dumps(task_state) if task_state is not None else None,
                    json.dumps(attachments) if attachments is not None else None,
                    token_count,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return {
            "id": mid,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "card_type": card_type,
            "card_data": card_data,
            "task_state": task_state,
            "attachments": attachments,
            "created_at": now,
        }

    def get_messages(self, conversation_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid"
        params: tuple = (conversation_id,)
        if limit is not None:
            if limit <= 0:
                return []
            count = self._row(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            offset = max(0, int(count["n"]) - limit)
            sql += " LIMIT ? OFFSET ?"
            params = (conversation_id, limit, offset)
        rows = self._rows(sql, params)
        for r in rows:
            for field in ("tool_calls", "tool_results", "card_data", "task_state", "attachments"):
                if r.get(field):
                    with suppress(TypeError, ValueError):
                        r[field] = json.loads(r[field])
        return rows

    def all_messages_with_attachments(self) -> list[dict[str, Any]]:
        """Every message that references stored media, with attachments parsed."""
        rows = self._rows(
            "SELECT conversation_id, attachments FROM messages WHERE attachments IS NOT NULL"
        )
        for row in rows:
            if row.get("attachments"):
                try:
                    row["attachments"] = json.loads(row["attachments"])
                except (TypeError, ValueError):
                    row["attachments"] = []
        return rows

    def search_messages(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        like = f"%{query}%"
        return self._rows(
            "SELECT m.*, c.title AS conversation_title FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?",
            (like, limit),
        )
