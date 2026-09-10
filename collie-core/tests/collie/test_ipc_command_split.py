"""Guard the IPC command-module split of :class:`collie_core.ipc.server.CollieIPCServer`.

``ipc/server.py`` was one 3,915 line file holding the connection, the frame
dispatch, every shared helper and all 135 ``_cmd_<kind>`` handlers. The handlers
now live in ``ipc/commands/``, one module per command group, and are mixed back
into ``CollieIPCServer``, which keeps the connection, the dispatch and the
shared server state. The renderer's wire contract is unchanged.

These tests pin what keeps the split honest:

- every handler is still reachable as ``CollieIPCServer._cmd_<kind>`` and is
  owned by the module named below, so dispatch keeps working;
- no two modules define the same handler, which would shadow silently through
  the MRO;
- each module declares the server state it reaches for through ``self``, so a
  command module cannot quietly grow a dependency on unrelated server internals;
- no handler is left behind in ``server.py`` after a move.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import collie_core.ipc.commands as command_modules
from collie_core.ipc.server import CollieIPCServer

# Frozen ownership table: which module owns which handler. Move a handler and
# this table moves with it; the test then tells you the wire contract still binds.
HANDLERS: dict[str, tuple[str, ...]] = {
    "agents": (
        "cancel_subagent",
        "create_subagent",
        "delete_subagent",
        "get_skill",
        "get_subagent_activity",
        "list_skills",
        "list_subagents",
        "update_subagent",
    ),
    "collaboration": (
        "collaboration_apply_page",
        "collaboration_bind_identity",
        "collaboration_cache_bootstrap",
        "collaboration_control_run",
        "collaboration_export_archive",
        "collaboration_get_cached_bootstrap",
        "collaboration_import_archive",
        "collaboration_list_messages",
        "collaboration_mark_routine_delivery",
        "collaboration_queue_event",
        "collaboration_run_shared",
        "collaboration_set_routine_delivery",
        "collaboration_status",
        "collaboration_write_archive",
    ),
    "connectors": (
        "begin_connector_auth",
        "cancel_connector_auth",
        "connect_service",
        "disconnect_service",
        "get_connector",
        "list_connector_catalog",
        "list_connector_connections",
        "list_connector_tools",
        "list_services",
        "remove_connector",
        "test_connector",
        "update_connector",
    ),
    "files": (
        "list_versions",
        "read_file",
        "rollback_artifact",
        "undo_file_changes",
        "write_file",
    ),
    "memory": (
        "add_date_memory",
        "add_person_memory",
        "apply_dream_proposal",
        "delete_date_memory",
        "delete_person_memory",
        "delete_profile_memory",
        "dismiss_dream_proposal",
        "get_dates",
        "get_dream_history",
        "get_dream_pending",
        "get_memory_journal",
        "get_people",
        "get_profile",
        "run_dream",
        "set_profile_memory",
        "update_date_memory",
        "update_person_memory",
    ),
    "messengers": (
        "get_messengers",
        "revoke_messenger_sender",
        "set_messenger",
        "set_messenger_secret",
    ),
    "providers": (
        "activate_managed_provider",
        "activate_provider",
        "cancel_oauth",
        "configure_provider_candidate",
        "delete_provider",
        "detect_local_models",
        "detect_models",
        "detect_provider_for_key",
        "finalize_provider_candidate",
        "get_provider_catalogue",
        "oauth_login",
        "oauth_logout",
        "refresh_provider_catalogue",
        "rollback_provider_candidate",
        "rollback_provider_catalogue",
        "set_api_key",
        "upsert_provider",
    ),
    "routines": (
        "create_automation",
        "create_routine",
        "delete_automation",
        "delete_routine",
        "get_routine",
        "list_automations",
        "list_routine_runs",
        "list_routines",
        "pause_routine",
        "resume_routine",
        "retry_routine_run",
        "run_routine_now",
        "test_routine",
        "toggle_automation",
        "update_automation",
        "update_routine",
    ),
}

# The server state each command module reaches for, minus its own handlers.
# A new entry here is a deliberate statement that the module needs that part of
# the server; a stale one means the reach went away and the table should shrink.
HOST_DEPENDENCIES: dict[str, frozenset[str]] = {
    "agents": frozenset(
        {
            "_activity_provider",
            "_prompt_writer",
            "_skills_workspace",
            "_status_provider",
            "_subagent_canceler",
            "_subagent_loader",
            "db",
        }
    ),
    "collaboration": frozenset(
        {
            "_archive_manager",
            "_collaboration_identity_binder",
            "_collaboration_run_controller",
            "_collaboration_store",
            "_shared_chat_runner",
            "db",
        }
    ),
    "connectors": frozenset(
        {
            "_reconfigure_quietly",
            "_service_manager",
            "broadcast",
            "db",
        }
    ),
    "files": frozenset(
        {
            "_artifact_target",
            "_classify_workspace_artifact",
            "_resolve_workspace_path",
            "_subagent_loader",
            "db",
        }
    ),
    "memory": frozenset(
        {
            "_dream_runner",
            "_memory",
            "db",
        }
    ),
    "messengers": frozenset(
        {
            "_messengers",
        }
    ),
    "providers": frozenset(
        {
            "_apply_provider_settings",
            "_catalogue",
            "_chat_tasks",
            "_oauth_attempts",
            "_oauth_generations",
            "_oauth_worker_tasks",
            "_on_configure",
            "_on_configure_provider_candidate",
            "_on_delete_api_key",
            "_on_finalize_provider_candidate",
            "_on_rollback_provider_candidate",
            "_on_set_api_key",
            "_send",
            "db",
        }
    ),
    "routines": frozenset(
        {
            "_chat_tasks",
            "_run_chat_turn",
            "broadcast",
            "db",
        }
    ),
}

COMMAND_DIR = Path(command_modules.__file__).parent
IPC_DIR = COMMAND_DIR.parent
# Handlers are always the first-level methods of a command mixin, so an exact
# four-space indent distinguishes them from anything nested inside a handler.
_HANDLER_RE = re.compile(r"^    async def _cmd_(\w+)\(", re.MULTILINE)


def _declared_handlers(module: str) -> set[str]:
    """Command kinds the module defines, read straight from its source."""
    return set(_HANDLER_RE.findall((COMMAND_DIR / f"{module}.py").read_text(encoding="utf-8")))


@pytest.mark.parametrize(("module", "handlers"), HANDLERS.items())
def test_module_owns_its_handlers(module: str, handlers: tuple[str, ...]) -> None:
    """Each listed handler is reachable on the server and owned by its module."""
    for kind in handlers:
        name = f"_cmd_{kind}"
        member = getattr(CollieIPCServer, name)
        assert callable(member), f"CollieIPCServer.{name} is not callable"
        assert member.__module__ == f"collie_core.ipc.commands.{module}", (
            f"{name} is owned by {member.__module__}, expected the {module} commands module"
        )


def test_no_module_shadows_another() -> None:
    """Two modules owning one handler would shadow silently through the MRO."""
    owners: dict[str, list[str]] = {}
    for module, handlers in HANDLERS.items():
        for kind in handlers:
            owners.setdefault(kind, []).append(module)
    collisions = {kind: mods for kind, mods in owners.items() if len(mods) > 1}
    assert collisions == {}


def test_no_handler_is_left_behind_in_server() -> None:
    """A handler defined in both server.py and a command module is a duplicate."""
    server_handlers = set(_HANDLER_RE.findall((IPC_DIR / "server.py").read_text(encoding="utf-8")))
    moved = {kind for handlers in HANDLERS.values() for kind in handlers}
    both = sorted(server_handlers & moved)
    assert both == [], f"these handlers exist in server.py and a command module: {both}"


def test_ownership_table_covers_every_handler_in_the_package() -> None:
    """Nothing may sit in the command package unowned, and nothing may vanish."""
    on_disk = set()
    for path in sorted(COMMAND_DIR.glob("*.py")):
        on_disk.update(_HANDLER_RE.findall(path.read_text(encoding="utf-8")))
    declared = {kind for handlers in HANDLERS.values() for kind in handlers}
    undeclared = sorted(on_disk - declared)
    phantom = sorted(declared - on_disk)
    assert undeclared == [], f"command modules define handlers missing from HANDLERS: {undeclared}"
    assert phantom == [], f"HANDLERS lists handlers no module defines: {phantom}"


@pytest.mark.parametrize("module", sorted(HANDLERS))
def test_module_declares_the_host_state_it_reaches(module: str) -> None:
    """A command module reaches the server through a declared surface only."""
    source = (COMMAND_DIR / f"{module}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    klass = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Commands")
    )
    own = {
        node.name
        for node in klass.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reached = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    reach = reached - own
    declared = set(HOST_DEPENDENCIES[module])
    undeclared = sorted(reach - declared)
    stale = sorted(declared - reach)
    assert undeclared == [], (
        f"the {module} commands module reaches for {undeclared} without declaring it in "
        f"HOST_DEPENDENCIES; either keep the handler on the server or declare the dependency"
    )
    assert stale == [], (
        f"HOST_DEPENDENCIES lists {stale} for {module} but the module no longer reaches them"
    )
