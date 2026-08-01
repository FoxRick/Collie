"""Canonical engine session identities shared by the runtime and IPC."""

from __future__ import annotations

__all__ = ["desktop_session_key"]


def desktop_session_key(conversation_id: str) -> str:
    """Return the canonical engine key for a desktop conversation."""
    return f"collie:{conversation_id}"
