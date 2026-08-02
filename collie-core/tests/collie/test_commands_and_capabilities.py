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
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, Risk
from collie_core.permissions.store import PermissionStore
from collie_core.runtime import CollieRuntime
from collie_core.subagents.loader import SubagentLoader
from collie_core.tools.capabilities import (
    CreateSkillTool,
    CreateSubagentTool,
    LoadSkillTool,
    create_workspace_skill,
)
from collie_core.tools.model_switch import (
    SetModelTool,
    bind_model_switcher,
    model_switcher,
)
from nanobot.agent.context import ContextBuilder
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


# ---------------------------------------------------------------------------
# /model command
# ---------------------------------------------------------------------------


def _model_controller(
    tmp_path: Path,
    *,
    switcher=None,
    providers=None,
    authorizer=None,
) -> CommandController:
    return CommandController(
        workspace=tmp_path / "workspace",
        subagent_loader=None,
        loop_provider=lambda: None,
        status_provider=lambda: {
            "model": "deepseek-v4-pro",
            "active_agents": [],
        },
        model_switcher=switcher,
        providers_provider=lambda: providers or [],
        model_authorizer=authorizer,
    )


def test_model_command_is_in_catalog(tmp_path: Path) -> None:
    db, _workspace, _loader, controller = _controller(tmp_path)
    try:
        model_command = next(
            item
            for item in controller.catalog()["commands"]
            if item["name"] == "model"
        )
        assert model_command["usage"] == "/model [model-id]"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_model_command_without_arguments_lists_current_and_providers(
    tmp_path: Path,
) -> None:
    providers = [
        {
            "name": "deepseek",
            "runtime_name": "DeepSeek",
            "model": "deepseek-v4-pro",
            "is_default": 1,
        },
        {
            "name": "openai",
            "runtime_name": "OpenAI",
            "model": "gpt-5.5",
            "is_default": 0,
        },
    ]
    controller = _model_controller(tmp_path, providers=providers)
    result = await controller.execute(
        "/model",
        session_key="collie:one",
        origin="desktop",
    )
    assert result is not None
    assert result["handled"] is True
    assert "**Current model:** deepseek-v4-pro" in result["content"]
    assert "**DeepSeek**: deepseek-v4-pro (active)" in result["content"]
    assert "**OpenAI**: gpt-5.5" in result["content"]
    assert result["card_type"] == "status"
    assert result["card_data"]["model"] == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_model_command_switches_via_runtime_bridge(tmp_path: Path) -> None:
    calls: list[str] = []

    async def switcher(name: str) -> dict:
        calls.append(name)
        return {
            "switched": True,
            "model": name,
            "previous": "deepseek-v4-pro",
            "applied": True,
        }

    controller = _model_controller(tmp_path, switcher=switcher)
    result = await controller.execute(
        "/model deepseek-v4-flash",
        session_key="collie:one",
        origin="desktop",
    )
    assert calls == ["deepseek-v4-flash"]
    assert result is not None
    assert result["handled"] is True
    assert (
        "switched from **deepseek-v4-pro** to **deepseek-v4-flash**"
        in result["content"]
    )
    assert result["card_type"] == "status"
    assert result["card_data"]["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_model_command_reports_unchanged_and_failures(
    tmp_path: Path,
) -> None:
    async def unchanged(name: str) -> dict:
        return {
            "switched": True,
            "model": name,
            "previous": name,
            "unchanged": True,
            "applied": True,
        }

    async def failing(name: str) -> dict:
        return {"switched": False, "error": "that model is not on the menu"}

    controller = _model_controller(tmp_path, switcher=unchanged)
    same = await controller.execute(
        "/model deepseek-v4-pro",
        session_key="collie:one",
        origin="desktop",
    )
    assert same and "already on **deepseek-v4-pro**" in same["content"]

    controller = _model_controller(tmp_path, switcher=failing)
    failed = await controller.execute(
        "/model nope-9",
        session_key="collie:one",
        origin="desktop",
    )
    assert failed and "I couldn't switch models" in failed["content"]
    assert "that model is not on the menu" in failed["content"]

    controller = _model_controller(tmp_path, switcher=None)
    unavailable = await controller.execute(
        "/model deepseek-v4-flash",
        session_key="collie:one",
        origin="desktop",
    )
    assert unavailable and "isn't available" in unavailable["content"]


@ pytest.mark.asyncio
async def test_model_command_switch_requires_approval_and_reports_denial(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def switcher(name: str) -> dict:
        calls.append(name)
        return {"switched": True, "model": name, "applied": True}

    async def denying(context: ExecutionContext, params: dict) -> None:
        raise PermissionDeniedError("You rejected this action.")

    controller = _model_controller(
        tmp_path, switcher=switcher, authorizer=denying
    )
    result = await controller.execute(
        "/model deepseek-v4-flash",
        session_key="collie:one",
        origin="desktop",
        conversation_id="conv_1",
    )
    assert result is not None
    assert result["handled"] is True
    assert "I can't switch models right now" in result["content"]
    assert "You rejected this action." in result["content"]
    assert calls == []  # the switcher never ran without approval


@ pytest.mark.asyncio
async def test_model_command_switch_passes_execution_context_to_authorizer(
    tmp_path: Path,
) -> None:
    seen: list[tuple[ExecutionContext, dict]] = []

    async def approving(context: ExecutionContext, params: dict) -> None:
        seen.append((context, params))

    async def switcher(name: str) -> dict:
        return {"switched": True, "model": name, "applied": True}

    controller = _model_controller(
        tmp_path, switcher=switcher, authorizer=approving
    )
    result = await controller.execute(
        "/model deepseek-v4-flash",
        session_key="collie:one",
        origin="desktop",
        conversation_id="conv_1",
        execution_mode="plan",
    )
    assert result is not None and result["handled"] is True
    assert len(seen) == 1
    context, params = seen[0]
    assert context.conversation_id == "conv_1"
    assert context.execution_mode == "plan"
    assert params == {"model": "deepseek-v4-flash"}


@ pytest.mark.asyncio
async def test_model_command_switch_is_denied_in_plan_mode(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="allow")
    broker = ApprovalBroker(db, evaluator)
    calls: list[str] = []

    async def switcher(name: str) -> dict:
        calls.append(name)
        return {"switched": True, "model": name, "applied": True}

    async def broker_authorizer(
        context: ExecutionContext, params: dict
    ) -> None:
        await broker.authorize(
            context,
            SimpleNamespace(name="set_model", id=""),
            SetModelTool.create(None),
            params,
        )

    controller = _model_controller(
        tmp_path, switcher=switcher, authorizer=broker_authorizer
    )
    result = await controller.execute(
        "/model deepseek-v4-flash",
        session_key="collie:one",
        origin="desktop",
        execution_mode="plan",
    )
    assert result is not None
    assert result["handled"] is True
    assert "I can't switch models right now" in result["content"]
    assert calls == []
    db.close()


# ---------------------------------------------------------------------------
# set_model tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_model_tool_switches_via_bound_runtime() -> None:
    calls: list[str] = []

    async def switcher(name: str) -> dict:
        calls.append(name)
        return {
            "switched": True,
            "model": name,
            "previous": "deepseek-v4-pro",
            "applied": True,
        }

    try:
        bind_model_switcher(switcher)
        assert model_switcher() is switcher
        tool = SetModelTool()
        out = await tool.execute(model="deepseek-v4-flash")
        assert calls == ["deepseek-v4-flash"]
        assert "deepseek-v4-flash" in out
        assert "next messages" in out
    finally:
        bind_model_switcher(None)


@pytest.mark.asyncio
async def test_set_model_tool_errors_are_explicit() -> None:
    try:
        bind_model_switcher(None)
        tool = SetModelTool()
        missing = await tool.execute()
        assert "Which model" in missing
        unbound = await tool.execute(model="deepseek-v4-flash")
        assert "not available" in unbound

        async def failing(name: str) -> dict:
            return {"switched": False, "error": "bad model"}

        bind_model_switcher(failing)
        failed = await tool.execute(model="deepseek-v4-flash")
        assert "bad model" in failed
    finally:
        bind_model_switcher(None)


def test_set_model_tool_permission_is_reversible_local_write() -> None:
    request = SetModelTool().permission_request({"model": "deepseek-v4-flash"})
    assert request.action == "runtime.set_model"
    assert request.resource == "deepseek-v4-flash"
    assert request.risk == Risk.LOCAL_WRITE
    assert request.reversible is True
    assert request.hard_approval is False
    # Provider/settings operations stay approval-gated (never automatic).
    assert request.approval_free is False
    assert request.approve_for_me is False


def test_set_model_tool_is_discoverable() -> None:
    import collie_core.tools as collie_tools

    names = {tool.__name__ for tool in ToolLoader(collie_tools).discover()}
    assert "SetModelTool" in names


# ---------------------------------------------------------------------------
# runtime model switching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_switch_model_persists_and_applies_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "collie-home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    db = CollieDB(home / "collie.db")
    runtime = CollieRuntime(port=0, db=db)
    selected: list[str] = []
    runtime.loop = SimpleNamespace(
        runtime_resolver=SimpleNamespace(
            select_model=lambda name: selected.append(name)
        )
    )
    try:
        result = await runtime._switch_model("deepseek-v4-flash")
        assert result == {
            "switched": True,
            "model": "deepseek-v4-flash",
            "previous": None,
            "applied": True,
        }
        assert db.get_setting("provider.model") == "deepseek-v4-flash"
        assert selected == ["deepseek-v4-flash"]

        again = await runtime._switch_model("deepseek-v4-flash")
        assert again["unchanged"] is True
        assert selected == ["deepseek-v4-flash"]

        bad = await runtime._switch_model("   ")
        assert bad == {"switched": False, "error": "A model name is required."}
    finally:
        db.close()


@pytest.mark.asyncio
async def test_runtime_switch_model_without_loop_persists_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "collie-home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    db = CollieDB(home / "collie.db")
    runtime = CollieRuntime(port=0, db=db)
    try:
        result = await runtime._switch_model("gpt-5.5")
        assert result["switched"] is True
        assert result["applied"] is False
        assert db.get_setting("provider.model") == "gpt-5.5"
    finally:
        db.close()


@ pytest.mark.asyncio
async def test_runtime_switch_model_keeps_provider_row_and_setting_in_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "collie-home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    db = CollieDB(home / "collie.db")
    db.upsert_provider(
        "deepseek",
        name="DeepSeek",
        auth_type="api_key",
        model="deepseek-v4-pro",
        is_default=True,
    )
    db.upsert_provider(
        "openai",
        name="OpenAI",
        auth_type="api_key",
        model="gpt-5.5",
    )
    runtime = CollieRuntime(port=0, db=db)
    try:
        result = await runtime._switch_model("deepseek-v4-flash")
        assert result["switched"] is True
        assert db.get_setting("provider.model") == "deepseek-v4-flash"
        default = db.get_provider("deepseek")
        assert default is not None and default["model"] == "deepseek-v4-flash"
        openai_row = db.get_provider("openai")
        assert openai_row is not None and openai_row["model"] == "gpt-5.5"

        # A provider reconfiguration that re-upserts the row's current model
        # cannot revert the switch — both sources were updated together.
        db.upsert_provider(
            "deepseek",
            name="DeepSeek",
            auth_type="api_key",
            model=str(default["model"]),
            is_default=True,
        )
        assert db.get_setting("provider.model") == "deepseek-v4-flash"
        reupserted = db.get_provider("deepseek")
        assert reupserted is not None and reupserted["model"] == "deepseek-v4-flash"
    finally:
        db.close()


def test_command_guidance_tells_the_agent_about_model_switching(
    tmp_path: Path,
) -> None:
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "skills").mkdir(exist_ok=True)
    builder = ContextBuilder(workspace=tmp_path)
    builder.command_guidance = True
    prompt = builder.build_system_prompt()
    assert "/model" in prompt
    assert "set_model" in prompt
    assert "do not invent other model commands" in prompt
