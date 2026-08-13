"""Tests for subagent observability — settled-status retention and roster feeds.

Covers the bounded "recent" retention added for the live roster + pet popup:
terminal outcome/ended_at recording (ok / error / cancelled), the
``get_recent_statuses_by_session`` surface, and the runtime/IPC plumbing that
turns the manager's monotonic clocks into UI-safe wall-clock rows.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collie_core.ipc.server import CollieIPCServer
from collie_core.runtime import CollieRuntime
from nanobot.agent import SubagentManager
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.subagent import SubagentStatus
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime


def _manager(tmp_path, **kw) -> SubagentManager:
    defaults = dict(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _runtime(*, model: str = "test-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


def _spawn_kwargs(session_key: str, task: str, **extra) -> dict:
    """Spawn kwargs scoped to a collie conversation session."""
    return {
        "task": task,
        "origin_channel": "collie",
        "origin_chat_id": session_key.rsplit(":", 1)[-1],
        "session_key": session_key,
        "runtime": _runtime(),
        **extra,
    }


async def _drain(sm: SubagentManager) -> None:
    tasks = [t for t in sm._running_tasks.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Settled-status retention (SubagentManager)
# ---------------------------------------------------------------------------


class TestSettledRetention:
    @pytest.mark.asyncio
    async def test_successful_spawn_retains_settled_status(self, tmp_path) -> None:
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="Barcelona sorted!",
                messages=[],
                stop_reason="completed",
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            result = await sm.spawn(
                **_spawn_kwargs("collie:conv-1", "Plan Barcelona", label="Trip Planner")
            )
            task_id = result.split("id: ")[1].split(")")[0]
            await _drain(sm)

        assert sm.get_running_count() == 0
        recent = sm.get_recent_statuses_by_session("collie:conv-1")
        assert len(recent) == 1
        row = recent[0]
        assert row["id"] == task_id
        assert row["name"] == "Trip Planner"
        assert row["phase"] == "done"
        assert row["outcome"] == "ok"
        assert row["ended_at"] is not None
        assert row["ended_at"] >= row["started_at"]

    @pytest.mark.asyncio
    async def test_tool_error_records_error_outcome(self, tmp_path) -> None:
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content=None,
                messages=[],
                stop_reason="tool_error",
                tool_events=[{"name": "read_file", "status": "error", "detail": "nope"}],
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "Do the thing"))
            await _drain(sm)

        recent = sm.get_recent_statuses_by_session("collie:conv-1")
        assert len(recent) == 1
        assert recent[0]["phase"] == "done"
        assert recent[0]["outcome"] == "error"

    @pytest.mark.asyncio
    async def test_exception_records_error_outcome(self, tmp_path) -> None:
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "Do the thing"))
            await _drain(sm)

        recent = sm.get_recent_statuses_by_session("collie:conv-1")
        assert len(recent) == 1
        assert recent[0]["phase"] == "error"
        assert recent[0]["outcome"] == "error"
        assert recent[0]["ended_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel_records_cancelled_outcome(self, tmp_path) -> None:
        sm = _manager(tmp_path)

        async def _slow_run(*args, **kwargs):
            await asyncio.sleep(30)

        sm.runner.run = AsyncMock(side_effect=_slow_run)
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "Do the thing", label="Web Searcher"))
            cancelled = await sm.cancel_by_session("collie:conv-1")
            await _drain(sm)

        assert cancelled == 1
        recent = sm.get_recent_statuses_by_session("collie:conv-1")
        assert len(recent) == 1
        assert recent[0]["phase"] == "cancelled"
        assert recent[0]["outcome"] == "cancelled"
        assert recent[0]["ended_at"] is not None

    @pytest.mark.asyncio
    async def test_recent_rows_are_session_scoped_and_newest_first(self, tmp_path) -> None:
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="ok",
                messages=[],
                stop_reason="completed",
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            first = (
                (await sm.spawn(**_spawn_kwargs("collie:conv-1", "First", label="A")))
                .split("id: ")[1]
                .split(")")[0]
            )
            await _drain(sm)
            await asyncio.sleep(0.02)
            second = (
                (await sm.spawn(**_spawn_kwargs("collie:conv-1", "Second", label="B")))
                .split("id: ")[1]
                .split(")")[0]
            )
            await _drain(sm)
            await sm.spawn(**_spawn_kwargs("collie:conv-2", "Other conv", label="C"))
            await _drain(sm)

        rows = sm.get_recent_statuses_by_session("collie:conv-1")
        assert [r["id"] for r in rows] == [second, first]
        assert len(sm.get_recent_statuses_by_session("collie:conv-2")) == 1

    @pytest.mark.asyncio
    async def test_recent_retention_is_bounded(self, tmp_path) -> None:
        sm = _manager(tmp_path, recent_status_limit=3)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="ok",
                messages=[],
                stop_reason="completed",
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            for index in range(5):
                await sm.spawn(
                    **_spawn_kwargs("collie:conv-1", f"Task {index}", label=f"Agent {index}")
                )
                await _drain(sm)

        rows = sm.get_recent_statuses_by_session("collie:conv-1")
        assert len(rows) == 3
        # Oldest dropped; newest retained.
        assert [r["name"] for r in rows] == ["Agent 4", "Agent 3", "Agent 2"]

    def test_status_defaults_stay_optional(self) -> None:
        status = SubagentStatus(task_id="t", label="l", task_description="d", started_at=1.0)
        assert status.ended_at is None
        assert status.outcome is None
        assert status.session_key is None

    @pytest.mark.asyncio
    async def test_retained_settled_records_are_lightweight_snapshots(self, tmp_path) -> None:
        """The bounded retention keeps a small immutable snapshot, not the
        full live status: no tool_events / usage / error payloads survive."""
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="ok",
                messages=[],
                stop_reason="completed",
                tool_events=[{"name": "read_file", "status": "ok", "detail": "x"}],
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "Do the thing", label="Trip Planner"))
            await _drain(sm)

        retained = list(sm._recent_statuses)
        assert len(retained) == 1
        snapshot = retained[0]
        # Heavy live-state fields must not be retained.
        assert not hasattr(snapshot, "tool_events")
        assert not hasattr(snapshot, "usage")
        assert not hasattr(snapshot, "error")
        assert not hasattr(snapshot, "stop_reason")
        assert not hasattr(snapshot, "iteration")
        # Everything the roster needs is present and correct.
        assert snapshot.session_key == "collie:conv-1"
        assert snapshot.label == "Trip Planner"
        assert snapshot.outcome == "ok"
        assert snapshot.ended_at is not None
        assert snapshot.ended_at >= snapshot.started_at


# ---------------------------------------------------------------------------
# All-session feed getters (SubagentManager)
# ---------------------------------------------------------------------------


class TestAllSessionGetters:
    @pytest.mark.asyncio
    async def test_get_running_statuses_covers_every_session(self, tmp_path) -> None:
        sm = _manager(tmp_path)

        async def _blocking(*args, **kwargs):
            await asyncio.sleep(30)

        sm.runner.run = AsyncMock(side_effect=_blocking)
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "Plan trip", label="Trip Planner"))
            await sm.spawn(**_spawn_kwargs("telegram:42", "Send note", label="Messenger Helper"))
            await asyncio.sleep(0.05)

        rows = sm.get_running_statuses()
        assert len(rows) == 2
        assert {row["session_key"] for row in rows} == {"collie:conv-1", "telegram:42"}
        assert {row["name"] for row in rows} == {"Trip Planner", "Messenger Helper"}
        # Clean up the still-running tasks.
        for task in list(sm._running_tasks.values()):
            task.cancel()
        await _drain(sm)

    @pytest.mark.asyncio
    async def test_get_recent_statuses_covers_every_session_newest_first(self, tmp_path) -> None:
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="ok",
                messages=[],
                stop_reason="completed",
            )
        )
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm.spawn(**_spawn_kwargs("collie:conv-1", "First", label="A"))
            await _drain(sm)
            await asyncio.sleep(0.02)
            await sm.spawn(**_spawn_kwargs("telegram:42", "Second", label="B"))
            await _drain(sm)

        rows = sm.get_recent_statuses()
        assert [row["name"] for row in rows] == ["B", "A"]
        assert {row["session_key"] for row in rows} == {"collie:conv-1", "telegram:42"}
        # The by-session surface still works alongside the all-session one.
        assert len(sm.get_recent_statuses_by_session("collie:conv-1")) == 1


# ---------------------------------------------------------------------------
# Runtime + IPC plumbing
# ---------------------------------------------------------------------------


def _activity_manager(rows_active=None, rows_recent=None):
    """Manager stub exposing the all-session feed used by subagent_activity()."""
    calls = {"active": 0, "recent": 0}

    def _active():
        calls["active"] += 1
        return list(rows_active or [])

    def _recent():
        calls["recent"] += 1
        return list(rows_recent or [])

    return SimpleNamespace(
        get_running_statuses=_active,
        get_recent_statuses=_recent,
        _calls_log=calls,
    )


class TestRuntimeRoster:
    def test_status_includes_recent_agents_with_wall_clock_ms(self, tmp_path) -> None:
        from collie_core.db import CollieDB

        db = CollieDB(tmp_path / "c.db")
        conv = db.create_conversation("trip")
        runtime = CollieRuntime(port=0, db=db)
        now = time.monotonic()

        runtime.loop = SimpleNamespace(
            subagents=_activity_manager(
                rows_recent=[
                    {
                        "id": "abc123",
                        "name": "Trip Planner",
                        "phase": "done",
                        "task_description": "Plan Barcelona",
                        "started_at": now - 10.0,
                        "ended_at": now - 2.0,
                        "outcome": "ok",
                        "execution_posture": "read_only",
                        "session_key": "collie:" + conv["id"],
                    }
                ],
            )
        )
        try:
            status = runtime._status()
            recent = status["recent_agents"]
            assert len(recent) == 1
            row = recent[0]
            assert row["conversation_id"] == conv["id"]
            assert row["outcome"] == "ok"
            assert isinstance(row["started_at_ms"], int)
            assert isinstance(row["ended_at_ms"], int)
            assert row["ended_at_ms"] > row["started_at_ms"]
            # Monotonic -> epoch conversion lands within a second of wall clock.
            assert abs(row["ended_at_ms"] - time.time() * 1000) < 5_000
            assert status["active_agents"] == []
        finally:
            runtime.loop = None
            db.close()

    def test_activity_does_not_include_full_status_payload(self, tmp_path) -> None:
        from collie_core.db import CollieDB

        db = CollieDB(tmp_path / "c.db")
        db.create_conversation("trip")
        runtime = CollieRuntime(port=0, db=db)
        runtime.loop = SimpleNamespace(subagents=_activity_manager())
        try:
            payload = runtime._status()
            assert "recent_agents" in payload
            assert "configured" in payload
        finally:
            runtime.loop = None
            db.close()

    def test_subagent_activity_never_walks_conversation_history(self, tmp_path) -> None:
        """The cheap feed reads the manager directly: db.list_conversations is
        not touched, so cost is independent of how many conversations exist."""
        from collie_core.db import CollieDB

        db = CollieDB(tmp_path / "c.db")
        for index in range(300):
            db.create_conversation(f"conv-{index}")
        runtime = CollieRuntime(port=0, db=db)
        now = time.monotonic()
        manager = _activity_manager(
            rows_active=[
                {
                    "id": "live1",
                    "name": "Trip Planner",
                    "phase": "awaiting_tools",
                    "task_description": "Plan Barcelona",
                    "started_at": now,
                    "execution_posture": "read_only",
                    "session_key": "collie:conv-7",
                }
            ],
            rows_recent=[
                {
                    "id": "done1",
                    "name": "Budget Checker",
                    "phase": "done",
                    "task_description": "Compare prices",
                    "started_at": now - 30,
                    "ended_at": now - 5,
                    "outcome": "ok",
                    "execution_posture": "read_only",
                    "session_key": "collie:conv-7",
                }
            ],
        )
        runtime.loop = SimpleNamespace(subagents=manager)
        try:
            with patch.object(db, "list_conversations", wraps=db.list_conversations) as spy:
                activity = runtime.subagent_activity()
            spy.assert_not_called()
            # One all-session pass, not one pass per conversation.
            assert manager._calls_log == {"active": 1, "recent": 1}
            assert [row["id"] for row in activity["active_agents"]] == ["live1"]
            assert [row["id"] for row in activity["recent_agents"]] == ["done1"]
        finally:
            runtime.loop = None
            db.close()

    def test_messenger_session_rows_surface_without_conversation_id(self, tmp_path) -> None:
        """Messenger session keys cannot be reverse-mapped to a desktop
        conversation, so those rows get conversation_id '' (roster-only)."""
        from collie_core.db import CollieDB

        db = CollieDB(tmp_path / "c.db")
        runtime = CollieRuntime(port=0, db=db)
        now = time.monotonic()
        manager = _activity_manager(
            rows_active=[
                {
                    "id": "live1",
                    "name": "Trip Planner",
                    "phase": "awaiting_tools",
                    "task_description": "Plan Barcelona",
                    "started_at": now,
                    "execution_posture": "read_only",
                    "session_key": "collie:conv-9",
                }
            ],
            rows_recent=[
                {
                    "id": "done1",
                    "name": "Messenger Helper",
                    "phase": "done",
                    "task_description": "Send note",
                    "started_at": now - 30,
                    "ended_at": now - 5,
                    "outcome": "ok",
                    "execution_posture": "read_only",
                    "session_key": "telegram:42",
                }
            ],
        )
        runtime.loop = SimpleNamespace(subagents=manager)
        try:
            activity = runtime.subagent_activity()
            by_id = {
                row["id"]: row for row in activity["active_agents"] + activity["recent_agents"]
            }
            assert by_id["live1"]["conversation_id"] == "conv-9"
            assert by_id["done1"]["conversation_id"] == ""
        finally:
            runtime.loop = None
            db.close()


class TestIpcActivity:
    @pytest.mark.asyncio
    async def test_get_subagent_activity_uses_dedicated_activity_provider(self, tmp_path) -> None:
        db = MagicMock()
        status_provider = MagicMock(
            return_value={
                "configured": True,
                "active_agents": [{"id": "a1"}],
                "recent_agents": [{"id": "a2"}],
            }
        )
        activity_provider = MagicMock(
            return_value={
                "active_agents": [{"id": "a1", "name": "Working"}],
                "recent_agents": [{"id": "a2", "name": "Done"}],
            }
        )
        srv = CollieIPCServer(
            db,
            status_provider=status_provider,
            activity_provider=activity_provider,
        )
        result = await srv._cmd_get_subagent_activity(MagicMock(), {})
        assert result == {
            "active_agents": [{"id": "a1", "name": "Working"}],
            "recent_agents": [{"id": "a2", "name": "Done"}],
        }
        activity_provider.assert_called_once_with()
        status_provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_subagent_activity_falls_back_to_status_provider(self, tmp_path) -> None:
        db = MagicMock()
        srv = CollieIPCServer(
            db,
            status_provider=lambda: {
                "configured": True,
                "active_agents": [{"id": "a1", "name": "Working"}],
                "recent_agents": [{"id": "a2", "name": "Done"}],
                "conversations": 99,
            },
        )
        result = await srv._cmd_get_subagent_activity(MagicMock(), {})
        assert result == {
            "active_agents": [{"id": "a1", "name": "Working"}],
            "recent_agents": [{"id": "a2", "name": "Done"}],
        }

    @pytest.mark.asyncio
    async def test_get_subagent_activity_without_provider(self, tmp_path) -> None:
        srv = CollieIPCServer(MagicMock())
        result = await srv._cmd_get_subagent_activity(MagicMock(), {})
        assert result == {"active_agents": [], "recent_agents": []}
