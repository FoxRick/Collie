"""Cron scheduling is recurring authority: add/remove always hard-ask.

A scheduled job keeps acting for the user after creation, so creating or
removing one is never automatic and never run-approvable (see
``collie_core/permissions/defaults.py`` for the ``cron.`` ineligible
prefix). Listing stays an ordinary ask.
"""

from pathlib import Path

from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, Risk
from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronService


def _tool(tmp_path: Path) -> CronTool:
    service = CronService(tmp_path / "cron" / "jobs.json")
    return CronTool(service)


def test_cron_add_is_hard_approval_and_always_asks(tmp_path: Path) -> None:
    request = _tool(tmp_path).permission_request({"action": "add", "message": "daily standup"})

    assert request.action == "cron.add"
    assert request.hard_approval is True
    assert request.approval_free is False
    assert request.approve_for_me is False
    # Even a run-wide "approve everything" never covers a new schedule.
    decision = PermissionEvaluator().evaluate(
        ExecutionContext(run_id="run-1", approve_all_for_run=True), request
    )
    assert decision.effect == Effect.ASK


def test_cron_remove_is_hard_destructive(tmp_path: Path) -> None:
    request = _tool(tmp_path).permission_request({"action": "remove", "job_id": "job-1"})

    assert request.action == "delete.destructive"
    assert request.risk == Risk.DESTRUCTIVE
    assert request.hard_approval is True
    assert request.approval_free is False


def test_cron_list_asks_and_is_never_run_approvable(tmp_path: Path) -> None:
    request = _tool(tmp_path).permission_request({"action": "list"})

    assert request.action == "cron.list"
    assert request.hard_approval is False
    assert request.approval_free is False
    assert request.approve_for_me is False
    decision = PermissionEvaluator().evaluate(ExecutionContext(run_id="run-1"), request)
    assert decision.effect == Effect.ASK
