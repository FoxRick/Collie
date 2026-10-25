"""Real runtime/IPC/engine persistence boundary, with only the model provider faked."""

import asyncio
import json

import pytest
import websockets
from loguru import logger

from collie_core.db import CollieDB
from collie_core.runtime import CollieRuntime
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class ProbeProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.waiting = asyncio.Event()

    def get_default_model(self):
        return "temporary-test"

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        text = json.dumps(messages)
        if "cancel-secret-9821" in text:
            self.waiting.set()
            await asyncio.Event().wait()
        if "ghost-secret-7612" in text:
            assert not tools  # a model-supplied memory call must still be refused
            if not any(m.get("role") == "tool" for m in messages):
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallRequest(
                            id="memory-attempt",
                            name="remember",
                            arguments={"fact": "ghost-secret-7612"},
                        )
                    ],
                )
            return LLMResponse(content="ghost-secret-7612 reply")
        return LLMResponse(content="ordinary persisted reply")


@pytest.mark.asyncio
async def test_temporary_runtime_persistence_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path))
    db = CollieDB(tmp_path / "collie.db")
    runtime = CollieRuntime(db=db, port=0)
    provider = ProbeProvider()
    monkeypatch.setattr(runtime, "_provider_override", lambda: provider)
    runtime.loop = runtime._build_loop()
    logs = tmp_path / "diagnostics.log"
    sink = logger.add(logs)
    await runtime.ipc.start()
    port = runtime.ipc._server.sockets[0].getsockname()[1]
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.recv()

            async def command(kind, **data):
                await ws.send(json.dumps({"type": kind, "id": kind, **data}))
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if event.get("id") == kind:
                        assert event["type"] == "ok", event
                        return event["data"]

            key = (await command("temporary_open"))["session_id"]
            await command("temporary_turn", session_id=key, content="ghost-secret-7612")
            deltas = []
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if event["type"] == "temporary_delta":
                    deltas.append(event["text"])
                if event["type"] == "temporary_done":
                    assert event["content"] == "ghost-secret-7612 reply"
                    break
            assert "".join(deltas) == "ghost-secret-7612 reply"
            with pytest.raises(ValueError, match="cannot be copied"):
                runtime._session_manager.fork_session_before_user_index(key, "desktop:copy", 1)
            assert runtime._session_manager.temporary[key].messages
            assert not runtime._session_manager.list_sessions()
            assert runtime._session_manager.read_session_file(key) is None
            assert db.list_conversations() == []
            await command("temporary_turn", session_id=key, content="cancel-secret-9821")
            await asyncio.wait_for(provider.waiting.wait(), 10)
            await command("temporary_stop", session_id=key)
            await command("temporary_close", session_id=key)
            assert key not in runtime._session_manager.temporary
            with pytest.raises(ValueError, match="closed"):
                runtime._session_manager.get_or_create(key)

            # An ordinary turn still traverses the existing chat/storage/telemetry path.
            reply = await command("chat", content="ordinary-visible-1234")
            conv_id = reply["conversation_id"]
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if event["type"] == "message" and event["message"]["role"] == "assistant":
                    break
            assert [m["role"] for m in db.get_messages(conv_id)] == ["user", "assistant"]
            runtime._session_manager.flush_all()
            assert runtime._session_manager.list_sessions()

        # Flush enabled durable sinks before scanning SQLite, WAL, JSONL, memory,
        # checkpoints, telemetry, artifact/version/backup directories and diagnostics.
        await runtime._shutdown_loop()
        runtime._flush_telemetry()
        dump = "\n".join(db._conn.iterdump())
        assert "ordinary-visible-1234" in dump
        for secret in ("ghost-secret-7612", "cancel-secret-9821"):
            assert secret not in dump
            for path in tmp_path.rglob("*"):
                if path.is_file():
                    assert secret.encode() not in path.read_bytes(), path
    finally:
        logger.remove(sink)
        await runtime._shutdown_loop()
        await runtime.ipc.stop()
        db.close()
