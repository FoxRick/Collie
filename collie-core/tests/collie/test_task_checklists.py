"""Durable task-checklist storage and plan-run task projections."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from collie_core.automations.scheduler import AutomationScheduler
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, PermissionRequest, Risk
from collie_core.permissions.store import PermissionStore
from collie_core.tools.task_checklists import ManageTaskChecklistTool, bind_task_checklists_db
from nanobot.agent.tools.context import RequestContext, request_context


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    database = CollieDB(tmp_path / "collie.db")
    bind_task_checklists_db(database)
    yield database
    database.close()


def _steps() -> list[dict[str, str]]:
    return [
        {"key": "inspect", "title": "Inspect the options"},
        {"key": "decide", "title": "Choose an approach"},
        {"key": "finish", "title": "Finish the task"},
    ]


def _review_metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "services": [],
        "material_commitment": False,
        "unstable_success_criterion": False,
        "requires_review": False,
    }
    metadata.update(overrides)
    return metadata


def _create_checklist(db: CollieDB, conversation_id: str, *, goal: str = "Plan a trip") -> dict:
    return db.create_task_checklist(
        conversation_id=conversation_id,
        goal=goal,
        steps=_steps(),
    )


def test_v9_persists_a_full_initial_task_snapshot(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")

    task = _create_checklist(db, str(conversation["id"]))

    assert db.schema_version == 12
    assert task["source"] == "checklist"
    assert task["conversation_id"] == conversation["id"]
    assert task["status"] == "active"
    assert task["revision"] == 1
    assert task["title"] == "Plan a trip"
    assert task["completed_count"] == 0
    assert task["total_count"] == 3
    assert task["current_step_key"] is None
    assert task["steps"] == [
        {
            "key": "inspect",
            "title": "Inspect the options",
            "status": "pending",
            "summary": None,
            "error_message": None,
        },
        {
            "key": "decide",
            "title": "Choose an approach",
            "status": "pending",
            "summary": None,
            "error_message": None,
        },
        {
            "key": "finish",
            "title": "Finish the task",
            "status": "pending",
            "summary": None,
            "error_message": None,
        },
    ]
    assert db.get_task_checklist(task["id"]) == task


def test_only_one_active_checklist_may_exist_per_conversation(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    first = _create_checklist(db, str(conversation["id"]))

    with pytest.raises(ValueError):
        _create_checklist(db, str(conversation["id"]), goal="Book a hotel")

    assert db.get_active_task(str(conversation["id"])) == first


def test_active_checklists_are_isolated_by_conversation(db: CollieDB) -> None:
    first_conversation = db.create_conversation("Trip")
    second_conversation = db.create_conversation("Work")
    first = _create_checklist(db, str(first_conversation["id"]))
    second = _create_checklist(db, str(second_conversation["id"]), goal="Prepare report")

    updated = db.update_task_checklist(
        first["id"],
        expected_revision=first["revision"],
        step_key="inspect",
        status="in_progress",
    )

    assert db.get_active_task(str(first_conversation["id"])) == updated
    assert db.get_active_task(str(second_conversation["id"])) == second
    assert db.get_task_checklist(second["id"])["current_step_key"] is None


def test_checklist_transitions_preserve_completed_history_and_one_active_step(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Trip")
    task = _create_checklist(db, str(conversation["id"]))
    started = db.update_task_checklist(
        task["id"],
        expected_revision=task["revision"],
        step_key="inspect",
        status="in_progress",
    )
    completed = db.update_task_checklist(
        task["id"],
        expected_revision=started["revision"],
        step_key="inspect",
        status="completed",
        summary="Three suitable options found.",
    )

    assert completed["current_step_key"] is None
    assert completed["completed_count"] == 1
    assert completed["steps"][0]["summary"] == "Three suitable options found."

    with pytest.raises(ValueError):
        db.update_task_checklist(
            task["id"],
            expected_revision=completed["revision"],
            step_key="inspect",
            status="pending",
        )

    running = db.update_task_checklist(
        task["id"],
        expected_revision=completed["revision"],
        step_key="decide",
        status="in_progress",
    )
    with pytest.raises(ValueError):
        db.update_task_checklist(
            task["id"],
            expected_revision=running["revision"],
            step_key="finish",
            status="in_progress",
        )


def test_checklist_updates_use_compare_and_set_revisions(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    task = _create_checklist(db, str(conversation["id"]))
    updated = db.update_task_checklist(
        task["id"],
        expected_revision=task["revision"],
        step_key="inspect",
        status="in_progress",
    )

    assert updated["revision"] == task["revision"] + 1
    with pytest.raises(ValueError, match="changed|revision"):
        db.update_task_checklist(
            task["id"],
            expected_revision=task["revision"],
            step_key="inspect",
            status="completed",
        )
    assert db.get_task_checklist(task["id"]) == updated


def test_terminal_checklist_rejects_further_step_updates(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    task = _create_checklist(db, str(conversation["id"]))
    cancelled = db.cancel_task_checklist(
        task["id"],
        expected_revision=task["revision"],
        reason="The user asked to stop.",
    )

    assert cancelled["status"] == "cancelled"
    assert db.get_active_task(str(conversation["id"])) is None
    with pytest.raises(ValueError):
        db.update_task_checklist(
            task["id"],
            expected_revision=cancelled["revision"],
            step_key="inspect",
            status="in_progress",
        )


def test_active_plan_run_projects_full_task_without_advancing_on_extra_tool_events(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    plan = db.create_plan(
        title="Reviewed work",
        goal="Complete reviewed work",
        plan={"steps": _steps()},
        conversation_id=str(conversation["id"]),
    )
    run = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])["run"]

    initial = db.get_active_task(str(conversation["id"]))
    assert initial is not None
    assert initial["id"] == run["id"]
    assert initial["source"] == "plan_run"
    assert [step["key"] for step in initial["steps"]] == ["inspect", "decide", "finish"]

    db.upsert_run_step(
        run["id"],
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="running",
        tool_name="web_search",
    )
    db.upsert_run_step(
        run["id"],
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="running",
        tool_name="web_fetch",
        output_summary="Sources opened.",
    )

    projected = db.get_active_task(str(conversation["id"]))
    assert projected is not None
    assert projected["current_step_key"] == "inspect"
    assert projected["steps"][0]["status"] == "in_progress"
    assert projected["steps"][0]["summary"] == "Sources opened."
    assert projected["steps"][1]["status"] == "pending"


def _tool_context(conversation_id: str, *, run_id: str | None = None):
    permission_context: dict[str, str] = {"conversation_id": conversation_id}
    if run_id is not None:
        permission_context["run_id"] = run_id
    return request_context(
        RequestContext(
            channel="websocket",
            chat_id=conversation_id,
            metadata={"permission_context": permission_context},
        )
    )


class _RecordingConnection:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        self.frames.append(payload)


class _Outbound:
    content = "Done."


def _claim_plan_run(db: CollieDB, conversation_id: str) -> dict:
    plan = db.create_plan(
        title="Reviewed work",
        goal="Complete reviewed work",
        plan={"steps": _steps()},
        conversation_id=conversation_id,
    )
    return db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])["run"]


def _permission_request(
    action: str, risk: Risk, *, approve_for_me: bool = True
) -> PermissionRequest:
    return PermissionRequest(
        action=action,
        resource=f"internal:{action}",
        risk=risk,
        summary=action,
        reversible=True,
        approve_for_me=approve_for_me and risk == Risk.LOCAL_WRITE,
    )


@pytest.mark.asyncio
async def test_task_tool_create_returns_full_renderer_event(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    tool = ManageTaskChecklistTool()

    with _tool_context(str(conversation["id"])):
        result = await tool.execute(
            operation="create", goal="Plan a trip", steps=_steps(), **_review_metadata()
        )

    assert not getattr(result, "is_error", False)
    event = json.loads(str(result))
    assert event["type"] == "task_state"
    assert event["conversation_id"] == conversation["id"]
    assert "conversation_id" not in event["task"]
    assert event["task"]["source"] == "checklist"
    assert event["task"]["revision"] == 1
    assert [step["key"] for step in event["task"]["steps"]] == [
        "inspect",
        "decide",
        "finish",
    ]


@pytest.mark.asyncio
async def test_task_tool_rejects_other_conversation_and_stale_revision(db: CollieDB) -> None:
    owner = db.create_conversation("Owner")
    other = db.create_conversation("Other")
    task = _create_checklist(db, str(owner["id"]))
    tool = ManageTaskChecklistTool()

    with _tool_context(str(other["id"])):
        foreign = await tool.execute(
            operation="update",
            checklist_id=task["id"],
            expected_revision=task["revision"],
            step_key="inspect",
            status="in_progress",
        )
    assert foreign.is_error is True
    assert "does not belong" in str(foreign)

    db.update_task_checklist(
        task["id"],
        expected_revision=task["revision"],
        step_key="inspect",
        status="in_progress",
    )
    with _tool_context(str(owner["id"])):
        stale = await tool.execute(
            operation="update",
            checklist_id=task["id"],
            expected_revision=task["revision"],
            step_key="inspect",
            status="completed",
        )
    assert stale.is_error is True
    assert "changed" in str(stale).lower() or "revision" in str(stale).lower()


@pytest.mark.asyncio
async def test_task_tool_redirects_seven_steps_to_reviewed_plan(db: CollieDB) -> None:
    conversation = db.create_conversation("Broad work")
    tool = ManageTaskChecklistTool()
    steps = [{"key": f"step-{index}", "title": f"Step {index}"} for index in range(1, 8)]

    with _tool_context(str(conversation["id"])):
        result = await tool.execute(
            operation="create", goal="Broad work", steps=steps, **_review_metadata()
        )

    assert result.is_error is True
    assert "present_plan" in str(result)
    assert db.get_active_task(str(conversation["id"])) is None


@pytest.mark.asyncio
async def test_task_tool_updates_only_the_explicit_plan_run_step(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    plan = db.create_plan(
        title="Reviewed work",
        goal="Complete reviewed work",
        plan={"steps": _steps()},
        conversation_id=str(conversation["id"]),
    )
    run = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])["run"]
    tool = ManageTaskChecklistTool()

    with _tool_context(str(conversation["id"]), run_id=str(run["id"])):
        started = await tool.execute(
            operation="update",
            checklist_id=run["id"],
            step_key="inspect",
            status="in_progress",
        )
        updated = await tool.execute(
            operation="update",
            checklist_id=run["id"],
            step_key="inspect",
            status="in_progress",
            summary="Two sources compared.",
        )

    assert not getattr(started, "is_error", False)
    event = json.loads(str(updated))
    assert event["task"]["source"] == "plan_run"
    assert event["task"]["current_step_key"] == "inspect"
    assert event["task"]["steps"][0]["summary"] == "Two sources compared."
    assert event["task"]["steps"][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_ipc_get_active_task_returns_only_the_renderer_snapshot(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    task = _create_checklist(db, str(conversation["id"]))
    server = CollieIPCServer(db)

    result = await server._cmd_get_active_task(None, {"conversation_id": conversation["id"]})  # type: ignore[arg-type]

    assert set(result) == {"task"}
    assert result["task"] is not None
    assert result["task"]["id"] == task["id"]
    assert "conversation_id" not in result["task"]
    await server.stop()


@pytest.mark.asyncio
async def test_ipc_get_active_task_returns_null_for_empty_or_other_conversation(
    db: CollieDB,
) -> None:
    owner = db.create_conversation("Trip")
    other = db.create_conversation("Work")
    _create_checklist(db, str(owner["id"]))
    server = CollieIPCServer(db)

    empty = await server._cmd_get_active_task(None, {"conversation_id": other["id"]})  # type: ignore[arg-type]
    missing = await server._cmd_get_active_task(None, {"conversation_id": "missing"})  # type: ignore[arg-type]

    assert empty == {"task": None}
    assert missing == {"task": None}
    await server.stop()


@pytest.mark.asyncio
async def test_active_task_reopens_after_core_restart_without_cross_conversation_leak(
    db: CollieDB,
) -> None:
    owner = db.create_conversation("Trip")
    other = db.create_conversation("Work")
    task = _create_checklist(db, str(owner["id"]))
    reopened = CollieDB(db.path)
    bind_task_checklists_db(reopened)
    server = CollieIPCServer(reopened)

    try:
        owner_result = await server._cmd_get_active_task(
            None, {"conversation_id": owner["id"]}
        )  # type: ignore[arg-type]
        other_result = await server._cmd_get_active_task(
            None, {"conversation_id": other["id"]}
        )  # type: ignore[arg-type]
        assert owner_result["task"]["id"] == task["id"]
        assert other_result == {"task": None}
    finally:
        await server.stop()
        reopened.close()
        bind_task_checklists_db(db)


def test_task_progress_is_a_narrow_plan_mode_local_write_exception(db: CollieDB) -> None:
    request = ManageTaskChecklistTool().permission_request({"operation": "create"})
    evaluator = PermissionEvaluator(PermissionStore(db))

    decision = evaluator.evaluate(ExecutionContext(execution_mode="plan"), request)

    assert request.action == "task.progress"
    assert request.risk == Risk.LOCAL_WRITE
    assert decision.effect == Effect.ALLOW


def test_task_progress_explicit_deny_and_read_only_posture_still_win(db: CollieDB) -> None:
    request = ManageTaskChecklistTool().permission_request({"operation": "create"})
    evaluator = PermissionEvaluator(PermissionStore(db))
    db.add_approval_rule(
        action="task.progress",
        resource_pattern="internal:task-progress",
        effect="deny",
        scope_type="global",
    )

    denied = evaluator.evaluate(ExecutionContext(execution_mode="plan"), request)
    read_only = PermissionEvaluator().evaluate(
        ExecutionContext(execution_mode="plan", execution_posture="read_only"), request
    )

    assert denied.effect == Effect.DENY
    assert read_only.effect == Effect.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "label"),
    [
        ({"services": ["google", "microsoft"]}, "two services"),
        ({"material_commitment": True}, "material commitment"),
        ({"unstable_success_criterion": True}, "unstable success criterion"),
        ({"requires_review": True}, "explicit review"),
    ],
)
async def test_task_tool_routes_review_required_create_metadata_to_present_plan(
    db: CollieDB, metadata: dict[str, object], label: str
) -> None:
    conversation = db.create_conversation(label)
    tool = ManageTaskChecklistTool()

    with _tool_context(str(conversation["id"])):
        result = await tool.execute(
            operation="create",
            goal="A review-required task",
            steps=_steps(),
            **_review_metadata(**metadata),
        )

    assert result.is_error is True
    assert "present_plan" in str(result)
    assert db.get_active_task(str(conversation["id"])) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_key",
    ["services", "material_commitment", "unstable_success_criterion", "requires_review"],
)
async def test_task_tool_requires_explicit_review_metadata(
    db: CollieDB, missing_key: str
) -> None:
    conversation = db.create_conversation("Explicit review metadata")
    tool = ManageTaskChecklistTool()
    metadata = _review_metadata()
    metadata.pop(missing_key)
    params = {"operation": "create", "goal": "Plan a trip", "steps": _steps(), **metadata}

    assert missing_key in " ".join(tool.validate_params(params))
    with _tool_context(str(conversation["id"])):
        result = await tool.execute(**params)

    assert getattr(result, "is_error", False) is True
    assert missing_key in str(result)
    assert db.get_active_task(str(conversation["id"])) is None


@pytest.mark.asyncio
async def test_review_required_create_persists_a_durable_conversation_gate(db: CollieDB) -> None:
    conversation = db.create_conversation("Broad work")
    tool = ManageTaskChecklistTool()

    with _tool_context(str(conversation["id"])):
        result = await tool.execute(
            operation="create",
            goal="A broad task",
            steps=_steps(),
            **_review_metadata(services=["google", "microsoft"]),
        )

    assert getattr(result, "is_error", False) is True
    gate = db.get_conversation_review_gate(str(conversation["id"]))
    assert gate is not None
    assert gate["conversation_id"] == conversation["id"]
    assert gate["reasons"]
    assert gate["declared_at"]


def test_review_gate_survives_reopen_and_blocks_non_read_actions_after_allows(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Broad work")
    db.require_conversation_review(str(conversation["id"]), reasons=["multiple_services"])
    reopened = CollieDB(db.path)
    try:
        gate = reopened.get_conversation_review_gate(str(conversation["id"]))
        assert gate is not None
        evaluator = PermissionEvaluator(
            PermissionStore(reopened),
            local_write_preset="allow",
            review_gate_provider=lambda conversation_id: (
                reopened.get_conversation_review_gate(conversation_id) is not None
            ),
        )
        context = ExecutionContext(conversation_id=str(conversation["id"]))
        reopened.add_approval_rule(
            action="external.send",
            resource_pattern="*",
            effect="allow",
            scope_type="global",
        )

        assert evaluator.evaluate(
            context,
            _permission_request("file.write", Risk.LOCAL_WRITE, approve_for_me=False),
        ).effect == Effect.DENY
        assert evaluator.evaluate(context, _permission_request("external.send", Risk.EXTERNAL_WRITE)).effect == Effect.DENY
        assert evaluator.evaluate(context, _permission_request("file.read", Risk.READ)).effect == Effect.ALLOW
        assert evaluator.evaluate(context, _permission_request("plan.present", Risk.LOCAL_WRITE)).effect == Effect.ALLOW
        assert evaluator.evaluate(context, _permission_request("task.progress", Risk.LOCAL_WRITE)).effect == Effect.ALLOW

        reopened.add_approval_rule(
            action="task.progress",
            resource_pattern="*",
            effect="deny",
            scope_type="global",
        )
        assert evaluator.evaluate(context, _permission_request("task.progress", Risk.LOCAL_WRITE)).effect == Effect.DENY
    finally:
        reopened.close()


def test_no_review_gate_leaves_safe_actions_and_explicit_allows_unchanged(db: CollieDB) -> None:
    conversation = db.create_conversation("Small task")
    evaluator = PermissionEvaluator(
        PermissionStore(db),
        local_write_preset="allow",
        review_gate_provider=lambda conversation_id: (
            db.get_conversation_review_gate(conversation_id) is not None
        ),
    )
    db.add_approval_rule(
        action="external.send",
        resource_pattern="*",
        effect="allow",
        scope_type="global",
    )
    context = ExecutionContext(conversation_id=str(conversation["id"]))

    assert db.get_conversation_review_gate(str(conversation["id"])) is None
    assert evaluator.evaluate(context, _permission_request("file.write", Risk.LOCAL_WRITE)).effect == Effect.ALLOW
    assert evaluator.evaluate(context, _permission_request("external.send", Risk.EXTERNAL_WRITE)).effect == Effect.ALLOW


@pytest.mark.asyncio
async def test_review_gate_rejects_later_ordinary_checklist_and_survives_task_updates(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Broad work")
    reasons = ["material_commitment"]
    db.require_conversation_review(str(conversation["id"]), reasons=reasons)
    tool = ManageTaskChecklistTool()

    with _tool_context(str(conversation["id"])):
        rejected = await tool.execute(
            operation="create", goal="An ordinary task", steps=_steps(), **_review_metadata()
        )
    assert getattr(rejected, "is_error", False) is True
    assert "present_plan" in str(rejected)
    assert db.get_conversation_review_gate(str(conversation["id"]))["reasons"] == reasons

    checklist = _create_checklist(db, str(conversation["id"]))
    updated = db.update_task_checklist(
        checklist["id"],
        expected_revision=checklist["revision"],
        step_key="inspect",
        status="in_progress",
    )
    db.cancel_task_checklist(
        checklist["id"], expected_revision=updated["revision"], reason="Stop."
    )
    assert db.get_conversation_review_gate(str(conversation["id"]))["reasons"] == reasons


def test_claiming_reviewed_plan_clears_gate_and_restores_planned_writes(db: CollieDB) -> None:
    conversation = db.create_conversation("Broad work")
    db.require_conversation_review(str(conversation["id"]), reasons=["requires_review"])
    plan = db.create_plan(
        title="Broad work",
        goal="Complete broad work",
        plan={"steps": _steps()},
        conversation_id=str(conversation["id"]),
    )
    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])
    evaluator = PermissionEvaluator(
        PermissionStore(db),
        local_write_preset="allow",
        review_gate_provider=lambda conversation_id: (
            db.get_conversation_review_gate(conversation_id) is not None
        ),
    )
    context = ExecutionContext(conversation_id=str(conversation["id"]), run_id=claim["run"]["id"])

    assert db.get_conversation_review_gate(str(conversation["id"])) is None
    assert evaluator.evaluate(context, _permission_request("file.write", Risk.LOCAL_WRITE)).effect == Effect.ALLOW


@pytest.mark.asyncio
async def test_subagent_interim_reply_keeps_ordinary_checklist_active(db: CollieDB) -> None:
    conversation = db.create_conversation("Long task")
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Long task", steps=_steps(), **_review_metadata()
            )
        await on_progress(
            tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(created)}]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner, subagents_running=lambda _conversation_id: 1)
    try:
        await server._run_chat_turn(str(conversation["id"]), "Start long task")
        active = db.get_active_task(str(conversation["id"]))
        final = db.get_messages(str(conversation["id"]))[-1]
        assert active is not None
        assert active["source"] == "checklist"
        assert active["status"] == "active"
        assert final["task_state"] is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_live_turn_persists_cancelled_checklist_snapshot(db: CollieDB) -> None:
    conversation = db.create_conversation("Long task")
    tool = ManageTaskChecklistTool()
    ready = asyncio.Event()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Long task", steps=_steps(), **_review_metadata()
            )
            task = json.loads(str(created))["task"]
            started = await tool.execute(
                operation="update",
                checklist_id=task["id"],
                expected_revision=task["revision"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(started)}]
        )
        ready.set()
        await asyncio.Event().wait()
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    live = asyncio.create_task(server._run_chat_turn(str(conversation["id"]), "Start long task"))
    server._chat_tasks[str(conversation["id"])] = live
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        response = await server._cmd_stop(None, {"conversation_id": conversation["id"]})  # type: ignore[arg-type]
        await live
        stopped = db.get_messages(str(conversation["id"]))[-1]
        assert response["stopped"] is True
        assert stopped["content"] == "Stopped."
        assert stopped["task_state"]["status"] == "cancelled"
        assert [step["status"] for step in stopped["task_state"]["steps"]] == [
            "skipped",
            "pending",
            "pending",
        ]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_plan_run_tool_result_uses_safe_summary_not_raw_payload(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()
    secret_payload = json.dumps({"api_key": "super-secret-value", "raw": ["private"]})

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            selected = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "manage_task_checklist",
                    "arguments": {"step_key": "inspect"},
                    "result": str(selected),
                },
                {"phase": "start", "name": "web_search"},
                {"phase": "end", "name": "web_search", "result": secret_payload},
            ]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(
            str(conversation["id"]), "Do the reviewed work", run_id=str(run["id"])
        )
        summary = db.list_run_steps(str(run["id"]))[0]["output_summary"]
        assert summary
        assert "super-secret-value" not in summary
        assert "api_key" not in summary
        assert secret_payload not in summary
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_all_terminal_checklist_steps_auto_complete_parent_when_model_omits_complete(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Trip")
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Plan a trip", steps=_steps(), **_review_metadata()
            )
            task = json.loads(str(created))["task"]
            for status, step in zip(("completed", "completed", "skipped"), _steps(), strict=True):
                updated = await tool.execute(
                    operation="update",
                    checklist_id=task["id"],
                    expected_revision=task["revision"],
                    step_key=step["key"],
                    status=status,
                    summary="Done.",
                )
                await on_progress(
                    tool_events=[
                        {
                            "phase": "end",
                            "name": "manage_task_checklist",
                            "result": str(updated),
                        }
                    ]
                )
                task = json.loads(str(updated))["task"]
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(str(conversation["id"]), "Plan my trip")
        final = db.get_messages(str(conversation["id"]))[-1]
        assert final["task_state"]["status"] == "completed"
        assert final["task_state"]["completed_count"] == 2
        assert final["task_state"]["total_count"] == 3
        assert db.get_active_task(str(conversation["id"])) is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_plan_run_advances_only_on_explicit_task_update_not_tool_cursor(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()
    observed: dict[str, object] = {}

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            selected = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="in_progress",
            )
            await on_progress(
                tool_events=[
                    {
                        "phase": "end",
                        "name": "manage_task_checklist",
                        "arguments": {"step_key": "inspect"},
                        "result": str(selected),
                    }
                ]
            )
        await on_progress(tool_events=[{"phase": "start", "name": "web_search"}])
        await on_progress(
            tool_events=[{"phase": "end", "name": "web_search", "result": "search result"}]
        )
        await on_progress(tool_events=[{"phase": "start", "name": "web_fetch"}])
        await on_progress(
            tool_events=[{"phase": "end", "name": "web_fetch", "result": "fetch result"}]
        )
        observed["steps"] = db.list_run_steps(str(run["id"]))
        with _tool_context(conversation_id, run_id=str(run["id"])):
            completed = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="completed",
                summary="First outcome verified.",
            )
            await on_progress(
                tool_events=[
                    {
                        "phase": "end",
                        "name": "manage_task_checklist",
                        "arguments": {"step_key": "inspect"},
                        "result": str(completed),
                    }
                ]
            )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    recorder = _RecordingConnection()
    server._clients.add(recorder)  # type: ignore[arg-type]
    try:
        await server._run_chat_turn(
            str(conversation["id"]), "Do the reviewed work", run_id=str(run["id"])
        )
        during_tools = observed["steps"]
        assert isinstance(during_tools, list)
        assert during_tools[0]["step_key"] == "inspect"
        assert during_tools[0]["status"] == "running"
        assert during_tools[0]["tool_name"] == "web_fetch"
        assert during_tools[0]["output_summary"] == "web_fetch finished successfully."
        assert "fetch result" not in during_tools[0]["output_summary"]
        assert during_tools[1]["status"] == "queued"
        assert db.list_run_steps(str(run["id"]))[0]["status"] == "completed"
        assert db.list_run_steps(str(run["id"]))[1]["status"] == "queued"
        assert db.get_run(str(run["id"]))["error_code"] == "incomplete_plan"
        assert any(
            frame.get("type") == "task_state"
            and frame.get("conversation_id") == conversation["id"]
            for frame in recorder.frames
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_plan_run_without_explicit_completion_fails_incomplete_plan(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            selected = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "manage_task_checklist",
                    "arguments": {"step_key": "inspect"},
                    "result": str(selected),
                },
                {"phase": "start", "name": "web_search"},
                {"phase": "end", "name": "web_search", "result": "found options"},
            ]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(
            str(conversation["id"]), "Do the reviewed work", run_id=str(run["id"])
        )
        failed = db.get_run(str(run["id"]))
        assert failed["status"] == "failed"
        assert failed["error_code"] == "incomplete_plan"
        assert [step["status"] for step in db.list_run_steps(str(run["id"]))] == [
            "failed",
            "queued",
            "queued",
        ]
        final = db.get_messages(str(conversation["id"]))[-1]
        assert final["task_state"]["source"] == "plan_run"
        assert final["task_state"]["status"] == "failed"
        assert final["task_state"]["revision"] == db.get_run_task(str(run["id"]))["revision"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_plan_run_tool_error_fails_only_the_selected_step(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            selected = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "manage_task_checklist",
                    "arguments": {"step_key": "inspect"},
                    "result": str(selected),
                },
                {"phase": "error", "name": "web_search", "error": "network failed"},
            ]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(
            str(conversation["id"]), "Do the reviewed work", run_id=str(run["id"])
        )
        assert [step["status"] for step in db.list_run_steps(str(run["id"]))] == [
            "failed",
            "queued",
            "queued",
        ]
        assert db.get_run(str(run["id"]))["error_code"] == "step_failed"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_plan_run_cancellation_skips_only_selected_step_and_emits_terminal_task(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            selected = await tool.execute(
                operation="update",
                checklist_id=run["id"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "manage_task_checklist",
                    "arguments": {"step_key": "inspect"},
                    "result": str(selected),
                }
            ]
        )
        raise asyncio.CancelledError()

    server = CollieIPCServer(db, chat_runner=runner)
    recorder = _RecordingConnection()
    server._clients.add(recorder)  # type: ignore[arg-type]
    try:
        await server._run_chat_turn(
            str(conversation["id"]), "Do the reviewed work", run_id=str(run["id"])
        )
        assert [step["status"] for step in db.list_run_steps(str(run["id"]))] == [
            "skipped",
            "queued",
            "queued",
        ]
        assert db.get_run(str(run["id"]))["status"] == "cancelled"
        stopped = db.get_messages(str(conversation["id"]))[-1]
        assert stopped["content"] == "Stopped."
        assert stopped["task_state"]["source"] == "plan_run"
        assert stopped["task_state"]["status"] == "cancelled"
        assert [step["status"] for step in stopped["task_state"]["steps"]] == [
            "skipped",
            "pending",
            "pending",
        ]
        terminal = [
            frame
            for frame in recorder.frames
            if frame.get("type") == "task_state"
            and frame.get("conversation_id") == conversation["id"]
            and isinstance(frame.get("task"), dict)
            and frame["task"].get("status") == "cancelled"
        ]
        assert terminal
    finally:
        await server.stop()


def test_plan_run_task_revisions_advance_for_enrichment_and_survive_reopen(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    selected = db.update_run_task_step(
        str(run["id"]), "inspect", status="in_progress", summary="Selected."
    )
    after_start = db.upsert_run_step(
        str(run["id"]),
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="running",
        tool_name="web_search",
    )
    first_enrichment = db.get_run_task(str(run["id"]))
    after_end = db.upsert_run_step(
        str(run["id"]),
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="running",
        tool_name="web_search",
        output_summary="Options found.",
    )
    second_enrichment = db.get_run_task(str(run["id"]))

    assert after_start["tool_name"] == "web_search"
    assert after_end["output_summary"] == "Options found."
    assert selected["revision"] < first_enrichment["revision"] < second_enrichment["revision"]
    reopened = CollieDB(db.path)
    try:
        assert reopened.get_run_task(str(run["id"]))["revision"] == second_enrichment["revision"]
    finally:
        reopened.close()


def test_plan_run_transition_bumps_task_revision(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    before = db.get_run_task(str(run["id"]))

    db.transition_run(str(run["id"]), "failed", error_code="forced", error_message="Forced")

    after = db.get_run_task(str(run["id"]))
    assert before["revision"] < after["revision"]
    assert after["status"] == "failed"


def test_message_task_state_round_trips_through_v9_and_reopen(db: CollieDB) -> None:
    conversation = db.create_conversation("Trip")
    active = _create_checklist(db, str(conversation["id"]))
    task_state = db.cancel_task_checklist(
        active["id"], expected_revision=active["revision"], reason="Stopped."
    )

    message = db.add_message(
        str(conversation["id"]),
        "assistant",
        "Stopped.",
        task_state=task_state,
    )

    assert message["task_state"] == task_state
    assert db.get_messages(str(conversation["id"]))[-1]["task_state"] == task_state
    reopened = CollieDB(db.path)
    try:
        assert reopened.get_messages(str(conversation["id"]))[-1]["task_state"] == task_state
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_completed_checklist_is_persisted_on_final_assistant_with_card_data(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Trip")
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Plan a trip", steps=_steps(), **_review_metadata()
            )
            await on_progress(
                tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(created)}]
            )
            task = json.loads(str(created))["task"]
            for step in _steps():
                started = await tool.execute(
                    operation="update",
                    checklist_id=task["id"],
                    expected_revision=task["revision"],
                    step_key=step["key"],
                    status="in_progress",
                )
                await on_progress(
                    tool_events=[
                        {"phase": "end", "name": "manage_task_checklist", "result": str(started)}
                    ]
                )
                task = json.loads(str(started))["task"]
                completed = await tool.execute(
                    operation="update",
                    checklist_id=task["id"],
                    expected_revision=task["revision"],
                    step_key=step["key"],
                    status="completed",
                    summary=f"{step['title']} done.",
                )
                await on_progress(
                    tool_events=[
                        {
                            "phase": "end",
                            "name": "manage_task_checklist",
                            "result": str(completed),
                        }
                    ]
                )
                task = json.loads(str(completed))["task"]
            terminal = await tool.execute(
                operation="complete",
                checklist_id=task["id"],
                expected_revision=task["revision"],
            )
            await on_progress(
                tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(terminal)}]
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "web_search",
                    "result": json.dumps({"card_type": "TravelCard", "destination": "Paris"}),
                }
            ]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(str(conversation["id"]), "Plan my trip")
        final = db.get_messages(str(conversation["id"]))[-1]
        assert final["card_type"] == "TravelCard"
        assert final["card_data"] == {"destination": "Paris"}
        assert final["task_state"]["source"] == "checklist"
        assert final["task_state"]["status"] == "completed"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_cancelled_checklist_is_persisted_with_pending_steps_unchanged(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Trip")
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Plan a trip", steps=_steps(), **_review_metadata()
            )
            task = json.loads(str(created))["task"]
            started = await tool.execute(
                operation="update",
                checklist_id=task["id"],
                expected_revision=task["revision"],
                step_key="inspect",
                status="in_progress",
            )
            await on_progress(
                tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(started)}]
            )
        raise asyncio.CancelledError()

    server = CollieIPCServer(db, chat_runner=runner)
    try:
        await server._run_chat_turn(str(conversation["id"]), "Plan my trip")
        stopped = db.get_messages(str(conversation["id"]))[-1]
        assert stopped["content"] == "Stopped."
        assert stopped["task_state"]["source"] == "checklist"
        assert stopped["task_state"]["status"] == "cancelled"
        assert [step["status"] for step in stopped["task_state"]["steps"]] == [
            "skipped",
            "pending",
            "pending",
        ]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_non_plan_routine_run_keeps_legacy_completion_without_task_snapshot(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Custom routine")
    run = db.create_run(
        trigger_type="custom",
        idempotency_key="custom-routine-run",
        routine_id="routine-custom",
        conversation_id=str(conversation["id"]),
    )

    async def runner(_content: str, **_kwargs) -> _Outbound:
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    recorder = _RecordingConnection()
    server._clients.add(recorder)  # type: ignore[arg-type]
    try:
        assert db.get_run_task(str(run["id"])) is None
        await server._run_chat_turn(
            str(conversation["id"]), "Run my routine", run_id=str(run["id"])
        )
        assert db.get_run(str(run["id"]))["status"] == "completed"
        assert any(
            frame.get("type") == "run_completed"
            and isinstance(frame.get("run"), dict)
            and frame["run"].get("id") == run["id"]
            for frame in recorder.frames
        )
        assert db.get_messages(str(conversation["id"]))[-1]["task_state"] is None
    finally:
        await server.stop()


def test_claim_plan_execution_cancels_an_active_ordinary_checklist(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    checklist = _create_checklist(db, str(conversation["id"]))
    plan = db.create_plan(
        title="Reviewed work",
        goal="Complete reviewed work",
        plan={"steps": _steps()},
        conversation_id=str(conversation["id"]),
    )

    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    cancelled = db.get_task_checklist(checklist["id"])
    assert claim["created"] is True
    assert cancelled["status"] == "cancelled"
    assert db.get_active_task(str(conversation["id"]))["id"] == claim["run"]["id"]


def test_plan_change_request_and_finalize_preserve_run_truth(db: CollieDB) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    db.update_run_task_step(
        str(run["id"]), "inspect", status="in_progress", summary="Started."
    )
    db.upsert_run_step(
        str(run["id"]),
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="running",
        tool_name="web_search",
        output_summary="Sources compared.",
    )

    request = db.request_plan_change(
        str(run["id"]),
        conversation_id=str(conversation["id"]),
        reason="Use a different destination.",
    )
    finalized = db.finalize_plan_change(str(run["id"]))
    terminal = finalized["task"]

    persisted_request = db.get_plan_change_request(str(run["id"]))
    assert persisted_request["run_id"] == request["run_id"]
    assert persisted_request["status"] == "finalized"
    assert request["conversation_id"] == conversation["id"]
    assert request["reason"] == "Use a different destination."
    assert terminal["source"] == "plan_run"
    assert terminal["status"] == "cancelled"
    assert terminal["steps"][0]["status"] == "skipped"
    assert terminal["steps"][0]["summary"] == "Sources compared."
    assert [step["status"] for step in terminal["steps"][1:]] == ["pending", "pending"]
    failed = db.get_run(str(run["id"]))
    assert failed["status"] == "cancelled"
    assert failed["error_code"] == "plan_superseded"


@pytest.mark.asyncio
async def test_change_plan_ipc_immediately_persists_one_terminal_message(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    db.update_run_task_step(str(run["id"]), "inspect", status="in_progress")
    server = CollieIPCServer(db)

    try:
        response = await server._cmd_change_plan(  # type: ignore[attr-defined,arg-type]
            None,
            {
                "conversation_id": conversation["id"],
                "run_id": run["id"],
                "reason": "The scope changed.",
            },
        )
        assert response["requested"] is True
        assert response["conversation_id"] == conversation["id"]
        assert response["run_id"] == run["id"]
        assert response["plan_id"] == run["plan_id"]
        assert response["version"] == 1
        assert response["plan_version"] == 1
        assert response["execution_mode"] == "plan"
        assert response["status"] == "cancelled"
        failed = db.get_run(str(run["id"]))
        assert failed["status"] == "cancelled"
        assert failed["error_code"] == "plan_superseded"
        terminal_messages = [
            message
            for message in db.get_messages(str(conversation["id"]))
            if message["role"] == "assistant"
            and isinstance(message.get("task_state"), dict)
            and message["task_state"].get("source") == "plan_run"
            and message["task_state"].get("status") == "cancelled"
        ]
        assert len(terminal_messages) == 1
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_change_plan_waits_for_all_overlapping_material_boundaries(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    tool = ManageTaskChecklistTool()
    observed: dict[str, object] = {}
    holder: dict[str, CollieIPCServer] = {}

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id, run_id=str(run["id"])):
            started = await tool.execute(
                operation="update",
                checklist_id=str(run["id"]),
                expected_revision=1,
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[
                {
                    "phase": "end",
                    "name": "manage_task_checklist",
                    "arguments": {"step_key": "inspect"},
                    "result": str(started),
                }
            ]
        )
        await on_progress(tool_events=[{"phase": "start", "name": "write_file"}])
        await on_progress(tool_events=[{"phase": "start", "name": "send_email"}])

        requested = await holder["server"]._cmd_change_plan(  # type: ignore[attr-defined,arg-type]
            None,
            {
                "conversation_id": conversation_id,
                "run_id": run["id"],
                "reason": "Use a different destination.",
            },
        )
        observed["requested"] = requested
        observed["before_first_end_messages"] = db.get_messages(conversation_id)

        await on_progress(
            tool_events=[{"phase": "end", "name": "write_file", "result": "written"}]
        )
        observed["after_first_end_run"] = db.get_run(str(run["id"]))
        observed["after_first_end_messages"] = db.get_messages(conversation_id)

        await on_progress(
            tool_events=[{"phase": "end", "name": "send_email", "result": "sent"}]
        )
        return _Outbound()

    server = CollieIPCServer(db, chat_runner=runner)
    holder["server"] = server
    try:
        await server._run_chat_turn(
            str(conversation["id"]),
            "Run the approved plan",
            run_id=str(run["id"]),
        )
        requested = observed["requested"]
        assert isinstance(requested, dict)
        assert requested["status"] == "pending_safe_boundary"
        assert requested["plan_version"] == 1
        assert observed["before_first_end_messages"] == []
        after_first_end = observed["after_first_end_run"]
        assert isinstance(after_first_end, dict)
        assert after_first_end["status"] == "running"
        assert observed["after_first_end_messages"] == []

        failed = db.get_run(str(run["id"]))
        assert failed["status"] == "cancelled"
        assert failed["error_code"] == "plan_superseded"
        terminal_messages = [
            message
            for message in db.get_messages(str(conversation["id"]))
            if message["role"] == "assistant"
            and isinstance(message.get("task_state"), dict)
            and message["task_state"].get("source") == "plan_run"
            and message["task_state"].get("status") == "cancelled"
        ]
        assert len(terminal_messages) == 1
    finally:
        await server.stop()


@pytest.mark.parametrize(
    ("initial_status", "regression_status"),
    [("completed", "running"), ("running", "queued")],
)
def test_upsert_run_step_cannot_regress_terminal_or_active_step(
    db: CollieDB, initial_status: str, regression_status: str
) -> None:
    conversation = db.create_conversation("Reviewed work")
    run = _claim_plan_run(db, str(conversation["id"]))
    db.upsert_run_step(
        str(run["id"]),
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status=initial_status,
        output_summary="Done.",
    )

    with pytest.raises(ValueError):
        db.upsert_run_step(
            str(run["id"]),
            "inspect",
            ordinal=0,
            title="Inspect the options",
            status=regression_status,
        )


@pytest.mark.asyncio
async def test_scheduler_failure_marks_current_or_first_nonterminal_not_completed_step(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Routine")
    run = _claim_plan_run(db, str(conversation["id"]))
    db.upsert_run_step(
        str(run["id"]),
        "inspect",
        ordinal=0,
        title="Inspect the options",
        status="completed",
        output_summary="Already done.",
    )
    db.upsert_run_step(
        str(run["id"]),
        "decide",
        ordinal=1,
        title="Choose an approach",
        status="running",
    )

    async def fail_runner(_auto: dict) -> None:
        raise RuntimeError("routine failed")

    scheduler = AutomationScheduler(db, runner=fail_runner)
    await scheduler._execute_claimed({"id": "routine-test"}, run)

    steps = db.list_run_steps(str(run["id"]))
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "failed"
    assert steps[2]["status"] == "queued"


@pytest.mark.asyncio
async def test_unexpected_turn_failure_persists_and_broadcasts_task_state_without_card(
    db: CollieDB,
) -> None:
    conversation = db.create_conversation("Trip")
    tool = ManageTaskChecklistTool()

    async def runner(_content: str, *, conversation_id: str, on_progress) -> _Outbound:
        with _tool_context(conversation_id):
            created = await tool.execute(
                operation="create", goal="Plan a trip", steps=_steps(), **_review_metadata()
            )
            task = json.loads(str(created))["task"]
            started = await tool.execute(
                operation="update",
                checklist_id=task["id"],
                expected_revision=task["revision"],
                step_key="inspect",
                status="in_progress",
            )
        await on_progress(
            tool_events=[{"phase": "end", "name": "manage_task_checklist", "result": str(started)}]
        )
        raise RuntimeError("forced failure")

    server = CollieIPCServer(db, chat_runner=runner)
    recorder = _RecordingConnection()
    server._clients.add(recorder)  # type: ignore[arg-type]
    try:
        await server._run_chat_turn(str(conversation["id"]), "Plan my trip")
        failure = db.get_messages(str(conversation["id"]))[-1]
        assert failure["role"] == "assistant"
        assert failure["task_state"]["status"] == "failed"
        assert failure["task_state"]["source"] == "checklist"
        assert failure["card_type"] is None
        assert failure["card_data"] is None
        assert any(
            frame.get("type") == "message"
            and isinstance(frame.get("message"), dict)
            and frame["message"].get("id") == failure["id"]
            for frame in recorder.frames
        )
    finally:
        await server.stop()
