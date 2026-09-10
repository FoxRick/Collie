"""Settings and profile storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``settings`` and ``profile``.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.db_primitives import utc_now


class SettingsDomain:
    """Settings and profile storage. Mixed into :class:`CollieDB` by composition, never a
    domain to domain call."""

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
