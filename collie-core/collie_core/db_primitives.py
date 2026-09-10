"""Shared storage primitives for the Collie SQLite layer.

Leaf module: it imports nothing from :mod:`collie_core`, so both
``collie_core.db`` and the domain modules under ``collie_core.db_domains``
can depend on it without an import cycle. ``collie_core.db`` imports and
re-exports every name here, so ``from collie_core.db import collie_home``
and ``from collie_core.db import utc_now`` keep working.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path


def collie_home() -> Path:
    """Return the Collie data directory (``~/.collie`` or ``$COLLIE_HOME``)."""
    root = os.environ.get("COLLIE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".collie"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex
