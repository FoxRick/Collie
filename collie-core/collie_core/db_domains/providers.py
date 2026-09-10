"""Provider, usage, and product metric storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``providers``, ``usage`` and the product metric tables.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ProvidersDomain:
    """Provider, usage, and product metric storage. Mixed into :class:`CollieDB` by
    composition, never a domain to domain call."""

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

    def _increment_product_metrics(
        self,
        conn: sqlite3.Connection,
        started_at: str | None,
        *,
        runs: int = 0,
        interactive_runs: int = 0,
        tool_calls: int = 0,
    ) -> None:
        if not self._product_metrics_enabled:
            return
        day = datetime.fromisoformat(started_at or utc_now()).astimezone(UTC).date().isoformat()
        conn.execute(
            "INSERT INTO product_metrics_daily (day, runs, interactive_runs, tool_calls) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
            "runs = runs + excluded.runs, "
            "interactive_runs = interactive_runs + excluded.interactive_runs, "
            "tool_calls = tool_calls + excluded.tool_calls",
            (day, runs, interactive_runs, tool_calls),
        )

    def product_metrics(self) -> dict[str, Any]:
        """Bounded, content-free cumulative counters; never export raw run records.

        Source identity and counters deliberately do not travel in cloud backups.
        Clearing local data rotates the source so new counts do not collide with
        high-water marks already accepted by the server.
        """
        if not self._product_metrics_enabled:
            return {}
        today = datetime.now(UTC).date()
        cutoff = (today - timedelta(days=34)).isoformat()
        with self._write_immediate() as conn:
            row = conn.execute("SELECT id FROM product_metrics_source").fetchone()
            source = row["id"] if row else str(uuid.uuid4())
            if not row:
                conn.execute("INSERT INTO product_metrics_source VALUES (?)", (source,))
            conn.execute("DELETE FROM product_metrics_daily WHERE day < ?", (cutoff,))
            days = [
                dict(row)
                for row in conn.execute(
                    "SELECT day, runs, interactive_runs, tool_calls FROM product_metrics_daily "
                    "WHERE day >= ? AND day <= ? ORDER BY day",
                    (cutoff, today.isoformat()),
                )
            ]
        return {"source_id": source, "days": days}
