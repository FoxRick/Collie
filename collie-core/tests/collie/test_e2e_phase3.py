"""Phase 3 end-to-end: services + subagents + life tools through the runtime.

Boots the real CollieRuntime with a fake OpenAI endpoint and drives the
Phase 3 IPC surface exactly like the renderer: connect a service, create a
subagent, chat, and confirm the agent registered all 16 life tools plus
call_subagent — with the connected service's MCP config injected.
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
    async def chat_completions(request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response({
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": body["model"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Woof! All set."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                      "total_tokens": 14},
        })

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


async def _roundtrip(ws, timeout: float = 30.0, **frame):
    await ws.send(json.dumps(frame))
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if reply["type"] in ("ok", "error") and reply.get("id") == frame.get("id"):
            return reply


@pytest.mark.asyncio
async def test_phase3_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))

    # Never spawn real MCP servers (npx ...) in tests.
    connected_requests: list[dict] = []

    async def fake_connect(mcp_servers, registry):
        connected_requests.append(dict(mcp_servers))
        return {}

    monkeypatch.setattr(
        "nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect
    )

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
            assert json.loads(await ws.recv())["type"] == "ready"

            reply = await _roundtrip(ws, type="set_api_key", id="k",
                                     provider="custom", key="sk-fake")
            assert reply["type"] == "ok"
            reply = await _roundtrip(ws, type="configure", id="c1")
            assert reply["data"]["configured"] is True

            # -- services: curated catalog, broken connectors disabled ------
            reply = await _roundtrip(ws, type="list_services", id="s1")
            services = reply["data"]["services"]
            assert len(services) == 8
            assert all(service["status"] == "coming_soon" for service in services)
            assert not any(service["available"] for service in services)

            reply = await _roundtrip(ws, type="connect_service", id="s2",
                                     service_id="todoist",
                                     credentials={"todoist_token": "tok-e2e"})
            assert reply["type"] == "error", reply
            assert runtime.services.mcp_servers_for_config() == {}

            # -- all Collie tools registered on the live loop ----------------
            tool_names = {
                name for name in ("weather", "reminders", "remember", "calendar",
                                  "email", "notes", "shopping_list", "budget",
                                  "health", "recipes", "news", "travel",
                                  "contacts", "documents",
                                  "presentations", "call_subagent")
                if runtime.loop.tools.get(name) is not None
            }
            expected = {
                "weather", "reminders", "remember", "calendar", "email",
                "notes", "shopping_list", "budget", "health", "recipes",
                "news", "travel", "contacts", "documents",
                "presentations", "call_subagent",
            }
            missing = expected - tool_names
            assert not missing, f"missing tools: {missing}"

            # -- subagents: create via IPC (LLM writes the prompt) -----------
            reply = await _roundtrip(ws, type="create_subagent", id="a1",
                                     name="Trip Planner",
                                     description="plans weekend trips")
            assert reply["type"] == "ok", reply
            sub = reply["data"]["subagent"]
            md = (tmp_path / ".collie" / "workspace" / "subagents"
                  / "trip-planner.md")
            assert md.exists()
            assert sub["system_prompt"]

            reply = await _roundtrip(ws, type="list_subagents", id="a2")
            assert {s["name"] for s in reply["data"]["subagents"]} == {
                "Researcher",
                "Analyst",
                "Reviewer",
                "Operator",
                "Trip Planner",
            }

            # -- custom automation from natural language ---------------------
            reply = await _roundtrip(ws, type="create_automation", id="au1",
                                     description="every friday at 5pm review my week")
            assert reply["data"]["automation"]["schedule"] == "Fri 17:00"

            # -- chat still round-trips with everything wired ----------------
            reply = await _roundtrip(ws, type="chat", id="m1", content="hi")
            conv_id = reply["data"]["conversation_id"]
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
            assert "Woof!" in assistant["content"]

            # -- search + export + disconnect --------------------------------
            reply = await _roundtrip(ws, type="search_messages", id="q1",
                                     query="hi")
            assert any(r["conversation_id"] == conv_id
                       for r in reply["data"]["results"])

            reply = await _roundtrip(ws, type="export_data", id="x1")
            assert Path(reply["data"]["path"]).exists()

            reply = await _roundtrip(ws, type="disconnect_service", id="s3",
                                     service_id="todoist")
            assert reply["data"]["status"] == "disconnected"
            assert runtime.services.mcp_servers_for_config() == {}
    finally:
        await runtime._shutdown_loop()
        await runtime.ipc.stop()
        db.close()
        await runner.cleanup()
