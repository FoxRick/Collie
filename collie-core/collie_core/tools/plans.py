"""Controlled Plan-mode state change for presenting a validated plan."""

from __future__ import annotations

import json
from typing import Any

from collie_core.db import CollieDB
from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.plans.models import validate_plan
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.runtime_context import RuntimeContextBlock

__all__ = ["PresentPlanTool", "bind_plans_db"]

_db: CollieDB | None = None


def bind_plans_db(db: CollieDB) -> None:
    global _db
    _db = db


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "plan": {"type": "object", "additionalProperties": True},
            "plan_id": {"type": ["string", "null"]},
        },
        "required": ["plan"],
    }
)
class PresentPlanTool(Tool):
    @property
    def name(self) -> str:
        return "present_plan"

    @property
    def description(self) -> str:
        return (
            "Present a structured plan for user review. Use this in Plan mode once the "
            "goal, assumptions, steps, expected tools, risks, verification, services, "
            "expected approvals, and success criteria are clear."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        plan = params.get("plan")
        title = plan.get("title") if isinstance(plan, dict) else ""
        return PermissionRequest(
            action="plan.present",
            resource="internal:plans",
            risk=Risk.LOCAL_WRITE,
            summary="Save this plan for review",
            reversible=True,
            suggested_scope=Scope.ONCE,
            redacted_parameters={"title": str(title or "")},
        )

    async def execute(self, **kwargs: Any) -> Any:
        if _db is None:
            return ToolResult.error("Plans are not ready yet.")
        try:
            plan = validate_plan(kwargs.get("plan"))
        except ValueError as exc:
            return ToolResult.error(str(exc))
        request = current_request_context()
        permission = request.metadata.get("permission_context", {}) if request else {}
        if not isinstance(permission, dict):
            permission = {}
        conversation_id = (
            str(permission.get("conversation_id") or request.chat_id or "") if request else ""
        )
        change = _db.get_plan_change_context(conversation_id) if conversation_id else None
        trusted_plan_id = str((change or {}).get("plan_id") or "") or None
        if change is not None and str(change.get("status") or "") == "requested":
            return ToolResult.error(
                "The current tool is still reaching a safe stopping point. "
                "Present the replacement plan after it stops."
            )
        terminal_message = (
            _db.claim_plan_change_terminal_message(str(change["run_id"]))
            if change is not None
            else None
        )
        row = _db.create_plan(
            title=plan["title"],
            goal=plan["goal"],
            plan=plan,
            conversation_id=conversation_id or None,
            plan_id=trusted_plan_id or str(kwargs.get("plan_id") or "") or None,
        )
        if change is not None:
            _db.mark_plan_change_replanned(str(change["run_id"]), int(row["version"]))
        result = {
            "card_type": "plan",
            "plan_id": row["id"],
            "version": row["version"],
            "plan_hash": row["plan_hash"],
            "plan": plan,
        }
        if terminal_message is not None:
            result["plan_change_terminal_message"] = terminal_message
        return json.dumps(result, ensure_ascii=False)

    def runtime_context_provider(self):
        async def provide(request: Any) -> RuntimeContextBlock:
            permission = request.metadata.get("permission_context", {})
            if not isinstance(permission, dict):
                permission = {}
            conversation_id = str(permission.get("conversation_id") or request.chat_id or "")
            change = _db.get_plan_change_context(conversation_id) if _db is not None else None
            guidance = ""
            if change is not None:
                guidance = (
                    " The user requested changes to the active reviewed plan. Present the "
                    f"replacement with the same immutable plan_id {change['plan_id']!r}; "
                    "the host will enforce that identity and create the next version."
                )
            return RuntimeContextBlock(source="plan-change", content=guidance)

        return provide
