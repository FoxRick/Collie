"""Durable local outbox, materialized shared history, and cursor storage."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from collie_core.db import utc_now


class SyncConflictError(ValueError):
    pass


class CollaborationStore:
    """A separate local database so personal chat schema and semantics stay intact."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._account_id = ""
        self._conn: sqlite3.Connection | None = None

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._migrate()
        return self._conn

    def _migrate(self) -> None:
        connection = self._conn
        assert connection is not None
        with connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS shared_events (
              account_id TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('message','edit','delete')),
              message_id TEXT NOT NULL, author_id TEXT NOT NULL, role TEXT NOT NULL,
              content TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
              payload_json TEXT NOT NULL, PRIMARY KEY(account_id,event_id), UNIQUE(account_id,session_id,seq)
            );
            CREATE INDEX IF NOT EXISTS idx_shared_events_session_seq
              ON shared_events(account_id,session_id, seq);
            CREATE TABLE IF NOT EXISTS shared_outbox (
              account_id TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT NOT NULL, payload_json TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT, created_at TEXT NOT NULL, cloud_seq INTEGER,
              PRIMARY KEY(account_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_shared_outbox_pending
              ON shared_outbox(account_id,state, created_at);
            CREATE TABLE IF NOT EXISTS shared_cursors (
              account_id TEXT NOT NULL, session_id TEXT NOT NULL, high_water INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL, PRIMARY KEY(account_id,session_id)
            );
            CREATE TABLE IF NOT EXISTS shared_session_cache (
              account_id TEXT NOT NULL, session_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
              membership_revision INTEGER NOT NULL, state TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(account_id,session_id)
            );
            """)

    def bind_account(self, account_id: str) -> None:
        self._account_id = str(account_id).strip()

    @property
    def is_bound(self) -> bool:
        return bool(self._account_id)

    @property
    def account_id(self) -> str:
        return self._account()

    def _account(self) -> str:
        if not self._account_id:
            raise ValueError("Sign in before using shared-session storage.")
        return self._account_id

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def queue_event(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist an optimistic event and outbox row in the same transaction."""
        return self.queue_event_for_account(self._account(), session_id, payload)

    def queue_event_for_account(
        self, account_id: str, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an event for its previously verified creator account.

        Scheduled routines may finish while another account (or no account) is
        bound to the UI.  Their configured creator identity is stored when the
        authenticated main process enables delivery, so enqueue directly into
        that account partition and let its next reconciliation publish it.
        """
        account = str(account_id).strip()
        if not account:
            raise ValueError("Shared event requires a creator account.")
        event = dict(payload)
        event.setdefault("event_id", str(uuid.uuid4()))
        event.setdefault("session_id", session_id)
        event.setdefault("created_at", utc_now())
        if event["session_id"] != session_id:
            raise SyncConflictError("The event session does not match its outbox session.")
        for key in ("event_id", "message_id", "author_id", "role", "kind"):
            if not str(event.get(key) or ""):
                raise ValueError(f"Shared event requires {key}.")
        if event["kind"] not in {"message", "edit", "delete"}:
            raise ValueError("Unsupported shared event kind.")
        event.setdefault("content", "")
        event.setdefault("revision", 1)
        raw = self._canonical(event)
        with self._lock, self._connection:
            prior = self._connection.execute(
                "SELECT payload_json FROM shared_outbox WHERE account_id=? AND event_id=?",
                (account, event["event_id"]),
            ).fetchone()
            if prior and prior[0] != raw:
                raise SyncConflictError("An event ID cannot be reused with a different payload.")
            self._connection.execute(
                "INSERT OR IGNORE INTO shared_outbox(account_id,event_id,session_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (account, event["event_id"], session_id, raw, event["created_at"]),
            )
        return event

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT payload_json,attempts,last_error FROM shared_outbox WHERE account_id=? AND state='pending' ORDER BY created_at LIMIT ?",
            (self._account(), max(1, min(int(limit), 500))),
        ).fetchall()
        return [json.loads(r[0]) | {"attempts": r[1], "last_error": r[2]} for r in rows]

    def mark_attempt(self, event_id: str, error: str | None = None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE shared_outbox SET attempts=attempts+1,last_error=? WHERE account_id=? AND event_id=? AND state='pending'",
                (error, self._account(), event_id),
            )

    def settle_outbox(self, event_id: str, status: str, error: str | None = None) -> None:
        if status not in {"acknowledged", "rejected"}:
            raise ValueError("invalid outbox terminal status")
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE shared_outbox SET state=?, attempts=attempts+1,last_error=?
                WHERE account_id=? AND event_id=? AND state='pending'""",
                (status, error, self._account(), event_id),
            )

    def apply_page(
        self, session_id: str, events: Iterable[dict[str, Any]], *, next_cursor: int
    ) -> int:
        """Apply a complete ordered page and advance its cursor atomically."""
        page = [dict(item) for item in events]
        with self._lock, self._connection:
            account = self._account()
            current = self.cursor(session_id)
            expected = current + 1
            for event in page:
                if str(event.get("session_id") or "") != session_id:
                    raise SyncConflictError("A remote page contained another session.")
                seq = int(event.get("seq") or 0)
                raw = self._canonical(event)
                prior = self._connection.execute(
                    "SELECT payload_json,seq FROM shared_events WHERE account_id=? AND event_id=?",
                    (account, event.get("event_id")),
                ).fetchone()
                if prior:
                    if prior[0] != raw:
                        raise SyncConflictError("A remote event changed after acceptance.")
                    expected = max(expected, int(prior[1]) + 1)
                    continue
                if seq != expected:
                    raise SyncConflictError(f"Expected shared sequence {expected}, received {seq}.")
                self._connection.execute(
                    """INSERT INTO shared_events
                    (account_id,event_id,session_id,seq,kind,message_id,author_id,role,content,revision,created_at,payload_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        account,
                        event["event_id"],
                        session_id,
                        seq,
                        event["kind"],
                        event["message_id"],
                        event["author_id"],
                        event["role"],
                        str(event.get("content") or ""),
                        int(event.get("revision") or 1),
                        event["created_at"],
                        raw,
                    ),
                )
                self._connection.execute(
                    "UPDATE shared_outbox SET state='acknowledged',cloud_seq=?,last_error=NULL WHERE account_id=? AND event_id=?",
                    (seq, account, event["event_id"]),
                )
                expected += 1
            if next_cursor < current or (page and next_cursor != int(page[-1]["seq"])):
                raise SyncConflictError("The remote page cursor does not match its last event.")
            if not page and next_cursor != current:
                raise SyncConflictError("An empty remote page cannot advance the cursor.")
            self._connection.execute(
                """INSERT INTO shared_cursors(account_id,session_id,high_water,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(account_id,session_id) DO UPDATE SET high_water=excluded.high_water,updated_at=excluded.updated_at""",
                (account, session_id, next_cursor, utc_now()),
            )
        return next_cursor

    def cursor(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT high_water FROM shared_cursors WHERE account_id=? AND session_id=?",
            (self._account(), session_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def events(self, session_id: str, *, through: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM shared_events WHERE account_id=? AND session_id=?"
        args: list[Any] = [self._account(), session_id]
        if through is not None:
            sql += " AND seq<=?"
            args.append(int(through))
        rows = self._connection.execute(sql + " ORDER BY seq", args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def materialized_messages(
        self, session_id: str, *, through: int | None = None
    ) -> list[dict[str, Any]]:
        messages: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        events = self.events(session_id, through=through)
        if through is None:
            pending_rows = self._connection.execute(
                """SELECT payload_json FROM shared_outbox
                WHERE account_id=? AND session_id=? AND state='pending' ORDER BY created_at""",
                (self._account(), session_id),
            ).fetchall()
            events.extend(json.loads(row[0]) | {"sync_state": "local"} for row in pending_rows)
        for event in events:
            mid = str(event["message_id"])
            if event["kind"] == "message":
                if mid not in messages:
                    order.append(mid)
                messages[mid] = event | {"deleted": False}
            elif mid in messages and int(event.get("revision", 0)) > int(
                messages[mid].get("revision", 0)
            ):
                messages[mid]["content"] = str(event.get("content") or "")
                messages[mid]["revision"] = int(event.get("revision") or 0)
                messages[mid]["last_event_seq"] = int(event.get("seq") or 0)
                messages[mid]["deleted"] = event["kind"] == "delete"
        return [messages[mid] for mid in order]

    def cache_bootstrap(self, snapshot: dict[str, Any]) -> None:
        raw = self._canonical(snapshot)
        if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("The collaboration bootstrap cache is too large.")
        revision = int(snapshot.get("revision") or 0)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO shared_session_cache
                (account_id,session_id,metadata_json,membership_revision,state,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(account_id,session_id) DO UPDATE SET
                  metadata_json=excluded.metadata_json,
                  membership_revision=excluded.membership_revision,
                  state=excluded.state,
                  updated_at=excluded.updated_at""",
                (self._account(), "__bootstrap__", raw, revision, "cached", utc_now()),
            )

    def cached_bootstrap(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT metadata_json FROM shared_session_cache WHERE account_id=? AND session_id='__bootstrap__'",
            (self._account(),),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
