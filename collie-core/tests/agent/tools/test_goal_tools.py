"""Focused contracts for session-scoped sustained-goal tools."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanobot.agent.goal_permission import goal_mutation_permission
from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.goal import CreateGoalTool, GetGoalTool, UpdateGoalTool
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.runtime_events import GoalStateChanged, RuntimeEventBus
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager


def _request(
    session_key: str,
    *,
    metadata: dict | None = None,
    original_user_text: str = "work",
) -> RequestContext:
    channel, chat_id = session_key.split(":", 1)
    return RequestContext(
        channel=channel,
        chat_id=chat_id,
        session_key=session_key,
        original_user_text=original_user_text,
        metadata=dict(metadata or {}),
    )


def _payload(result: str) -> dict:
    return json.loads(result)


def test_goal_tools_are_discoverable_with_strict_schemas(tmp_path):
    discovered = ToolLoader().discover()
    assert CreateGoalTool in discovered
    assert GetGoalTool in discovered
    assert UpdateGoalTool in discovered

    registry = ToolRegistry()
    registered = ToolLoader(
        test_classes=[CreateGoalTool, GetGoalTool, UpdateGoalTool]
    ).load(
        ToolContext(
            config=SimpleNamespace(),
            workspace=str(tmp_path),
            sessions=SessionManager(tmp_path / "workspace"),
        ),
        registry,
    )
    assert set(registered) == {"create_goal", "get_goal", "update_goal"}
    assert registry.get("create_goal").parameters["required"] == ["objective"]
    assert registry.get("get_goal").read_only is True
    assert registry.get("update_goal").parameters["properties"]["action"]["enum"] == [
        "complete",
        "cancel",
        "block",
        "replace",
    ]
    assert registry.get("create_goal").validate_params({"objective": "   "}) == [
        "objective must not be blank"
    ]


@pytest.mark.asyncio
async def test_create_requires_complete_trusted_marker_not_user_text(tmp_path):
    sessions = SessionManager(tmp_path)
    tool = CreateGoalTool(sessions)

    attempts = [
        _request("cli:one", original_user_text="/goal spoofed"),
        _request("cli:one", metadata={"goal_requested": True}),
        _request("cli:one", metadata={"original_command": "/goal"}),
    ]
    for request in attempts:
        with request_context(request), goal_mutation_permission(True):
            result = await tool.execute("Must not persist")
        assert getattr(result, "is_error", False) is True
    assert GOAL_STATE_KEY not in sessions.get_or_create("cli:one").metadata

    trusted = _request(
        "cli:one",
        metadata={"goal_requested": True, "original_command": "/goal"},
    )
    with request_context(trusted):
        result = await tool.execute("Still unauthorized without the runner binding")
    assert getattr(result, "is_error", False) is True


@pytest.mark.asyncio
async def test_create_get_persist_and_isolate_sessions_and_publish_event(tmp_path):
    sessions = SessionManager(tmp_path)
    events = RuntimeEventBus()
    seen: list[GoalStateChanged] = []
    events.subscribe(seen.append, GoalStateChanged)
    create = CreateGoalTool(sessions, events)
    get = GetGoalTool(sessions, events)
    trusted = {"goal_requested": True, "original_command": "/goal"}

    with request_context(_request("cli:one", metadata=trusted)), goal_mutation_permission(True):
        created = _payload(await create.execute("Ship the durable result", "Ship result"))
    assert created["active"] is True
    assert created["status"] == "active"
    assert seen[-1].context.session_key == "cli:one"

    with request_context(_request("cli:one")):
        assert _payload(await get.execute())["objective"] == "Ship the durable result"
    with request_context(_request("cli:two")):
        assert _payload(await get.execute()) == {"active": False, "status": "none"}

    reloaded = SessionManager(tmp_path)
    assert reloaded.get_or_create("cli:one").metadata[GOAL_STATE_KEY]["status"] == "active"
    assert GOAL_STATE_KEY not in reloaded.get_or_create("cli:two").metadata

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "status"),
    [("complete", "completed"), ("cancel", "cancelled"), ("block", "blocked")],
)
async def test_terminal_transitions_require_active_goal_and_reset_rounds(
    tmp_path,
    action,
    status,
):
    sessions = SessionManager(tmp_path / action)
    session = sessions.get_or_create("cli:goal")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish",
    }
    session.metadata["_sustained_goal_continuation_rounds"] = 7
    sessions.save(session)
    update = UpdateGoalTool(sessions)

    with request_context(_request("cli:goal")), goal_mutation_permission(True):
        result = _payload(await update.execute(action))
    assert result["active"] is False
    assert result["status"] == status
    assert "_sustained_goal_continuation_rounds" not in session.metadata

    with request_context(_request("cli:goal")), goal_mutation_permission(True):
        rejected = await update.execute("complete")
    assert getattr(rejected, "is_error", False) is True


@pytest.mark.asyncio
async def test_replace_requires_trusted_start_and_resets_active_goal(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:goal")
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "Old"}
    session.metadata["_sustained_goal_continuation_rounds"] = 5
    update = UpdateGoalTool(sessions)

    with request_context(_request("cli:goal")), goal_mutation_permission(True):
        rejected = await update.execute("replace", "New")
    assert getattr(rejected, "is_error", False) is True
    assert session.metadata[GOAL_STATE_KEY]["objective"] == "Old"

    trusted = {"goal_requested": True, "original_command": "/goal"}
    with request_context(_request("cli:goal", metadata=trusted)), goal_mutation_permission(True):
        replaced = _payload(await update.execute("replace", "New", "New goal"))
    assert replaced["objective"] == "New"
    assert replaced["status"] == "active"
    assert "_sustained_goal_continuation_rounds" not in session.metadata


@pytest.mark.asyncio
async def test_goal_guidance_only_renders_for_explicit_start_or_active_goal(tmp_path):
    sessions = SessionManager(tmp_path)
    provider = CreateGoalTool(sessions).runtime_context_provider()

    assert await provider(_request("cli:none")) is None

    trusted = {"goal_requested": True, "original_command": "/goal"}
    start_block = await provider(_request("cli:start", metadata=trusted))
    assert start_block is not None
    assert "Record the sustained goal promptly" in start_block.content

    session = sessions.get_or_create("cli:active")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Persisted objective",
    }
    active_block = await provider(_request("cli:active"))
    assert active_block is not None
    assert "Persisted objective" in active_block.content
    assert "Execute sustained work" in active_block.content
    assert "Record the sustained goal promptly" not in active_block.content


@pytest.mark.asyncio
async def test_get_and_update_accept_legacy_goal_key(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:legacy")
    session.metadata["thread_goal"] = {"status": "active", "objective": "Legacy"}
    get = GetGoalTool(sessions)
    update = UpdateGoalTool(sessions)

    with request_context(_request("cli:legacy")):
        assert _payload(await get.execute())["objective"] == "Legacy"
    with request_context(_request("cli:legacy")), goal_mutation_permission(True):
        completed = _payload(await update.execute("complete"))
    assert completed["status"] == "completed"
    assert "thread_goal" not in session.metadata
