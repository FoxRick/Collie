"""Phase 1 end-to-end: Electron-style client -> IPC -> AgentLoop -> LLM -> SQLite.

Boots the real CollieRuntime against a local fake OpenAI-compatible endpoint
and drives it exactly like the renderer does over the WebSocket protocol.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest
import websockets
from aiohttp import web

from collie_core.db import CollieDB
from collie_core.runtime import CollieRuntime


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _fake_openai_app() -> web.Application:
    async def chat_completions(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body.get("model")
        if body.get("stream"):
            resp = web.StreamResponse(
                headers={"Content-Type": "text/event-stream"}
            )
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


@pytest.mark.asyncio
async def test_phase1_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))

    llm_port = _free_port()
    runner = web.AppRunner(await _fake_openai_app())
    await runner.setup()
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

            # Welcome flow: inject the API key, then configure
            await ws.send(json.dumps({
                "type": "set_api_key", "id": "k",
                "provider": "custom", "key": "sk-fake",
            }))
            assert json.loads(await ws.recv())["type"] == "ok"

            await ws.send(json.dumps({"type": "configure", "id": "c"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "ok", reply
            assert reply["data"]["configured"] is True, reply["data"]
            assert reply["data"]["model"] == "collie-test-model"

            # Chat round trip
            await ws.send(json.dumps({
                "type": "chat", "id": "m1", "content": "hello",
            }))

            states: list[str] = []
            deltas: list[str] = []
            assistant = None
            conv_id = None
            for _ in range(200):
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if frame["type"] == "ok" and frame.get("id") == "m1":
                    conv_id = frame["data"]["conversation_id"]
                elif frame["type"] == "thinking":
                    states.append(frame["state"])
                elif frame["type"] == "delta":
                    deltas.append(frame["text"])
                elif (frame["type"] == "message"
                      and frame["message"]["role"] == "assistant"):
                    assistant = frame["message"]
                    break
                elif frame["type"] == "error":
                    pytest.fail(f"IPC error: {frame}")

            assert assistant is not None
            assert "Woof!" in assistant["content"]
            assert "".join(deltas) == "Woof! You said: hello."
            assert "processing" in states
            assert states[-1] == "done"

            # Persistence + usage tracking
            msgs = db.get_messages(conv_id)
            assert [m["role"] for m in msgs] == ["user", "assistant"]
            assert db.usage_this_month()["messages"] == 1
            assert db.usage_this_month()["tokens"] == 26

            # Second turn continues the same conversation with history
            await ws.send(json.dumps({
                "type": "chat", "id": "m2",
                "conversation_id": conv_id, "content": "hello",
            }))
            assistant2 = None
            for _ in range(200):
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if (frame["type"] == "message"
                        and frame["message"]["role"] == "assistant"):
                    assistant2 = frame["message"]
                    break
                if frame["type"] == "error":
                    pytest.fail(f"IPC error: {frame}")
            assert assistant2 is not None
            assert len(db.get_messages(conv_id)) == 4
    finally:
        await runtime._shutdown_loop()
        await runtime.ipc.stop()
        db.close()
        await runner.cleanup()
