"""Operation-level permission policy for ordinary personal work."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker
from collie_core.permissions.classifier import classify_tool
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, PermissionRequest, Risk
from collie_core.permissions.store import PermissionStore
from collie_core.tools.calendar import CalendarTool
from collie_core.tools.contacts import ContactsTool
from collie_core.tools.documents import DocumentsTool
from collie_core.tools.email import EmailTool
from collie_core.tools.notes import NotesTool
from collie_core.tools.presentations import PresentationsTool
from collie_core.tools.recipes import RecipesTool
from collie_core.tools.reminders import RemindersTool
from collie_core.tools.shopping import ShoppingTool
from collie_core.tools.subagent import CallSubagentTool
from collie_core.tools.travel import TravelTool


def _evaluator(db: CollieDB, *, gated: bool = False) -> PermissionEvaluator:
    return PermissionEvaluator(
        PermissionStore(db),
        review_gate_provider=(lambda _conversation_id: {"reasons": ["stale"]}) if gated else None,
    )


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        (NotesTool(), {"action": "create"}),
        (RemindersTool(), {"action": "complete", "reminder_id": "r-1"}),
        (ShoppingTool(), {"action": "check", "item": "Milk"}),
        (CalendarTool(), {"action": "create", "title": "Lunch"}),
        (ContactsTool(), {"action": "upsert", "name": "Pat"}),
        (CallSubagentTool(manager=object()), {"name": "Researcher", "task": "Compare options"}),
    ],
)
def test_named_ordinary_operations_are_approval_free(tool, params) -> None:
    request = tool.permission_request(params)
    assert request.risk == Risk.LOCAL_WRITE
    assert request.approval_free is True
    assert request.approve_for_me is True


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        (CalendarTool(), {"action": "list"}),
        (ContactsTool(), {"action": "find", "name": "Pat"}),
        (DocumentsTool(), {"action": "read", "query": "Brief"}),
        (EmailTool(), {"action": "search", "query": "invoice"}),
        (PresentationsTool(), {"action": "outline", "topic": "Launch"}),
        (RecipesTool(), {"action": "search", "query": "pasta"}),
        (TravelTool(), {"action": "itinerary", "destination": "Tokyo"}),
    ],
)
def test_read_like_operations_are_read_risk(tool, params) -> None:
    assert tool.permission_request(params).risk == Risk.READ


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        (RemindersTool(), {"action": "delete", "reminder_id": "r-1"}),
        (ShoppingTool(), {"action": "remove", "item": "Milk"}),
        (ShoppingTool(), {"action": "clear_checked"}),
    ],
)
def test_personal_deletes_are_hard_destructive(tool, params) -> None:
    request = tool.permission_request(params)
    assert request.action == "delete.destructive"
    assert request.risk == Risk.DESTRUCTIVE
    assert request.hard_approval is True
    assert request.approval_free is False


def test_repeating_reminder_is_not_approval_free() -> None:
    request = RemindersTool().permission_request(
        {"action": "create", "text": "Stretch", "recurrence": "daily"}
    )
    assert request.approval_free is False
    assert request.approve_for_me is False


def test_contact_allergy_upsert_needs_fresh_sensitive_approval(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    db.add_approval_rule(
        action="*", resource_pattern="*", effect="allow", scope_type="run", scope_value="run-1"
    )
    request = ContactsTool().permission_request(
        {"action": "upsert", "name": "Pat", "allergies": "peanuts"}
    )

    assert request.action == "contacts.upsert_sensitive"
    assert request.risk == Risk.SENSITIVE
    assert request.hard_approval is True
    assert request.approval_free is False
    assert request.approve_for_me is False
    assert _evaluator(db).evaluate(ExecutionContext(run_id="run-1"), request).effect == Effect.ASK
    db.close()


def test_ordinary_work_bypasses_stale_review_gate_but_not_plan_mode(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    request = NotesTool().permission_request({"action": "create"})
    evaluator = _evaluator(db, gated=True)
    assert (
        evaluator.evaluate(
            ExecutionContext(conversation_id="c", execution_mode="execute"), request
        ).effect
        == Effect.ALLOW
    )
    assert (
        evaluator.evaluate(
            ExecutionContext(conversation_id="c", execution_mode="plan"), request
        ).effect
        == Effect.DENY
    )
    db.close()


def test_run_wide_approval_needs_explicit_eligibility(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    db.add_approval_rule(
        action="*", resource_pattern="*", effect="allow", scope_type="run", scope_value="run-1"
    )
    evaluator = _evaluator(db)
    unknown_local = PermissionRequest(
        action="tool.future_write",
        resource="x",
        risk=Risk.LOCAL_WRITE,
        summary="Future write",
        reversible=True,
    )
    unknown_mcp = PermissionRequest(
        action="mcp.unknown",
        resource="server",
        risk=Risk.EXTERNAL_WRITE,
        summary="Unknown MCP",
        reversible=True,
        approve_for_me=True,
    )
    recurring = PermissionRequest(
        action=" Routine.create ",
        resource="daily",
        risk=Risk.LOCAL_WRITE,
        summary="Create routine",
        reversible=True,
        approve_for_me=True,
    )
    capability = PermissionRequest(
        action="capability.skill.create",
        resource="skill",
        risk=Risk.LOCAL_WRITE,
        summary="Create skill",
        reversible=True,
        approve_for_me=True,
    )
    bounded_file_write = PermissionRequest(
        action="local_file.write",
        resource="C:/project/report.md",
        risk=Risk.LOCAL_WRITE,
        summary="Save local file",
        reversible=False,
        approve_for_me=True,
    )
    mis_tagged_hard = PermissionRequest(
        action=" External.Publish ",
        resource="destination",
        risk=Risk.LOCAL_WRITE,
        summary="Publish",
        reversible=True,
        approval_free=True,
        approve_for_me=True,
    )
    ordinary = NotesTool().permission_request({"action": "create"})
    context = ExecutionContext(run_id="run-1")
    assert evaluator.evaluate(context, unknown_local).effect == Effect.ASK
    assert evaluator.evaluate(context, unknown_mcp).effect == Effect.ASK
    assert evaluator.evaluate(context, recurring).effect == Effect.ASK
    assert evaluator.evaluate(context, capability).effect == Effect.ASK
    assert evaluator.evaluate(context, bounded_file_write).effect == Effect.ALLOW
    assert evaluator.evaluate(context, mis_tagged_hard).effect == Effect.ASK
    assert evaluator.evaluate(context, ordinary).effect == Effect.ALLOW
    db.close()


def test_allow_run_rejects_an_ineligible_pending_request(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    broker = ApprovalBroker(db, _evaluator(db))
    row = db.create_approval_request(
        action="mcp.unknown",
        resource="server",
        risk=Risk.EXTERNAL_WRITE,
        display={"summary": "Unknown MCP", "approve_for_me_eligible": False},
        run_id="run-1",
    )

    async def resolve() -> None:
        with pytest.raises(ValueError, match="not eligible"):
            await broker.resolve(str(row["id"]), "allow_run")

    asyncio.run(resolve())
    assert db.list_approval_rules() == []
    db.close()


@pytest.mark.parametrize("action", [" Routine.create ", "capability.agent.create", "MESSAGE.SEND"])
def test_mistagged_durable_actions_never_receive_automatic_approval(
    tmp_path: Path, action: str
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="allow")
    request = PermissionRequest(
        action=action,
        resource="local",
        risk=Risk.LOCAL_WRITE,
        summary="Mis-tagged action",
        reversible=True,
        approval_free=True,
        approve_for_me=True,
    )
    assert evaluator.evaluate(ExecutionContext(), request).effect == Effect.ASK
    db.close()


def test_generic_action_delete_is_not_hidden_by_a_read_name() -> None:
    request = classify_tool(object(), "news_manage", {"action": "delete"})
    assert request.action == "delete.destructive"
    assert request.risk == Risk.DESTRUCTIVE
