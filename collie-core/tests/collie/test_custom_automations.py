"""Tests for Phase 3 Steps 40 + 42: custom automations, export, clear."""

from __future__ import annotations

import asyncio
import json
import socket
import zipfile
from pathlib import Path
from typing import Any

import pytest
import websockets

from collie_core.automations.custom import create_custom_automation, parse_schedule
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer

# -- schedule parsing --------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("every day at 7am", "07:00"),
    ("daily at 07:30", "07:30"),
    ("every morning", "08:00"),
    ("every evening remind me to stretch", "20:00"),
    ("every friday at 5pm ask me how my week went", "Fri 17:00"),
    ("Fridays at 10am", "Fri 10:00"),
    ("on sunday at noon", "Sun 12:00"),
    ("every month on the 1st at 9am", "01 09:00"),
    ("the 15th of every month at 6:15 pm", "15 18:15"),
    ("every tuesday", "Tue 09:00"),
    ("at 12am daily", "00:00"),
    ("remind me sometime", None),
    ("", None),
])
def test_parse_schedule(text: str, expected: str | None) -> None:
    assert parse_schedule(text) == expected


def test_create_custom_automation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    try:
        row = create_custom_automation(
            db, "Every Friday at 5pm, ask me how my week went and suggest "
                "something fun happening this weekend in Berlin",
        )
        assert row["schedule"] == "Fri 17:00"
        assert row["action_type"] == "custom"
        assert row["enabled"] == 1
        assert row["routine_status"] == "enabled"
        config = json.loads(row["action_config"])
        assert "how my week went" in config["prompt"]
        assert len(row["name"]) <= 48

        with pytest.raises(ValueError, match="sniff"):
            create_custom_automation(db, "do something nice for me")
        with pytest.raises(ValueError):
            create_custom_automation(db, "")
    finally:
        db.close()


# -- IPC -----------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _connect(srv: CollieIPCServer):
    ws = await websockets.connect(f"ws://127.0.0.1:{srv.port}")
    json.loads(await ws.recv())  # ready
    return ws


async def _roundtrip(ws, **frame: Any) -> dict:
    await ws.send(json.dumps(frame))
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if reply["type"] in ("ok", "error") and reply.get("id") == frame.get("id"):
            return reply


@pytest.mark.asyncio
async def test_automation_ipc_create_and_delete(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    srv = CollieIPCServer(db, port=_free_port())
    await srv.start()
    try:
        ws = await _connect(srv)
        reply = await _roundtrip(ws, type="create_automation", id="1",
                                 description="every monday at 9am plan my week")
        auto = reply["data"]["automation"]
        assert auto["schedule"] == "Mon 09:00"

        reply = await _roundtrip(ws, type="create_automation", id="2",
                                 description="whenever you feel like it")
        assert reply["type"] == "error"
        assert "sniff" in reply["message"] or "sniff" in str(reply.get("detail"))

        reply = await _roundtrip(ws, type="delete_automation", id="3",
                                 automation_id=auto["id"])
        assert reply["data"]["deleted"] is True

        reply = await _roundtrip(ws, type="delete_automation", id="4",
                                 automation_id="collie-morning-briefing")
        assert reply["type"] == "error"
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_builtin_routine_can_resume_without_plan_review(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    db.add_automation(
        "Evening Wind-Down",
        automation_id="collie-evening-wind-down",
        schedule="21:00",
        action_type="briefing",
        action_config={"prompt": "Help me wind down."},
        enabled=False,
    )
    srv = CollieIPCServer(db, port=_free_port())
    await srv.start()
    try:
        ws = await _connect(srv)
        reply = await _roundtrip(
            ws,
            type="resume_routine",
            id="resume",
            routine_id="collie-evening-wind-down",
        )
        assert reply["type"] == "ok"
        assert reply["data"]["routine"]["enabled"] == 1
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_export_and_clear_ipc(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "home" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "VISION.md").write_text("# Personality\n", encoding="utf-8")

    db = CollieDB(tmp_path / "home" / "collie.db")
    conv = db.create_conversation("hello")
    db.add_message(conv["id"], "user", "hi collie")

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        legacy_oauth_root=tmp_path / "legacy-oauth",
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        reply = await _roundtrip(ws, type="export_data", id="1")
        zip_path = Path(reply["data"]["path"])
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            assert "collie-data.json" in names
            assert "workspace/VISION.md" in names
            data = json.loads(zf.read("collie-data.json"))
            assert len(data["conversations"]) == 1

        reply = await _roundtrip(ws, type="clear_all_data", id="2")
        assert reply["type"] == "error"  # missing confirm

        reply = await _roundtrip(ws, type="clear_all_data", id="3", confirm=True)
        assert reply["data"]["cleared"] is True
        assert reply["data"]["partial"] is False
        assert reply["data"]["database_cleared"] is True
        assert reply["data"]["filesystem_cleared"] is True
        assert reply["data"]["warnings"] == []
        assert db.list_conversations(include_archived=True) == []
        await ws.close()
    finally:
        await srv.stop()
        db.close()
