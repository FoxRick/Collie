"""Shared helpers for the IPC command modules.

A leaf module on purpose: ``collie_core.ipc.server`` imports the command
modules, so anything both sides need has to live above them. ``server.py``
imports these names back, so existing references keep working.
"""

from __future__ import annotations

from typing import Any

_MAX_LIST_LIMIT = 500


def _bounded_list_limit(
    value: Any,
    *,
    default: int | None,
    maximum: int = _MAX_LIST_LIMIT,
) -> int | None:
    """Parse a renderer list limit without allowing unbounded SQLite LIMITs."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default if default is not None else maximum
    return max(1, min(parsed, maximum))
