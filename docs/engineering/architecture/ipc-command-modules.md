# IPC command modules — splitting the CollieIPCServer god class

> **Status (2026-09-10):** landed as a behavior-preserving refactor. The wire
> contract did not change: every handler is still `server._cmd_<kind>`, so the
> renderer and the tests that speak to it are untouched.

## Goal

`collie_core/ipc/server.py` had grown to 3,915 lines: connection handling, the
frame dispatch, shared helpers and all 135 `_cmd_<kind>` handlers in one class.
Working on the routine handlers meant reading past collaboration, providers,
connectors, files and the chat turn first.

The refactor adds no behaviour. It groups the handlers by command family, one
module each, and mixes them back into `CollieIPCServer`.

## Shape

```
collie_core/ipc/
  server.py             2,456 lines (was 3,915)
                        connection handling, frame dispatch, shared server
                        state, chat turn, plan/run/approval flow, conversation
                        CRUD, data management, 42 handlers
  command_support.py    _MAX_LIST_LIMIT, _bounded_list_limit   (leaf module)
  commands/
    __init__.py         re-exports the eight mixins
    collaboration.py    CollaborationCommands    14 handlers
    memory.py           MemoryCommands           17   people, dates, Dream
    routines.py         RoutineCommands          16   routines, automations
    providers.py        ProviderCommands         17   catalogue, keys, OAuth
    connectors.py       ConnectorCommands        12   services, connectors
    files.py            FileCommands              5   workspace files, versions
    agents.py           AgentCommands             8   subagents, skills
    messengers.py       MessengerCommands         4
```

`CollieIPCServer` composes them:

```python
class CollieIPCServer(
    CollaborationCommands,
    MemoryCommands,
    RoutineCommands,
    ProviderCommands,
    ConnectorCommands,
    FileCommands,
    AgentCommands,
    MessengerCommands,
):
```

Dispatch is unchanged because it was already indirect:
`getattr(self, f"_cmd_{kind}")` walks the MRO, so a handler defined on a mixin
is found exactly as before.

## The seam

A command module reaches the server through `self` and nothing else. The host
state each module actually uses is declared in
`tests/collie/test_ipc_command_split.py` under `HOST_DEPENDENCIES`, so the
surface a module depends on is readable instead of implied:

| Module | Server state it reaches |
| --- | --- |
| collaboration | `_collaboration_store`, `_collaboration_identity_binder`, `_collaboration_run_controller`, `_archive_manager`, `_shared_chat_runner`, `db` |
| memory | `_memory`, `_dream_runner`, `db` |
| routines | `_chat_tasks`, `_run_chat_turn`, `broadcast`, `db` |
| providers | `_catalogue`, `_apply_provider_settings`, `_oauth_attempts`, `_oauth_generations`, `_oauth_worker_tasks`, `_send`, `_chat_tasks`, the three `_on_*` provider callbacks, `_on_configure`, `_on_set_api_key`, `_on_delete_api_key`, `db` |
| connectors | `_service_manager`, `broadcast`, `_reconfigure_quietly`, `db` |
| files | `_resolve_workspace_path`, `_classify_workspace_artifact`, `_artifact_target`, `_subagent_loader`, `db` |
| agents | `_subagent_loader`, `_subagent_canceler`, `_prompt_writer`, `_activity_provider`, `_status_provider`, `_skills_workspace`, `db` |
| messengers | `_messengers` |

Three names had two readers, one on each side of the new seam, so they moved to
the leaf module `command_support.py`: `_MAX_LIST_LIMIT` and
`_bounded_list_limit` (used by moving and staying handlers). `_PERSON_FIELDS`
moved to the memory module and `_OAuthAttemptState` to the providers module,
because each had exactly one reader.

## Why this is safe

Every one of the 170 members of `CollieIPCServer` (135 handlers plus 35 helpers)
was compared with `inspect.getsource` between `origin/main` and this branch:
0 missing, 0 changed. The full backend suite and the `ruff` gates are the
behavioural proof.

`tests/collie/test_ipc_contract.py` used to grep the handler names out of
`server.py` alone. It now scans the whole `ipc/` package, so a handler moving
between modules cannot change the wire contract by itself, and the renderer
check still runs in both directions.

`tests/collie/test_ipc_command_split.py` keeps the split honest: it pins which
module owns which handler, rejects a handler owned by two modules (a silent MRO
shadow), rejects a handler left behind in `server.py`, and requires each module
to declare the server state it reaches for.

## Adding a command

1. Put the handler in the module matching its family. Add a module if the family
   is genuinely new.
2. Reach for server state through `self`, and declare anything new in that
   module's `HOST_DEPENDENCIES` entry.
3. Add the command kind to `HANDLERS` in the guard test.
4. If the renderer calls it, wire it in `collie-ui`; if nothing calls it, add it
   to `_SERVER_ONLY_ALLOWLIST` in the contract test with the reason.

## Left in server.py on purpose

The chat turn, the plan/run/approval flow, conversation CRUD and the
data-management commands stay. They share the chat task plumbing
(`_chat_tasks`, `_run_chat_turn`) and the run state, so they are one cluster
rather than several command families. Splitting the chat turn is a separate job
with its own risk profile: it owns the longest handler in the file.
