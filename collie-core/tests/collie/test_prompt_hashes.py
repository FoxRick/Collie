"""Prompt-hash telemetry (evaluation lab enabler, Part B).

Covers ``collie_core/telemetry/prompt_hashes.py`` pure functions, the
schema V12 migration (additive NULL columns), and the end-to-end wiring:
a real turn records non-NULL prompt/tool-schema/config hashes on its run
record row.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web

import collie_core.db as db_mod
from collie_core.db import CollieDB
from collie_core.telemetry.prompt_hashes import (
    hash_config,
    hash_system_prompt,
    hash_tool_schema,
)

# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def test_prompt_hash_changes_with_template(tmp_path: Path) -> None:
    """Hashing the rendered system prompt is sensitive to template changes."""
    from nanobot.agent.context import ContextBuilder

    def build(agents_text: str) -> str:
        (tmp_path / "VISION.md").write_text("# Vision\n\nSame vision.\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text(agents_text, encoding="utf-8")
        builder = ContextBuilder(tmp_path)
        return builder.build_system_prompt()

    baseline = build("Instructions: be helpful.")
    changed = build("Instructions: be VERY helpful.")
    assert baseline != changed
    assert hash_system_prompt([baseline]) != hash_system_prompt([changed])
    # Deterministic: identical input hashes identically.
    assert hash_system_prompt([baseline]) == hash_system_prompt([baseline])


def test_tool_schema_hash_stable() -> None:
    a = [{"name": "read_file", "parameters": {"type": "object"}}]
    b = [{"name": "write_file", "parameters": {"type": "object"}}]
    assert hash_tool_schema(a) == hash_tool_schema([dict(a[0])])
    assert hash_tool_schema(a) != hash_tool_schema(b)
    assert hash_tool_schema([]) != hash_tool_schema(a)


def test_config_hash_stable() -> None:
    generation = {"temperature": 0.1, "max_tokens": 8192}
    limits = {"max_tool_iterations": 50, "context_window_tokens": 200_000}
    assert hash_config("deepseek-chat", "deepseek", generation, limits) == (
        hash_config("deepseek-chat", "deepseek", dict(generation), dict(limits))
    )
    assert hash_config("deepseek-chat", "deepseek", generation, limits) != (
        hash_config("deepseek-chat", "deepseek", generation, {"max_tool_iterations": 51})
    )


# ---------------------------------------------------------------------------
# Schema V12 migration
# ---------------------------------------------------------------------------


def _build_db_at_version(path: Path, version: int) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:version]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    conn.close()


def test_v12_adds_hash_columns_on_fresh_db(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    try:
        assert db.schema_version == 14
        columns = {row["name"] for row in db._rows("PRAGMA table_info(turn_events)")}
        assert {"prompt_hash", "tool_schema_hash", "config_hash"} <= columns
    finally:
        db.close()


def test_old_rows_null_hashes(tmp_path: Path) -> None:
    """Pre-migration turn rows read back with NULL hashes and upsert works."""
    path = tmp_path / "v11.db"
    _build_db_at_version(path, 11)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO turn_events (id, turn_kind, status, started_at) "
        "VALUES ('old1', 'chat', 'ok', '2026-08-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    upgraded = CollieDB(path)
    try:
        assert upgraded.schema_version == 14
        rows = upgraded.list_turn_events()
        assert len(rows) == 1
        assert rows[0]["prompt_hash"] is None
        assert rows[0]["tool_schema_hash"] is None
        assert rows[0]["config_hash"] is None

        # Upsert still works and can now carry hashes.
        upgraded.record_turn_event(
            turn_id="old1",
            turn_kind="chat",
            status="ok",
            prompt_hash="sha256:aaa",
            tool_schema_hash="sha256:bbb",
            config_hash="sha256:ccc",
            finished_at="2026-08-01T00:00:05+00:00",
        )
        rows = upgraded.list_turn_events()
        assert rows[0]["prompt_hash"] == "sha256:aaa"
        assert rows[0]["tool_schema_hash"] == "sha256:bbb"
        assert rows[0]["config_hash"] == "sha256:ccc"
        assert rows[0]["turn_kind"] == "chat"
    finally:
        upgraded.close()


def test_start_turn_hashes_not_clobbered_by_finish(tmp_path: Path) -> None:
    """finish_turn (no hash kwargs) must not wipe hashes set at start."""
    db = CollieDB(tmp_path / "collie.db")
    try:
        db.record_turn_event(
            turn_id="t1",
            turn_kind="chat",
            status="running",
            prompt_hash="sha256:start",
            tool_schema_hash="sha256:tools",
            config_hash="sha256:cfg",
            started_at="2026-08-02T00:00:00+00:00",
        )
        db.record_turn_event(
            turn_id="t1",
            turn_kind="chat",
            status="ok",
            tokens_in=5,
            finished_at="2026-08-02T00:00:01+00:00",
        )
        row = db.list_turn_events()[0]
        assert row["prompt_hash"] == "sha256:start"
        assert row["tool_schema_hash"] == "sha256:tools"
        assert row["config_hash"] == "sha256:cfg"
        assert row["status"] == "ok"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# End-to-end: a real turn records non-NULL hashes
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _fake_openai_app() -> web.Application:
    async def chat_completions(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            await resp.write(f"data: {json.dumps(payload)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


@pytest.mark.asyncio
async def test_run_record_has_hash_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real turn through the runtime records non-NULL hashes."""
    import collie_core.headless as headless
    from collie_core.telemetry.recorder import RunRecorder

    monkeypatch.setenv("COLLIE_BENCH_KEY", "sk-bench-secret-0123456789abcdef")
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    llm_port = _free_port()
    runner = web.AppRunner(await _fake_openai_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", llm_port)
    await site.start()
    try:
        exit_code, document = await headless.run_one(
            headless._parse_args(
                [
                    "--task=hello",
                    f"--home={tmp_path / 'bench-home'}",
                    "--model=collie-test-model",
                    "--provider=custom",
                    f"--api-base=http://127.0.0.1:{llm_port}/v1",
                    "--api-key-env=COLLIE_BENCH_KEY",
                ]
            )
        )
    finally:
        await runner.cleanup()

    assert exit_code == 0
    assert document["exit_state"] == "ok"
    for key in ("prompt_hash", "tool_schema_hash", "config_hash"):
        assert document[key] and document[key].startswith("sha256:")

    # The run record row itself carries the hashes (not just the doc).
    db = CollieDB(tmp_path / "bench-home" / "collie.db")
    try:
        rows = db.list_turn_events(conversation_id=document["conversation_id"])
        assert rows, "expected a turn record"
        row = rows[0]
        assert row["prompt_hash"] == document["prompt_hash"]
        assert row["tool_schema_hash"] == document["tool_schema_hash"]
        assert row["config_hash"] == document["config_hash"]
        assert row["finished_at"] is not None
    finally:
        recorder = RunRecorder.active_for(db)
        if recorder is not None:
            recorder.shutdown()
        db.close()
