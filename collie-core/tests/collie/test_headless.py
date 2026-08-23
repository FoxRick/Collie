"""Headless engine mode (evaluation lab enabler).

Runs ``collie_core.headless`` against a local fake OpenAI-compatible
endpoint (the same pattern as ``test_e2e_phase1``) and asserts the stable
result-document contract: exit codes, schema keys, hashes, isolation, and
parity with the live IPC loop path.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

import collie_core.headless as headless
from collie_core.db import CollieDB
from collie_core.telemetry.prompt_hashes import hash_tool_schema

FAKE_KEY = "sk-bench-secret-0123456789abcdef"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_openai_app(*, delay_s: float = 0.0) -> web.Application:
    """OpenAI-compatible chat completions endpoint mirroring the e2e fake."""

    async def chat_completions(request: web.Request) -> web.StreamResponse:
        if delay_s:
            await asyncio.sleep(delay_s)
        body = await request.json()
        assert body.get("model")
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            chunks = ["Hi! ", "You said: ", "hello."]
            for i, text in enumerate(chunks):
                payload = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": (
                                {"role": "assistant", "content": text}
                                if i == 0
                                else {"content": text}
                            ),
                            "finish_reason": None,
                        }
                    ],
                }
                await resp.write(f"data: {json.dumps(payload)}\n\n".encode())
            done = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            }
            await resp.write(f"data: {json.dumps(done)}\n\n".encode())
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
                        "message": {"role": "assistant", "content": "Hi! You said: hello."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


async def _serve(app: web.Application) -> tuple[web.AppRunner, int]:
    llm_port = _free_port()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", llm_port)
    await site.start()
    return runner, llm_port


def _args(
    tmp_path: Path,
    llm_port: int,
    *,
    task: str = "hello",
    timeout: int = 60,
    api_key_env: str = "COLLIE_BENCH_KEY",
    approval_preset: str = "allow",
    **overrides: Any,
) -> Any:
    """Build headless args pointing at the fake endpoint + an isolated home."""
    values: dict[str, Any] = {
        "task": task,
        "home": str(tmp_path / "bench-home"),
        "model": "collie-test-model",
        "provider": "custom",
        "api_base": f"http://127.0.0.1:{llm_port}/v1",
        "api_key_env": api_key_env,
        "timeout": timeout,
        "max_iterations": 50,
        "approval_preset": approval_preset,
        "session_key": None,
        "json_out": None,
    }
    values.update(overrides)
    return headless._parse_args(
        [
            f"--task={values['task']}",
            f"--model={values['model']}",
            f"--provider={values['provider']}",
            f"--api-base={values['api_base']}",
            f"--api-key-env={values['api_key_env']}",
            f"--timeout={values['timeout']}",
            f"--max-iterations={values['max_iterations']}",
            f"--approval-preset={values['approval_preset']}",
        ]
        + ([f"--home={values['home']}"] if values["home"] is not None else [])
        + ([f"--session-key={values['session_key']}"] if values["session_key"] else [])
        + ([f"--json-out={values['json_out']}"] if values["json_out"] else [])
    )


def _capture_auto_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> Path:
    """Make the otherwise-random default temp home observable to a test."""
    auto_home = tmp_path / name

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "collie-bench-"
        auto_home.mkdir()
        return str(auto_home)

    monkeypatch.setattr(headless.tempfile, "mkdtemp", fake_mkdtemp)
    return auto_home


@pytest.mark.asyncio
async def test_headless_runs_task_and_outputs_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    runner, llm_port = await _serve(_fake_openai_app())
    try:
        exit_code, document = await headless.run_one(_args(tmp_path, llm_port))
    finally:
        await runner.cleanup()

    assert exit_code == 0
    assert document["exit_state"] == "ok"
    assert document["error"] is None

    # Stable contract keys
    for key in (
        "schema_version",
        "run_id",
        "harness",
        "commit",
        "model",
        "provider",
        "task",
        "session_key",
        "conversation_id",
        "prompt_hash",
        "tool_schema_hash",
        "config_hash",
        "final_text",
        "usage",
        "calls",
        "latency_ms",
        "exit_state",
        "error",
    ):
        assert key in document, f"missing contract key {key}"
    assert document["schema_version"] == 1
    assert document["harness"] == "collie"
    assert document["model"] == "collie-test-model"
    assert document["provider"] == "custom"
    assert document["task"] == "hello"
    assert document["session_key"].startswith("collie:")
    assert document["conversation_id"]

    # Final answer from the fake endpoint, sanitized
    assert document["final_text"] == "Hi! You said: hello."

    # Usage/calls are ints >= 0
    for value in document["usage"].values():
        assert isinstance(value, int) and value >= 0
    for value in document["calls"].values():
        assert isinstance(value, int) and value >= 0
    assert document["usage"]["input_tokens"] == 20
    assert document["usage"]["output_tokens"] == 6

    # Hashes are non-empty sha256 fingerprints
    for key in ("prompt_hash", "tool_schema_hash", "config_hash"):
        value = document[key]
        assert value and value.startswith("sha256:"), f"{key} missing"

    # The secret never leaves the process
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err

    # One JSON line on stdout
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["exit_state"] == "ok"


@pytest.mark.asyncio
async def test_headless_default_temp_home_is_removed_after_database_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    auto_home = _capture_auto_home(tmp_path, monkeypatch, "owned-success-home")
    events: list[str] = []
    original_close = headless.CollieDB.close
    original_rmtree = headless.shutil.rmtree

    def recording_close(db: CollieDB) -> None:
        events.append("db.close")
        original_close(db)

    def recording_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
        assert Path(path) == auto_home
        assert "db.close" in events
        events.append("rmtree")
        original_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(headless.CollieDB, "close", recording_close)
    monkeypatch.setattr(headless.shutil, "rmtree", recording_rmtree)

    runner, llm_port = await _serve(_fake_openai_app())
    try:
        exit_code, _document = await headless.run_one(
            _args(tmp_path, llm_port, home=None)
        )
    finally:
        await runner.cleanup()

    assert exit_code == 0
    assert events[-2:] == ["db.close", "rmtree"]
    assert not auto_home.exists()
    assert "COLLIE_HOME" not in headless.os.environ


@pytest.mark.asyncio
async def test_headless_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    auto_home = _capture_auto_home(tmp_path, monkeypatch, "owned-timeout-home")

    runner, llm_port = await _serve(_fake_openai_app(delay_s=30))
    try:
        exit_code, document = await headless.run_one(
            _args(tmp_path, llm_port, timeout=1, home=None)
        )
    finally:
        await runner.cleanup()

    assert exit_code == 2
    assert document["exit_state"] == "timeout"
    assert "timed out" in (document["error"] or "")
    assert not auto_home.exists()
    assert "COLLIE_HOME" not in headless.os.environ


@pytest.mark.asyncio
async def test_headless_default_temp_home_is_removed_after_task_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    auto_home = _capture_auto_home(tmp_path, monkeypatch, "owned-error-home")

    async def fail_chat(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("bench task failed")

    monkeypatch.setattr(headless.CollieRuntime, "_chat", fail_chat)
    runner, llm_port = await _serve(_fake_openai_app())
    try:
        exit_code, document = await headless.run_one(
            _args(tmp_path, llm_port, home=None)
        )
    finally:
        await runner.cleanup()

    assert exit_code == 1
    assert document["exit_state"] == "error"
    assert document["error"] == "bench task failed"
    assert not auto_home.exists()
    assert "COLLIE_HOME" not in headless.os.environ


@pytest.mark.asyncio
async def test_headless_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # No key in the environment at all.
    monkeypatch.delenv("COLLIE_BENCH_KEY", raising=False)
    monkeypatch.delenv("COLLIE_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    auto_home = _capture_auto_home(tmp_path, monkeypatch, "owned-missing-key-home")

    exit_code, document = await headless.run_one(_args(tmp_path, 9999, home=None))

    assert exit_code == 3
    assert document["exit_state"] == "error"
    assert document["error"] and "API key" in document["error"]
    captured = capsys.readouterr()
    assert "COLLIE_BENCH_KEY" in document["error"]
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    # The key never appears in the JSON either
    assert FAKE_KEY not in json.dumps(document)
    assert not auto_home.exists()
    assert "COLLIE_HOME" not in headless.os.environ


@pytest.mark.asyncio
async def test_headless_configuration_failure_cleans_default_temp_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    auto_home = _capture_auto_home(tmp_path, monkeypatch, "owned-config-home")

    async def reject_candidate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"configured": False, "error": "provider rejected"}

    monkeypatch.setattr(
        headless.CollieRuntime,
        "_configure_provider_candidate",
        reject_candidate,
    )
    exit_code, document = await headless.run_one(_args(tmp_path, 9999, home=None))

    assert exit_code == 3
    assert document["error"] == "provider rejected"
    assert not auto_home.exists()
    assert "COLLIE_HOME" not in headless.os.environ


@pytest.mark.asyncio
async def test_headless_preserves_explicit_and_inherited_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COLLIE_BENCH_KEY", raising=False)
    monkeypatch.delenv("COLLIE_PROVIDER_API_KEY", raising=False)
    inherited_home = tmp_path / "inherited-home"
    explicit_home = tmp_path / "explicit-home"
    inherited_home.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(inherited_home))

    exit_code, _document = await headless.run_one(
        _args(tmp_path, 9999, home=str(explicit_home))
    )
    assert exit_code == 3
    assert explicit_home.is_dir()
    assert headless.os.environ["COLLIE_HOME"] == str(inherited_home)

    exit_code, _document = await headless.run_one(_args(tmp_path, 9999, home=None))
    assert exit_code == 3
    assert inherited_home.is_dir()
    assert headless.os.environ["COLLIE_HOME"] == str(inherited_home)


@pytest.mark.asyncio
async def test_headless_parity_tool_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headless path and a direct runtime build register the same tools."""
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    runner, llm_port = await _serve(_fake_openai_app())
    try:
        exit_code, document = await headless.run_one(_args(tmp_path, llm_port))
    finally:
        await runner.cleanup()
    assert exit_code == 0

    # Direct runtime build from the SAME settings — identical schemas must
    # produce the identical fingerprint the headless run recorded.
    home = tmp_path / "bench-home"
    db = CollieDB(home / "collie.db")
    from collie_core.runtime import CollieRuntime

    runtime = CollieRuntime(port=0, db=db)
    try:
        loop = runtime._build_loop()
        definitions = loop.tools.get_definitions()
        assert hash_tool_schema(definitions) == document["tool_schema_hash"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_headless_isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--home wins over the real user home; the default home stays untouched."""
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)
    real_home = tmp_path / "real-home"
    monkeypatch.setenv("HOME", str(real_home))

    runner, llm_port = await _serve(_fake_openai_app())
    try:
        exit_code, document = await headless.run_one(_args(tmp_path, llm_port))
    finally:
        await runner.cleanup()
    assert exit_code == 0

    # The run wrote inside --home, then restored the caller's environment.
    assert (tmp_path / "bench-home" / "collie.db").exists()
    assert "COLLIE_HOME" not in headless.os.environ
    assert document["conversation_id"]

    # The real user home never gained a collie DB
    assert not (real_home / ".collie" / "collie.db").exists()


@pytest.mark.asyncio
async def test_headless_json_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    runner, llm_port = await _serve(_fake_openai_app())
    out_file = tmp_path / "result.json"
    try:
        exit_code, document = await headless.run_one(
            _args(tmp_path, llm_port, json_out=str(out_file))
        )
    finally:
        await runner.cleanup()

    assert exit_code == 0
    written = json.loads(out_file.read_text())
    assert written == document
    assert written["exit_state"] == "ok"
