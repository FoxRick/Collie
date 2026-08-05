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


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    """Poll until ``predicate()`` is truthy (telemetry writes are async)."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


async def _wait_for_turn(db: CollieDB) -> dict[str, Any]:
    """Wait for a finished turn row and return it (most recent first)."""
    await _wait_for(
        lambda: bool(db.list_turn_events())
        and db.list_turn_events()[0]["finished_at"] is not None
    )
    return db.list_turn_events()[0]


async def _wait_for_tool(db: CollieDB) -> dict[str, Any]:
    """Wait for a finished tool row and return it (most recent first)."""
    await _wait_for(
        lambda: bool(db.list_tool_events())
        and db.list_tool_events()[0]["finished_at"] is not None
    )
    return db.list_tool_events()[0]


# ---------------------------------------------------------------------------
# Task 1.1 — schema migration V11 + DB methods
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    from collie_core.telemetry.recorder import RunRecorder

    recorder = RunRecorder.active_for(d)
    if recorder is not None:
        recorder.shutdown()
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
    assert db.schema_version == 14
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
        assert upgraded.schema_version == 14
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


def test_clear_all_drops_queued_writes_and_resumes(db: CollieDB) -> None:
    """Queued telemetry must not resurrect rows after a wipe."""
    from collie_core.telemetry.recorder import RunRecorder

    rec = RunRecorder.for_db(db)
    rec.start_turn(turn_id="t1", turn_kind="chat")  # queued, not yet written

    db.clear_all()

    assert db.list_turn_events() == []

    # The recorder is still usable for new turns after the wipe.
    rec.start_turn(turn_id="t2", turn_kind="chat")
    rec.flush()
    assert [t["id"] for t in db.list_turn_events()] == ["t2"]


def test_export_all_flushes_recorder_first(db: CollieDB) -> None:
    """Export must include records still queued in the writer."""
    from collie_core.telemetry.recorder import RunRecorder

    rec = RunRecorder.for_db(db)
    rec.start_turn(turn_id="t1", turn_kind="chat")

    data = db.export_all()

    assert any(t["id"] == "t1" for t in data["turn_events"])


def test_shutdown_drains_unregisters_and_allows_new_recorder(db: CollieDB) -> None:
    from collie_core.telemetry.recorder import RunRecorder

    rec = RunRecorder.for_db(db)
    rec.start_turn(turn_id="t1", turn_kind="chat")

    rec.shutdown()

    # Drained before stopping — recent evidence is not lost.
    assert any(t["id"] == "t1" for t in db.list_turn_events())
    # Registry entry removed; a fresh, live recorder is handed out next.
    fresh = RunRecorder.for_db(db)
    assert fresh is not rec
    assert fresh._stopped is False


def test_timestamps_captured_at_event_time_not_drain_time(
    db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backlog must not corrupt chronological evidence."""
    import threading
    import time as _time
    from datetime import datetime

    from collie_core.telemetry.recorder import RunRecorder

    blocked = threading.Event()
    original = CollieDB.record_turn_event

    def slow(self, **kwargs: Any) -> None:
        blocked.wait(5)
        original(self, **kwargs)

    monkeypatch.setattr(CollieDB, "record_turn_event", slow)

    rec = RunRecorder(db)
    try:
        rec.start_turn(turn_id="t1", turn_kind="chat")
        _time.sleep(1.0)
        rec.finish_turn(turn_id="t1", status="ok")
        blocked.set()
        rec.flush()

        row = db.list_turn_events()[0]
        start = datetime.fromisoformat(row["started_at"])
        end = datetime.fromisoformat(row["finished_at"])
        # Captured at enqueue time (1s apart), not at drain time (~0 apart).
        assert (end - start).total_seconds() >= 0.9
    finally:
        blocked.set()
        rec.shutdown()


def test_recorder_queue_is_bounded_with_drop_counter(
    db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck database must not accumulate unbounded raw writes in memory."""
    import threading

    from collie_core.telemetry.recorder import MAX_QUEUED_WRITES, RunRecorder

    blocked = threading.Event()
    original = CollieDB.record_turn_event

    def slow(self, **kwargs: Any) -> None:
        blocked.wait(5)
        original(self, **kwargs)

    monkeypatch.setattr(CollieDB, "record_turn_event", slow)

    rec = RunRecorder(db)
    try:
        for i in range(MAX_QUEUED_WRITES + 100):
            rec.start_turn(turn_id=f"t{i}", turn_kind="chat")

        assert rec.dropped_writes >= 90  # drops, never blocks, never grows unbounded
        blocked.set()
        rec.flush()
        assert len(db.list_turn_events()) <= MAX_QUEUED_WRITES
    finally:
        blocked.set()
        rec.shutdown()


def test_export_all_includes_telemetry_and_clear_all_removes_it(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_tool_event(tool_id="te1", turn_id="t1", tool_name="web_search",
                         status="ok", started_at="2026-08-02T00:00:00+00:00")

    data = db.export_all()
    assert len(data["turn_events"]) == 1
    assert len(data["tool_events"]) == 1

    db.clear_all()
    assert db.list_turn_events() == []
    assert db.list_tool_events() == []


def test_finish_turn_preserves_turn_kind_captured_at_start(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="routine", status="running",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_turn_event(turn_id="t1", status="ok",
                         finished_at="2026-08-02T00:00:01+00:00")
    row = db.list_turn_events()[0]
    assert row["turn_kind"] == "routine"
    assert row["status"] == "ok"


def test_finish_tool_preserves_start_timestamp(db: CollieDB) -> None:
    db.record_turn_event(turn_id="t1", turn_kind="chat", status="ok",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_tool_event(tool_id="te1", turn_id="t1", tool_name="web_search",
                         status="running",
                         started_at="2026-08-02T00:00:00+00:00")
    db.record_tool_event(tool_id="te1", turn_id="t1", tool_name="web_search",
                         status="ok",
                         finished_at="2026-08-02T00:00:05+00:00")
    row = db.list_tool_events()[0]
    assert row["started_at"] == "2026-08-02T00:00:00+00:00"
    assert row["finished_at"] == "2026-08-02T00:00:05+00:00"


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


def test_summarize_redacts_secrets_in_strings_and_nested_values() -> None:
    from collie_core.telemetry.recorder import summarize

    # String tool outputs must not store secrets verbatim.
    assert "sk-super-secret" not in (
        summarize("Authorization: Bearer sk-super-secret-1234567890", 500) or ""
    )
    # Dict values whose KEYS are innocent still get value-level sanitizing.
    nested = summarize({"text": "password: hunter2hunter2hunter2"}, 500)
    assert nested is not None
    assert "hunter2hunter2hunter2" not in nested
    assert "ghp_" not in (
        summarize("pat = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ", 500) or ""
    )


def test_summarize_redacts_modern_provider_secrets() -> None:
    from collie_core.telemetry.recorder import summarize

    probes = [
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",          # OpenAI project key
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456789",  # Anthropic key
        "AIzaSyA1234567890abcdefghijklmnopqrstuvwxyz",       # Google API key
        "ya29.a0AfH6SMCabcdefghijklmnopqrstuvwxyz123456",    # Google OAuth token
    ]
    for probe in probes:
        out = summarize(probe, 500) or ""
        assert probe not in out, f"leaked: {probe}"
        assert "[redacted]" in out, f"not redacted: {probe}"


def test_error_messages_are_sanitized(db: CollieDB) -> None:
    from collie_core.telemetry.recorder import RunRecorder

    rec = RunRecorder(db)
    try:
        rec.start_turn(turn_id="t1", turn_kind="chat")
        rec.finish_turn(
            turn_id="t1", status="error",
            error_message="Authorization: Bearer sk-leaky-secret-12345",
        )
        rec.start_tool(tool_id="te1", turn_id="t1", tool_name="web_search")
        rec.finish_tool(
            tool_id="te1", turn_id="t1", tool_name="web_search", status="error",
            error_message="token: ghp_ABCDEFGHIJKLMNOPQRST",
        )
        rec.flush()

        turn = db.list_turn_events()[0]
        tool = db.list_tool_events()[0]
        assert "sk-leaky-secret" not in (turn["error_message"] or "")
        assert "ghp_" not in (tool["error_message"] or "")
    finally:
        rec.shutdown()


def _make_loop(tmp_path: Path, *, hook_factories: list[Any] | None = None) -> AgentLoop:
    from nanobot.providers.base import GenerationSettings

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
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
    await _wait_for_turn(db)

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

    await _wait_for_tool(db)
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
    await _wait_for_turn(db)
    turn = db.list_turn_events()[0]
    assert turn["status"] == "ok"  # tool failure does not fail the turn
    await _wait_for_tool(db)
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

    await _wait_for_turn(db)
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


class _DenyAll:
    async def authorize(self, execution_context, tool_call, tool, params) -> None:
        raise PermissionError("nope")


async def test_process_direct_records_denied_tool_with_action_and_resource(
    tmp_path: Path, db: CollieDB
) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)
    loop.authorizer = _DenyAll()

    result = await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )

    assert result is not None
    await _wait_for_tool(db)
    tools = db.list_tool_events()
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "web_search"
    assert tools[0]["status"] == "denied"
    assert "nope" in (tools[0]["error_message"] or "")
    assert tools[0]["action"] == "web.read"
    assert tools[0]["resource"] == "web_search"

    await _wait_for_turn(db)
    turn = db.list_turn_events()[0]
    assert turn["status"] == "ok"  # denial is soft; turn continues
    assert turn["tool_count"] == 1


async def test_process_direct_marks_routine_turn_kind_from_metadata(
    tmp_path: Path, db: CollieDB
) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)

    result = await loop.process_direct(
        "hello",
        session_key="collie:conv1",
        channel="collie",
        chat_id="conv1",
        permission_context={"origin": "routine", "routine_id": "r1"},
    )

    assert result is not None
    await _wait_for_turn(db)
    turn = db.list_turn_events()[0]
    assert turn["turn_kind"] == "routine"


async def test_process_direct_marks_plan_turn_kind_from_execution_mode(
    tmp_path: Path, db: CollieDB
) -> None:
    from collie_core.telemetry.hook import create_telemetry_hook_factory

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)

    result = await loop.process_direct(
        "hello",
        session_key="collie:conv1",
        channel="collie",
        chat_id="conv1",
        permission_context={"execution_mode": "plan"},
    )

    assert result is not None
    await _wait_for_turn(db)
    turn = db.list_turn_events()[0]
    assert turn["turn_kind"] == "plan"


async def test_max_iterations_turn_marked_stopped(tmp_path: Path, db: CollieDB) -> None:
    from collie_core.telemetry.hook import TelemetryHook
    from collie_core.telemetry.recorder import RunRecorder
    from nanobot.agent.hook import AgentRunHookContext

    rec = RunRecorder(db)
    hook = TelemetryHook(rec, session_key="collie:conv1")
    try:
        await hook.before_run(AgentRunHookContext(messages=[]))
        await hook.after_run(AgentRunHookContext(
            messages=[], stop_reason="max_iterations"
        ))
        rec.flush()
        row = db.list_turn_events()[0]
        assert row["status"] == "stopped"
    finally:
        rec.shutdown()


async def test_subagent_run_composes_telemetry_hook(tmp_path: Path, db: CollieDB) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from collie_core.telemetry.hook import TelemetryHook, create_telemetry_hook_factory
    from nanobot.agent.hook import CompositeHook
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.bus.queue import MessageBus
    from nanobot.utils.llm_runtime import LLMRuntime

    mgr = SubagentManager(
        workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000
    )
    mgr.hook_factories = [create_telemetry_hook_factory(db)]
    mgr.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="ok", messages=[], stop_reason="completed"
    ))
    mgr._announce_result = AsyncMock()

    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    status = SubagentStatus(
        task_id="t1", label="lbl", task_description="task", started_at=0.0
    )
    await mgr._run_subagent(
        "t1", "task", "lbl",
        {"channel": "cli", "chat_id": "direct", "session_key": "cli:direct"},
        status,
        LLMRuntime.capture(provider, "m", context_window_tokens=128_000),
    )

    spec = mgr.runner.run.call_args[0][0]
    assert isinstance(spec.hook, CompositeHook)
    telemetry = [h for h in spec.hook._hooks if isinstance(h, TelemetryHook)]
    assert len(telemetry) == 1
    assert telemetry[0]._session_key == "cli:direct"
    assert telemetry[0]._metadata.get("turn_kind") == "subagent"


async def test_subagent_without_factories_keeps_plain_hook(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from nanobot.agent.hook import CompositeHook
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager, SubagentStatus, _SubagentHook
    from nanobot.bus.queue import MessageBus
    from nanobot.utils.llm_runtime import LLMRuntime

    mgr = SubagentManager(
        workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000
    )
    mgr.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="ok", messages=[], stop_reason="completed"
    ))
    mgr._announce_result = AsyncMock()

    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    status = SubagentStatus(
        task_id="t1", label="lbl", task_description="task", started_at=0.0
    )
    await mgr._run_subagent(
        "t1", "task", "lbl",
        {"channel": "cli", "chat_id": "direct", "session_key": "cli:direct"},
        status,
        LLMRuntime.capture(provider, "m", context_window_tokens=128_000),
    )

    spec = mgr.runner.run.call_args[0][0]
    assert isinstance(spec.hook, _SubagentHook)
    assert not isinstance(spec.hook, CompositeHook)


async def test_telemetry_writes_off_event_loop(
    tmp_path: Path, db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from collie_core.telemetry.hook import create_telemetry_hook_factory

    write_idents: list[int] = []
    original = CollieDB.record_turn_event

    def spy(self, **kwargs: Any) -> None:
        write_idents.append(threading.get_ident())
        original(self, **kwargs)

    monkeypatch.setattr(CollieDB, "record_turn_event", spy)

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)

    await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )
    await _wait_for(lambda: bool(write_idents))

    loop_thread = threading.get_ident()
    assert all(ident != loop_thread for ident in write_idents)


async def test_telemetry_write_stall_does_not_delay_turn(
    tmp_path: Path, db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow/locked database must never delay a turn (fire-and-forget)."""
    import time as _time

    from collie_core.telemetry.hook import create_telemetry_hook_factory

    original = CollieDB.record_turn_event

    def slow(self, **kwargs: Any) -> None:
        _time.sleep(0.5)  # simulate a database under lock contention
        original(self, **kwargs)

    monkeypatch.setattr(CollieDB, "record_turn_event", slow)

    loop = _make_loop(tmp_path, hook_factories=[create_telemetry_hook_factory(db)])
    _fake_tool_turn(loop)

    started = _time.monotonic()
    result = await loop.process_direct(
        "hello", session_key="collie:conv1", channel="collie", chat_id="conv1"
    )
    elapsed = _time.monotonic() - started

    assert result is not None
    assert elapsed < 0.4, f"turn waited {elapsed:.2f}s on a telemetry write"


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
        for i, text in enumerate(["Hi! ", "You said: ", "hello."]):
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

            await _wait_for_turn(db)
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
    await on_stream("Hi!")
    return FakeOutbound("Hi! Here you go.")


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
