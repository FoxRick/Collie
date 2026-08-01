"""Slash-command and chat-created capability coverage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from collie_core.commands import CommandController, parse_command
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext
from collie_core.runtime import CollieRuntime
from collie_core.subagents.loader import SubagentLoader
from collie_core.tools.capabilities import (
    CreateSkillTool,
    CreateSubagentTool,
    LoadSkillTool,
    create_workspace_skill,
)
from nanobot.agent.tools.loader import ToolLoader


def _controller(tmp_path: Path):
    db = CollieDB(tmp_path / "collie.db")
    workspace = tmp_path / "workspace"
    loader = SubagentLoader(workspace, db)
    loader.create(
        "Researcher",
        description="Finds reliable sources.",
        system_prompt="Research carefully.",
    )
    controller = CommandController(
        workspace=workspace,
        subagent_loader=loader,
        loop_provider=lambda: None,
        status_provider=lambda: {"model": "test-model", "active_agents": []},
    )
    return db, workspace, loader, controller


def test_command_parser_only_accepts_standalone_commands() -> None:
    assert parse_command("/agents") == ("agents", "")
    assert parse_command("/agent Researcher compare plans") == (
        "agent",
        "Researcher compare plans",
    )
    assert parse_command("please /compact") is None


@pytest.mark.asyncio
async def test_natural_model_identity_query_is_deterministic(tmp_path: Path) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    try:
        for query in (
            "Which model is currently in use?",
            "What model are you currently using?",
            "What is the current LLM?",
        ):
            result = await controller.execute(
                query,
                session_key="collie:one",
                origin="desktop",
            )
            assert result is not None
            assert result["handled"] is True
            assert result["content"] == "I'm currently using **test-model**."
            assert result["card_data"]["model"] == "test-model"

        unrelated = await controller.execute(
            "Which model should I use for coding?",
            session_key="collie:one",
            origin="desktop",
        )
        assert unrelated is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dynamic_agents_and_forwarding(tmp_path: Path) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    try:
        menu = await controller.execute(
            "/agents",
            session_key="collie:one",
            origin="desktop",
        )
        assert menu is not None
        assert menu["handled"] is True
        assert menu["card_type"] == "capability_list"
        assert menu["card_data"]["items"][0]["name"] == "Researcher"

        invocation = await controller.execute(
            "/agent Researcher compare these plans",
            session_key="collie:one",
            origin="desktop",
        )
        assert invocation is not None
        assert invocation["handled"] is False
        assert "call_subagent" in invocation["forward_prompt"]
        assert "message_metadata" not in invocation
    finally:
        db.close()


@pytest.mark.asyncio
async def test_goal_command_requires_objective_and_returns_trusted_metadata(
    tmp_path: Path,
) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    try:
        goal_command = next(
            item for item in controller.catalog()["commands"] if item["name"] == "goal"
        )
        assert goal_command["usage"] == "/goal <objective>"

        empty = await controller.execute(
            "/goal",
            session_key="collie:one",
            origin="desktop",
        )
        assert empty == {
            "handled": True,
            "content": "Tell me the objective after the command: `/goal <objective>`.",
        }

        unknown = await controller.execute(
            "/goals keep working",
            session_key="collie:one",
            origin="desktop",
        )
        assert unknown == {
            "handled": True,
            "content": "I don't know /goals yet. Try /help to see what I can do.",
        }

        goal = await controller.execute(
            "/goal Ship the release without skipping verification",
            session_key="collie:one",
            origin="desktop",
        )
        assert goal == {
            "handled": False,
            "forward_prompt": "Ship the release without skipping verification",
            "message_metadata": {
                "goal_requested": True,
                "original_command": "/goal",
            },
        }
    finally:
        db.close()


@pytest.mark.asyncio
async def test_desktop_goal_metadata_reaches_chat_runner_without_leaking_to_other_turns(
    tmp_path: Path,
) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    calls: list[tuple[str, dict]] = []

    async def chat_runner(content: str, **kwargs):
        calls.append((content, kwargs))
        return SimpleNamespace(content="done")

    server = CollieIPCServer(
        db,
        chat_runner=chat_runner,
        command_runner=controller.execute,
        command_catalog=controller.catalog,
    )

    class Connection:
        def __init__(self) -> None:
            self.frames: list[dict] = []

        async def send(self, raw: str) -> None:
            self.frames.append(json.loads(raw))

    connection = Connection()

    async def submit(content: str, conversation_id: str | None = None) -> str:
        frame = {"type": "chat", "id": content, "content": content}
        if conversation_id is not None:
            frame["conversation_id"] = conversation_id
        await server._cmd_chat(connection, frame)  # type: ignore[arg-type]
        reply = next(
            item
            for item in reversed(connection.frames)
            if item["type"] == "ok" and item["id"] == content
        )
        active_id = str(reply["data"]["conversation_id"])
        task = server._chat_tasks.get(active_id)
        if task is not None:
            await asyncio.wait_for(task, timeout=2)
        return active_id

    try:
        conversation_id = await submit("/goal Finish the migration safely")
        assert calls[0][0] == "Finish the migration safely"
        assert calls[0][1]["message_metadata"] == {
            "goal_requested": True,
            "original_command": "/goal",
        }

        await submit("ordinary message", conversation_id)
        assert calls[1][0] == "ordinary message"
        assert "message_metadata" not in calls[1][1]

        await submit("/agent Researcher compare plans", conversation_id)
        assert "call_subagent" in calls[2][0]
        assert "message_metadata" not in calls[2][1]
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_runtime_chat_forwards_only_explicit_message_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "collie-home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    db = CollieDB(home / "collie.db")
    runtime = CollieRuntime(port=0, db=db)
    process_direct = AsyncMock(return_value=SimpleNamespace(content="done"))
    runtime.loop = SimpleNamespace(process_direct=process_direct, _last_usage={})
    conversation_id = str(db.create_conversation("Goal")['id'])

    async def noop(*_args, **_kwargs) -> None:
        return None

    try:
        await runtime._chat(
            "objective",
            conversation_id=conversation_id,
            on_stream=noop,
            on_progress=noop,
            message_metadata={
                "goal_requested": True,
                "original_command": "/goal",
            },
        )
        assert process_direct.await_args.kwargs["message_metadata"] == {
            "goal_requested": True,
            "original_command": "/goal",
        }

        process_direct.reset_mock()
        await runtime._chat(
            "ordinary",
            conversation_id=conversation_id,
            on_stream=noop,
            on_progress=noop,
        )
        assert process_direct.await_args.kwargs["message_metadata"] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_create_commands_translate_to_explicit_tool_requests(tmp_path: Path) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    try:
        agent = await controller.execute(
            "/create-agent watches release notes",
            session_key="collie:one",
            origin="desktop",
        )
        skill = await controller.execute(
            "/create-skill weekly planning",
            session_key="collie:one",
            origin="desktop",
        )
        assert agent and "create_subagent" in agent["forward_prompt"]
        assert skill and "create_skill" in skill["forward_prompt"]
    finally:
        db.close()


def test_workspace_skill_is_validated_and_loadable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = create_workspace_skill(
        workspace,
        name="Weekly Review",
        description="Run a consistent weekly review.",
        instructions="## Steps\n\n1. Review commitments.\n2. Choose next actions.",
    )
    assert result["name"] == "weekly-review"
    loaded = LoadSkillTool(workspace)._loader.load_skill("weekly-review")
    assert loaded is not None
    assert "Review commitments" in loaded
    assert not (tmp_path / "evil").exists()


def test_capability_tools_require_fresh_review_even_in_plan_mode() -> None:
    evaluator = PermissionEvaluator()
    agent_request = CreateSubagentTool().permission_request({
        "name": "Researcher",
        "description": "Researches",
        "instructions": "Use reliable sources.",
    })
    skill_request = CreateSkillTool(Path(".")).permission_request({
        "name": "weekly-review",
        "description": "Reviews",
        "instructions": "Review the week.",
    })
    context = ExecutionContext(execution_mode="plan")
    assert evaluator.evaluate(context, agent_request).effect == Effect.ASK
    assert evaluator.evaluate(context, skill_request).effect == Effect.ASK
    assert agent_request.hard_approval is True
    assert skill_request.hard_approval is True


def test_capability_tools_are_discoverable() -> None:
    import collie_core.tools as collie_tools

    names = {tool.__name__ for tool in ToolLoader(collie_tools).discover()}
    assert {"CreateSubagentTool", "CreateSkillTool", "LoadSkillTool"} <= names


@pytest.mark.asyncio
async def test_ipc_commands_work_without_a_model_and_persist_rich_cards(
    tmp_path: Path,
) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)

    class Connection:
        def __init__(self) -> None:
            self.frames: list[dict] = []

        async def send(self, raw: str) -> None:
            self.frames.append(json.loads(raw))

    server = CollieIPCServer(
        db,
        chat_runner=None,
        command_runner=controller.execute,
        command_catalog=controller.catalog,
    )
    connection = Connection()
    try:
        await server._cmd_chat(
            connection,  # type: ignore[arg-type]
            {"type": "chat", "id": "one", "content": "/agents"},
        )
        reply = next(frame for frame in connection.frames if frame["type"] == "ok")
        conversation_id = reply["data"]["conversation_id"]
        messages = db.get_messages(conversation_id)
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[-1]["card_type"] == "capability_list"

        connection.frames.clear()
        await server._cmd_chat(
            connection,  # type: ignore[arg-type]
            {
                "type": "chat",
                "id": "model",
                "conversation_id": conversation_id,
                "content": "What model are you using?",
            },
        )
        messages = db.get_messages(conversation_id)
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "I'm currently using **test-model**."
        assert messages[-1]["card_type"] == "status"

        connection.frames.clear()
        await server._cmd_chat(
            connection,  # type: ignore[arg-type]
            {
                "type": "chat",
                "id": "two",
                "conversation_id": conversation_id,
                "content": "/new",
            },
        )
        reply = next(frame for frame in connection.frames if frame["type"] == "ok")
        assert reply["data"]["command_handled"] is True
        assert reply["data"]["conversation_id"] != conversation_id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_messenger_new_cancels_and_deletes_only_short_term_session(
    tmp_path: Path,
) -> None:
    db, _workspace, _loader, _controller_unused = _controller(tmp_path)
    calls: list[tuple[str, str]] = []

    class Sessions:
        def delete_session(self, key: str) -> bool:
            calls.append(("delete", key))
            return True

    class Loop:
        sessions = Sessions()

        async def cancel_session(self, key: str) -> int:
            calls.append(("cancel", key))
            return 1

    controller = CommandController(
        workspace=tmp_path / "workspace",
        subagent_loader=_loader,
        loop_provider=lambda: Loop(),
        status_provider=lambda: {},
    )
    try:
        result = await controller.execute(
            "/new",
            session_key="telegram:42",
            origin="telegram",
        )
        assert result and result["new_conversation"] is False
        assert calls == [("cancel", "telegram:42"), ("delete", "telegram:42")]
    finally:
        db.close()
