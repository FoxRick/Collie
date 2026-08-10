"""IPC undo_file_changes command: one-tap restore of journaled file writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.undo.journal import record_write


def _server(tmp_path: Path) -> CollieIPCServer:
    return CollieIPCServer(CollieDB(tmp_path / "collie.db"))


def _call(server: CollieIPCServer, frame: dict):
    return server._cmd_undo_file_changes(cast(Any, None), frame)


@pytest.mark.asyncio
async def test_undo_file_changes_restores_all_entries(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(home))
    server = _server(tmp_path)

    edited = home / "notes.md"
    edited.write_text("before", encoding="utf-8")
    entry = record_write("conv-1", edited, "overwrite")
    edited.write_text("after", encoding="utf-8")

    result = await _call(server, {"conversation_id": "conv-1", "entry_ids": []})
    assert [item["id"] for item in result["undone"]] == [entry]
    assert result["errors"] == []
    assert edited.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_undo_file_changes_removes_created_file(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(home))
    server = _server(tmp_path)

    created = home / "fresh.md"
    entry = record_write("conv-1", created, "create")
    created.write_text("made", encoding="utf-8")

    result = await _call(server, {"conversation_id": "conv-1", "entry_ids": [str(entry)]})
    assert len(result["undone"]) == 1
    assert not created.exists()


@pytest.mark.asyncio
async def test_undo_file_changes_is_scoped_to_conversation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(home))
    server = _server(tmp_path)

    other = home / "other.md"
    other.write_text("other", encoding="utf-8")
    record_write("conv-2", other, "overwrite")
    other.write_text("other2", encoding="utf-8")

    result = await _call(server, {"conversation_id": "conv-1", "entry_ids": []})
    assert result["undone"] == []
    assert other.read_text(encoding="utf-8") == "other2"
