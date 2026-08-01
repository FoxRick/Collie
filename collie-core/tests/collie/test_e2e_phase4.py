"""Phase 4 end-to-end: phone access via messengers through the real runtime.

Boots CollieRuntime with a fake OpenAI endpoint, swaps the vendored Telegram
channel for an in-process fake, and drives the Phase 4 IPC surface exactly
like the renderer: configure + enable a messenger, chat from the "phone"
(inbound bus round trip -> reply lands back on the phone AND mirrors into a
desktop conversation), approve a pairing code, and deliver an automation to
the phone.
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
from nanobot.bus.events import InboundMessage, OutboundMessage


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


class FakePhoneChannel:
    """Stands in for the vendored TelegramChannel in the gate test."""

    name = "telegram"
    instances: list["FakePhoneChannel"] = []

    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.sent: list[OutboundMessage] = []
        self.send_progress = True
        self.send_tool_hints = True
        self.show_reasoning = True
        FakePhoneChannel.instances.append(self)

    async def start(self):
        await asyncio.Event().wait()

    async def stop(self):
        pass

    async def send(self, msg: OutboundMessage):
        self.sent.append(msg)

    async def send_delta(self, chat_id, delta, metadata=None, *, stream_id=None,
                         stream_end=False, resuming=False):
        pass


class FakeTelegramBot:
    def __init__(self, token: str):
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get_me(self):
        return {"id": 1, "username": "collie_test_bot"}


async def _wait_for(predicate, timeout: float = 30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.mark.asyncio
async def test_phase4_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    monkeypatch.setattr(
        "nanobot.channels.registry.load_channel_class",
        lambda _n: FakePhoneChannel,
    )
    monkeypatch.setattr("telegram.Bot", FakeTelegramBot)
    FakePhoneChannel.instances = []

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

            # -- Telegram is the only release messenger ---------------------
            reply = await _roundtrip(ws, type="get_messengers", id="p1")
            rows = {m["id"]: m for m in reply["data"]["messengers"]}
            assert set(rows) == {"telegram"}
            assert not any(m["enabled"] for m in rows.values())

            # -- connect Telegram: secret + enable + configure ----------------
            reply = await _roundtrip(ws, type="set_messenger_secret", id="p2",
                                     messenger="telegram", key="token",
                                     value="123:fake")
            assert reply["type"] == "ok"
            reply = await _roundtrip(ws, type="set_messenger", id="p3",
                                     messenger="telegram", enabled=True)
            assert reply["type"] == "ok"

            reply = await _roundtrip(ws, type="configure", id="c1")
            assert reply["data"]["configured"] is True
            await _wait_for(lambda: runtime.messengers.is_running("telegram"))

            reply = await _roundtrip(ws, type="get_messengers", id="p4")
            tg = next(m for m in reply["data"]["messengers"]
                      if m["id"] == "telegram")
            assert tg["enabled"] and tg["configured"] and tg["running"]

            # -- chat from the phone: bus round trip --------------------------
            await runtime.loop.bus.publish_inbound(InboundMessage(
                channel="telegram", sender_id="777", chat_id="4242",
                content="what's up, Collie?",
            ))
            channel = FakePhoneChannel.instances[-1]
            await _wait_for(lambda: channel.sent)
            assert "Woof!" in channel.sent[0].content

            # ...and it mirrors into a desktop conversation
            conv_id = db.get_setting("messengers.telegram.conversation_id")
            assert conv_id
            messages = db.get_messages(conv_id)
            roles = [m["role"] for m in messages]
            assert "user" in roles and "assistant" in roles
            assert db.get_setting("messengers.telegram.last_chat_id") == "4242"

            # -- pairing: stranger's code approved from Settings -> Phone -----
            from nanobot.pairing import generate_code, is_approved

            code = generate_code("telegram", "31337")
            reply = await _roundtrip(ws, type="get_messengers", id="p5")
            tg = next(m for m in reply["data"]["messengers"]
                      if m["id"] == "telegram")
            assert any(p["code"] == code for p in tg["pending"])

            reply = await _roundtrip(ws, type="approve_pairing", id="p6",
                                     code=code)
            assert reply["data"]["approved"] is True
            assert reply["data"]["confirmed"] is True
            assert is_approved("telegram", "31337")

            # -- automation delivery to the phone ------------------------------
            reply = await _roundtrip(ws, type="set_messenger", id="p7",
                                     messenger="telegram",
                                     deliver_automations=True)
            assert reply["type"] == "ok"
            await _wait_for(lambda: runtime.messengers.is_running("telegram"))
            channel = FakePhoneChannel.instances[-1]

            from collie_core.automations.scheduler import seed_builtin_automations

            seed_builtin_automations(db)
            morning = next(a for a in db.list_automations()
                           if a["id"] == "collie-morning-briefing")
            await runtime._run_automation(morning)

            # Delivery goes through the async per-channel queue now.
            await _wait_for(
                lambda: any("🔔 Morning Briefing" in m.content for m in channel.sent)
            )
            assert any("🔔 Morning Briefing" in m.content for m in channel.sent)
            briefing_conv = db.get_setting(
                "automations.collie-morning-briefing.conversation_id"
            )
            assert briefing_conv
            briefing_msgs = db.get_messages(briefing_conv)
            assert briefing_msgs[-1]["role"] == "assistant"
            assert "Woof!" in briefing_msgs[-1]["content"]
    finally:
        await runtime._shutdown_loop()
        await runtime.ipc.stop()
        db.close()
