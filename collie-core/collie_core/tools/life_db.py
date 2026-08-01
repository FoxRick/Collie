"""Shared CollieDB binding for the in-app life tools (shopping, budget, health…).

The runtime binds the database once at boot; tools read it at execute time.
"""

from __future__ import annotations

from collie_core.db import CollieDB

__all__ = ["bind_life_db", "life_db"]

_db: CollieDB | None = None


def bind_life_db(db: CollieDB | None) -> None:
    global _db
    _db = db


def life_db() -> CollieDB | None:
    return _db
