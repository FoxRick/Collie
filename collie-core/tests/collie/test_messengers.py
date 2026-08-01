"""Tests for Phase 4 messengers: manager lifecycle, routing, pairing, delivery."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.messengers import MESSENGERS, CollieBus, MessengerManager
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.outbound_events import (
    ProgressEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
)
from nanobot.pairing import PAIRING_CODE_META_KEY


@pytest.fixture(autouse=True)
def _isolated_pairing_store(tmp_path: Path):
    """Anchor the engine data dir (pairing.json, media) to the test tmp."""
    from nanobot.config.loader import get_config_path, set_config_path

    previous_config_path = get_config_path()
    set_config_path(tmp_path / "engine" / "config.json")
    yield
    set_config_path(previous_config_path)


@pytest.fixture()
def db(tmp_path: Path):
    database = CollieDB(tmp_path / "collie.db")
    yield database
    database.close()


class FakeChannel:
    """Stand-in for a vendored channel: records sends, runs forever."""

    name = "telegram"

    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.sent: list[OutboundMessage] = []
        self.deltas: list[tuple[str, str, bool]] = []
        self.send_progress = True
        self.send_tool_hints = True
        self.show_reasoning = True
        self.stopped = False

    async def start(self):
        await asyncio.Event().wait()

    async def stop(self):
        self.stopped = True

    async def send(self, msg: OutboundMessage):
        self.sent.append(msg)

    async def send_delta(self, chat_id, delta, metadata=None, *, stream_id=None,
                         stream_end=False, resuming=False):
        self.deltas.append((chat_id, delta, stream_end))


async def _started_manager(db, monkeypatch, *, name="telegram"):
    manager = MessengerManager(db)
    manager.set_enabled(name, True)
    for key in MESSENGERS[name]["secrets"]:
        manager.set_secret(name, key, "sekrit")
    monkeypatch.setattr(
        "nanobot.channels.registry.load_channel_class", lambda _n: FakeChannel
    )
    await manager.start(CollieBus())
    return manager


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """Wait for the async per-channel drain worker to finish a delivery."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")


# -- settings & secrets ---------------------------------------------------------


def test_enabled_names_from_settings(db) -> None:
    manager = MessengerManager(db)
    assert manager.enabled_names() == []
    manager.set_enabled("telegram", True)
    manager.set_enabled("discord", True)
    assert manager.enabled_names() == ["telegram"]


def test_secrets_ok(db) -> None:
    manager = MessengerManager(db)
    assert set(MESSENGERS) == {"telegram"}
    assert not manager.secrets_ok("telegram")
    manager.set_secret("telegram", "token", "123:abc")
    assert manager.secrets_ok("telegram")


def test_set_secret_rejects_unknown(db) -> None:
    manager = MessengerManager(db)
    with pytest.raises(ValueError):
        manager.set_secret("carrier-pigeon", "token", "coo")


def test_clear_local_connection_forgets_token_and_pairings(db) -> None:
    from nanobot.pairing import approve_code, generate_code, is_approved

    manager = MessengerManager(db)
    manager.set_secret("telegram", "token", "123:abc")
    code = generate_code("telegram", "42")
    assert approve_code(code) == ("telegram", "42")
    assert is_approved("telegram", "42")

    manager.clear_local_connection("telegram")

    assert not manager.secrets_ok("telegram")
    assert not is_approved("telegram", "42")


def test_config_shapes(db) -> None:
    manager = MessengerManager(db)
    manager.set_secret("telegram", "token", "123:abc")
    cfg = manager._config_for("telegram")
    assert cfg["enabled"] is True and cfg["token"] == "123:abc"
    assert "discord" not in MESSENGERS


# -- lifecycle ---------------------------------------------------------------------


async def test_start_skips_unconfigured(db, monkeypatch) -> None:
    manager = MessengerManager(db)
    manager.set_enabled("telegram", True)  # no token injected
    await manager.start(CollieBus())
    assert manager.channels == {}
    assert "missing credentials" in manager._errors["telegram"]
    await manager.stop()


async def test_start_and_stop(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    assert manager.is_running("telegram")
    channel = manager.channels["telegram"]
    # Messengers stay quiet about tool churn
    assert channel.send_progress is False
    assert channel.send_tool_hints is False
    await manager.stop()
    assert not manager.is_running("telegram")
    assert channel.stopped is True
    assert manager.channels == {}


# -- outbound dispatch -------------------------------------------------------------


async def test_dispatch_unknown_channel(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    handled = await manager.dispatch(
        OutboundMessage(channel="collie", chat_id="c1", content="hi")
    )
    assert handled is False
    await manager.stop()


async def test_dispatch_final_message_mirrors_and_remembers(db, monkeypatch) -> None:
    events: list[dict] = []

    async def broadcaster(payload):
        events.append(payload)

    manager = await _started_manager(db, monkeypatch)
    manager.broadcaster = broadcaster
    handled = await manager.dispatch(
        OutboundMessage(channel="telegram", chat_id="42", content="Woof, done!")
    )
    assert handled is True
    channel = manager.channels["telegram"]
    await _wait_until(lambda: len(channel.sent) == 1)
    assert channel.sent[0].content == "Woof, done!"
    await _wait_until(
        lambda: db.get_setting("messengers.telegram.last_chat_id") == "42"
    )

    conv_id = db.get_setting("messengers.telegram.conversation_id")
    conv = db.get_conversation(conv_id)
    assert conv["title"] == "📱 Telegram"
    messages = db.get_messages(conv_id)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "Woof, done!"
    assert any(e["type"] == "message" for e in events)
    await manager.stop()


async def test_dispatch_pairing_code_not_mirrored(db, monkeypatch) -> None:
    events: list[dict] = []

    async def broadcaster(payload):
        events.append(payload)

    manager = await _started_manager(db, monkeypatch)
    manager.broadcaster = broadcaster
    await manager.dispatch(
        OutboundMessage(
            channel="telegram", chat_id="99", content="pairing code inside",
            metadata={PAIRING_CODE_META_KEY: "ABCD-EFGH"},
        )
    )
    channel = manager.channels["telegram"]
    await _wait_until(lambda: len(channel.sent) == 1)  # code still goes out
    assert db.get_setting("messengers.telegram.last_chat_id") is None
    assert db.get_setting("messengers.telegram.conversation_id") is None
    await _wait_until(lambda: any(e["type"] == "messenger_pairing" for e in events))
    await manager.stop()


async def test_dispatch_progress_dropped_streams_forwarded(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    channel = manager.channels["telegram"]

    await manager.dispatch(OutboundMessage(
        channel="telegram", chat_id="42", content="using tool...",
        event=ProgressEvent(content="using tool..."),
    ))
    # Progress events are dropped by the channel worker: nothing to wait for.
    assert channel.sent == [] and channel.deltas == []

    await manager.dispatch(OutboundMessage(
        channel="telegram", chat_id="42", content="Wo",
        event=StreamDeltaEvent(content="Wo", stream_id="s1"),
    ))
    await manager.dispatch(OutboundMessage(
        channel="telegram", chat_id="42", content="of",
        event=StreamEndEvent(content="of", stream_id="s1"),
    ))
    await _wait_until(lambda: len(channel.deltas) == 2)
    assert channel.deltas == [("42", "Wo", False), ("42", "of", True)]
    assert channel.sent == []

    # Final streamed response: no re-send, but mirrored to the desktop
    await manager.dispatch(OutboundMessage(
        channel="telegram", chat_id="42", content="Woof",
        event=StreamedResponseEvent(),
    ))
    conv_id = db.get_setting("messengers.telegram.conversation_id")
    await _wait_until(
        lambda: conv_id is not None
        and db.get_messages(conv_id)
        and db.get_messages(conv_id)[-1]["content"] == "Woof"
    )
    assert channel.sent == []
    messages = db.get_messages(conv_id)
    assert messages[-1]["content"] == "Woof"
    await manager.stop()


# -- inbound mirroring ---------------------------------------------------------------


async def test_on_inbound_mirrors_user_message(db, monkeypatch) -> None:
    events: list[dict] = []

    async def broadcaster(payload):
        events.append(payload)

    manager = await _started_manager(db, monkeypatch)
    manager.broadcaster = broadcaster
    await manager.on_inbound(InboundMessage(
        channel="telegram", sender_id="7", chat_id="42", content="hi from phone"
    ))
    conv_id = db.get_setting("messengers.telegram.conversation_id")
    messages = db.get_messages(conv_id)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "hi from phone"
    await manager.stop()


async def test_on_inbound_ignores_non_messenger(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    await manager.on_inbound(InboundMessage(
        channel="websocket", sender_id="7", chat_id="42", content="hi"
    ))
    assert db.get_setting("messengers.websocket.conversation_id") is None
    await manager.stop()


async def test_collie_bus_observer_then_queue(db) -> None:
    seen: list[str] = []

    async def observer(msg):
        seen.append(msg.content)

    bus = CollieBus(on_inbound=observer)
    msg = InboundMessage(channel="telegram", sender_id="1", chat_id="2", content="ping")
    await bus.publish_inbound(msg)
    assert seen == ["ping"]
    assert (await bus.consume_inbound()) is msg


# -- automation delivery --------------------------------------------------------------


async def test_deliver_requires_known_chat(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    assert await manager.deliver("telegram", "Morning!") is False
    db.set_setting("messengers.telegram.last_chat_id", "42")
    assert await manager.deliver("telegram", "Morning!") is True
    channel = manager.channels["telegram"]
    await _wait_until(lambda: len(channel.sent) == 1)
    assert channel.sent[-1].content == "Morning!"
    assert channel.sent[-1].chat_id == "42"
    await manager.stop()


async def test_deliver_never_targets_group_chats(db, monkeypatch) -> None:
    """A remembered Telegram group id must never receive automations."""
    manager = await _started_manager(db, monkeypatch)
    db.set_setting("messengers.telegram.last_chat_id", "-1001234567890")
    assert await manager.deliver("telegram", "Morning!") is False
    channel = manager.channels["telegram"]
    await _wait_until(lambda: len(channel.sent) >= 0)
    assert channel.sent == []
    await manager.stop()


async def test_automation_targets(db, monkeypatch) -> None:
    manager = await _started_manager(db, monkeypatch)
    assert manager.automation_targets() == []
    manager.set_deliver_automations("telegram", True)
    assert manager.automation_targets() == ["telegram"]
    manager.set_deliver_automations("discord", True)  # not running -> excluded
    assert manager.automation_targets() == ["telegram"]
    await manager.stop()


# -- status & pairing ------------------------------------------------------------------


async def test_status_shape(db, monkeypatch) -> None:
    from nanobot.pairing import generate_code

    manager = await _started_manager(db, monkeypatch)
    generate_code("telegram", "555")
    rows = {row["id"]: row for row in manager.status()}
    assert set(rows) == {"telegram"}
    tg = rows["telegram"]
    assert tg["enabled"] and tg["configured"] and tg["running"]
    assert tg["pending"][0]["sender_id"] == "555"
    await manager.stop()


async def test_scheduler_prefers_runner(tmp_path: Path) -> None:
    from collie_core.automations.scheduler import AutomationScheduler

    db = CollieDB(tmp_path / "sched.db")
    try:
        fired: list[str] = []
        broadcasts: list[dict] = []

        async def runner(auto):
            fired.append(auto["name"])

        async def broadcaster(payload):
            broadcasts.append(payload)

        scheduler = AutomationScheduler(db, broadcaster=broadcaster, runner=runner)
        await scheduler._fire({"id": "a1", "name": "Test", "action_config": {}})
        assert fired == ["Test"]
        assert broadcasts == []
    finally:
        db.close()
