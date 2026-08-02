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
from collie_core.db import CollieDB, collie_home
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
            chunks = ["Woof! ", "You said: ", "hello."]
            for i, text in enumerate(chunks):
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
        return web.json_response({
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": body["model"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Woof! You said: hello."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
        })

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
    return headless._parse_args([
        f"--task={values['task']}",
        f"--home={values['home']}",
        f"--model={values['model']}",
        f"--provider={values['provider']}",
        f"--api-base={values['api_base']}",
        f"--api-key-env={values['api_key_env']}",
        f"--timeout={values['timeout']}",
        f"--max-iterations={values['max_iterations']}",
        f"--approval-preset={values['approval_preset']}",
    ] + ([f"--session-key={values['session_key']}"] if values["session_key"] else [])
      + ([f"--json-out={values['json_out']}"] if values["json_out"] else []))


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
        "schema_version", "run_id", "harness", "commit", "model", "provider",
        "task", "session_key", "conversation_id", "prompt_hash",
        "tool_schema_hash", "config_hash", "final_text", "usage", "calls",
        "latency_ms", "exit_state", "error",
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
    assert document["final_text"] == "Woof! You said: hello."

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
async def test_headless_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLLIE_BENCH_KEY", FAKE_KEY)
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    runner, llm_port = await _serve(_fake_openai_app(delay_s=30))
    try:
        exit_code, document = await headless.run_one(
            _args(tmp_path, llm_port, timeout=1)
        )
    finally:
        await runner.cleanup()

    assert exit_code == 2
    assert document["exit_state"] == "timeout"
    assert "timed out" in (document["error"] or "")


@pytest.mark.asyncio
async def test_headless_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # No key in the environment at all.
    monkeypatch.delenv("COLLIE_BENCH_KEY", raising=False)
    monkeypatch.delenv("COLLIE_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("COLLIE_HOME", raising=False)

    exit_code, document = await headless.run_one(_args(tmp_path, 9999))

    assert exit_code == 3
    assert document["exit_state"] == "error"
    assert document["error"] and "API key" in document["error"]
    captured = capsys.readouterr()
    assert "COLLIE_BENCH_KEY" in document["error"]
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    # The key never appears in the JSON either
    assert FAKE_KEY not in json.dumps(document)


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
async def test_headless_isolated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # collie_home() points inside --home (COLLIE_HOME was set by run_one)
    assert collie_home().resolve() == (tmp_path / "bench-home").resolve()
    assert document["conversation_id"]

    # The real user home never gained a collie DB
    assert not (real_home / ".collie" / "collie.db").exists()


@pytest.mark.asyncio
async def test_headless_json_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
