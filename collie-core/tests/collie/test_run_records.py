"""PR 1 — Run records (telemetry foundation).

Covers the Gardener Foundations plan PR 1:
- Task 1.1: _SCHEMA_V11 (turn_events + tool_events) and CollieDB methods.
- Task 1.2: RunRecorder + TelemetryHook wired through AgentLoop.process_direct.
- Task 1.3: IPC get_run_records / get_tool_events round-trips.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets

import collie_core.db as db_mod
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Task 1.1 — schema migration V11 + DB methods
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    d.close()


def _table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def test_schema_v11_tables_created_on_fresh_db(db: CollieDB) -> None:
    assert db.schema_version == 11
    tables = _table_names(db.path)
    assert {"turn_events", "tool_events"} <= tables


def test_v10_db_upgrades_to_v11_preserving_data(tmp_path: Path) -> None:
    path = tmp_path / "v10.db"
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:10]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (10)")
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) "
        "VALUES ('c1', 'Keep me', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    upgraded = CollieDB(path)
    try:
        assert upgraded.schema_version == 11
        assert upgraded.get_conversation("c1")["title"] == "Keep me"
    finally:
        upgraded.close()


def test_turn_event_round_trip(db: CollieDB) -> None:
    db.record_turn_event(
        turn_id="t1",
        conversation_id="conv1",
        session_key="collie:conv1",
        turn_kind="chat",
        provider="custom",
        model="collie-test-model",
        status="ok",
        error_message=None,
        tokens_in=10,
        tokens_out=5,
        latency_ms=12,
        tool_count=1,
        started_at="2026-08-02T00:00:00+00:00",
        finished_at="2026-08-02T00:00:05+00:00",
    )

    turns = db.list_turn_events()
    assert len(turns) == 1
    row = turns[0]
    assert row["id"] == "t1"
    assert row["conversation_id"] == "conv1"
    assert row["session_key"] == "collie:conv1"
    assert row["turn_kind"] == "chat"
    assert row["provider"] == "custom"
    assert row["model"] == "collie-test-model"
    assert row["status"] == "ok"
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 5
    assert row["latency_ms"] == 12
    assert row["tool_count"] == 1


def test_record_turn_event_upserts_instead_of_duplicating(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="running",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         tokens_in=7, tokens_out=3, finished_at="2026-08-02T00:00:01+00:00")
    turns = db.list_turn_events()
    assert len(turns) == 1
    assert turns[0]["status"] == "ok"
    assert turns[0]["tokens_in"] == 7
    assert turns[0]["started_at"] == "2026-08-02T00:00:00+00:00"


def test_list_turn_events_filters_by_conversation_since_and_limit(db: CollieDB) -> None:
    for i in range(5):
        db.record_turn_event(
            turn_id=f"t{i}",
            conversation_id=f"conv{i % 2}",
            session_key=f"collie:conv{i % 2}",
            turn_kind="chat",
            status="ok",
            started_at=f"2026-08-0{i + 1}T00:00:00+00:00",
        )

    only_conv0 = db.list_turn_events(conversation_id="conv0")
    assert [t["id"] for t in only_conv0] == ["t4", "t2", "t0"]

    since = db.list_turn_events(since="2026-08-03T00:00:00+00:00")
    assert [t["id"] for t in since] == ["t4", "t3", "t2"]

    limited = db.list_turn_events(limit=2)
    assert len(limited) == 2

    by_session = db.list_turn_events(session_key="collie:conv0")
    assert len(by_session) == 3


def test_tool_event_round_trip_and_filters(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_tool_event(
        tool_id="te1", turn_id="t1", tool_name="web_search",
        action="read", resource="https://example.com",
        input_summary='{"query": "hello"}',
        output_summary="three results",
        status="ok",
        latency_ms=4,
        started_at="2026-08-02T00:00:01+00:00",
        finished_at="2026-08-02T00:00:02+00:00",
    )

    events = db.list_tool_events()
    assert len(events) == 1
    row = events[0]
    assert row["id"] == "te1"
    assert row["turn_id"] == "t1"
    assert row["tool_name"] == "web_search"
    assert row["action"] == "read"
    assert row["input_summary"] == '{"query": "hello"}'
    assert row["status"] == "ok"
    assert row["latency_ms"] == 4

    assert len(db.list_tool_events(turn_id="t1")) == 1
    assert len(db.list_tool_events(turn_id="nope")) == 0
    assert len(db.list_tool_events(tool_name="web_search")) == 1
    assert len(db.list_tool_events(tool_name="other")) == 0


def test_turn_event_stats_reports_per_tool_failures(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         started_at="2026-08-02T00:00:00+00:00")
    for i, (tool, status) in enumerate([
        ("web_search", "ok"),
        ("web_search", "error"),
        ("web_search", "error"),
        ("write_file", "error"),
        ("write_file", "denied"),
    ]):
        db.record_tool_event(
            tool_id=f"te{i}", turn_id="t1", tool_name=tool, status=status,
            started_at="2026-08-02T00:00:00+00:00",
        )

    stats = db.turn_event_stats(since="2026-08-01T00:00:00+00:00")
    by_tool_status = {(s["tool_name"], s["status"]): s["count"] for s in stats}
    assert by_tool_status[("web_search", "error")] == 2
    assert by_tool_status[("web_search", "ok")] == 1
    assert by_tool_status[("write_file", "error")] == 1
    assert by_tool_status[("write_file", "denied")] == 1

    assert db.turn_event_stats(since="2099-01-01T00:00:00+00:00") == []


def test_tool_events_cascade_delete_with_turn(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_tool_event(tool_id="te1", turn_id="t1", tool_name="web_search",
                         status="ok", started_at="2026-08-02T00:00:00+00:00")
    assert len(db.list_tool_events()) == 1

    with db._write() as conn:
        conn.execute("DELETE FROM turn_events WHERE id = 't1'")
    assert len(db.list_tool_events()) == 0


# ---------------------------------------------------------------------------
# Task 1.2 — RunRecorder + TelemetryHook through AgentLoop.process_direct
# ---------------------------------------------------------------------------


def test_summarize_redacts_secrets_and_truncates() -> None:
    from collie_core.telemetry.recorder import summarize

    text = summarize(
        {"query": "hello world", "api_key": "sk-super-secret"},
        limit=500,
    )
    assert text is not None
    assert "hello world" in text
    assert "[redacted]" in text
    assert "sk-super-secret" not in text

    long = summarize("x" * 5000, limit=500)
    assert long is not None
    assert len(long) <= 500

    assert summarize(None, limit=500) is None


def _make_loop(tmp_path: Path, *, hook_factories: list[Any] | None = None) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=8192,
        hook_factories=list(hook_factories or []),
    )


def _fake_tool_turn(loop: AgentLoop, *, tool_error: bool = False) -> None:
    calls = iter([
        LLMResponse(content="Visible", tool_calls=[
            ToolCallRequest(id="call1", name="web_search",
                            arguments={"query": "hello"}),
        ]),
        LLMResponse(content="Done", tool_calls=[]),
    ])

    async def chat_with_retry(*_args: Any, **_kwargs: Any) -> LLMResponse:
        return next(calls)

    loop.provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.tools.prepare_call = MagicMock(return_value=(
        None, {"query": "hello", "api_key": "sk-top-secret"}, None,
    ))
    if tool_error:
        loop.tools.execute = AsyncMock(side_effect=RuntimeError("tool boom"))
    else:
        loop.tools.execute = AsyncMock(return_value="found 3 results")


async def test_process_direct_records_turn_and_tools_with_redaction(
    tmp_path: Path, db: CollieDB
) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)

    result = await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )

    assert result is not None
    assert result.content == "Done"

    turns = db.list_turn_events()
    assert len(turns) == 1
    turn = turns[0]
    assert turn["status"] == "ok"
    assert turn["turn_kind"] == "chat"
    assert turn["conversation_id"] == "conv1"
    assert turn["session_key"] == "collie:conv1"
    assert turn["tool_count"] == 1
    assert turn["tokens_in"] > 0  # engine-reported prompt usage
    assert turn["tokens_out"] > 0
    assert turn["finished_at"] is not None

    tools = db.list_tool_events(turn_id=turn["id"])
    assert len(tools) == 1
    tool = tools[0]
    assert tool["tool_name"] == "web_search"
    assert tool["status"] == "ok"
    assert "sk-top-secret" not in (tool["input_summary"] or "")
    assert "[redacted]" in (tool["input_summary"] or "")
    assert "found 3 results" in (tool["output_summary"] or "")


async def test_process_direct_records_tool_error(tmp_path: Path, db: CollieDB) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop, tool_error=True)

    result = await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )

    assert result is not None
    turn = db.list_turn_events()[0]
    assert turn["status"] == "ok"  # tool failure does not fail the turn
    tool = db.list_tool_events(turn_id=turn["id"])[0]
    assert tool["status"] == "error"
    assert "tool boom" in (tool["error_message"] or "")


async def test_process_direct_records_error_turn(tmp_path: Path, db: CollieDB) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])

    async def explode(*_args: Any, **_kwargs: Any) -> LLMResponse:
        raise RuntimeError("model exploded")

    loop.provider.chat_with_retry = AsyncMock(side_effect=explode)

    with pytest.raises(RuntimeError, match="model exploded"):
        await loop.process_direct(
            "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
        )

    turns = db.list_turn_events()
    assert len(turns) == 1
    assert turns[0]["status"] == "error"
    assert "model exploded" in (turns[0]["error_message"] or "")


async def test_telemetry_failure_never_breaks_turn(tmp_path: Path, db: CollieDB) -> None:
    from nanobot.agent.hook import AgentHook, AgentRunHookContext

    class ExplodingHook(AgentHook):
        async def before_run(self, context: AgentRunHookContext) -> None:
            raise RuntimeError("telemetry broke")

    def factory(_context: Any) -> AgentHook:
        return ExplodingHook()

    loop = _make_loop(tmp_path, hook_factories=[factory])
    _fake_tool_turn(loop)

    result = await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )

    assert result is not None
    assert result.content == "Done"


async def test_runtime_records_chat_turn_via_build_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full CollieRuntime boot: a real chat turn lands in turn_events.

    Proves the ``_build_loop`` wiring (hook_factories registration) end to
    end, exactly the way the renderer drives the core.
    """
    from aiohttp import web

    from collie_core.runtime import CollieRuntime

    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))

    async def chat_completions(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body.get("model")
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for i, text in enumerate(["Woof! ", "You said: ", "hello."]):
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{
                    "index": 0,
                    "delta": ({"role": "assistant", "content": text}
                              if i == 0 else {"content": text}),
                    "finish_reason": None,
                }],
            }
            await resp.write(f"data: {json.dumps(payload)}\n\n".encode())
        done = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "model": body["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6,
                      "total_tokens": 26},
        }
        await resp.write(f"data: {json.dumps(done)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    llm_port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", llm_port)
    await site.start()

    ipc_port = _free_port()
    db = CollieDB(tmp_path / ".collie" / "collie.db")
    db.set_setting("provider.name", "custom")
    db.set_setting("provider.api_base", f"http://127.0.0.1:{llm_port}/v1")
    db.set_setting("provider.model", "collie-test-model")
    db.upsert_provider("custom", name="Test", auth_type="api_key", is_default=True)

    runtime = CollieRuntime(port=ipc_port, db=db)
    await runtime.ipc.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{ipc_port}") as ws:
            ready = json.loads(await ws.recv())
            assert ready["type"] == "ready"

            await ws.send(json.dumps({
                "type": "set_api_key", "id": "k",
                "provider": "custom", "key": "sk-fake",
            }))
            assert json.loads(await ws.recv())["type"] == "ok"

            await ws.send(json.dumps({"type": "configure", "id": "c"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "ok", reply

            await ws.send(json.dumps({
                "type": "chat", "id": "m1", "content": "hello",
            }))
            assistant = None
            for _ in range(200):
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if (frame["type"] == "message"
                        and frame["message"]["role"] == "assistant"):
                    assistant = frame["message"]
                    break
                if frame["type"] == "error":
                    pytest.fail(f"IPC error: {frame}")
            assert assistant is not None

            turns = db.list_turn_events()
            assert len(turns) == 1
            assert turns[0]["turn_kind"] == "chat"
            assert turns[0]["status"] == "ok"
            assert turns[0]["provider"] == "custom"
            assert turns[0]["conversation_id"] is not None
    finally:
        await runtime._shutdown_loop()
        await runtime.ipc.stop()
        db.close()
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Task 1.3 — IPC surface (read-only, additive)
# ---------------------------------------------------------------------------


class FakeOutbound:
    def __init__(self, content: str) -> None:
        self.content = content


async def fake_chat_runner(content: str, *, conversation_id: str, on_stream, on_progress):
    await on_progress("", tool_events=[{"phase": "start", "name": "web_search"}])
    await on_stream("Woof!")
    return FakeOutbound("Woof! Here you go.")


async def test_ipc_get_run_records_and_tool_events_round_trip(
    tmp_path: Path,
) -> None:
    d = CollieDB(tmp_path / "collie.db")
    d.record_turn_event(
        turn_id="t1", conversation_id="conv1", session_key="collie:conv1",
        turn_kind="chat", status="ok", tokens_in=3, tokens_out=2,
        started_at="2026-08-02T00:00:00+00:00",
        finished_at="2026-08-02T00:00:01+00:00",
    )
    d.record_tool_event(
        tool_id="te1", turn_id="t1", tool_name="web_search", status="ok",
        input_summary='{"query": "hello"}',
        started_at="2026-08-02T00:00:00+00:00",
        finished_at="2026-08-02T00:00:01+00:00",
    )
    srv = CollieIPCServer(
        d,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        on_set_api_key=lambda provider, key: None,
        status_provider=lambda: {"configured": True, "model": "test-model"},
    )
    await srv.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as ws:
            ready = json.loads(await ws.recv())
            assert ready["type"] == "ready"

            await ws.send(json.dumps({
                "type": "get_run_records", "id": "r1",
                "conversation_id": "conv1",
            }))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "ok"
            turns = reply["data"]["turns"]
            assert len(turns) == 1
            assert turns[0]["id"] == "t1"
            assert turns[0]["status"] == "ok"

            await ws.send(json.dumps({
                "type": "get_tool_events", "id": "r2", "turn_id": "t1",
            }))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "ok"
            events = reply["data"]["tool_events"]
            assert len(events) == 1
            assert events[0]["tool_name"] == "web_search"
            assert events[0]["status"] == "ok"

            # Unknown commands stay rejected (additive-only surface).
            await ws.send(json.dumps({"type": "no_such_command", "id": "r3"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "error"
    finally:
        await srv.stop()
        d.close()
