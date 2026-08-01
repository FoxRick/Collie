"""Tests for /stop task cancellation."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import GenerationSettings
from nanobot.session.keys import UNIFIED_SESSION_KEY
from nanobot.utils.llm_runtime import LLMRuntime

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _runtime(provider: MagicMock | None = None) -> LLMRuntime:
    provider = provider or MagicMock()
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)


def _make_loop(*, tools_config=None):
    """Create a minimal AgentLoop with mocked dependencies."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace, tools_config=tools_config)
    return loop, bus


class TestDispatch:
    async def test_dispatch_processes_and_publishes(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        loop._process_message = AsyncMock(
            return_value=OutboundMessage(channel="test", chat_id="c1", content="hi")
        )
        await loop._dispatch(msg)
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert out.content == "hi"

    @pytest.mark.asyncio
    async def test_dispatch_streaming_preserves_message_metadata(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.bus.outbound_events import StreamDeltaEvent, StreamEndEvent

        loop, bus = _make_loop()
        msg = InboundMessage(
            channel="matrix",
            sender_id="u1",
            chat_id="!room:matrix.org",
            content="hello",
            metadata={
                "_wants_stream": True,
                "thread_root_event_id": "$root1",
                "thread_reply_to_event_id": "$reply1",
            },
        )

        async def fake_process(_msg, *, on_stream=None, on_stream_end=None, **kwargs):
            assert on_stream is not None
            assert on_stream_end is not None
            await on_stream("hi")
            await on_stream_end(resuming=False)
            return None

        loop._process_message = fake_process

        await loop._dispatch(msg)
        first = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        second = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert first.metadata["thread_root_event_id"] == "$root1"
        assert first.metadata["thread_reply_to_event_id"] == "$reply1"
        assert isinstance(first.event, StreamDeltaEvent)
        assert second.metadata["thread_root_event_id"] == "$root1"
        assert second.metadata["thread_reply_to_event_id"] == "$reply1"
        assert isinstance(second.event, StreamEndEvent)

    @pytest.mark.asyncio
    async def test_processing_lock_serializes(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        order = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def mock_process(m, **kwargs):
            order.append(f"start-{m.content}")
            if m.content == "a":
                first_started.set()
                await release_first.wait()
            order.append(f"end-{m.content}")
            return OutboundMessage(channel="test", chat_id="c1", content=m.content)

        loop._process_message = mock_process
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

        t1 = asyncio.create_task(loop._dispatch(msg1))
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        t2 = asyncio.create_task(loop._dispatch(msg2))
        await asyncio.sleep(0)
        assert order == ["start-a"]

        release_first.set()
        await asyncio.gather(t1, t2)
        assert order == ["start-a", "end-a", "start-b", "end-b"]


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        mgr = SubagentManager(
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        mgr = SubagentManager(
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        assert await mgr.cancel_by_session("nonexistent") == 0

class TestSubagentAnnounceSessionKey:
    """Verify _announce_result uses the effective session key for mid-turn routing."""

    def _make_mgr(self):
        """Create a SubagentManager with mocked deps and its bus."""
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        mgr = SubagentManager(
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        return mgr, bus

    @pytest.mark.asyncio
    async def test_announce_uses_effective_key_in_unified_mode(self):
        """In unified session mode, session_key_override must be 'unified:default'
        so the result matches the pending queue key."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "telegram", "chat_id": "111", "session_key": UNIFIED_SESSION_KEY}
        await mgr._announce_result("sub-1", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == UNIFIED_SESSION_KEY
        assert msg.session_key == UNIFIED_SESSION_KEY

    @pytest.mark.asyncio
    async def test_announce_uses_raw_key_in_normal_mode(self):
        """Without unified sessions, session_key_override is the raw channel:chat_id."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "telegram", "chat_id": "222", "session_key": "telegram:222"}
        await mgr._announce_result("sub-2", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == "telegram:222"
        assert msg.session_key == "telegram:222"

    @pytest.mark.asyncio
    async def test_announce_falls_back_to_origin_when_no_session_key(self):
        """When session_key is None, fallback to f'{channel}:{chat_id}'."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "discord", "chat_id": "333", "session_key": None}
        await mgr._announce_result("sub-3", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == "discord:333"
        assert msg.channel == "system"
        assert msg.chat_id == "discord:333"

    @pytest.mark.asyncio
    async def test_session_key_flows_through_run_subagent(self):
        """Verify session_key in origin propagates from _run_subagent to _announce_result."""
        from nanobot.agent.subagent import SubagentStatus

        mgr, bus = self._make_mgr()

        async def fake_run(spec):
            return SimpleNamespace(
                stop_reason="done",
                final_content="done",
                error=None,
                tool_events=[],
            )

        mgr.runner.run = AsyncMock(side_effect=fake_run)

        status = SubagentStatus(
            task_id="sub-4", label="label", task_description="task",
            started_at=time.monotonic(),
        )
        await mgr._run_subagent(
            "sub-4", "task", "label",
            {"channel": "telegram", "chat_id": "444", "session_key": UNIFIED_SESSION_KEY},
            status,
            _runtime(),
        )

        msg = await bus.consume_inbound()
        assert msg.session_key_override == UNIFIED_SESSION_KEY
