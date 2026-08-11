"""Coverage backfill: the permission broker's full request lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, PermissionRequest, Risk
from collie_core.permissions.store import PermissionStore


class FakeTool:
    """An explicitly approved ordinary local action."""

    read_only = False

    @property
    def name(self) -> str:
        return "write_note"

    def permission_request(self, params: dict) -> PermissionRequest:
        return PermissionRequest(
            action="notes.write",
            resource="notes",
            risk=Risk.LOCAL_WRITE,
            summary="Write a note",
            reversible=True,
            approve_for_me=True,
        )


class FakeReadTool:
    read_only = True


@pytest.fixture()
def broker(tmp_path: Path):
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db))
    events: list[dict] = []

    async def broadcaster(payload):
        events.append(payload)

    b = ApprovalBroker(db, evaluator, broadcaster=broadcaster, timeout_seconds=1)
    b._events = events  # type: ignore[attr-defined]
    yield b
    db.close()


def _tool_call(name: str = "write_note", call_id: str = "call-1"):
    return SimpleNamespace(id=call_id, name=name)


@pytest.mark.asyncio
async def test_authorize_allows_read_actions(broker: ApprovalBroker) -> None:
    await broker.authorize(
        ExecutionContext(),
        _tool_call("web_search"),
        FakeReadTool(),
        {"query": "puppies"},
    )


@pytest.mark.asyncio
async def test_authorize_deny_raises(broker: ApprovalBroker) -> None:
    with pytest.raises(PermissionDeniedError):
        await broker.authorize(
            ExecutionContext(execution_posture="read_only"),
            _tool_call(),
            FakeTool(),
            {},
        )


@pytest.mark.asyncio
async def test_authorize_asks_then_resolves_allow_once(broker: ApprovalBroker) -> None:
    task = asyncio.create_task(
        broker.authorize(ExecutionContext(), _tool_call(), FakeTool(), {"text": "hi"})
    )
    await asyncio.sleep(0.05)
    assert broker._events[-1]["type"] == "approval_requested"  # type: ignore[attr-defined]
    pending = broker.db.list_pending_approvals()
    assert len(pending) == 1
    await broker.resolve(pending[0]["id"], "allow_once")
    await task
    assert broker.db.list_pending_approvals() == []


@pytest.mark.asyncio
async def test_authorize_reject_raises(broker: ApprovalBroker) -> None:
    task = asyncio.create_task(
        broker.authorize(ExecutionContext(), _tool_call(), FakeTool(), {"text": "hi"})
    )
    await asyncio.sleep(0.05)
    pending = broker.db.list_pending_approvals()
    await broker.resolve(pending[0]["id"], "reject")
    with pytest.raises(PermissionDeniedError):
        await task


@pytest.mark.asyncio
async def test_authorize_timeout_denies(broker: ApprovalBroker) -> None:
    with pytest.raises(PermissionDeniedError, match="timed out"):
        await broker.authorize(ExecutionContext(), _tool_call(), FakeTool(), {"text": "hi"})
    rows = broker.db.list_pending_approvals()
    assert rows == []


@pytest.mark.asyncio
async def test_authorize_run_scope_creates_rule(broker: ApprovalBroker) -> None:
    task = asyncio.create_task(
        broker.authorize(
            ExecutionContext(run_id="run-9"),
            _tool_call(),
            FakeTool(),
            {"text": "hi"},
        )
    )
    await asyncio.sleep(0.05)
    pending = broker.db.list_pending_approvals()
    await broker.resolve(
        pending[0]["id"],
        "allow_run",
        scope_type="global",
        scope_value="attacker-selected",
    )
    await task
    rules = broker.db.list_approval_rules()
    assert len(rules) == 1
    assert rules[0]["scope_type"] == "run"
    assert rules[0]["scope_value"] == "run-9"


@pytest.mark.asyncio
async def test_cancel_conversation_closes_pending(broker: ApprovalBroker) -> None:
    task = asyncio.create_task(
        broker.authorize(
            ExecutionContext(conversation_id="conv-1"),
            _tool_call(),
            FakeTool(),
            {"text": "hi"},
        )
    )
    await asyncio.sleep(0.05)
    cancelled = await broker.cancel_conversation("conv-1")
    assert cancelled == 1
    # The awaiting tool coroutine is cancelled alongside its future.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.db.list_pending_approvals() == []


@pytest.mark.asyncio
async def test_resolve_public_allow_scope_rejected(broker: ApprovalBroker) -> None:
    row = broker.db.create_approval_request(
        action="tool.write",
        resource="x",
        risk="local_write",
        display={"summary": "s"},
    )
    with pytest.raises(ValueError, match="unsupported approval resolution"):
        await broker.resolve(row["id"], "allow_scope", scope_type="global")
    assert broker.db.list_approval_rules() == []
    assert [item["id"] for item in broker.db.list_pending_approvals()] == [row["id"]]


@pytest.mark.asyncio
async def test_resolve_unknown_request_raises(broker: ApprovalBroker) -> None:
    with pytest.raises(ValueError, match="no longer pending"):
        await broker.resolve("missing-id", "allow_once")


def test_denied_error_message() -> None:
    error = PermissionDeniedError("nope")
    assert str(error) == "nope"
    assert isinstance(error, RuntimeError)


# -- classifier table -----------------------------------------------------------


def test_classifier_risk_table() -> None:
    from collie_core.permissions.classifier import classify_tool
    from collie_core.permissions.models import Risk

    class AnyTool:
        read_only = False

    cases: list[tuple[str, str, Risk, bool]] = [
        ("web_search", "web.read", Risk.READ, False),
        ("weather", "weather.read", Risk.READ, False),
        ("message", "message.send", Risk.EXTERNAL_WRITE, True),
        ("delete_file", "delete.destructive", Risk.DESTRUCTIVE, True),
        ("buy_stuff", "financial.purchase", Risk.SENSITIVE, True),
        ("send_email", "email.send", Risk.EXTERNAL_WRITE, True),
        ("publish_post", "external.publish", Risk.EXTERNAL_WRITE, True),
        ("mcp_gmail_list", "mcp.gmail_list", Risk.EXTERNAL_WRITE, False),
        ("write_note", "tool.write_note", Risk.LOCAL_WRITE, False),
    ]
    for name, action, risk, hard in cases:
        request = classify_tool(AnyTool(), name, {})
        assert request.action == action, name
        assert request.risk == risk, name
        assert request.hard_approval is hard, name


def test_classifier_redacts_secrets() -> None:
    from collie_core.permissions.classifier import classify_tool

    request = classify_tool(
        SimpleNamespace(read_only=False),
        "connect_connector",
        {"api_key": "sk-super-secret", "host": "example.com"},
    )
    serialized = request.redacted_parameters
    assert serialized["api_key"] == "[redacted]"
    assert "sk-super-secret" not in str(serialized)


def test_classifier_read_actions_stay_allowed_in_plan_mode() -> None:
    from collie_core.permissions.classifier import classify_tool
    from collie_core.permissions.models import Risk

    db = None
    from collie_core.permissions.evaluator import PermissionEvaluator

    request = classify_tool(SimpleNamespace(read_only=True), "web_fetch", {"url": "x"})
    assert request.risk == Risk.READ
    decision = PermissionEvaluator().evaluate(ExecutionContext(execution_mode="plan"), request)
    assert decision.effect == Effect.ALLOW
    del db
