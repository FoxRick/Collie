"""Collie SQLite storage layer.

Single database at ``~/.collie/collie.db`` holding everything structured:
conversations, messages, profile, people, important dates, automations,
services, subagents, settings, providers, and usage.

Design:
- One writer connection guarded by a lock; WAL mode so readers never block.
- ``schema_version`` table + ordered migration list for upgrades.
- All timestamps are ISO-8601 UTC strings.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

__all__ = ["CollieDB", "collie_home", "utc_now"]


def collie_home() -> Path:
    """Return the Collie data directory (``~/.collie`` or ``$COLLIE_HOME``)."""
    root = os.environ.get("COLLIE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".collie"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT,
    updated_at TEXT,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    role TEXT,
    content TEXT,
    tool_calls TEXT,
    tool_results TEXT,
    card_type TEXT,
    card_data TEXT,
    token_count INTEGER,
    created_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT,
    relationship TEXT,
    birthday TEXT,
    allergies TEXT,
    preferences TEXT,
    gift_ideas TEXT,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS important_dates (
    id TEXT PRIMARY KEY,
    date TEXT,
    label TEXT,
    recurring INTEGER DEFAULT 0,
    reminder_days_before INTEGER DEFAULT 7,
    person_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    schedule TEXT,
    action_type TEXT,
    action_config TEXT,
    enabled INTEGER DEFAULT 1,
    delivery_channels TEXT,
    last_run TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    name TEXT,
    provider TEXT,
    auth_type TEXT,
    status TEXT,
    account_info TEXT,
    connected_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS subagents (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    system_prompt TEXT,
    filename TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT,
    auth_type TEXT,
    is_default INTEGER DEFAULT 0,
    model TEXT,
    created_at TEXT,
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    id TEXT PRIMARY KEY,
    provider_id TEXT,
    date TEXT,
    message_count INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    FOREIGN KEY (provider_id) REFERENCES providers(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_provider_date
    ON usage(provider_id, date);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    text TEXT,
    due_at TEXT,
    recurrence TEXT,
    completed INTEGER DEFAULT 0,
    snoozed_until TEXT,
    created_at TEXT
);
"""

# Phase 3 in-app life-tool tables: shopping list, budget, health (F027, F031, F032).
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS shopping_items (
    id TEXT PRIMARY KEY,
    list_name TEXT DEFAULT 'Groceries',
    item TEXT,
    category TEXT DEFAULT 'Other',
    quantity TEXT,
    checked INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shopping_list ON shopping_items(list_name, checked);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    amount REAL,
    category TEXT DEFAULT 'Other',
    description TEXT,
    spent_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(spent_at);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS health_logs (
    id TEXT PRIMARY KEY,
    metric TEXT,
    value REAL,
    logged_on TEXT,
    note TEXT,
    created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_health_metric_day
    ON health_logs(metric, logged_on);
"""

_SCHEMA_V3 = """
ALTER TABLE messages ADD COLUMN attachments TEXT;
"""

_SCHEMA_V4 = """
ALTER TABLE conversations ADD COLUMN execution_mode TEXT DEFAULT 'plan';

ALTER TABLE automations ADD COLUMN timezone TEXT DEFAULT 'UTC';
ALTER TABLE automations ADD COLUMN schedule_json TEXT;
ALTER TABLE automations ADD COLUMN plan_id TEXT;
ALTER TABLE automations ADD COLUMN plan_version INTEGER;
ALTER TABLE automations ADD COLUMN next_run_at TEXT;
ALTER TABLE automations ADD COLUMN last_success_at TEXT;
ALTER TABLE automations ADD COLUMN last_failure_at TEXT;
ALTER TABLE automations ADD COLUMN consecutive_failures INTEGER DEFAULT 0;
ALTER TABLE automations ADD COLUMN missed_run_policy TEXT DEFAULT 'recent_once';
ALTER TABLE automations ADD COLUMN routine_status TEXT DEFAULT 'paused';
ALTER TABLE automations ADD COLUMN updated_at TEXT;

CREATE TABLE IF NOT EXISTS plans (
    id TEXT NOT NULL,
    conversation_id TEXT,
    routine_id TEXT,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    source_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    PRIMARY KEY(id, version)
);
CREATE INDEX IF NOT EXISTS idx_plans_conversation
    ON plans(conversation_id, updated_at);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    plan_version INTEGER,
    routine_id TEXT,
    conversation_id TEXT,
    trigger_type TEXT NOT NULL,
    scheduled_for TEXT,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(routine_id, scheduled_for, plan_version)
);
CREATE INDEX IF NOT EXISTS idx_runs_routine_created
    ON runs(routine_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, heartbeat_at);

CREATE TABLE IF NOT EXISTS run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    tool_name TEXT,
    input_summary TEXT,
    output_summary TEXT,
    approval_request_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    UNIQUE(run_id, step_key),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, ordinal);

CREATE TABLE IF NOT EXISTS approval_rules (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    resource_pattern TEXT NOT NULL,
    effect TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_value TEXT,
    created_by TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_rules_action
    ON approval_rules(action, effect);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    conversation_id TEXT,
    step_id TEXT,
    tool_call_id TEXT,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    risk TEXT NOT NULL,
    display_json TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    rule_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_status
    ON approval_requests(status, requested_at);
"""

_SCHEMA_V5 = """
ALTER TABLE conversations ADD COLUMN project_path TEXT;
"""

_SCHEMA_V6 = """
ALTER TABLE providers ADD COLUMN runtime_name TEXT;
ALTER TABLE providers ADD COLUMN protocol TEXT DEFAULT 'openai';
ALTER TABLE providers ADD COLUMN api_base TEXT;
ALTER TABLE providers ADD COLUMN secret_name TEXT;

ALTER TABLE subagents ADD COLUMN execution_posture TEXT DEFAULT 'read_only';

UPDATE providers SET runtime_name = name WHERE runtime_name IS NULL;
UPDATE providers SET secret_name = name WHERE secret_name IS NULL;
UPDATE providers
SET api_base = (
    SELECT json_extract(value, '$') FROM settings WHERE key = 'provider.api_base'
)
WHERE is_default = 1
  AND api_base IS NULL
  AND EXISTS (SELECT 1 FROM settings WHERE key = 'provider.api_base');
UPDATE providers
SET protocol = 'anthropic'
WHERE lower(runtime_name) = 'anthropic';
"""

_SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS connector_connections (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    display_name TEXT,
    account_label TEXT,
    driver TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    status TEXT NOT NULL,
    granted_scopes_json TEXT,
    enabled_capabilities_json TEXT,
    enabled_tools_json TEXT,
    tool_policy_json TEXT,
    remote_account_id TEXT,
    connected_at TEXT,
    updated_at TEXT,
    last_verified_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT
);
CREATE INDEX IF NOT EXISTS connector_connections_provider
    ON connector_connections(provider_id);

CREATE TABLE IF NOT EXISTS connector_tool_cache (
    connection_id TEXT NOT NULL,
    remote_tool_name TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    annotations_json TEXT,
    risk TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY(connection_id, remote_tool_name),
    FOREIGN KEY(connection_id) REFERENCES connector_connections(id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO connector_connections (
    id, provider_id, display_name, account_label, driver, auth_type, status,
    connected_at, updated_at, last_error_message
)
SELECT
    'con_legacy_' || replace(id, '-', '_'), id, name, account_info,
    'legacy_service', auth_type, status, connected_at,
    COALESCE(connected_at, CURRENT_TIMESTAMP), last_error
FROM services
WHERE status IN ('connected', 'failed', 'disconnected');
"""

_SCHEMA_V8 = """
DELETE FROM approval_rules
WHERE action = 'subagent.spawn'
  AND resource_pattern = '*'
  AND effect = 'allow'
  AND scope_type = 'global'
  AND created_by = 'system';
"""

_SCHEMA_V9 = """
ALTER TABLE messages ADD COLUMN task_state TEXT;

CREATE TABLE IF NOT EXISTS conversation_review_gates (
    conversation_id TEXT PRIMARY KEY,
    reasons_json TEXT NOT NULL,
    declared_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_checklists (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step_key TEXT,
    review_plan_id TEXT,
    review_plan_version INTEGER,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_checklists_active_conversation
    ON task_checklists(conversation_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_task_checklists_conversation_updated
    ON task_checklists(conversation_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS task_checklist_steps (
    id TEXT PRIMARY KEY,
    checklist_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(checklist_id, step_key),
    FOREIGN KEY (checklist_id) REFERENCES task_checklists(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_checklist_steps_checklist
    ON task_checklist_steps(checklist_id, ordinal);

CREATE TABLE IF NOT EXISTS run_task_state_revisions (
    run_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
INSERT OR IGNORE INTO run_task_state_revisions (run_id, revision, updated_at)
SELECT id, 1, COALESCE(heartbeat_at, started_at, created_at, CURRENT_TIMESTAMP)
FROM runs;

CREATE TABLE IF NOT EXISTS plan_change_requests (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    finalized_at TEXT,
    replacement_plan_version INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_plan_change_requests_conversation
    ON plan_change_requests(conversation_id, requested_at DESC);
"""

_SCHEMA_V10 = """
ALTER TABLE plan_change_requests ADD COLUMN terminal_message_id TEXT;
"""

_SCHEMA_V11 = """
CREATE TABLE IF NOT EXISTS turn_events (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  session_key TEXT,
  turn_kind TEXT NOT NULL,          -- chat|plan|routine|cron|subagent|automation
  provider TEXT, model TEXT,
  status TEXT NOT NULL,             -- running|ok|error|stopped|cancelled
  error_message TEXT,
  tokens_in INTEGER, tokens_out INTEGER,
  latency_ms INTEGER,
  tool_count INTEGER DEFAULT 0,
  started_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_turn_events_conv ON turn_events(conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_turn_events_status ON turn_events(status, started_at DESC);

CREATE TABLE IF NOT EXISTS tool_events (
  id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  action TEXT, resource TEXT,       -- from permission classifier (join to approval_requests)
  input_summary TEXT,               -- redacted + truncated (<=500 chars)
  output_summary TEXT,              -- redacted + truncated (<=1000 chars)
  status TEXT NOT NULL,             -- running|ok|error|denied|timeout
  error_message TEXT,
  latency_ms INTEGER,
  started_at TEXT NOT NULL, finished_at TEXT,
  FOREIGN KEY (turn_id) REFERENCES turn_events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tool_events_tool ON tool_events(tool_name, status, started_at DESC);
"""

_SCHEMA_V12 = """
ALTER TABLE turn_events ADD COLUMN prompt_hash TEXT;
ALTER TABLE turn_events ADD COLUMN tool_schema_hash TEXT;
ALTER TABLE turn_events ADD COLUMN config_hash TEXT;
"""

# Append-only journal of every memory mutation. Powers the Settings -> Memory
# "recently remembered" view and one-action undo (Gardener rollback rail):
# kind (fact|person|date), subject (key/name/label), action (add|update|delete),
# value (new value / snapshot for undo), created_at.
_SCHEMA_V13 = """
CREATE TABLE IF NOT EXISTS memory_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    action TEXT NOT NULL,
    value TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_journal_created
    ON memory_journal(created_at DESC);
"""

# Versioned artifact store (PR 2 of the Gardener Foundations plan): every
# user-visible artifact edit (subagent files, VISION.md / AGENTS.md /
# MEMORY.md, dream consolidations, Gardener applies) is snapshotted with a
# before/after text pair + unified diff so any change can be rolled back
# without clobbering newer owner edits. Complementary to the memory_journal
# (which records per-key memory mutations) — this records full-file states.
_SCHEMA_V14 = """
CREATE TABLE IF NOT EXISTS artifact_versions (
  id TEXT PRIMARY KEY,
  artifact_type TEXT NOT NULL,      -- subagent|vision|agents|memory_profile|memory_dream|skill
  artifact_key TEXT NOT NULL,       -- subagent filename | "VISION.md" | "AGENTS.md" | "MEMORY.md" | "memory/MEMORY.md"
  version INTEGER NOT NULL,
  before_text TEXT,
  after_text TEXT,
  diff_text TEXT,                   -- difflib.unified_diff
  evidence_json TEXT,               -- Gardener trigger evidence (run ids, tool stats)
  source TEXT NOT NULL DEFAULT 'user',  -- user|collie|gardener
  status TEXT NOT NULL DEFAULT 'applied', -- applied|rolled_back
  created_at TEXT NOT NULL,
  UNIQUE(artifact_type, artifact_key, version)
);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_type
  ON artifact_versions(artifact_type, artifact_key, version DESC);
"""

# Ordered migrations: index 0 == schema version 1, etc.
_MIGRATIONS: list[str] = [
    _SCHEMA_V1,
    _SCHEMA_V2,
    _SCHEMA_V3,
    _SCHEMA_V4,
    _SCHEMA_V5,
    _SCHEMA_V6,
    _SCHEMA_V7,
    _SCHEMA_V8,
    _SCHEMA_V9,
    _SCHEMA_V10,
    _SCHEMA_V11,
    _SCHEMA_V12,
    _SCHEMA_V13,
    _SCHEMA_V14,
]


class CollieDB:
    """Thread-safe SQLite wrapper for all Collie structured storage."""

    def __init__(self, db_path: Path | None = None):
        self.path = db_path or (collie_home() / "collie.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Second instances (double-launched cores) must wait instead of
        # failing mid-write with "database is locked".
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> CollieDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- migrations ----------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            current = row["version"] if row else 0
            for i, script in enumerate(_MIGRATIONS, start=1):
                if i > current:
                    # executescript() commits any pending transaction first, so
                    # the DDL and the version bump must share one explicit
                    # BEGIN/COMMIT block: a crash between them used to re-run
                    # the migration on the next boot (failing on ALTERs).
                    self._conn.executescript(
                        f"BEGIN IMMEDIATE;\n{script}\n"
                        "DELETE FROM schema_version;\n"
                        f"INSERT INTO schema_version (version) VALUES ({i});\n"
                        "COMMIT;"
                    )

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            return int(row["version"]) if row else 0

    # -- low-level helpers -----------------------------------------------------

    @staticmethod
    def _local_today() -> str:
        return date.today().isoformat()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._conn:
            yield self._conn

    @contextmanager
    def _write_immediate(self) -> Iterator[sqlite3.Connection]:
        """Open a write transaction before performing any validation reads.

        ``BEGIN IMMEDIATE`` makes a plan claim serialize across separate
        ``CollieDB`` instances as well as across threads sharing this instance.
        That keeps the compare-and-set and idempotency lookup in one SQLite
        transaction instead of relying only on the in-process ``RLock``.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    # -- settings --------------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._row("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def set_active_model(self, model: str) -> None:
        """Persist the active model atomically: the global setting AND the
        default provider row stay in agreement (one canonical source)."""
        with self._write() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("provider.model", json.dumps(model)),
            )
            conn.execute(
                "UPDATE providers SET model = ? WHERE is_default = 1",
                (model,),
            )

    def all_settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self._rows("SELECT key, value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                out[row["key"]] = row["value"]
        return out

    def delete_setting(self, key: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    # -- conversations -----------------------------------------------------------

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

    def archive_conversation(self, conv_id: str, archived: bool = True) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE conversations SET archived = ?, updated_at = ? WHERE id = ?",
                (1 if archived else 0, utc_now(), conv_id),
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

    # -- messages -----------------------------------------------------------------

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

    def delete_message(self, msg_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))

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

    # -- profile ---------------------------------------------------------------------

    def get_profile(self, key: str, default: Any = None) -> Any:
        row = self._row("SELECT value FROM profile WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_profile(self, key: str, value: Any) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO profile (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, json.dumps(value), utc_now()),
            )

    def all_profile(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self._rows("SELECT key, value FROM profile"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                out[row["key"]] = row["value"]
        return out

    def delete_profile(self, key: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM profile WHERE key = ?", (key,))

    # -- people -----------------------------------------------------------------------

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

    # -- important dates -----------------------------------------------------------------

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

    # -- memory journal ----------------------------------------------------------

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

    # -- reminders ---------------------------------------------------------------------------

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
    def _reminder_match(reminder_id: str) -> str:
        """Full-ID or displayed 8-char prefix match."""
        return reminder_id if len(reminder_id) >= 32 else reminder_id + "%"

    def complete_reminder(self, reminder_id: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reminders SET completed = 1 WHERE id = ?",
                (self._reminder_match(reminder_id),),
            )
            return cursor.rowcount > 0

    def snooze_reminder(self, reminder_id: str, until: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reminders SET snoozed_until = ? WHERE id = ?",
                (until, self._reminder_match(reminder_id)),
            )
            return cursor.rowcount > 0

    def delete_reminder(self, reminder_id: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM reminders WHERE id = ?", (self._reminder_match(reminder_id),)
            )
            return cursor.rowcount > 0

    # -- shopping list -----------------------------------------------------------------------

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

    def check_shopping_item(self, item_id: str, checked: bool = True) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE shopping_items SET checked = ? WHERE id = ?",
                (1 if checked else 0, item_id),
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

    def delete_shopping_item(self, item_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))

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

    # -- budget ------------------------------------------------------------------------------

    def add_expense(
        self,
        amount: float,
        *,
        category: str = "Other",
        description: str | None = None,
        spent_at: str | None = None,
    ) -> dict[str, Any]:
        eid = new_id()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO expenses (id, amount, category, description, spent_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    eid,
                    amount,
                    category or "Other",
                    description,
                    spent_at or utc_now()[:10],
                    utc_now(),
                ),
            )
        return self._row("SELECT * FROM expenses WHERE id = ?", (eid,))  # type: ignore[return-value]

    def expenses_for_month(self, month: str) -> list[dict[str, Any]]:
        """List expenses for a month given as ``YYYY-MM``."""
        return self._rows(
            "SELECT * FROM expenses WHERE spent_at LIKE ? ORDER BY spent_at",
            (f"{month}%",),
        )

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

    # -- health -------------------------------------------------------------------------------

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

    # -- automations ------------------------------------------------------------------------

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

    def mark_automation_run(self, automation_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE automations SET last_run = ? WHERE id = ?",
                (utc_now(), automation_id),
            )

    def mark_routine_result(
        self, automation_id: str, *, success: bool, error: str | None = None
    ) -> None:
        now = utc_now()
        with self._write() as conn:
            if success:
                conn.execute(
                    "UPDATE automations SET last_run = ?, last_success_at = ?, "
                    "consecutive_failures = 0, updated_at = ? WHERE id = ?",
                    (now, now, now, automation_id),
                )
            else:
                conn.execute(
                    "UPDATE automations SET last_failure_at = ?, "
                    "consecutive_failures = consecutive_failures + 1, updated_at = ? "
                    "WHERE id = ?",
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
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            row = self.get_automation(automation_id)
            if row is None:
                raise ValueError("routine not found")
            return row
        for key in ("schedule_json",):
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

    def delete_automation(self, automation_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))

    # -- task checklists ---------------------------------------------------------------

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

    def clear_conversation_review_gate(self, conversation_id: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM conversation_review_gates WHERE conversation_id = ?",
                (conversation_id,),
            )
            return cursor.rowcount == 1

    _CHECKLIST_STEP_STATUSES = frozenset(
        {"pending", "in_progress", "completed", "blocked", "skipped", "failed"}
    )
    _CHECKLIST_TERMINAL_STATUSES = frozenset({"completed", "blocked", "failed", "cancelled"})

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

    def fail_task_checklist(self, checklist_id: str, *, expected_revision: int) -> dict[str, Any]:
        """Fail an active checklist when a turn ends without a mutable step."""
        now = utc_now()
        with self._write_immediate() as conn:
            self._require_checklist_revision(
                conn.execute(
                    "SELECT * FROM task_checklists WHERE id = ?", (checklist_id,)
                ).fetchone(),
                expected_revision,
            )
            cursor = conn.execute(
                "UPDATE task_checklists SET status = 'failed', current_step_key = NULL, "
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

    @staticmethod
    def _normalize_run_step_status(status: str) -> str:
        return {"queued": "pending", "running": "in_progress", "waiting": "in_progress"}.get(
            status, status
        )

    @staticmethod
    def _ensure_run_task_revision_with(
        conn: sqlite3.Connection, run_id: str, *, now: str | None = None
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO run_task_state_revisions "
            "(run_id, revision, updated_at) VALUES (?, 1, ?)",
            (run_id, now or utc_now()),
        )

    @classmethod
    def _bump_run_task_revision_with(
        cls, conn: sqlite3.Connection, run_id: str, *, now: str | None = None
    ) -> None:
        changed_at = now or utc_now()
        cls._ensure_run_task_revision_with(conn, run_id, now=changed_at)
        conn.execute(
            "UPDATE run_task_state_revisions SET revision = revision + 1, updated_at = ? "
            "WHERE run_id = ?",
            (changed_at, run_id),
        )

    @classmethod
    def _plan_run_task_with(cls, conn: sqlite3.Connection, run: sqlite3.Row) -> dict[str, Any]:
        raw_steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY ordinal", (run["id"],)
        ).fetchall()
        steps = [
            {
                "key": str(step["step_key"]),
                "title": str(step["title"]),
                "status": cls._normalize_run_step_status(str(step["status"])),
                "summary": step["output_summary"],
                "error_message": step["error_message"],
            }
            for step in raw_steps
        ]
        plan = conn.execute(
            "SELECT title, goal FROM plans WHERE id = ? AND version = ?",
            (run["plan_id"], run["plan_version"]),
        ).fetchone()
        failed = any(step["status"] == "failed" for step in steps)
        blocked = any(step["status"] == "blocked" for step in steps)
        run_status = str(run["status"])
        if blocked:
            task_status = "blocked"
        elif failed or run_status in {"failed", "interrupted"}:
            task_status = "failed"
        elif run_status in {"cancelled", "skipped"}:
            task_status = "cancelled"
        elif run_status == "completed":
            task_status = "completed"
        else:
            task_status = "active"

        revision_row = conn.execute(
            "SELECT revision FROM run_task_state_revisions WHERE run_id = ?",
            (run["id"],),
        ).fetchone()
        revision = int(revision_row["revision"]) if revision_row is not None else 1
        current = next((step["key"] for step in steps if step["status"] == "in_progress"), None)
        return {
            "id": str(run["id"]),
            "conversation_id": str(run["conversation_id"] or ""),
            "source": "plan_run",
            "status": task_status,
            "revision": revision,
            "title": str(
                (plan["title"] if plan else None) or (plan["goal"] if plan else None) or "Plan"
            ),
            "completed_count": sum(1 for step in steps if step["status"] == "completed"),
            "total_count": len(steps),
            "current_step_key": current,
            "steps": steps,
            "created_at": run["created_at"],
            "completed_at": run["finished_at"],
        }

    def get_run_task(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None or not run["plan_id"] or run["plan_version"] is None:
                return None
            return self._plan_run_task_with(self._conn, run)

    def get_active_task(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._conn.execute(
                "SELECT * FROM runs WHERE conversation_id = ? AND trigger_type = 'plan_approval' "
                "AND status IN ('queued', 'running', 'waiting') ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if run is not None:
                return self._plan_run_task_with(self._conn, run)
            checklist = self._conn.execute(
                "SELECT * FROM task_checklists WHERE conversation_id = ? AND status = 'active' "
                "ORDER BY updated_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            return (
                self._checklist_task_with(self._conn, checklist) if checklist is not None else None
            )

    def get_current_run_step(self, run_id: str) -> dict[str, Any] | None:
        return self._row(
            "SELECT * FROM run_steps WHERE run_id = ? AND status IN ('running', 'waiting') "
            "ORDER BY ordinal LIMIT 1",
            (run_id,),
        )

    def update_run_task_step(
        self,
        run_id: str,
        step_key: str,
        *,
        status: str,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in self._CHECKLIST_STEP_STATUSES:
            raise ValueError(f"Unknown plan step status: {status}")
        if status in {"blocked", "failed"} and not str(error_message or "").strip():
            raise ValueError(f"A {status} plan step needs an error message.")
        stored_status = {"pending": "queued", "in_progress": "running"}.get(status, status)
        now = utc_now()
        with self._write_immediate() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None or str(run["status"]) not in {"queued", "running", "waiting"}:
                raise ValueError("This plan run is not active.")
            step = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? AND step_key = ?",
                (run_id, step_key),
            ).fetchone()
            if step is None:
                raise ValueError("Plan step not found.")
            previous = self._normalize_run_step_status(str(step["status"]))
            if previous == "completed" and status != "completed":
                raise ValueError("A completed plan step cannot move backwards.")
            if previous in {"blocked", "failed", "skipped"} and status != previous:
                raise ValueError("A finished plan step cannot move backwards.")
            if status == "in_progress":
                other = conn.execute(
                    "SELECT 1 FROM run_steps WHERE run_id = ? AND status IN ('running', 'waiting') "
                    "AND step_key != ? LIMIT 1",
                    (run_id, step_key),
                ).fetchone()
                if other is not None:
                    raise ValueError("Only one plan step can be in progress.")
            started_at = step["started_at"] or (now if status == "in_progress" else None)
            finished_at = now if status in {"completed", "blocked", "failed", "skipped"} else None
            conn.execute(
                "UPDATE run_steps SET status = ?, "
                "output_summary = COALESCE(?, output_summary), "
                "error_message = COALESCE(?, error_message), "
                "started_at = ?, finished_at = ? WHERE run_id = ? AND step_key = ?",
                (
                    stored_status,
                    str(summary).strip() if summary is not None else None,
                    str(error_message).strip() if error_message is not None else None,
                    started_at,
                    finished_at,
                    run_id,
                    step_key,
                ),
            )
            if status == "blocked":
                conn.execute(
                    "UPDATE runs SET status = 'waiting', heartbeat_at = ? WHERE id = ?",
                    (now, run_id),
                )
            self._bump_run_task_revision_with(conn, run_id, now=now)
            refreshed = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert refreshed is not None
            return self._plan_run_task_with(conn, refreshed)

    def request_plan_change(
        self,
        run_id: str,
        *,
        conversation_id: str,
        reason: str = "Plan change requested by the user.",
    ) -> dict[str, Any]:
        """Durably request that a reviewed run stop at its next safe boundary."""
        reason = str(reason or "").strip() or "Plan change requested by the user."
        now = utc_now()
        with self._write_immediate() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("run not found")
            if str(run["conversation_id"] or "") != conversation_id:
                raise ValueError("This plan run does not belong to the current conversation.")
            if not run["plan_id"] or run["plan_version"] is None:
                raise ValueError("Only a reviewed plan run can be changed this way.")
            if str(run["status"]) not in {"queued", "running", "waiting"}:
                raise ValueError("This plan run is no longer active.")
            conn.execute(
                "INSERT INTO plan_change_requests "
                "(run_id, conversation_id, plan_id, plan_version, reason, status, "
                "requested_at) VALUES (?, ?, ?, ?, ?, 'requested', ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "reason = CASE WHEN plan_change_requests.status = 'requested' "
                "THEN excluded.reason ELSE plan_change_requests.reason END",
                (
                    run_id,
                    conversation_id,
                    str(run["plan_id"]),
                    int(run["plan_version"]),
                    reason,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM plan_change_requests WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def get_plan_change_request(self, run_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM plan_change_requests WHERE run_id = ?", (run_id,))

    def get_plan_change_context(self, conversation_id: str) -> dict[str, Any] | None:
        return self._row(
            "SELECT * FROM plan_change_requests WHERE conversation_id = ? "
            "AND status IN ('requested', 'finalized') ORDER BY requested_at DESC LIMIT 1",
            (conversation_id,),
        )

    def finalize_plan_change(self, run_id: str) -> dict[str, Any]:
        """Stop a run without undoing work and preserve every terminal step."""
        now = utc_now()
        with self._write_immediate() as conn:
            change = conn.execute(
                "SELECT * FROM plan_change_requests WHERE run_id = ?", (run_id,)
            ).fetchone()
            if change is None:
                raise ValueError("No plan change was requested for this run.")
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("run not found")
            changed = str(change["status"]) == "requested"
            if changed:
                if str(run["status"]) not in {"queued", "running", "waiting"}:
                    raise ValueError("This plan run is no longer active.")
                current = conn.execute(
                    "SELECT * FROM run_steps WHERE run_id = ? "
                    "AND status IN ('running', 'waiting') ORDER BY ordinal LIMIT 1",
                    (run_id,),
                ).fetchone()
                if current is not None:
                    conn.execute(
                        "UPDATE run_steps SET status = 'skipped', finished_at = ?, "
                        "error_message = COALESCE(error_message, ?) "
                        "WHERE id = ? AND status IN ('running', 'waiting')",
                        (now, str(change["reason"]), str(current["id"])),
                    )
                conn.execute(
                    "UPDATE runs SET status = 'cancelled', finished_at = ?, heartbeat_at = ?, "
                    "error_code = 'plan_superseded', error_message = ? WHERE id = ?",
                    (now, now, str(change["reason"]), run_id),
                )
                conn.execute(
                    "UPDATE plan_change_requests SET status = 'finalized', finalized_at = ? "
                    "WHERE run_id = ? AND status = 'requested'",
                    (now, run_id),
                )
                self._bump_run_task_revision_with(conn, run_id, now=now)
                run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                change = conn.execute(
                    "SELECT * FROM plan_change_requests WHERE run_id = ?", (run_id,)
                ).fetchone()
                assert run is not None and change is not None
            return {
                "changed": changed,
                "request": dict(change),
                "run": dict(run),
                "task": self._plan_run_task_with(conn, run),
            }

    def mark_plan_change_replanned(self, run_id: str, replacement_version: int) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE plan_change_requests SET status = 'replanned', "
                "replacement_plan_version = ? WHERE run_id = ? AND status = 'finalized'",
                (replacement_version, run_id),
            )

    def claim_plan_change_terminal_message(
        self,
        run_id: str,
        *,
        content: str = "I stopped before the next action so we can change the plan.",
    ) -> dict[str, Any] | None:
        """Atomically persist the one terminal assistant message for a plan change."""
        now = utc_now()
        message_id = new_id()
        with self._write_immediate() as conn:
            change = conn.execute(
                "SELECT * FROM plan_change_requests WHERE run_id = ?", (run_id,)
            ).fetchone()
            if change is None or str(change["status"]) not in {"finalized", "replanned"}:
                raise ValueError("This plan change has not reached a safe boundary.")
            if change["terminal_message_id"]:
                return None
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("run not found")
            task = self._plan_run_task_with(conn, run)
            renderer_task = {key: value for key, value in task.items() if key != "conversation_id"}
            cursor = conn.execute(
                "UPDATE plan_change_requests SET terminal_message_id = ? "
                "WHERE run_id = ? AND terminal_message_id IS NULL",
                (message_id, run_id),
            )
            if cursor.rowcount != 1:
                return None
            conversation_id = str(change["conversation_id"])
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, task_state, "
                "created_at) VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    str(content),
                    json.dumps(renderer_task),
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            return {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": str(content),
                "card_type": None,
                "card_data": None,
                "task_state": renderer_task,
                "attachments": None,
                "created_at": now,
            }

    # -- plans and durable runs ---------------------------------------------------------

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def create_plan(
        self,
        *,
        title: str,
        goal: str,
        plan: dict[str, Any],
        conversation_id: str | None = None,
        routine_id: str | None = None,
        source_message_id: str | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        pid = plan_id or new_id()
        canonical = self._canonical_json(plan)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = utc_now()
        with self._write() as conn:
            previous = conn.execute(
                "SELECT MAX(version) AS version FROM plans WHERE id = ?", (pid,)
            ).fetchone()
            version = int(previous["version"] or 0) + 1
            conn.execute(
                "UPDATE plans SET status = 'superseded', updated_at = ? "
                "WHERE id = ? AND status != 'superseded'",
                (now, pid),
            )
            conn.execute(
                "INSERT INTO plans (id, conversation_id, routine_id, title, goal, "
                "version, status, plan_json, plan_hash, source_message_id, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)",
                (
                    pid,
                    conversation_id,
                    routine_id,
                    title,
                    goal,
                    version,
                    canonical,
                    digest,
                    source_message_id,
                    now,
                    now,
                ),
            )
        return self.get_plan(pid, version)  # type: ignore[return-value]

    def get_plan(self, plan_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is None:
            row = self._row(
                "SELECT * FROM plans WHERE id = ? ORDER BY version DESC LIMIT 1",
                (plan_id,),
            )
        else:
            row = self._row(
                "SELECT * FROM plans WHERE id = ? AND version = ?",
                (plan_id, version),
            )
        if row and isinstance(row.get("plan_json"), str):
            row["plan"] = json.loads(row["plan_json"])
        return row

    def approve_plan(self, plan_id: str, version: int, plan_hash: str) -> dict[str, Any]:
        now = utc_now()
        with self._write() as conn:
            row = conn.execute(
                "SELECT plan_hash, status FROM plans WHERE id = ? AND version = ?",
                (plan_id, version),
            ).fetchone()
            if row is None:
                raise ValueError("plan version not found")
            if row["plan_hash"] != plan_hash:
                raise ValueError("plan changed; review the new version before approving")
            if row["status"] == "superseded":
                raise ValueError("this plan version has been superseded")
            conn.execute(
                "UPDATE plans SET status = 'approved', approved_at = ?, updated_at = ? "
                "WHERE id = ? AND version = ?",
                (now, now, plan_id, version),
            )
        return self.get_plan(plan_id, version)  # type: ignore[return-value]

    @staticmethod
    def _plan_result(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if isinstance(result.get("plan_json"), str):
            result["plan"] = json.loads(result["plan_json"])
        return result

    def get_run_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,))

    @classmethod
    def _cancel_active_checklist_with(
        cls, conn: sqlite3.Connection, conversation_id: str, *, now: str
    ) -> dict[str, Any] | None:
        checklist = conn.execute(
            "SELECT * FROM task_checklists WHERE conversation_id = ? AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if checklist is None:
            return None
        conn.execute(
            "UPDATE task_checklist_steps SET status = 'skipped', finished_at = ?, "
            "error_message = COALESCE(error_message, 'Superseded by an approved plan.') "
            "WHERE checklist_id = ? AND status = 'in_progress'",
            (now, str(checklist["id"])),
        )
        conn.execute(
            "UPDATE task_checklists SET status = 'cancelled', current_step_key = NULL, "
            "revision = revision + 1, updated_at = ?, completed_at = ? WHERE id = ?",
            (now, now, str(checklist["id"])),
        )
        refreshed = conn.execute(
            "SELECT * FROM task_checklists WHERE id = ?", (str(checklist["id"]),)
        ).fetchone()
        assert refreshed is not None
        return cls._checklist_task_with(conn, refreshed)

    def claim_plan_execution(
        self,
        plan_id: str,
        version: int,
        plan_hash: str,
    ) -> dict[str, Any]:
        """Atomically approve a plan version and create its one execution run.

        An already approved plan can still be claimed when it was approved for
        use as a routine but has no direct-execution run. A duplicate direct
        claim returns that run without starting a second execution.
        """
        key = f"plan:{plan_id}:v{version}"
        now = utc_now()
        with self._write_immediate() as conn:
            plan_row = conn.execute(
                "SELECT * FROM plans WHERE id = ? AND version = ?",
                (plan_id, version),
            ).fetchone()
            if plan_row is None:
                raise ValueError("plan version not found")
            if plan_row["plan_hash"] != plan_hash:
                raise ValueError("plan changed; review the new version before approving")
            if plan_row["status"] == "superseded":
                raise ValueError("this plan version has been superseded")
            if plan_row["status"] not in {"draft", "approved"}:
                raise ValueError("this plan version is not reviewable")

            conversation_id = str(plan_row["conversation_id"] or "")
            if not conversation_id:
                raise ValueError("this plan has no conversation to run in")
            conversation = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ValueError("this plan's conversation no longer exists")

            existing = conn.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if plan_row["status"] != "approved":
                    raise ValueError("an execution run exists for an unapproved plan")
                superseded_checklist = None
                if str(existing["status"]) in {"queued", "running", "waiting"}:
                    superseded_checklist = self._cancel_active_checklist_with(
                        conn, conversation_id, now=now
                    )
                    conn.execute(
                        "DELETE FROM conversation_review_gates WHERE conversation_id = ?",
                        (conversation_id,),
                    )
                return {
                    "plan": self._plan_result(plan_row),
                    "run": dict(existing),
                    "created": False,
                    "superseded_checklist": superseded_checklist,
                }

            if plan_row["status"] == "draft":
                cursor = conn.execute(
                    "UPDATE plans SET status = 'approved', approved_at = ?, updated_at = ? "
                    "WHERE id = ? AND version = ? AND plan_hash = ? AND status = 'draft'",
                    (now, now, plan_id, version, plan_hash),
                )
                if cursor.rowcount != 1:
                    raise ValueError("plan changed; review the new version before approving")

            run_id = new_id()
            conn.execute(
                "INSERT INTO runs (id, plan_id, plan_version, conversation_id, "
                "trigger_type, status, idempotency_key, created_at) "
                "VALUES (?, ?, ?, ?, 'plan_approval', 'queued', ?, ?)",
                (run_id, plan_id, version, conversation_id, key, now),
            )
            self._ensure_run_task_revision_with(conn, run_id, now=now)
            self._seed_run_steps_with(conn, run_id, plan_id, version)
            superseded_checklist = self._cancel_active_checklist_with(
                conn, conversation_id, now=now
            )
            conn.execute(
                "DELETE FROM conversation_review_gates WHERE conversation_id = ?",
                (conversation_id,),
            )
            approved_plan = conn.execute(
                "SELECT * FROM plans WHERE id = ? AND version = ?",
                (plan_id, version),
            ).fetchone()
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert approved_plan is not None and run_row is not None
            return {
                "plan": self._plan_result(approved_plan),
                "run": dict(run_row),
                "created": True,
                "superseded_checklist": superseded_checklist,
            }

    def requeue_failed_plan_execution(self, run_id: str) -> dict[str, Any]:
        """Retry a run that failed before its task could start.

        The same idempotent run row is reused. This preserves exactly-once run
        creation while recording retries through ``attempt``.
        """
        with self._write_immediate() as conn:
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run_row is None:
                raise ValueError("run not found")
            if (
                run_row["trigger_type"] != "plan_approval"
                or run_row["status"] != "failed"
                or run_row["error_code"] != "task_start_failed"
            ):
                raise ValueError("Only a plan run that failed to start can be retried.")

            plan_row = conn.execute(
                "SELECT * FROM plans WHERE id = ? AND version = ?",
                (run_row["plan_id"], run_row["plan_version"]),
            ).fetchone()
            if plan_row is None or plan_row["status"] != "approved":
                raise ValueError("The plan changed and needs review before retrying.")
            expected_key = f"plan:{plan_row['id']}:v{plan_row['version']}"
            if run_row["idempotency_key"] != expected_key:
                raise ValueError("The failed run does not match this approved plan.")

            conversation_id = str(run_row["conversation_id"] or "")
            conversation = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not conversation_id or conversation is None:
                raise ValueError("This plan's conversation no longer exists.")

            cursor = conn.execute(
                "UPDATE runs SET status = 'queued', attempt = attempt + 1, "
                "started_at = NULL, finished_at = NULL, heartbeat_at = NULL, "
                "error_code = NULL, error_message = NULL "
                "WHERE id = ? AND status = 'failed' AND error_code = 'task_start_failed'",
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("This failed run was already retried.")
            conn.execute(
                "UPDATE run_steps SET status = 'queued', tool_name = NULL, "
                "input_summary = NULL, output_summary = NULL, approval_request_id = NULL, "
                "started_at = NULL, finished_at = NULL, retry_count = retry_count + 1, "
                "error_message = NULL WHERE run_id = ?",
                (run_id,),
            )
            self._bump_run_task_revision_with(conn, run_id)
            refreshed_run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert refreshed_run is not None
            return {
                "plan": self._plan_result(plan_row),
                "run": dict(refreshed_run),
            }

    def attach_plan_to_routine(self, plan_id: str, version: int, routine_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE plans SET routine_id = ?, updated_at = ? WHERE id = ? AND version = ?",
                (routine_id, utc_now(), plan_id, version),
            )

    def create_run(
        self,
        *,
        trigger_type: str,
        idempotency_key: str,
        plan_id: str | None = None,
        plan_version: int | None = None,
        routine_id: str | None = None,
        conversation_id: str | None = None,
        scheduled_for: str | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        rid = new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO runs (id, plan_id, plan_version, routine_id, "
                "conversation_id, trigger_type, scheduled_for, status, "
                "idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    plan_id,
                    plan_version,
                    routine_id,
                    conversation_id,
                    trigger_type,
                    scheduled_for,
                    status,
                    idempotency_key,
                    now,
                ),
            )
            self._ensure_run_task_revision_with(conn, rid, now=now)
            self._seed_run_steps_with(conn, rid, plan_id, plan_version)
        return self.get_run(rid)  # type: ignore[return-value]

    def claim_scheduled_run(
        self,
        routine_id: str,
        *,
        scheduled_for: str,
        next_run_at: str | None,
        plan_id: str | None,
        plan_version: int | None,
    ) -> dict[str, Any] | None:
        """Atomically claim one due occurrence and advance its routine."""
        now = utc_now()
        key = f"routine:{routine_id}:{scheduled_for}:v{plan_version or 0}"
        with self._write() as conn:
            active = conn.execute(
                "SELECT 1 FROM runs WHERE routine_id = ? "
                "AND status IN ('queued', 'running', 'waiting') LIMIT 1",
                (routine_id,),
            ).fetchone()
            if active:
                return None
            rid = new_id()
            try:
                conn.execute(
                    "INSERT INTO runs (id, plan_id, plan_version, routine_id, "
                    "trigger_type, scheduled_for, status, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, 'schedule', ?, 'queued', ?, ?)",
                    (rid, plan_id, plan_version, routine_id, scheduled_for, key, now),
                )
            except sqlite3.IntegrityError:
                return None
            conn.execute(
                "UPDATE automations SET next_run_at = ?, updated_at = ? WHERE id = ?",
                (next_run_at, now, routine_id),
            )
            self._ensure_run_task_revision_with(conn, rid, now=now)
            self._seed_run_steps_with(conn, rid, plan_id, plan_version)
        return self.get_run(rid)

    def _seed_run_steps_with(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        plan_id: str | None,
        plan_version: int | None,
    ) -> None:
        """Seed queued run steps inside the caller's open transaction."""
        if not plan_id or plan_version is None:
            return
        plan = conn.execute(
            "SELECT plan_json FROM plans WHERE id = ? AND version = ?",
            (plan_id, plan_version),
        ).fetchone()
        if plan is None:
            return
        try:
            payload = json.loads(plan["plan_json"])
        except (TypeError, ValueError):
            payload = None
        steps = payload.get("steps") if isinstance(payload, dict) else None
        if not isinstance(steps, list):
            return
        seeded = False
        for ordinal, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            conn.execute(
                "INSERT INTO run_steps (id, run_id, step_key, ordinal, title, status) "
                "VALUES (?, ?, ?, ?, ?, 'queued')",
                (
                    new_id(),
                    run_id,
                    str(step.get("key") or f"step-{ordinal + 1}"),
                    ordinal,
                    str(step.get("title") or f"Step {ordinal + 1}"),
                ),
            )
            seeded = True
        if seeded:
            self._bump_run_task_revision_with(conn, run_id)

    def _seed_run_steps(self, run_id: str, plan_id: str | None, plan_version: int | None) -> None:
        with self._write() as conn:
            self._seed_run_steps_with(conn, run_id, plan_id, plan_version)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM runs WHERE id = ?", (run_id,))

    def list_runs(self, *, routine_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if routine_id:
            return self._rows(
                "SELECT * FROM runs WHERE routine_id = ? ORDER BY created_at DESC LIMIT ?",
                (routine_id, limit),
            )
        return self._rows("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))

    def transition_run(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        started = now if status == "running" else None
        finished = (
            now
            if status in {"completed", "failed", "cancelled", "interrupted", "skipped"}
            else None
        )
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = ?, started_at = COALESCE(started_at, ?), "
                "finished_at = COALESCE(?, finished_at), heartbeat_at = ?, "
                "error_code = ?, error_message = ? WHERE id = ?",
                (status, started, finished, now, error_code, error_message, run_id),
            )
            if cursor.rowcount:
                self._bump_run_task_revision_with(conn, run_id, now=now)
        return self.get_run(run_id)  # type: ignore[return-value]

    def recover_stale_runs(self, stale_before: str) -> int:
        with self._write() as conn:
            stale = conn.execute(
                "SELECT id FROM runs WHERE status IN ('running', 'waiting', 'queued') "
                "AND COALESCE(heartbeat_at, started_at, created_at) < ?",
                (stale_before,),
            ).fetchall()
            now = utc_now()
            cursor = conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ?, "
                "error_code = 'process_restart', "
                "error_message = 'Collie restarted while this run was active.' "
                "WHERE status IN ('running', 'waiting', 'queued') "
                "AND COALESCE(heartbeat_at, started_at, created_at) < ?",
                (now, stale_before),
            )
            for row in stale:
                self._bump_run_task_revision_with(conn, str(row["id"]), now=now)
            return cursor.rowcount

    def upsert_run_step(
        self,
        run_id: str,
        step_key: str,
        *,
        ordinal: int,
        title: str,
        status: str,
        tool_name: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        sid = new_id()
        now = utc_now()
        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? AND step_key = ?",
                (run_id, step_key),
            ).fetchone()
            existing_status = (
                self._normalize_run_step_status(str(existing["status"]))
                if existing is not None
                else None
            )
            incoming_status = self._normalize_run_step_status(status)
            status_rank = {
                "pending": 0,
                "in_progress": 1,
                "completed": 2,
                "failed": 2,
                "skipped": 2,
                "blocked": 2,
            }
            if existing_status is not None and status_rank.get(
                incoming_status, -1
            ) < status_rank.get(existing_status, -1):
                raise ValueError("A run step cannot move backwards.")
            preserve_terminal = (
                existing_status in {"completed", "failed", "skipped", "blocked"}
                and incoming_status != existing_status
            )
            if preserve_terminal:
                raise ValueError("A terminal run step cannot move backwards.")
            started = (
                now if status in {"running", "waiting", "completed", "failed", "blocked"} else None
            )
            finished = now if status in {"completed", "failed", "skipped", "blocked"} else None
            conn.execute(
                "INSERT INTO run_steps (id, run_id, step_key, ordinal, title, status, "
                "tool_name, input_summary, output_summary, started_at, finished_at, "
                "error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, step_key) DO UPDATE SET status = excluded.status, "
                "tool_name = COALESCE(excluded.tool_name, run_steps.tool_name), "
                "input_summary = COALESCE(excluded.input_summary, run_steps.input_summary), "
                "output_summary = COALESCE(excluded.output_summary, run_steps.output_summary), "
                "started_at = COALESCE(run_steps.started_at, excluded.started_at), "
                "finished_at = excluded.finished_at, error_message = excluded.error_message",
                (
                    sid,
                    run_id,
                    step_key,
                    ordinal,
                    title,
                    status,
                    tool_name,
                    input_summary,
                    output_summary,
                    started,
                    finished,
                    error_message,
                ),
            )
            self._bump_run_task_revision_with(conn, run_id, now=now)
        return self._row(
            "SELECT * FROM run_steps WHERE run_id = ? AND step_key = ?",
            (run_id, step_key),
        )  # type: ignore[return-value]

    def list_run_steps(self, run_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM run_steps WHERE run_id = ? ORDER BY ordinal", (run_id,))

    # -- approval rules and requests ---------------------------------------------------

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

    # -- services ----------------------------------------------------------------------------

    def upsert_service(
        self,
        service_id: str,
        *,
        name: str,
        provider: str,
        auth_type: str = "oauth",
        status: str = "disconnected",
        account_info: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO services (id, name, provider, auth_type, status, "
                "account_info, connected_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
                "account_info = excluded.account_info, last_error = excluded.last_error, "
                "connected_at = CASE WHEN excluded.status = 'connected' "
                "THEN excluded.connected_at ELSE services.connected_at END",
                (
                    service_id,
                    name,
                    provider,
                    auth_type,
                    status,
                    account_info,
                    utc_now() if status == "connected" else None,
                    last_error,
                ),
            )

    def get_service(self, service_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM services WHERE id = ?", (service_id,))

    def list_services(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM services ORDER BY name COLLATE NOCASE")

    def delete_service(self, service_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM services WHERE id = ?", (service_id,))

    # -- connectors --------------------------------------------------------------------------

    def upsert_connector_connection(
        self,
        connection_id: str,
        *,
        provider_id: str,
        driver: str,
        auth_type: str,
        status: str,
        display_name: str | None = None,
        account_label: str | None = None,
        granted_scopes: list[str] | None = None,
        enabled_capabilities: list[str] | None = None,
        enabled_tools: list[str] | None = None,
        tool_policy: dict[str, Any] | None = None,
        remote_account_id: str | None = None,
        last_verified_at: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO connector_connections (
                    id, provider_id, display_name, account_label, driver, auth_type,
                    status, granted_scopes_json, enabled_capabilities_json,
                    enabled_tools_json, tool_policy_json, remote_account_id,
                    connected_at, updated_at, last_verified_at, last_error_code,
                    last_error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, display_name),
                    account_label = COALESCE(excluded.account_label, account_label),
                    status = excluded.status,
                    granted_scopes_json = COALESCE(
                        excluded.granted_scopes_json, granted_scopes_json
                    ),
                    enabled_capabilities_json = COALESCE(
                        excluded.enabled_capabilities_json, enabled_capabilities_json
                    ),
                    enabled_tools_json = COALESCE(
                        excluded.enabled_tools_json, enabled_tools_json
                    ),
                    tool_policy_json = COALESCE(
                        excluded.tool_policy_json, tool_policy_json
                    ),
                    remote_account_id = COALESCE(
                        excluded.remote_account_id, remote_account_id
                    ),
                    connected_at = CASE
                        WHEN excluded.status = 'connected'
                        THEN COALESCE(connected_at, excluded.connected_at)
                        ELSE connected_at
                    END,
                    updated_at = excluded.updated_at,
                    last_verified_at = COALESCE(
                        excluded.last_verified_at, last_verified_at
                    ),
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message
                """,
                (
                    connection_id,
                    provider_id,
                    display_name,
                    account_label,
                    driver,
                    auth_type,
                    status,
                    json.dumps(granted_scopes) if granted_scopes is not None else None,
                    (
                        json.dumps(enabled_capabilities)
                        if enabled_capabilities is not None
                        else None
                    ),
                    json.dumps(enabled_tools) if enabled_tools is not None else None,
                    json.dumps(tool_policy) if tool_policy is not None else None,
                    remote_account_id,
                    now if status == "connected" else None,
                    now,
                    last_verified_at,
                    last_error_code,
                    last_error_message,
                ),
            )
        return self.get_connector_connection(connection_id)  # type: ignore[return-value]

    def get_connector_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM connector_connections WHERE id = ?", (connection_id,))

    def list_connector_connections(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        if provider_id:
            return self._rows(
                "SELECT * FROM connector_connections WHERE provider_id = ? "
                "ORDER BY updated_at DESC",
                (provider_id,),
            )
        return self._rows("SELECT * FROM connector_connections ORDER BY updated_at DESC")

    def delete_connector_connection(self, connection_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM connector_connections WHERE id = ?", (connection_id,))

    def replace_connector_tools(self, connection_id: str, tools: list[dict[str, Any]]) -> None:
        with self._write() as conn:
            conn.execute(
                "DELETE FROM connector_tool_cache WHERE connection_id = ?",
                (connection_id,),
            )
            conn.executemany(
                "INSERT INTO connector_tool_cache "
                "(connection_id, remote_tool_name, schema_hash, annotations_json, "
                "risk, discovered_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        connection_id,
                        str(tool["name"]),
                        str(tool["schema_hash"]),
                        json.dumps(tool.get("annotations") or {}),
                        str(tool["risk"]),
                        utc_now(),
                    )
                    for tool in tools
                ],
            )

    def list_connector_tools(self, connection_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM connector_tool_cache WHERE connection_id = ? ORDER BY remote_tool_name",
            (connection_id,),
        )

    # -- subagents ----------------------------------------------------------------------------

    def upsert_subagent(
        self,
        name: str,
        *,
        description: str = "",
        system_prompt: str = "",
        filename: str = "",
        execution_posture: str = "read_only",
        subagent_id: str | None = None,
    ) -> dict[str, Any]:
        sid = subagent_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO subagents (id, name, description, system_prompt, filename, "
                "execution_posture, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "description = excluded.description, system_prompt = excluded.system_prompt, "
                "filename = excluded.filename, execution_posture = excluded.execution_posture, "
                "updated_at = excluded.updated_at",
                (
                    sid,
                    name,
                    description,
                    system_prompt,
                    filename,
                    execution_posture,
                    now,
                    now,
                ),
            )
        return self._row("SELECT * FROM subagents WHERE id = ?", (sid,))  # type: ignore[return-value]

    def list_subagents(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM subagents ORDER BY name COLLATE NOCASE")

    def delete_subagent(self, subagent_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM subagents WHERE id = ?", (subagent_id,))

    # -- providers & usage -----------------------------------------------------------------------

    _PROVIDER_SETTING_KEYS = (
        "provider.auth",
        "provider.name",
        "provider.model",
        "provider.api_base",
        "provider.secret_name",
    )

    def snapshot_provider_configuration(self, provider_id: str) -> dict[str, Any]:
        """Capture exactly the provider state needed for a failed candidate rollback."""
        with self._lock:
            provider_row = self._conn.execute(
                "SELECT * FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
            default_row = self._conn.execute(
                "SELECT id FROM providers WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            raw_settings: dict[str, str | None] = {}
            for key in self._PROVIDER_SETTING_KEYS:
                row = self._conn.execute(
                    "SELECT value FROM settings WHERE key = ?", (key,)
                ).fetchone()
                raw_settings[key] = row["value"] if row is not None else None
            return {
                "provider_id": provider_id,
                "provider": dict(provider_row) if provider_row is not None else None,
                "default_provider_id": default_row["id"] if default_row is not None else None,
                "settings": raw_settings,
            }

    def configure_provider_candidate_record(
        self,
        provider_id: str,
        *,
        name: str,
        auth_type: str,
        model: str | None,
        runtime_name: str,
        protocol: str,
        api_base: str | None,
        secret_name: str,
    ) -> dict[str, Any]:
        """Tentatively select a provider row and its settings in one transaction."""
        now = utc_now()
        settings = {
            "provider.auth": auth_type,
            "provider.name": runtime_name,
            "provider.model": model,
            "provider.api_base": api_base,
            "provider.secret_name": secret_name,
        }
        with self._write() as conn:
            conn.execute("UPDATE providers SET is_default = 0")
            conn.execute(
                "INSERT INTO providers (id, name, auth_type, is_default, model, created_at, "
                "runtime_name, protocol, api_base, secret_name) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "auth_type = excluded.auth_type, is_default = 1, model = excluded.model, "
                "runtime_name = excluded.runtime_name, protocol = excluded.protocol, "
                "api_base = excluded.api_base, secret_name = excluded.secret_name",
                (
                    provider_id,
                    name,
                    auth_type,
                    model,
                    now,
                    runtime_name,
                    protocol,
                    api_base,
                    secret_name,
                ),
            )
            for key, value in settings.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
        return self.get_provider(provider_id)  # type: ignore[return-value]

    def restore_provider_configuration(self, snapshot: dict[str, Any]) -> None:
        """Restore a snapshot made by :meth:`snapshot_provider_configuration`."""
        provider_id = str(snapshot["provider_id"])
        previous = snapshot.get("provider")
        with self._write() as conn:
            if previous is None:
                conn.execute("DELETE FROM usage WHERE provider_id = ?", (provider_id,))
                conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            else:
                columns = (
                    "id",
                    "name",
                    "auth_type",
                    "is_default",
                    "model",
                    "created_at",
                    "last_used",
                    "runtime_name",
                    "protocol",
                    "api_base",
                    "secret_name",
                )
                values = tuple(previous.get(column) for column in columns)
                conn.execute(
                    "INSERT INTO providers (" + ", ".join(columns) + ") "
                    "VALUES (" + ", ".join("?" for _ in columns) + ") "
                    "ON CONFLICT(id) DO UPDATE SET "
                    + ", ".join(
                        f"{column} = excluded.{column}" for column in columns if column != "id"
                    ),
                    values,
                )
            conn.execute("UPDATE providers SET is_default = 0")
            default_provider_id = snapshot.get("default_provider_id")
            if default_provider_id:
                conn.execute(
                    "UPDATE providers SET is_default = 1 WHERE id = ?",
                    (str(default_provider_id),),
                )
            for key, raw_value in dict(snapshot.get("settings") or {}).items():
                if raw_value is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, raw_value),
                    )

    def upsert_provider(
        self,
        provider_id: str,
        *,
        name: str,
        auth_type: str,
        model: str | None = None,
        runtime_name: str | None = None,
        protocol: str = "openai",
        api_base: str | None = None,
        secret_name: str | None = None,
        is_default: bool = False,
    ) -> None:
        with self._write() as conn:
            if is_default:
                conn.execute("UPDATE providers SET is_default = 0")
            conn.execute(
                "INSERT INTO providers (id, name, auth_type, is_default, model, created_at, "
                "runtime_name, protocol, api_base, secret_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "auth_type = excluded.auth_type, is_default = excluded.is_default, "
                "model = excluded.model, runtime_name = excluded.runtime_name, "
                "protocol = excluded.protocol, api_base = excluded.api_base, "
                "secret_name = excluded.secret_name",
                (
                    provider_id,
                    name,
                    auth_type,
                    1 if is_default else 0,
                    model,
                    utc_now(),
                    runtime_name or name,
                    protocol,
                    api_base,
                    secret_name or name,
                ),
            )

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM providers WHERE id = ?", (provider_id,))

    def list_providers(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM providers ORDER BY created_at")

    def default_provider(self) -> dict[str, Any] | None:
        row = self._row("SELECT * FROM providers WHERE is_default = 1 LIMIT 1")
        if row:
            return row
        return self._row("SELECT * FROM providers ORDER BY created_at LIMIT 1")

    def set_default_provider(self, provider_id: str) -> None:
        with self._write() as conn:
            conn.execute("UPDATE providers SET is_default = 0")
            conn.execute("UPDATE providers SET is_default = 1 WHERE id = ?", (provider_id,))

    def touch_provider(self, provider_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE providers SET last_used = ? WHERE id = ?",
                (utc_now(), provider_id),
            )

    def delete_provider(self, provider_id: str) -> None:
        with self._write() as conn:
            # usage rows carry an FK to providers — remove them first or the
            # delete itself fails while leaving both rows behind.
            conn.execute("DELETE FROM usage WHERE provider_id = ?", (provider_id,))
            conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))

    def record_usage(self, provider_id: str, *, messages: int = 1, tokens: int = 0) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with self._write() as conn:
            conn.execute(
                "INSERT INTO usage (id, provider_id, date, message_count, token_count) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id, date) DO UPDATE SET "
                "message_count = usage.message_count + excluded.message_count, "
                "token_count = usage.token_count + excluded.token_count",
                (new_id(), provider_id, day, messages, tokens),
            )

    def usage_this_month(self, provider_id: str | None = None) -> dict[str, int]:
        month_prefix = datetime.now(UTC).strftime("%Y-%m") + "%"
        if provider_id:
            rows = self._rows(
                "SELECT COALESCE(SUM(message_count),0) AS messages, "
                "COALESCE(SUM(token_count),0) AS tokens FROM usage "
                "WHERE date LIKE ? AND provider_id = ?",
                (month_prefix, provider_id),
            )
        else:
            rows = self._rows(
                "SELECT COALESCE(SUM(message_count),0) AS messages, "
                "COALESCE(SUM(token_count),0) AS tokens FROM usage WHERE date LIKE ?",
                (month_prefix,),
            )
        row = rows[0] if rows else {"messages": 0, "tokens": 0}
        return {"messages": int(row["messages"]), "tokens": int(row["tokens"])}

    # -- run records (telemetry) ----------------------------------------------------------------

    def record_turn_event(
        self,
        *,
        turn_id: str,
        conversation_id: str | None = None,
        session_key: str | None = None,
        turn_kind: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        status: str | None = None,
        error_message: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int | None = None,
        tool_count: int = 0,
        prompt_hash: str | None = None,
        tool_schema_hash: str | None = None,
        config_hash: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        """Insert or update one turn event row (upsert keyed by id).

        The recorder writes a ``running`` row when a turn starts and an
        upsert with the final status when it finishes; COALESCE keeps the
        start-time fields (and the ``turn_kind`` captured at start) intact
        across the two writes. Defaults are applied on INSERT only so a
        finishing write can never clobber values it does not carry.
        """
        with self._write() as conn:
            updated = conn.execute(
                """
                UPDATE turn_events SET
                    conversation_id = COALESCE(?, conversation_id),
                    session_key = COALESCE(?, session_key),
                    turn_kind = COALESCE(?, turn_kind),
                    provider = COALESCE(?, provider),
                    model = COALESCE(?, model),
                    status = COALESCE(?, status),
                    error_message = COALESCE(?, error_message),
                    tokens_in = COALESCE(?, tokens_in),
                    tokens_out = COALESCE(?, tokens_out),
                    latency_ms = COALESCE(?, latency_ms),
                    tool_count = COALESCE(?, tool_count),
                    prompt_hash = COALESCE(?, prompt_hash),
                    tool_schema_hash = COALESCE(?, tool_schema_hash),
                    config_hash = COALESCE(?, config_hash),
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (
                    conversation_id,
                    session_key,
                    turn_kind,
                    provider,
                    model,
                    status,
                    error_message,
                    tokens_in,
                    tokens_out,
                    latency_ms,
                    tool_count,
                    prompt_hash,
                    tool_schema_hash,
                    config_hash,
                    started_at,
                    finished_at,
                    turn_id,
                ),
            )
            if updated.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO turn_events (
                        id, conversation_id, session_key, turn_kind, provider, model,
                        status, error_message, tokens_in, tokens_out, latency_ms,
                        tool_count, prompt_hash, tool_schema_hash, config_hash,
                        started_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        conversation_id,
                        session_key,
                        turn_kind or "chat",
                        provider,
                        model,
                        status or "running",
                        error_message,
                        tokens_in,
                        tokens_out,
                        latency_ms,
                        tool_count,
                        prompt_hash,
                        tool_schema_hash,
                        config_hash,
                        started_at or utc_now(),
                        finished_at,
                    ),
                )

    def record_tool_event(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        action: str | None = None,
        resource: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        status: str = "running",
        error_message: str | None = None,
        latency_ms: int | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        """Insert or update one tool event row (upsert keyed by id).

        ``started_at`` defaults to now on INSERT only, so the finishing
        write preserves the original start time.
        """
        with self._write() as conn:
            updated = conn.execute(
                """
                UPDATE tool_events SET
                    action = COALESCE(?, action),
                    resource = COALESCE(?, resource),
                    input_summary = COALESCE(?, input_summary),
                    output_summary = COALESCE(?, output_summary),
                    status = COALESCE(?, status),
                    error_message = COALESCE(?, error_message),
                    latency_ms = COALESCE(?, latency_ms),
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (
                    action,
                    resource,
                    input_summary,
                    output_summary,
                    status,
                    error_message,
                    latency_ms,
                    started_at,
                    finished_at,
                    tool_id,
                ),
            )
            if updated.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO tool_events (
                        id, turn_id, tool_name, action, resource, input_summary,
                        output_summary, status, error_message, latency_ms,
                        started_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tool_id,
                        turn_id,
                        tool_name,
                        action,
                        resource,
                        input_summary,
                        output_summary,
                        status,
                        error_message,
                        latency_ms,
                        started_at or utc_now(),
                        finished_at,
                    ),
                )

    def list_turn_events(
        self,
        conversation_id: str | None = None,
        session_key: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List turn events, most recent first, with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if session_key:
            clauses.append("session_key = ?")
            params.append(session_key)
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._rows(
            f"SELECT * FROM turn_events {where} ORDER BY started_at DESC LIMIT ?",
            tuple(params),
        )

    def list_tool_events(
        self,
        turn_id: str | None = None,
        tool_name: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """List tool events, most recent first, with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if turn_id:
            clauses.append("turn_id = ?")
            params.append(turn_id)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._rows(
            f"SELECT * FROM tool_events {where} ORDER BY started_at DESC LIMIT ?",
            tuple(params),
        )

    def turn_event_stats(self, since: str | None = None) -> list[dict[str, Any]]:
        """Per-tool status counts (feeds Gardener evidence queries)."""
        if since:
            rows = self._rows(
                """
                SELECT tool_name, status, COUNT(*) AS count FROM tool_events
                WHERE started_at >= ? GROUP BY tool_name, status
                ORDER BY tool_name, status
                """,
                (since,),
            )
        else:
            rows = self._rows(
                """
                SELECT tool_name, status, COUNT(*) AS count FROM tool_events
                GROUP BY tool_name, status ORDER BY tool_name, status
                """
            )
        return [
            {"tool_name": row["tool_name"], "status": row["status"], "count": int(row["count"])}
            for row in rows
        ]

    # -- artifact versions (Gardener rollback rail) ---------------------------------------------

    def snapshot_artifact(
        self,
        artifact_type: str,
        artifact_key: str,
        before_text: str,
        after_text: str,
        diff_text: str,
        evidence: Any = None,
        source: str = "user",
    ) -> dict[str, Any]:
        """Append a version row for an artifact edit; returns the new row."""
        with self._write_immediate() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM artifact_versions "
                "WHERE artifact_type = ? AND artifact_key = ?",
                (artifact_type, artifact_key),
            ).fetchone()
            version = int(row["v"] or 0) + 1
            version_id = new_id()
            conn.execute(
                "INSERT INTO artifact_versions (id, artifact_type, artifact_key, "
                "version, before_text, after_text, diff_text, evidence_json, "
                "source, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'applied', ?)",
                (
                    version_id,
                    artifact_type,
                    artifact_key,
                    version,
                    before_text,
                    after_text,
                    diff_text,
                    json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
                    source,
                    utc_now(),
                ),
            )
            return {
                "id": version_id,
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version": version,
                "status": "applied",
            }

    def latest_artifact_version(self, artifact_type: str, artifact_key: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) AS v FROM artifact_versions "
                "WHERE artifact_type = ? AND artifact_key = ?",
                (artifact_type, artifact_key),
            ).fetchone()
            return int(row["v"] or 0) if row else 0

    def list_artifact_versions(
        self,
        artifact_type: str | None = None,
        artifact_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List artifact versions, most recent first, with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if artifact_type:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        if artifact_key:
            clauses.append("artifact_key = ?")
            params.append(artifact_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._rows(
            f"SELECT * FROM artifact_versions {where} "
            "ORDER BY created_at DESC, version DESC LIMIT ?",
            tuple(params),
        )

    def get_artifact_version(self, version_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM artifact_versions WHERE id = ?", (version_id,))

    def mark_artifact_rolled_back(self, version_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE artifact_versions SET status = 'rolled_back' WHERE id = ?",
                (version_id,),
            )

    # -- data management ----------------------------------------------------------------------------

    def export_all(self) -> dict[str, Any]:
        """Full data export (for F104 data export)."""
        # Include telemetry that is still queued in the writer thread, or the
        # export would silently omit recent-but-not-yet-durable records.
        from collie_core.telemetry.recorder import RunRecorder

        recorder = RunRecorder.active_for(self)
        if recorder is not None:
            recorder.flush()
        return {
            "exported_at": utc_now(),
            "schema_version": self.schema_version,
            "conversations": self.list_conversations(include_archived=True),
            "messages": self._rows("SELECT * FROM messages ORDER BY created_at"),
            "profile": self.all_profile(),
            "people": self.list_people(),
            "important_dates": self.list_dates(),
            "reminders": self.list_reminders(include_completed=True),
            "automations": self.list_automations(),
            "plans": self._rows("SELECT * FROM plans ORDER BY created_at"),
            "runs": self.list_runs(limit=10000),
            "run_steps": self._rows("SELECT * FROM run_steps ORDER BY run_id, ordinal"),
            "run_task_state_revisions": self._rows(
                "SELECT * FROM run_task_state_revisions ORDER BY run_id"
            ),
            "plan_change_requests": self._rows(
                "SELECT * FROM plan_change_requests ORDER BY requested_at"
            ),
            "task_checklists": self._rows("SELECT * FROM task_checklists ORDER BY created_at"),
            "task_checklist_steps": self._rows(
                "SELECT * FROM task_checklist_steps ORDER BY checklist_id, ordinal"
            ),
            "conversation_review_gates": self._rows(
                "SELECT * FROM conversation_review_gates ORDER BY declared_at"
            ),
            "turn_events": self._rows("SELECT * FROM turn_events ORDER BY started_at"),
            "tool_events": self._rows("SELECT * FROM tool_events ORDER BY started_at"),
            "memory_journal": self._rows("SELECT * FROM memory_journal ORDER BY created_at"),
            "artifact_versions": self._rows(
                "SELECT * FROM artifact_versions ORDER BY created_at, version"
            ),
            "approval_rules": self.list_approval_rules(),
            "approval_requests": self._rows(
                "SELECT * FROM approval_requests ORDER BY requested_at"
            ),
            "shopping_items": self._rows("SELECT * FROM shopping_items ORDER BY created_at"),
            "expenses": self._rows("SELECT * FROM expenses ORDER BY spent_at"),
            "budgets": self.list_budgets(),
            "health_logs": self._rows("SELECT * FROM health_logs ORDER BY logged_on"),
            "services": self.list_services(),
            "connector_connections": self.list_connector_connections(),
            "connector_tools": self._rows(
                "SELECT * FROM connector_tool_cache ORDER BY connection_id, remote_tool_name"
            ),
            "subagents": self.list_subagents(),
            "providers": self.list_providers(),
            "settings": self.all_settings(),
        }

    def clear_all(self) -> None:
        """Wipe all user data (for F105 data deletion)."""
        # Suspend + drain telemetry first: queued recorder writes must not
        # execute after the wipe and resurrect deleted rows. The recorder
        # stays suspended until the deletes finish, so hooks enqueueing
        # mid-wipe are dropped too, then recording resumes for new turns.
        from collie_core.telemetry.recorder import RunRecorder

        recorder = RunRecorder.active_for(self)
        if recorder is not None:
            recorder.suspend_and_drain()
        try:
            # Children before parents: run_steps cascades from runs anyway, but
            # usage (FK -> providers), connector_tool_cache (FK -> connections),
            # messages (FK -> conversations), and plans/runs must go first so no
            # foreign-key violation aborts the wipe halfway.
            tables = [
                "task_checklist_steps",
                "task_checklists",
                "conversation_review_gates",
                "plan_change_requests",
                "run_task_state_revisions",
                "tool_events",
                "turn_events",
                "artifact_versions",
                "run_steps",
                "approval_requests",
                "approval_rules",
                "runs",
                "plans",
                "messages",
                "conversations",
                "profile",
                "people",
                "important_dates",
                "reminders",
                "automations",
                "shopping_items",
                "expenses",
                "budgets",
                "health_logs",
                "services",
                "connector_tool_cache",
                "connector_connections",
                "subagents",
                "usage",
                "providers",
                "settings",
            ]
            with self._write() as conn:
                for table in tables:
                    conn.execute(f"DELETE FROM {table}")
        finally:
            if recorder is not None:
                recorder.resume()
