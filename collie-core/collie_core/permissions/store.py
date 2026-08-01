"""SQLite adapter for permission rules."""

from __future__ import annotations

from typing import Any

from collie_core.db import CollieDB


class PermissionStore:
    def __init__(self, db: CollieDB) -> None:
        self.db = db

    def list_rules(self) -> list[dict[str, Any]]:
        return self.db.list_approval_rules()
