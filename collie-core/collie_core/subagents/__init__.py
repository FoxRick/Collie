"""Collie subagents.

Specialized assistants stored as plain ``.md`` files in
``~/.collie/workspace/subagents/`` — editable in Settings or any text editor —
and mirrored into the SQLite ``subagents`` table for the UI.
"""

from collie_core.subagents.loader import (
    STARTERS,
    SubagentLoader,
    bind_subagent_loader,
    draft_system_prompt,
    get_subagent_loader,
)

__all__ = [
    "STARTERS",
    "SubagentLoader",
    "bind_subagent_loader",
    "draft_system_prompt",
    "get_subagent_loader",
]
