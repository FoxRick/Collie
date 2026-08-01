from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.runtime_context import public_history_message
from nanobot.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from nanobot.session.goal_state import GOAL_STATE_KEY


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=4096)
    provider.estimate_prompt_tokens.return_value = (0, "test-counter")
    response = LLMResponse(content="done", tool_calls=[])
    provider.chat_with_retry = AsyncMock(return_value=response)
    provider.chat_stream_with_retry = AsyncMock(return_value=response)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    loop._connect_mcp = AsyncMock()  # type: ignore[method-assign]
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


@pytest.mark.asyncio
async def test_process_direct_runs_goal_continuation_with_same_turn_dependencies(
    tmp_path: Path,
) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    session_key = "collie:desktop-goal"
    session = loop.sessions.get_or_create(session_key)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the desktop goal.",
    }
    loop.sessions.save(session)

    progress: list[str] = []
    stream: list[str] = []
    stream_ends: list[bool] = []

    async def on_progress(content: str, **_kwargs: object) -> None:
        progress.append(content)

    async def on_stream(content: str) -> None:
        stream.append(content)

    async def on_stream_end(*, resuming: bool = False) -> None:
        stream_ends.append(resuming)

    runtime = loop.llm_runtime()
    turn_tools = ToolRegistry()
    seen: list[dict[str, object]] = []

    async def fake_run_agent_loop(initial_messages, **kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            return (
                "paused",
                [],
                [*initial_messages, {"role": "assistant", "content": "paused"}],
                "max_iterations",
                False,
            )
        await kwargs["on_progress"]("continuing")
        await kwargs["on_stream"]("done")
        await kwargs["on_stream_end"](resuming=False)
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "completed",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    response = await loop.process_direct(
        "start the goal",
        session_key=session_key,
        channel="collie",
        chat_id="desktop-goal",
        on_progress=on_progress,
        on_stream=on_stream,
        on_stream_end=on_stream_end,
        tools=turn_tools,
        runtime=runtime,
        permission_context={"execution_mode": "execute", "run_id": "run-1"},
        workspace_scope={"project_path": "C:/safe/project", "access_mode": "restricted"},
    )

    assert response is not None
    assert response.content == "done"
    assert len(seen) == 2
    assert all(call["runtime"] is runtime for call in seen)
    assert all(call["tools"] is turn_tools for call in seen)
    assert all(call["on_progress"] is on_progress for call in seen)
    assert all(call["on_stream"] is on_stream for call in seen)
    assert all(call["on_stream_end"] is on_stream_end for call in seen)
    assert all(call["metadata"]["permission_context"]["run_id"] == "run-1" for call in seen)
    assert all(
        call["metadata"][WORKSPACE_SCOPE_METADATA_KEY]["project_path"] == "C:/safe/project"
        for call in seen
    )
    assert progress == ["continuing"]
    assert stream == ["done"]
    assert stream_ends == [False]
    assert session_key not in loop._pending_queues

    history = [
        {key: value for key, value in message.items() if key in {"role", "content"}}
        for message in map(
            public_history_message,
            loop.sessions.get_or_create(session_key).messages,
        )
    ]
    assert history == [
        {"role": "user", "content": "start the goal"},
        {"role": "assistant", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_process_direct_republishes_non_continuation_leftovers(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    injected = InboundMessage(
        channel="collie",
        sender_id="user",
        chat_id="conversation",
        content="new user message",
        session_key_override="collie:conversation",
    )

    async def fake_process(msg: InboundMessage, **kwargs):
        kwargs["pending_queue"].put_nowait(injected)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="done")

    loop._process_message = fake_process  # type: ignore[method-assign]
    response = await loop.process_direct(
        "initial",
        session_key="collie:conversation",
        channel="collie",
        chat_id="conversation",
    )

    assert response is not None
    assert response.content == "done"
    assert await asyncio.wait_for(loop.bus.consume_inbound(), timeout=1) is injected
    assert "collie:conversation" not in loop._pending_queues


@pytest.mark.asyncio
async def test_active_session_lock_survives_more_than_512_other_keys(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    active_started = asyncio.Event()
    release_active = asyncio.Event()
    second_started = asyncio.Event()
    order: list[str] = []

    async def fake_process(msg: InboundMessage, **_kwargs):
        if msg.content == "active":
            order.append("active-start")
            active_started.set()
            await release_active.wait()
            order.append("active-end")
        elif msg.content == "second":
            order.append("second-start")
            second_started.set()
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=msg.content)

    loop._process_message = fake_process  # type: ignore[method-assign]
    active = asyncio.create_task(loop.process_direct("active", session_key="collie:target"))
    await asyncio.wait_for(active_started.wait(), timeout=1)

    for index in range(520):
        await loop.process_direct("filler", session_key=f"collie:other-{index}")

    second = asyncio.create_task(loop.process_direct("second", session_key="collie:target"))
    await asyncio.sleep(0)

    assert not second_started.is_set()
    assert loop._session_locks["collie:target"].users == 2
    assert len(loop._session_locks) <= 512

    release_active.set()
    await asyncio.gather(active, second)
    assert order == ["active-start", "active-end", "second-start"]


@pytest.mark.asyncio
async def test_session_lock_registry_evicts_oldest_idle_entry(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    async def fake_process(msg: InboundMessage, **_kwargs):
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=msg.content)

    loop._process_message = fake_process  # type: ignore[method-assign]
    for index in range(513):
        await loop.process_direct("turn", session_key=f"collie:idle-{index}")

    assert len(loop._session_locks) == 512
    assert "collie:idle-0" not in loop._session_locks
    assert "collie:idle-1" in loop._session_locks
    assert "collie:idle-512" in loop._session_locks
    assert all(entry.users == 0 for entry in loop._session_locks.values())


@pytest.mark.asyncio
async def test_two_rapid_direct_turns_do_not_interleave_persisted_history(
    tmp_path: Path,
) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        latest_user = next(
            message["content"]
            for message in reversed(initial_messages)
            if message.get("role") == "user"
        )
        if latest_user == "first":
            first_started.set()
            await release_first.wait()
        content = f"reply:{latest_user}"
        return (
            content,
            [],
            [*initial_messages, {"role": "assistant", "content": content}],
            "completed",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    session_key = "collie:rapid"
    first = asyncio.create_task(loop.process_direct("first", session_key=session_key))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(loop.process_direct("second", session_key=session_key))
    await asyncio.sleep(0)

    current = loop.sessions.get_or_create(session_key)
    assert [message["content"] for message in current.messages if message["role"] == "user"] == [
        "first"
    ]

    release_first.set()
    first_response, second_response = await asyncio.gather(first, second)
    assert first_response is not None and first_response.content == "reply:first"
    assert second_response is not None and second_response.content == "reply:second"

    history = [
        {key: value for key, value in message.items() if key in {"role", "content"}}
        for message in map(
            public_history_message,
            loop.sessions.get_or_create(session_key).messages,
        )
    ]
    assert history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply:first"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "reply:second"},
    ]


@pytest.mark.asyncio
async def test_cancelling_direct_continuation_cleans_queue_and_lock(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    session_key = "collie:cancel-continuation"
    session = loop.sessions.get_or_create(session_key)
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "Keep going."}
    loop.sessions.save(session)
    continuation_started = asyncio.Event()

    async def fake_run_agent_loop(initial_messages, **kwargs):
        if kwargs["metadata"].get("_internal_continuation") is True:
            continuation_started.set()
            await asyncio.Event().wait()
        return (
            "paused",
            [],
            [*initial_messages, {"role": "assistant", "content": "paused"}],
            "max_iterations",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    task = asyncio.create_task(loop.process_direct("start", session_key=session_key))
    await asyncio.wait_for(continuation_started.wait(), timeout=2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert session_key not in loop._pending_queues
    assert loop._session_locks[session_key].users == 0
