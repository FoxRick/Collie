"""Tests for subagent progress visibility (buddy state + background delivery)."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.runtime import CollieRuntime
from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeOutbound:
    def __init__(self, content: str):
        self.content = content


async def _quiet_chat_runner(content, *, conversation_id, on_stream, on_progress):
    return FakeOutbound("I've called in my buddy — back soon!")


@pytest.mark.asyncio
async def test_chat_turn_ends_in_buddy_state_while_subagent_runs(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=_quiet_chat_runner,
        subagents_running=lambda conv_id: 1,
    )
    await srv.start()
    try:
        ws = await websockets.connect(f"ws://127.0.0.1:{srv.port}")
        json.loads(await ws.recv())  # ready
        await ws.send(json.dumps({"type": "chat", "id": "1", "content": "plan a trip"}))

        states: list[str] = []
        assistant = None
        while assistant is None:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if frame["type"] == "thinking":
                states.append(frame["state"])
            elif (frame["type"] == "message"
                  and frame["message"]["role"] == "assistant"):
                assistant = frame["message"]
        assert states[-1] == "buddy"
        assert "done" not in states
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_outbound_consumer_delivers_subagent_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    db = CollieDB(tmp_path / ".collie" / "collie.db")
    runtime = CollieRuntime(port=_free_port(), db=db)

    conv = db.create_conversation("trip")
    bus = MessageBus()
    runtime.loop = SimpleNamespace(
        bus=bus,
        subagents=SimpleNamespace(get_running_count_by_session=lambda key: 0),
    )

    broadcasts: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> None:
        broadcasts.append(payload)

    monkeypatch.setattr(runtime.ipc, "broadcast", capture)

    task = asyncio.create_task(runtime._consume_outbound())
    try:
        # A message for another channel is ignored
        await bus.publish_outbound(OutboundMessage(
            channel="telegram", chat_id="x", content="nope",
        ))
        # A message for a deleted conversation is ignored
        await bus.publish_outbound(OutboundMessage(
            channel="collie", chat_id="gone", content="nope",
        ))
        # The subagent result lands in the conversation
        await bus.publish_outbound(OutboundMessage(
            channel="collie", chat_id=conv["id"],
            content="Trip Planner here — Barcelona is sorted!",
        ))
        for _ in range(100):
            if any(b.get("type") == "message" for b in broadcasts):
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    msgs = db.get_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["assistant"]
    assert "Barcelona" in msgs[0]["content"]

    message_events = [b for b in broadcasts if b.get("type") == "message"]
    assert len(message_events) == 1
    assert message_events[0]["conversation_id"] == conv["id"]
    thinking_events = [b for b in broadcasts if b.get("type") == "thinking"]
    assert thinking_events[-1]["state"] == "done"
    assert db.list_conversations()[0]["title"] == "trip"

    db.close()


@pytest.mark.asyncio
async def test_outbound_consumer_keeps_buddy_state_when_more_running(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    db = CollieDB(tmp_path / ".collie" / "collie.db")
    runtime = CollieRuntime(port=_free_port(), db=db)
    conv = db.create_conversation("trip")
    bus = MessageBus()
    runtime.loop = SimpleNamespace(
        bus=bus,
        subagents=SimpleNamespace(get_running_count_by_session=lambda key: 2),
    )

    broadcasts: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> None:
        broadcasts.append(payload)

    monkeypatch.setattr(runtime.ipc, "broadcast", capture)
    task = asyncio.create_task(runtime._consume_outbound())
    try:
        await bus.publish_outbound(OutboundMessage(
            channel="collie", chat_id=conv["id"], content="First buddy done!",
        ))
        for _ in range(100):
            if any(b.get("type") == "thinking" for b in broadcasts):
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    thinking_events = [b for b in broadcasts if b.get("type") == "thinking"]
    assert thinking_events[-1]["state"] == "buddy"
    db.close()


@pytest.mark.asyncio
async def test_shutdown_freezes_intake_before_queued_messenger_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued messenger turn cannot spawn after shutdown snapshots children."""
    home = tmp_path / ".collie"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    db = CollieDB(home / "collie.db")
    runtime = CollieRuntime(port=_free_port(), db=db)

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=runtime.workspace,
        model="test-model",
    )
    loop._connect_mcp = AsyncMock()
    loop.close_mcp = AsyncMock()
    loop._dispatch = AsyncMock()
    runtime.loop = loop
    runtime._loop_task = asyncio.create_task(loop.run())

    await loop.bus.publish_inbound(
        InboundMessage(
            channel="slack",
            sender_id="U123",
            chat_id="D123",
            content="queued just before shutdown",
            session_key_override="slack:D123:1712345678.000100",
        )
    )
    await runtime._shutdown_loop()

    loop._dispatch.assert_not_awaited()
    assert loop._active_tasks == {}
    assert runtime._loop_task is None
    assert runtime.loop is None
    db.close()


@pytest.mark.asyncio
async def test_outbound_consumer_artifact_event_reaches_messenger_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    """ArtifactEvent on a messenger channel goes to the channel queue, not IPC.

    The normie text fallback (``📎 Made: …``) is the messenger UX for things;
    intercepting the event before the channel dispatch would swallow it.
    """
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    db = CollieDB(tmp_path / ".collie" / "collie.db")
    runtime = CollieRuntime(port=_free_port(), db=db)
    bus = MessageBus()
    runtime.loop = SimpleNamespace(
        bus=bus,
        subagents=SimpleNamespace(get_running_count_by_session=lambda key: 0),
    )

    dispatched: list[Any] = []
    broadcasts: list[dict[str, Any]] = []

    async def capture_dispatch(msg: Any) -> bool:
        dispatched.append(msg)
        return True

    async def capture_broadcast(payload: dict[str, Any]) -> None:
        broadcasts.append(payload)

    monkeypatch.setattr(runtime.messengers, "dispatch", capture_dispatch)
    monkeypatch.setattr(runtime.ipc, "broadcast", capture_broadcast)

    from nanobot.bus.outbound_events import ArtifactEvent, outbound_message_for_event

    task = asyncio.create_task(runtime._consume_outbound())
    try:
        # Messenger channel: must reach messengers.dispatch (text fallback).
        await bus.publish_outbound(
            outbound_message_for_event(
                channel="telegram",
                chat_id="12345",
                event=ArtifactEvent(
                    artifact_id="th_abc",
                    title="Dog walk flyer",
                    kind="image",
                    file_path="/tmp/flyer.png",
                    size_bytes=8,
                    created_at=1.0,
                ),
                content="📎 Made: Dog walk flyer · Open",
            )
        )
        # Collie channel: must become an IPC artifact broadcast, never a chat
        # bubble (the model mentions the thing in its own reply).
        conv = db.create_conversation("trip")
        await bus.publish_outbound(
            outbound_message_for_event(
                channel="collie",
                chat_id=conv["id"],
                event=ArtifactEvent(
                    artifact_id="th_def",
                    title="Trip notes",
                    kind="document",
                    file_path="/tmp/notes.md",
                    size_bytes=8,
                    created_at=1.0,
                ),
                content="📎 Made: Trip notes · Open",
            )
        )
        for _ in range(100):
            if dispatched and any(b.get("type") == "artifact" for b in broadcasts):
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert len(dispatched) == 1
    assert dispatched[0].channel == "telegram"
    assert dispatched[0].content == "📎 Made: Dog walk flyer · Open"

    artifacts = [b for b in broadcasts if b.get("type") == "artifact"]
    assert len(artifacts) == 1
    assert artifacts[0]["conversation_id"] == conv["id"]
    assert artifacts[0]["artifact"]["title"] == "Trip notes"
    # No chat bubble for the collie artifact (skip the duplicate).
    message_events = [b for b in broadcasts if b.get("type") == "message"]
    assert message_events == []
    db.close()
