from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.messengers import MESSENGERS, MessengerManager
from collie_core.messengers import manager as messenger_manager_module
from collie_core.runtime import CollieRuntime
from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.events import InboundMessage

EXACT_MESSENGER_IDENTITIES = (
    ("telegram", "Telegram", "telegram:-10042:topic:7", "-10042"),
    ("slack", "Slack", "slack:D123:1712345678.000100", "D123"),
    ("discord", "Discord", "discord:123:thread:456", "456"),
)
EXACT_INBOUND_IDENTITIES = (
    *EXACT_MESSENGER_IDENTITIES,
    ("whatsapp", "WhatsApp", "whatsapp:15551234567", "15551234567"),
)


@pytest.fixture
def db(tmp_path: Path):
    database = CollieDB(tmp_path / "collie.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "collie-home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    database = CollieDB(home / "collie.db")
    instance = CollieRuntime(port=0, db=database)
    try:
        yield instance
    finally:
        database.close()


@pytest.mark.parametrize(
    ("name", "label", "session_key", "chat_id"),
    EXACT_MESSENGER_IDENTITIES,
)
def test_messenger_session_identity_persists_across_manager_recreation(
    db: CollieDB,
    name: str,
    label: str,
    session_key: str,
    chat_id: str,
) -> None:
    manager = MessengerManager(db)
    conversation_id = str(db.create_conversation(f"{label} mirror")["id"])
    db.set_setting(f"messengers.{name}.conversation_id", conversation_id)

    assert (
        manager._mirror_conversation(  # noqa: SLF001 - exercises persisted inbound identity
            name,
            label,
            session_key=session_key,
            chat_id=chat_id,
        )
        == conversation_id
    )
    assert db.get_setting(f"messengers.{name}.session_keys") == [session_key]
    assert db.get_setting(f"messengers.{name}.session_key") == session_key
    assert db.get_setting(f"messengers.{name}.session_chat_id") == chat_id

    recreated = MessengerManager(db)
    assert recreated.session_keys_for_conversation(conversation_id) == {session_key}
    assert recreated.session_target_for_conversation(conversation_id) == (
        session_key,
        name,
        chat_id,
    )

    recreated.forget_conversation(conversation_id)
    assert recreated.session_keys_for_conversation(conversation_id) == set()
    assert recreated.session_target_for_conversation(conversation_id) is None
    for suffix in ("conversation_id", "session_keys", "session_key", "session_chat_id"):
        assert db.get_setting(f"messengers.{name}.{suffix}") is None

    # Persisted identities must not make disabled source-only channels connectable.
    assert set(MESSENGERS) == {"telegram"}
    assert recreated.enabled_names() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "label", "session_key", "chat_id"),
    EXACT_INBOUND_IDENTITIES,
)
async def test_actual_messenger_inbound_persists_its_exact_session_override(
    db: CollieDB,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    label: str,
    session_key: str,
    chat_id: str,
) -> None:
    # Source-only channels stay disabled in production. This local catalogue
    # patch solely lets the common inbound producer path be exercised.
    monkeypatch.setattr(
        messenger_manager_module,
        "MESSENGERS",
        {name: {"label": label, "secrets": (), "emoji": "test"}},
    )
    manager = MessengerManager(db)
    manager.channels[name] = object()

    await manager.on_inbound(
        InboundMessage(
            channel=name,
            sender_id="user-1",
            chat_id=chat_id,
            content="hello from the messenger",
            session_key_override=session_key,
        )
    )

    conversation_id = str(db.get_setting(f"messengers.{name}.conversation_id") or "")
    assert conversation_id
    assert db.get_setting(f"messengers.{name}.session_keys") == [session_key]
    assert db.get_setting(f"messengers.{name}.session_key") == session_key
    assert db.get_setting(f"messengers.{name}.session_chat_id") == chat_id
    assert manager.session_keys_for_conversation(conversation_id) == {session_key}
    assert manager.session_target_for_conversation(conversation_id) == (
        session_key,
        name,
        chat_id,
    )


def test_runtime_resolves_exact_desktop_topic_and_thread_session_keys(
    runtime: CollieRuntime,
) -> None:
    desktop_id = str(runtime.db.create_conversation("Desktop")["id"])
    assert runtime.session_keys_for_conversation(desktop_id) == {f"collie:{desktop_id}"}
    assert runtime._session_target(desktop_id) == (f"collie:{desktop_id}", "desktop")

    for name, label, session_key, chat_id in EXACT_MESSENGER_IDENTITIES:
        conversation_id = str(runtime.db.create_conversation(f"{label} mirror")["id"])
        runtime.db.set_setting(f"messengers.{name}.conversation_id", conversation_id)
        runtime.messengers._mirror_conversation(  # noqa: SLF001
            name,
            label,
            session_key=session_key,
            chat_id=chat_id,
        )

        assert runtime.session_keys_for_conversation(conversation_id) == {
            f"collie:{conversation_id}",
            session_key,
        }
        assert runtime._session_target(conversation_id) == (session_key, name)


@pytest.mark.asyncio
async def test_runtime_lists_stops_and_deletes_work_across_all_conversation_sessions(
    runtime: CollieRuntime,
) -> None:
    conversation_id = str(runtime.db.create_conversation("Telegram topic")["id"])
    topic_key = "telegram:-10042:topic:7"
    desktop_key = f"collie:{conversation_id}"
    runtime.db.set_setting("messengers.telegram.conversation_id", conversation_id)
    runtime.messengers._mirror_conversation(  # noqa: SLF001
        "telegram",
        "Telegram",
        session_key=topic_key,
        chat_id="-10042",
    )

    statuses = {
        desktop_key: [
            {
                "id": "shared",
                "name": "Researcher",
                "phase": "working",
                "task_description": "Desktop task",
                "started_at": 2.0,
            }
        ],
        topic_key: [
            {
                "id": "topic-only",
                "name": "Writer",
                "phase": "working",
                "task_description": "Topic task",
                "started_at": 1.0,
            },
            {
                "id": "shared",
                "name": "Researcher",
                "phase": "working",
                "task_description": "Duplicate view",
                "started_at": 2.0,
            },
        ],
    }
    subagents = SimpleNamespace(
        get_running_statuses_by_session=MagicMock(side_effect=lambda key: statuses[key]),
        cancel_by_session=AsyncMock(return_value=1),
    )
    cancel_session = AsyncMock(side_effect=lambda key: 2 if key == desktop_key else 3)
    runtime.loop = SimpleNamespace(subagents=subagents, cancel_session=cancel_session)

    active = runtime.active_subagents_for_conversation(conversation_id)
    assert [item["id"] for item in active] == ["topic-only", "shared"]
    assert all(item["conversation_id"] == conversation_id for item in active)
    assert subagents.get_running_statuses_by_session.call_args_list == [
        call(desktop_key),
        call(topic_key),
    ]

    assert await runtime.cancel_subagents_for_conversation(conversation_id) == 2
    assert subagents.cancel_by_session.await_args_list == [
        call(desktop_key),
        call(topic_key),
    ]

    assert await runtime.cancel_conversation_work(conversation_id) == 5
    assert cancel_session.await_args_list == [call(desktop_key), call(topic_key)]

    session_manager = MagicMock()
    runtime._session_manager = session_manager
    runtime.delete_conversation_sessions(conversation_id)
    assert session_manager.delete_session.call_args_list == [
        call(desktop_key),
        call(topic_key),
    ]
    assert runtime.messengers.session_keys_for_conversation(conversation_id) == set()
    assert runtime.session_keys_for_conversation(conversation_id) == {desktop_key}


@pytest.mark.asyncio
async def test_agent_loop_cancel_all_sessions_drains_root_turn_and_exact_session_subagents() -> (
    None
):
    loop = AgentLoop.__new__(AgentLoop)
    manager = SubagentManager.__new__(SubagentManager)
    stopped: list[str] = []

    async def work(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(name)

    root_key = "whatsapp:15551234567"
    root_task = asyncio.create_task(work(root_key))
    subagent_tasks = {
        session_key: asyncio.create_task(work(session_key))
        for _name, _label, session_key, _chat_id in EXACT_MESSENGER_IDENTITIES
    }
    await asyncio.sleep(0)
    manager._running_tasks = {
        f"subagent-{index}": task for index, task in enumerate(subagent_tasks.values())
    }
    manager._session_tasks = {
        session_key: {f"subagent-{index}"} for index, session_key in enumerate(subagent_tasks)
    }
    loop.subagents = manager
    loop._active_tasks = {root_key: [root_task]}

    assert await loop.cancel_all_sessions() == 4
    assert root_task.done()
    assert all(task.done() for task in subagent_tasks.values())
    assert set(stopped) == {root_key, *subagent_tasks}
    assert loop._active_tasks == {}


@pytest.mark.asyncio
async def test_ipc_stop_and_delete_pass_raw_conversation_id_before_session_deletion(
    db: CollieDB,
) -> None:
    conversation_id = str(db.create_conversation("Exact identity lifecycle")["id"])
    order: list[tuple[str, str]] = []

    async def cancel_conversation(value: str) -> int:
        order.append(("cancel", value))
        return 2

    def delete_sessions(value: str) -> None:
        order.append(("delete-sessions", value))

    server = CollieIPCServer(
        db,
        port=0,
        conversation_canceler=cancel_conversation,
        conversation_deleter=delete_sessions,
    )
    server._prune_conversation_media = MagicMock()  # type: ignore[method-assign]

    stopped = await server._cmd_stop(  # noqa: SLF001
        MagicMock(),
        {"conversation_id": conversation_id},
    )
    assert stopped["stopped"] is True
    assert stopped["cancelled_subagents"] == 2

    deleted = await server._cmd_delete_conversation(  # noqa: SLF001
        MagicMock(),
        {"conversation_id": conversation_id},
    )
    assert deleted == {"deleted": True}
    assert order == [
        ("cancel", conversation_id),
        ("cancel", conversation_id),
        ("delete-sessions", conversation_id),
    ]
    assert not conversation_id.startswith("collie:")
    assert db.get_conversation(conversation_id) is None


@pytest.mark.asyncio
async def test_subagent_cancel_all_drains_every_task_and_runtime_shutdown_uses_it(
    runtime: CollieRuntime,
) -> None:
    manager = SubagentManager.__new__(SubagentManager)
    stopped: list[str] = []

    async def work(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(name)

    first = asyncio.create_task(work("first"))
    second = asyncio.create_task(work("second"))
    await asyncio.sleep(0)
    manager._running_tasks = {"first": first, "second": second}

    assert await manager.cancel_all() == 2
    assert first.done() and second.done()
    assert sorted(stopped) == ["first", "second"]

    shutdown_order: list[str] = []
    cancel_all_sessions = AsyncMock(side_effect=lambda: shutdown_order.append("sessions") or 3)
    cancel_all = AsyncMock(side_effect=lambda: shutdown_order.append("subagents") or 2)
    stop = MagicMock(side_effect=lambda: shutdown_order.append("stop"))
    close_provider = MagicMock(side_effect=lambda: shutdown_order.append("provider-close"))
    runtime.loop = SimpleNamespace(
        subagents=SimpleNamespace(cancel_all=cancel_all),
        cancel_all_sessions=cancel_all_sessions,
        stop=stop,
        llm_runtime=MagicMock(
            return_value=SimpleNamespace(provider=SimpleNamespace(close=close_provider))
        ),
    )

    await runtime._shutdown_loop()

    cancel_all_sessions.assert_awaited_once_with()
    cancel_all.assert_awaited_once_with()
    assert shutdown_order == ["stop", "sessions", "subagents", "provider-close"]
    stop.assert_called_once_with()
    close_provider.assert_called_once_with()
    assert runtime.loop is None
