"""Focused IPC and turn-scope coverage for product local-file access."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import nanobot.security.workspace_access as workspace_access
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.runtime import CollieRuntime
from nanobot.security.workspace_access import (
    WorkspaceScopeError,
    WorkspaceScopeResolver,
    bind_workspace_scope,
    current_scope_allows_loopback,
    current_tool_workspace,
    is_local_filesystem_path,
    reset_workspace_scope,
    resolve_effective_workspace_scope,
    validate_local_file_access_scope_payload,
    validate_workspace_scope_payload,
    workspace_scope_from_metadata,
)


class _Connection:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, raw: str) -> None:
        self.frames.append(json.loads(raw))


def test_chosen_folders_are_canonicalized_and_deduplicated(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    roots, unrestricted = validate_local_file_access_scope_payload(
        {"mode": "chosen_folders", "roots": [str(first), str(second), str(first)]},
        selected_folder=None,
    )

    assert roots == (first.resolve(), second.resolve())
    assert unrestricted is False


def test_mapped_windows_drive_is_rejected_after_canonicalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    monkeypatch.setattr(workspace_access, "_running_on_windows", lambda: True)
    monkeypatch.setattr(workspace_access, "_windows_drive_type", lambda _anchor: 4)

    with pytest.raises(WorkspaceScopeError, match="must be on a local drive"):
        validate_local_file_access_scope_payload(
            {"mode": "chosen_folders", "roots": [str(selected)]},
            selected_folder=None,
        )


@pytest.mark.parametrize(
    ("drive_type", "expected"),
    [(2, True), (3, True), (6, True), (4, False), (0, False), (None, False)],
)
def test_windows_drive_type_allows_only_local_drives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drive_type: int | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(workspace_access, "_running_on_windows", lambda: True)
    monkeypatch.setattr(workspace_access, "_windows_drive_type", lambda _anchor: drive_type)

    assert is_local_filesystem_path(tmp_path) is expected


@pytest.mark.parametrize(
    "payload, selected_folder, error",
    [
        ({"mode": "selected_folder", "roots": ["C:/not-used"]}, None, "does not accept roots"),
        ({"mode": "chosen_folders", "roots": ["relative"]}, None, "must be absolute"),
        ({"mode": "chosen_folders", "roots": [r"\\server\share"]}, None, "network directory"),
        ({"mode": "chosen_folders", "roots": []}, None, "requires one or more"),
        ({"mode": "full_file_access", "roots": ["C:/not-used"]}, None, "does not accept roots"),
        ({"mode": "not-a-mode"}, None, "must be selected_folder"),
    ],
)
def test_file_access_payload_rejects_invalid_choices(
    payload: dict, selected_folder: Path | None, error: str
) -> None:
    with pytest.raises(WorkspaceScopeError, match=error):
        validate_local_file_access_scope_payload(payload, selected_folder=selected_folder)


def test_explicit_full_file_access_does_not_enable_loopback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    scope = validate_workspace_scope_payload(
        {
            "project_path": str(project),
            "access_mode": "restricted",
            "file_access_scope": {"mode": "full_file_access"},
        },
        default_workspace=project,
        default_restrict_to_workspace=True,
        source_channel="websocket",
    )

    token = bind_workspace_scope(scope)
    try:
        tool_scope = current_tool_workspace(None)
        assert tool_scope.unrestricted_local_files is True
        assert tool_scope.allowed_local_file_roots == ()
        assert current_scope_allows_loopback(enabled=True) is False
    finally:
        reset_workspace_scope(token)

    with pytest.raises(WorkspaceScopeError, match="requires restricted access_mode"):
        validate_workspace_scope_payload(
            {
                "project_path": str(project),
                "access_mode": "full",
                "file_access_scope": {"mode": "full_file_access"},
            },
            default_workspace=project,
            default_restrict_to_workspace=True,
            source_channel="websocket",
        )


def test_selected_folder_metadata_round_trips_without_falling_back(tmp_path: Path) -> None:
    project = tmp_path / "project"
    fallback = tmp_path / "fallback"
    project.mkdir()
    fallback.mkdir()
    scope = validate_workspace_scope_payload(
        {
            "project_path": str(project),
            "access_mode": "restricted",
            "file_access_scope": {"mode": "selected_folder"},
        },
        default_workspace=fallback,
        default_restrict_to_workspace=True,
        source_channel="collie",
    )

    assert scope.metadata()["file_access_scope"] == {"mode": "selected_folder"}
    restored = workspace_scope_from_metadata(
        {"workspace_scope": scope.metadata()},
        default_workspace=fallback,
        default_restrict_to_workspace=True,
        source_channel="collie",
    )

    assert restored.project_path == project.resolve()
    assert restored.local_file_access_mode == "selected_folder"
    assert restored.local_file_roots == (project.resolve(),)


def test_full_file_access_is_not_persisted_as_session_authority(tmp_path: Path) -> None:
    project = tmp_path / "project"
    fallback = tmp_path / "fallback"
    project.mkdir()
    fallback.mkdir()
    resolver = WorkspaceScopeResolver(
        default_workspace=fallback,
        default_restrict_to_workspace=True,
    )
    session = SimpleNamespace(metadata={})
    message = SimpleNamespace(
        channel="collie",
        metadata={
            "workspace_scope": {
                "project_path": str(project),
                "access_mode": "restricted",
                "file_access_scope": {"mode": "full_file_access"},
            }
        },
    )

    resolver.persist_message_scope(session, message)

    stored = session.metadata["workspace_scope"]
    assert stored == {"project_path": str(project), "access_mode": "restricted"}
    restored = resolve_effective_workspace_scope(
        message_metadata=None,
        session_metadata=session.metadata,
        default_workspace=fallback,
        default_restrict_to_workspace=True,
        source_channel="collie",
    )
    assert restored.unrestricted_local_files is False
    assert restored.local_file_roots == (project.resolve(),)


def test_explicit_general_chat_selection_replaces_persisted_full_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    fallback = tmp_path / "fallback"
    project.mkdir()
    fallback.mkdir()

    scope = resolve_effective_workspace_scope(
        message_metadata={
            "workspace_scope": {
                "access_mode": "restricted",
                "file_access_scope": {"mode": "selected_folder"},
            }
        },
        session_metadata={
            "workspace_scope": {
                "project_path": str(project),
                "access_mode": "restricted",
                "file_access_scope": {"mode": "full_file_access"},
            }
        },
        default_workspace=fallback,
        default_restrict_to_workspace=True,
        source_channel="collie",
    )

    assert scope.project_path == fallback.resolve()
    assert scope.local_file_access_mode == "selected_folder"
    assert scope.local_file_roots == (fallback.resolve(),)
    assert scope.unrestricted_local_files is False


@pytest.mark.asyncio
async def test_ipc_forwards_validated_file_scope_without_changing_project_selection(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    chosen = tmp_path / "chosen"
    project.mkdir()
    chosen.mkdir()
    received: dict = {}
    called = asyncio.Event()

    async def runner(content: str, **kwargs):
        received.update(kwargs)
        called.set()
        return SimpleNamespace(content="Done")

    db = CollieDB(tmp_path / "collie.db")
    server = CollieIPCServer(db, chat_runner=runner)
    connection = _Connection()
    try:
        await server._cmd_chat(connection, {
            "id": "file-scope",
            "content": "Review these folders",
            "project_path": str(project),
            "file_access_scope": {
                "mode": "chosen_folders",
                "roots": [str(chosen), str(chosen)],
            },
        })
        await asyncio.wait_for(called.wait(), timeout=1)
    finally:
        await asyncio.gather(*server._chat_tasks.values(), return_exceptions=True)
        db.close()

    assert received["project_path"] == str(project.resolve())
    assert received["execution_mode"] == "execute"
    assert received["file_access_scope"] == {
        "mode": "chosen_folders",
        "roots": [str(chosen.resolve())],
    }


@pytest.mark.asyncio
async def test_ipc_rejects_invalid_file_scope_before_creating_conversation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    server = CollieIPCServer(db, chat_runner=None)
    connection = _Connection()
    try:
        await server._cmd_chat(connection, {
            "id": "bad-file-scope",
            "content": "Read it",
            "file_access_scope": {"mode": "selected_folder", "roots": ["C:/not-used"]},
        })
        assert db.list_conversations() == []
    finally:
        db.close()

    assert connection.frames == [{
        "type": "error",
        "id": "bad-file-scope",
        "message": "That file access choice is not available: selected_folder does not accept roots",
    }]


@pytest.mark.asyncio
async def test_selected_folder_scope_survives_ipc_to_agent_validation(tmp_path: Path) -> None:
    class _Loop:
        scope = None

        async def process_direct(self, _content: str, **kwargs):
            self.scope = validate_workspace_scope_payload(
                kwargs["workspace_scope"],
                default_workspace=tmp_path,
                default_restrict_to_workspace=True,
                source_channel="collie",
            )
            return SimpleNamespace(content="Done")

    project = tmp_path / "project"
    project.mkdir()
    db = CollieDB(tmp_path / "collie.db")
    runtime = CollieRuntime.__new__(CollieRuntime)
    loop = _Loop()
    runtime.loop = loop
    runtime.db = db
    runtime._conversation_target = lambda conversation_id: (
        f"desktop:{conversation_id}", "collie", conversation_id
    )
    server = CollieIPCServer(db, chat_runner=runtime._chat)
    connection = _Connection()
    try:
        await server._cmd_chat(connection, {
            "id": "selected-folder",
            "content": "Review this folder",
            "project_path": str(project),
            "file_access_scope": {"mode": "selected_folder"},
        })
        for _ in range(100):
            if loop.scope is not None:
                break
            await asyncio.sleep(0.01)
        assert loop.scope is not None
    finally:
        await asyncio.gather(*server._chat_tasks.values(), return_exceptions=True)
        db.close()

    assert loop.scope.local_file_access_mode == "selected_folder"
    assert loop.scope.local_file_roots == (project.resolve(),)
    assert loop.scope.unrestricted_local_files is False


@pytest.mark.asyncio
async def test_general_chat_selected_folder_uses_runtime_default_workspace(tmp_path: Path) -> None:
    class _Loop:
        scope = None

        async def process_direct(self, _content: str, **kwargs):
            self.scope = validate_workspace_scope_payload(
                kwargs["workspace_scope"],
                default_workspace=tmp_path,
                default_restrict_to_workspace=True,
                source_channel="collie",
            )
            return SimpleNamespace(content="Done")

    db = CollieDB(tmp_path / "collie.db")
    runtime = CollieRuntime.__new__(CollieRuntime)
    loop = _Loop()
    runtime.loop = loop
    runtime.db = db
    runtime._conversation_target = lambda conversation_id: (
        f"desktop:{conversation_id}", "collie", conversation_id
    )
    server = CollieIPCServer(db, chat_runner=runtime._chat)
    connection = _Connection()
    try:
        await server._cmd_chat(connection, {
            "id": "general-chat-selected-folder",
            "content": "Review my workspace",
            "file_access_scope": {"mode": "selected_folder"},
        })
        for _ in range(100):
            if loop.scope is not None:
                break
            await asyncio.sleep(0.01)
        assert loop.scope is not None
    finally:
        await asyncio.gather(*server._chat_tasks.values(), return_exceptions=True)
        db.close()

    assert loop.scope.project_path == tmp_path.resolve()
    assert loop.scope.local_file_access_mode == "selected_folder"
    assert loop.scope.local_file_roots == (tmp_path.resolve(),)
    assert loop.scope.unrestricted_local_files is False


@pytest.mark.asyncio
async def test_runtime_passes_local_file_scope_to_the_agent_loop(tmp_path: Path) -> None:
    class _Loop:
        received: dict | None = None

        async def process_direct(self, _content: str, **kwargs):
            self.received = kwargs
            return SimpleNamespace(content="Done")

    db = CollieDB(tmp_path / "collie.db")
    runtime = CollieRuntime.__new__(CollieRuntime)
    loop = _Loop()
    runtime.loop = loop
    runtime.db = db
    runtime._conversation_target = lambda conversation_id: (
        f"desktop:{conversation_id}", "collie", conversation_id
    )

    async def noop(*_args, **_kwargs) -> None:
        return None

    try:
        await runtime._chat(
            "Review my files",
            conversation_id="conversation-1",
            on_stream=noop,
            on_progress=noop,
            project_path=str(tmp_path),
            file_access_scope={"mode": "full_file_access"},
        )
    finally:
        db.close()

    assert loop.received is not None
    assert loop.received["workspace_scope"] == {
        "project_path": str(tmp_path),
        "access_mode": "restricted",
        "file_access_scope": {"mode": "full_file_access"},
    }
