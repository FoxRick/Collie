"""Tests for Phase 3 subagents: loader, invocation tool, IPC commands."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest
import websockets

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.subagents.loader import (
    STARTERS,
    SubagentLoader,
    bind_subagent_loader,
    draft_system_prompt,
)
from collie_core.tools.subagent import CallSubagentTool


@pytest.fixture()
def loader(tmp_path: Path):
    db = CollieDB(tmp_path / "collie.db")
    ldr = SubagentLoader(tmp_path / "workspace", db)
    yield ldr
    db.close()


# -- loader ------------------------------------------------------------------


def test_create_writes_md_with_frontmatter(loader: SubagentLoader) -> None:
    row = loader.create("Trip Planner", "Plans travel", "You are a travel expert.")
    assert row["name"] == "Trip Planner"
    assert row["filename"] == "trip-planner.md"
    raw = (loader.dir / "trip-planner.md").read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    assert "name: Trip Planner" in raw
    assert raw.rstrip().endswith("You are a travel expert.")


def test_create_generates_prompt_when_missing(loader: SubagentLoader) -> None:
    row = loader.create("Gift Advisor", "finds thoughtful gifts")
    assert "Gift Advisor" in row["system_prompt"]
    assert "finds thoughtful gifts" in row["system_prompt"]


def test_create_rejects_duplicates_and_blank_names(loader: SubagentLoader) -> None:
    loader.create("Coach", "helps")
    with pytest.raises(ValueError, match="already have"):
        loader.create("Coach", "helps again")
    with pytest.raises(ValueError, match="name"):
        loader.create("   ")


def test_sync_discovers_hand_written_files(loader: SubagentLoader) -> None:
    loader.dir.mkdir(parents=True, exist_ok=True)
    (loader.dir / "researcher.md").write_text(
        "---\nname: Researcher\ndescription: Digs deep.\n---\n\nYou research.\n",
        encoding="utf-8",
    )
    (loader.dir / "bare.md").write_text("Just a prompt, no frontmatter.\n", encoding="utf-8")
    rows = loader.sync()
    by_name = {r["name"]: r for r in rows}
    assert by_name["Researcher"]["description"] == "Digs deep."
    assert by_name["Researcher"]["system_prompt"] == "You research."
    assert by_name["Bare"]["system_prompt"] == "Just a prompt, no frontmatter."


def test_sync_removes_rows_for_deleted_files(loader: SubagentLoader) -> None:
    loader.create("Coach", "helps")
    (loader.dir / "coach.md").unlink()
    assert loader.sync() == []


def test_update_and_delete(loader: SubagentLoader) -> None:
    row = loader.create("Coach", "helps")
    updated = loader.update(row["id"], system_prompt="You are a great coach.")
    assert updated["system_prompt"] == "You are a great coach."
    raw = (loader.dir / "coach.md").read_text(encoding="utf-8")
    assert "You are a great coach." in raw
    with pytest.raises(ValueError, match="empty"):
        loader.update(row["id"], system_prompt="   ")
    loader.delete(row["id"])
    assert loader.db.list_subagents() == []
    assert not (loader.dir / "coach.md").exists()


def test_find_is_fuzzy(loader: SubagentLoader) -> None:
    loader.create("Trip Planner", "travel")
    loader.create("Writing Coach", "writing")
    assert loader.find("trip planner")["name"] == "Trip Planner"
    assert loader.find("Trip")["name"] == "Trip Planner"
    assert loader.find("coach")["name"] == "Writing Coach"
    assert loader.find("dogwalker") is None
    assert loader.find("") is None


def test_starters_are_complete() -> None:
    assert len(STARTERS) == 4
    assert {starter["name"] for starter in STARTERS} == {
        "Researcher",
        "Analyst",
        "Reviewer",
        "Operator",
    }
    for starter in STARTERS:
        assert starter["name"] and starter["description"]
        assert len(starter["system_prompt"]) > 50
        assert starter["execution_posture"] in {"read_only", "inherit"}


def test_bundled_agents_seed_once_and_deletion_wins(loader: SubagentLoader) -> None:
    loader.seed_bundled_once()
    rows = loader.sync()
    assert {row["name"] for row in rows} == {
        "Researcher",
        "Analyst",
        "Reviewer",
        "Operator",
    }
    researcher = next(row for row in rows if row["name"] == "Researcher")
    loader.delete(str(researcher["id"]))
    loader.seed_bundled_once()
    assert loader.find("Researcher") is None


def test_draft_prompt_is_dog_free_and_grounded() -> None:
    prompt = draft_system_prompt("Meal Planner", "plans weekly meals.")
    assert "Meal Planner" in prompt
    assert "plans weekly meals" in prompt


# -- invocation tool ------------------------------------------------------------


class FakeManager:
    def __init__(self, running: int = 0, limit: int = 3):
        self.running = running
        self.max_concurrent_subagents = limit
        self.spawned: list[dict[str, Any]] = []

    def get_running_count(self) -> int:
        return self.running

    async def spawn(self, **kwargs: Any) -> str:
        self.spawned.append(kwargs)
        return f"Subagent [{kwargs['label']}] started."


def _bind_request_ctx():
    from nanobot.agent.tools.context import RequestContext, bind_request_context

    return bind_request_context(
        RequestContext(
            channel="collie",
            chat_id="c1",
            session_key="collie:c1",
            runtime=object(),  # type: ignore[arg-type]
        )
    )


async def test_call_subagent_spawns_with_prompt(loader: SubagentLoader) -> None:
    loader.create("Trip Planner", "travel", "You are a travel expert.")
    bind_subagent_loader(loader)
    manager = FakeManager()
    tool = CallSubagentTool(manager)
    token = _bind_request_ctx()
    try:
        result = await tool.execute(name="trip planner", task="Plan Barcelona")
    finally:
        from nanobot.agent.tools.context import reset_request_context

        reset_request_context(token)
        bind_subagent_loader(None)
    assert "started" in result
    spawn = manager.spawned[0]
    assert spawn["label"] == "Trip Planner"
    assert "You are a travel expert." in spawn["task"]
    assert "Plan Barcelona" in spawn["task"]
    assert spawn["session_key"] == "collie:c1"
    assert spawn["execution_posture"] == "read_only"


async def test_call_subagent_inherits_parent_workspace_scope(
    loader: SubagentLoader, tmp_path: Path
) -> None:
    from nanobot.security.workspace_access import (
        bind_workspace_scope,
        build_workspace_scope,
        reset_workspace_scope,
    )

    project = tmp_path / "selected-project"
    project.mkdir()
    scope = build_workspace_scope(project, "restricted", source_channel="collie")
    loader.create("Reviewer", "reviews", "Review only what is in scope.")
    bind_subagent_loader(loader)
    manager = FakeManager()
    tool = CallSubagentTool(manager)
    request_token = _bind_request_ctx()
    workspace_token = bind_workspace_scope(scope)
    try:
        result = await tool.execute(name="Reviewer", task="Review this project")
    finally:
        reset_workspace_scope(workspace_token)
        from nanobot.agent.tools.context import reset_request_context

        reset_request_context(request_token)
        bind_subagent_loader(None)

    assert "started" in result
    inherited = manager.spawned[0]["workspace_scope"]
    assert inherited is scope
    assert inherited.project_path == project.resolve()
    assert inherited.access_mode == "restricted"
    assert inherited.restrict_to_workspace is True


async def test_call_subagent_unknown_name_lists_helpers(loader: SubagentLoader) -> None:
    loader.create("Writing Coach", "writing")
    bind_subagent_loader(loader)
    tool = CallSubagentTool(FakeManager())
    token = _bind_request_ctx()
    try:
        result = await tool.execute(name="Chef", task="cook")
    finally:
        from nanobot.agent.tools.context import reset_request_context

        reset_request_context(token)
        bind_subagent_loader(None)
    assert "Writing Coach" in result


async def test_call_subagent_respects_concurrency(loader: SubagentLoader) -> None:
    loader.create("Coach", "helps")
    bind_subagent_loader(loader)
    tool = CallSubagentTool(FakeManager(running=3, limit=3))
    token = _bind_request_ctx()
    try:
        result = await tool.execute(name="Coach", task="go")
    finally:
        from nanobot.agent.tools.context import reset_request_context

        reset_request_context(token)
        bind_subagent_loader(None)
    assert "helpers are busy right now" in result


# -- IPC ------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _connect(srv: CollieIPCServer):
    ws = await websockets.connect(f"ws://127.0.0.1:{srv.port}")
    ready = json.loads(await ws.recv())
    assert ready["type"] == "ready"
    return ws


async def _roundtrip(ws, **frame: Any) -> dict:
    await ws.send(json.dumps(frame))
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if reply["type"] in ("ok", "error") and reply.get("id") == frame.get("id"):
            return reply


@pytest.mark.asyncio
async def test_subagent_ipc_crud(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    loader = SubagentLoader(tmp_path / "ws", db)

    async def fake_writer(name: str, description: str) -> str:
        return f"You are {name}, written by the model. {description}"

    srv = CollieIPCServer(db, port=_free_port(), subagent_loader=loader, prompt_writer=fake_writer)
    await srv.start()
    try:
        ws = await _connect(srv)

        reply = await _roundtrip(ws, type="list_subagents", id="1")
        assert reply["data"]["subagents"] == []
        assert len(reply["data"]["starters"]) == 4

        reply = await _roundtrip(
            ws, type="create_subagent", id="2", name="Trip Planner", description="plans trips"
        )
        sub = reply["data"]["subagent"]
        assert reply["data"]["prompt_written_by_collie"] is True
        assert "written by the model" in sub["system_prompt"]

        reply = await _roundtrip(
            ws,
            type="update_subagent",
            id="3",
            subagent_id=sub["id"],
            system_prompt="You plan trips carefully.",
            execution_posture="inherit",
        )
        assert reply["data"]["subagent"]["system_prompt"] == "You plan trips carefully."
        assert reply["data"]["subagent"]["execution_posture"] == "inherit"

        reply = await _roundtrip(ws, type="create_subagent", id="4", name="")
        assert reply["type"] == "error"

        reply = await _roundtrip(ws, type="delete_subagent", id="5", subagent_id=sub["id"])
        assert reply["data"]["deleted"] is True

        reply = await _roundtrip(ws, type="list_subagents", id="6")
        assert reply["data"]["subagents"] == []
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_subagent_ipc_prompt_writer_failure_falls_back(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    loader = SubagentLoader(tmp_path / "ws", db)

    async def broken_writer(name: str, description: str) -> str:
        raise RuntimeError("model asleep")

    srv = CollieIPCServer(
        db, port=_free_port(), subagent_loader=loader, prompt_writer=broken_writer
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        reply = await _roundtrip(
            ws, type="create_subagent", id="1", name="Coach", description="coaches writing"
        )
        sub = reply["data"]["subagent"]
        assert reply["data"]["prompt_written_by_collie"] is False
        assert "Coach" in sub["system_prompt"]
        assert "coaches writing" in sub["system_prompt"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()
