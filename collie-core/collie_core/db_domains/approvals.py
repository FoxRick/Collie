"""Approval rule and request storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``approval_rules`` and ``approval_requests``.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ApprovalsDomain:
    """Approval rule and request storage. Mixed into :class:`CollieDB` by composition, never a
    domain to domain call."""

    def add_approval_rule(
        self,
        *,
        action: str,
        resource_pattern: str,
        effect: str,
        scope_type: str,
        scope_value: str | None = None,
        created_by: str = "user",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        rule_id = new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO approval_rules (id, action, resource_pattern, effect, "
                "scope_type, scope_value, created_by, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    action,
                    resource_pattern,
                    effect,
                    scope_type,
                    scope_value,
                    created_by,
                    expires_at,
                    now,
                    now,
                ),
            )
        return self._row("SELECT * FROM approval_rules WHERE id = ?", (rule_id,))  # type: ignore[return-value]

    def list_approval_rules(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM approval_rules ORDER BY created_at DESC")

    def delete_approval_rule(self, rule_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM approval_rules WHERE id = ?", (rule_id,))

    def create_approval_request(
        self,
        *,
        action: str,
        resource: str,
        risk: str,
        display: dict[str, Any],
        run_id: str | None = None,
        conversation_id: str | None = None,
        step_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = new_id()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO approval_requests (id, run_id, conversation_id, step_id, "
                "tool_call_id, action, resource, risk, display_json, status, requested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    request_id,
                    run_id,
                    conversation_id,
                    step_id,
                    tool_call_id,
                    action,
                    resource,
                    risk,
                    self._canonical_json(display),
                    utc_now(),
                ),
            )
        return self._row("SELECT * FROM approval_requests WHERE id = ?", (request_id,))  # type: ignore[return-value]

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY requested_at"
        )
        for row in rows:
            row["display"] = json.loads(row["display_json"])
        return rows

    def resolve_approval_request(
        self, request_id: str, resolution: str, rule_id: str | None = None
    ) -> dict[str, Any]:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE approval_requests SET status = 'resolved', resolved_at = ?, "
                "resolution = ?, rule_id = ? WHERE id = ? AND status = 'pending'",
                (utc_now(), resolution, rule_id, request_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("approval request is no longer pending")
        return self._row("SELECT * FROM approval_requests WHERE id = ?", (request_id,))  # type: ignore[return-value]
