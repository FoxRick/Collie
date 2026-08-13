"""IPC contract drift guard.

Collie's renderer talks to the Python core over a WebSocket where every
command is a string frame (``type``), dispatched server-side through
``CollieIPCServer._cmd_<type>``. Nothing mechanically enforced that the
two sides of that contract stay in sync:

- a renderer command with no server handler dies at runtime with
  "unknown command" — the user just sees the action do nothing;
- a server handler nothing ever calls is dead weight that review
  mistakes for a live feature.

This test extracts both sides from source and fails on any NEW
asymmetry. Deliberate asymmetries live in ``_SERVER_ONLY_ALLOWLIST``
below with a reason — that list is the reviewable record of intent, so
adding a command is a conscious act on both sides of the wire.

Mechanics:
- Server side: ``async def _cmd_<kind>(`` in ``ipc/server.py``.
- Client side: every ``.command(<kind>)`` literal across ``collie-ui/src``
  (the renderer also calls ``command`` directly in components, e.g.
  ``MemoryTab.tsx``, so the scan is repo-wide, not just ``ipc.ts``).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVER_FILE = _REPO_ROOT / "collie-core" / "collie_core" / "ipc" / "server.py"
_UI_SRC = _REPO_ROOT / "collie-ui" / "src"

# Server commands with no caller anywhere in the UI. Keeping a command
# here is a deliberate decision — each entry must say why.
#   - Engine/test-only: exercised by the backend suite, not the renderer.
#   - Dead: no caller at all; candidates for removal, listed so the
#     decision to remove (or wire up) is explicit.
_SERVER_ONLY_ALLOWLIST: dict[str, str] = {
    # Health check used by backend tests (tests/collie/test_ipc.py).
    "ping": "test-only heartbeat; renderer relies on the WS itself",
    # Plan engine commands driven by backend tests; the UI plans flow
    # through approve_plan / change_plan instead (test_plan_execution.py).
    "create_plan": "plan engine API exercised by backend tests",
    "get_plan": "plan engine API exercised by backend tests",
    "retry_plan_execution": "plan engine API exercised by backend tests",
    # Routine CRUD handlers with no current UI caller — RoutinesScreen
    # uses list/run/pause/resume/retry. Verify before removing.
    "get_routine": "no caller in UI; verify or remove",
    "update_routine": "no caller in UI; verify or remove",
    "delete_routine": "no caller in UI; verify or remove",
    "test_routine": "no caller in UI; verify or remove",
}

_SERVER_RE = re.compile(r"^    async def _cmd_(\w+)\(", re.MULTILINE)
# .command(<T>)('kind', ...) — kind may sit on the next line and be
# single- or double-quoted (see create_subagent / begin_connector_auth).
_COMMAND_RE = re.compile(r"\.command(?:<[^>]*>)?\(\s*['\"]([^'\"]+)['\"]")


def _server_command_kinds() -> set[str]:
    return set(_SERVER_RE.findall(_SERVER_FILE.read_text(encoding="utf-8")))


def _ui_wire_commands() -> set[str]:
    kinds: set[str] = set()
    for path in sorted(_UI_SRC.rglob("*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        kinds.update(_COMMAND_RE.findall(path.read_text(encoding="utf-8")))
    return kinds


def test_every_ui_command_has_a_server_handler() -> None:
    """The critical direction: a UI command with no handler fails at runtime."""
    server_kinds = _server_command_kinds()
    ui_commands = _ui_wire_commands()
    missing = sorted(ui_commands - server_kinds)
    assert not missing, (
        "UI sends IPC commands with no server handler — these die with "
        f"'unknown command' at runtime: {missing}. Add _cmd_<kind> in "
        "collie_core/collie_core/ipc/server.py (or fix the client)."
    )


def test_new_server_commands_are_wired_or_allowlisted() -> None:
    """Server-only commands must be intentional, not accidental."""
    server_kinds = _server_command_kinds()
    ui_commands = _ui_wire_commands()
    unwired = sorted(server_kinds - ui_commands - set(_SERVER_ONLY_ALLOWLIST))
    assert not unwired, (
        "Server commands nothing in the UI sends: "
        f"{unwired}. Wire them in collie-ui (ipc.ts or the calling "
        "component), or add them to _SERVER_ONLY_ALLOWLIST with a reason."
    )


def test_allowlist_entries_still_exist() -> None:
    """Allowlist rot: entries whose handler disappeared should be removed."""
    server_kinds = _server_command_kinds()
    stale = sorted(set(_SERVER_ONLY_ALLOWLIST) - server_kinds)
    assert not stale, (
        f"Allowlist entries with no matching server handler: {stale}. "
        "Remove them from _SERVER_ONLY_ALLOWLIST."
    )
