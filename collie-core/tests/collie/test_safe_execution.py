from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import (
    Effect,
    ExecutionContext,
    PermissionRequest,
    Risk,
)
from collie_core.permissions.store import PermissionStore
from collie_core.plans.models import validate_plan
from collie_core.routines.schedule import next_occurrence, parse_schedule


def _request(risk: Risk = Risk.LOCAL_WRITE, action: str = "file.write") -> PermissionRequest:
    return PermissionRequest(
        action=action,
        resource="C:/work/file.txt",
        risk=risk,
        summary="Change a file",
        reversible=True,
        approve_for_me=risk == Risk.LOCAL_WRITE,
    )


def test_plan_mode_hard_denies_mutation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="allow")
    decision = evaluator.evaluate(ExecutionContext(execution_mode="plan"), _request())
    assert decision.effect == Effect.DENY
    db.close()


def test_deny_preset_denies_local_writes_but_keeps_reads(tmp_path: Path) -> None:
    """The bench 'deny' posture refuses local writes; reads stay allowed."""
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="deny")

    denied = evaluator.evaluate(
        ExecutionContext(execution_mode="execute"),
        _request(risk=Risk.LOCAL_WRITE),
    )
    assert denied.effect == Effect.DENY

    # Even an otherwise ordinary-safe local write is denied under deny.
    safe_write = PermissionRequest(
        action="file.write",
        resource="C:/work/notes.txt",
        risk=Risk.LOCAL_WRITE,
        summary="Write a note",
        reversible=True,
        approval_free=True,
        approve_for_me=True,
    )
    assert (
        evaluator.evaluate(ExecutionContext(execution_mode="execute"), safe_write).effect
        == Effect.DENY
    )

    # Reads are unaffected by the deny preset.
    read = evaluator.evaluate(
        ExecutionContext(execution_mode="execute"),
        _request(risk=Risk.READ),
    )
    assert read.effect == Effect.ALLOW

    # An explicit allow rule still wins over the deny preset.
    db.add_approval_rule(
        action="file.write",
        resource_pattern="C:/work/notes.txt",
        effect="allow",
        scope_type="global",
    )
    allowed = evaluator.evaluate(ExecutionContext(execution_mode="execute"), safe_write)
    assert allowed.effect == Effect.ALLOW
    db.close()


def test_read_only_specialist_hard_denies_mutation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="allow")
    decision = evaluator.evaluate(
        ExecutionContext(execution_mode="execute", execution_posture="read_only"),
        _request(),
    )
    assert decision.effect == Effect.DENY
    db.close()


def test_explicit_deny_beats_run_auto_approval(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="file.*",
        resource_pattern="*",
        effect="deny",
        scope_type="global",
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    decision = evaluator.evaluate(
        ExecutionContext(run_id="run-1", approve_all_for_run=True), _request()
    )
    assert decision.effect == Effect.DENY
    db.close()


def test_sensitive_action_always_asks(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="financial.purchase",
        resource="merchant",
        risk=Risk.SENSITIVE,
        summary="Buy something",
        reversible=False,
        hard_approval=True,
    )
    decision = evaluator.evaluate(
        ExecutionContext(run_id="run-1", approve_all_for_run=True), request
    )
    assert decision.effect == Effect.ASK
    db.close()


def test_structured_weekdays_and_monthly_schedules() -> None:
    weekdays = parse_schedule("Every weekday at 8am", "Asia/Shanghai")
    monthly = parse_schedule("First day of every month at 9am", "Asia/Shanghai")
    assert weekdays.kind == "weekdays"
    assert monthly.kind == "monthly"
    assert monthly.day == 1
    occurrence = next_occurrence(weekdays, datetime(2026, 7, 26, 0, 0, tzinfo=timezone.utc))
    assert occurrence is not None
    assert occurrence.astimezone().tzinfo is not None


def test_plan_versions_invalidate_old_approval(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    raw = {
        "title": "Weekly update",
        "goal": "Prepare an update",
        "steps": [{"key": "collect", "title": "Collect", "risk": "read"}],
    }
    plan = validate_plan(raw)
    first = db.create_plan(title=plan["title"], goal=plan["goal"], plan=plan)
    db.approve_plan(first["id"], first["version"], first["plan_hash"])
    second = db.create_plan(
        title=plan["title"],
        goal=plan["goal"],
        plan={**plan, "assumptions": ["New input"]},
        plan_id=first["id"],
    )
    assert second["version"] == 2
    with pytest.raises(ValueError, match="superseded"):
        db.approve_plan(first["id"], first["version"], first["plan_hash"])
    db.close()


def test_scheduled_run_claim_is_idempotent(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    auto = db.add_automation("Routine", enabled=False)
    first = db.claim_scheduled_run(
        auto["id"],
        scheduled_for="2026-07-27T00:00:00+00:00",
        next_run_at="2026-07-28T00:00:00+00:00",
        plan_id="p",
        plan_version=1,
    )
    second = db.claim_scheduled_run(
        auto["id"],
        scheduled_for="2026-07-27T00:00:00+00:00",
        next_run_at="2026-07-28T00:00:00+00:00",
        plan_id="p",
        plan_version=1,
    )
    assert first is not None
    assert second is None
    db.close()


# -- workspace boundary tests -------------------------------------------------


def test_read_outside_project_asks_for_approval(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    outside = str(tmp_path / "other" / "secret.txt")
    request = PermissionRequest(
        action="file.read",
        resource=outside,
        risk=Risk.READ,
        summary="Read a file outside the project",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ASK
    assert "outside the active project" in decision.reason
    db.close()


def test_read_inside_project_is_auto_allowed(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    inside = str(project / "data.csv")
    request = PermissionRequest(
        action="file.read",
        resource=inside,
        risk=Risk.READ,
        summary="Read a file inside the project",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ALLOW
    db.close()


def test_read_inside_granted_local_root_is_auto_allowed(tmp_path: Path) -> None:
    """A folder granted via Files -> Choose other folders needs no project ask."""
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    granted = tmp_path / "granted"
    granted.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    target = str(granted / "notes.md")
    request = PermissionRequest(
        action="local_file.read",
        resource=target,
        risk=Risk.READ,
        summary="List a folder the user granted",
        reversible=True,
        redacted_parameters={"allowed_local_roots": [str(granted)]},
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ALLOW
    db.close()


def test_read_with_full_local_file_access_is_auto_allowed(tmp_path: Path) -> None:
    """Full file access skips the project-boundary ask for local file tools."""
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="local_file.read",
        resource=str(tmp_path / "anywhere" / "notes.md"),
        risk=Risk.READ,
        summary="List anywhere with full file access",
        reversible=True,
        redacted_parameters={"unrestricted_local_files": True},
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ALLOW
    db.close()


def test_read_without_project_is_auto_allowed(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="web.read",
        resource="https://example.com",
        risk=Risk.READ,
        summary="Web read — no project_path set",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=None), request)
    assert decision.effect == Effect.ALLOW
    db.close()


def test_read_non_path_resource_auto_allowed_even_with_project(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="weather.read",
        resource="New York",
        risk=Risk.READ,
        summary="Weather read is not a filesystem path",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ALLOW
    db.close()


@pytest.mark.parametrize(
    ("action", "resource"),
    [
        ("web_fetch", "https://heycollie.com"),
        ("web_search", "http://example.com/search?q=collie"),
    ],
)
def test_read_only_http_tools_are_auto_allowed_with_active_project(
    tmp_path: Path, action: str, resource: str
) -> None:
    """HTTP URLs are remote resources, not paths outside the selected project."""
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    request = PermissionRequest(
        action=action,
        resource=resource,
        risk=Risk.READ,
        summary="Read a public web page",
        reversible=True,
    )
    decision = PermissionEvaluator(PermissionStore(db)).evaluate(
        ExecutionContext(project_path=str(project)), request
    )
    assert decision.effect == Effect.ALLOW
    db.close()


@pytest.mark.parametrize(
    "resource",
    [
        r"C:\outside\secret.txt",
        r"\\server\share\secret.txt",
        "/outside/secret.txt",
        "../outside/secret.txt",
    ],
)
def test_path_like_reads_outside_project_still_ask_for_approval(
    tmp_path: Path, resource: str
) -> None:
    """Windows, UNC, POSIX, and relative filesystem paths retain the boundary."""
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    request = PermissionRequest(
        action="file.read",
        resource=resource,
        risk=Risk.READ,
        summary="Read outside the project",
        reversible=True,
    )
    decision = PermissionEvaluator(PermissionStore(db)).evaluate(
        ExecutionContext(project_path=str(project)), request
    )
    assert decision.effect == Effect.ASK
    db.close()


def test_write_outside_project_still_asks_regardless(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    project = tmp_path / "project"
    project.mkdir()
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="file.write",
        resource=str(tmp_path / "other" / "out.txt"),
        risk=Risk.LOCAL_WRITE,
        summary="Write outside project",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ASK
    db.close()


def test_folder_scoped_allow_grants_read_inside_folder(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    allowed = tmp_path / "shared"
    allowed.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    db.add_approval_rule(
        action="file.*",
        resource_pattern=str(allowed),
        effect="allow",
        scope_type="folder",
        scope_value=str(allowed),
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="file.read",
        resource=str(allowed / "report.pdf"),
        risk=Risk.READ,
        summary="Read from an explicitly allowed shared folder",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(project_path=str(project)), request)
    assert decision.effect == Effect.ALLOW
    db.close()


# -- D1: evaluator hardening ----------------------------------------------------


def test_naive_expiry_does_not_crash_evaluator(tmp_path: Path) -> None:
    """A naive (tz-less) expires_at must be tolerated, not raise TypeError."""
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="file.write",
        resource_pattern="C:/work/file.txt",
        effect="allow",
        scope_type="global",
        expires_at="2026-07-20T13:00:00",  # naive on purpose
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    decision = evaluator.evaluate(ExecutionContext(), _request())
    assert decision.effect in (Effect.ALLOW, Effect.ASK)
    db.close()


def test_deny_rule_with_unparseable_expiry_stays_active(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="file.write",
        resource_pattern="*",
        effect="deny",
        scope_type="global",
        expires_at="not-a-date",
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    decision = evaluator.evaluate(ExecutionContext(), _request())
    assert decision.effect == Effect.DENY
    db.close()


def test_empty_run_scope_never_matches_normal_chat(tmp_path: Path) -> None:
    """A run rule with a blank scope_value must not approve every chat."""
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="file.write",
        resource_pattern="*",
        effect="allow",
        scope_type="run",
        scope_value="",
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    decision = evaluator.evaluate(ExecutionContext(run_id=None), _request())
    assert decision.effect == Effect.ASK
    db.close()


def test_run_scoped_rule_matches_only_its_run(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="file.write",
        resource_pattern="C:/work/file.txt",
        effect="allow",
        scope_type="run",
        scope_value="run-42",
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    assert evaluator.evaluate(ExecutionContext(run_id="run-42"), _request()).effect == Effect.ALLOW
    assert evaluator.evaluate(ExecutionContext(run_id="run-99"), _request()).effect == Effect.ASK
    other_resource = PermissionRequest(
        action="file.write",
        resource="different-resource",
        risk=Risk.LOCAL_WRITE,
        summary="Write somewhere else",
        reversible=True,
    )
    assert (
        evaluator.evaluate(ExecutionContext(run_id="run-42"), other_resource).effect == Effect.ASK
    )
    db.close()


@pytest.mark.parametrize("scope_type", ["once", "service", "unknown", ""])
def test_non_authoritative_scopes_never_match_chat(tmp_path: Path, scope_type: str) -> None:
    db = CollieDB(tmp_path / f"{scope_type or 'empty'}.sqlite")
    db.add_approval_rule(
        action="file.write",
        resource_pattern="*",
        effect="allow",
        scope_type=scope_type,
        scope_value="anything",
    )
    decision = PermissionEvaluator(PermissionStore(db)).evaluate(ExecutionContext(), _request())
    assert decision.effect == Effect.ASK
    db.close()


def test_deny_rule_beats_plan_mode_short_circuit(tmp_path: Path) -> None:
    """Plan mode's allow for plan.present must not mask an explicit deny."""
    db = CollieDB(tmp_path / "db.sqlite")
    db.add_approval_rule(
        action="plan.present",
        resource_pattern="*",
        effect="deny",
        scope_type="global",
    )
    evaluator = PermissionEvaluator(PermissionStore(db))
    request = PermissionRequest(
        action="plan.present",
        resource="internal:plans",
        risk=Risk.LOCAL_WRITE,
        summary="Save this plan",
        reversible=True,
    )
    decision = evaluator.evaluate(ExecutionContext(execution_mode="plan"), request)
    assert decision.effect == Effect.DENY
    db.close()


def test_message_tool_classifies_as_hard_external_write() -> None:
    """C10: the message tool must be a hard-approval external write."""
    from collie_core.permissions.classifier import classify_tool

    request = classify_tool(object(), "message", {"content": "hi", "channel": "telegram"})
    assert request.action == "message.send"
    assert request.risk == Risk.EXTERNAL_WRITE
    assert request.hard_approval is True


def test_allow_run_requires_real_run_id(tmp_path: Path) -> None:
    """H26: 'allow for this run' without a run id must be rejected."""
    import asyncio

    from collie_core.permissions.broker import ApprovalBroker

    db = CollieDB(tmp_path / "db.sqlite")
    broker = ApprovalBroker(db, PermissionEvaluator(PermissionStore(db)))
    request = db.create_approval_request(
        action="file.write",
        resource="x",
        risk="local_write",
        display={"summary": "s"},
        run_id=None,  # normal chat — no run
    )

    async def run() -> None:
        with pytest.raises(ValueError, match="not tied to a run"):
            await broker.resolve(str(request["id"]), "allow_run")

    asyncio.run(run())
    assert db.list_approval_rules() == []
    assert [row["id"] for row in db.list_pending_approvals()] == [request["id"]]
    db.close()
