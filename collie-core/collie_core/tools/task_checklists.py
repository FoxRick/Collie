"""Model-owned task checklist progress backed by Collie's durable store."""

from __future__ import annotations

import json
from typing import Any

from collie_core.db import CollieDB
from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.runtime_context import RuntimeContextBlock

__all__ = ["ManageTaskChecklistTool", "bind_task_checklists_db"]

_db: CollieDB | None = None
_CREATE_REVIEW_FIELDS = (
    "services",
    "material_commitment",
    "unstable_success_criterion",
    "requires_review",
)


def bind_task_checklists_db(db: CollieDB) -> None:
    global _db
    _db = db


def _request_scope() -> tuple[str, str | None]:
    request = current_request_context()
    if request is None:
        return "", None
    permission = request.metadata.get("permission_context", {})
    if not isinstance(permission, dict):
        permission = {}
    conversation_id = str(permission.get("conversation_id") or request.chat_id or "")
    run_id = str(permission.get("run_id") or "") or None
    return conversation_id, run_id


def _renderer_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key != "conversation_id"}


def _event(task: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": "task_state",
            "conversation_id": str(task.get("conversation_id") or ""),
            "task": _renderer_task(task),
        },
        ensure_ascii=False,
    )


def _create_review_metadata_errors(params: dict[str, Any]) -> list[str]:
    errors = [f"{name} is required for create" for name in _CREATE_REVIEW_FIELDS if name not in params]
    services = params.get("services")
    if "services" in params and (
        not isinstance(services, list)
        or not all(isinstance(service, str) for service in services)
    ):
        errors.append("services must be a list of strings")
    for name in _CREATE_REVIEW_FIELDS[1:]:
        if name in params and not isinstance(params.get(name), bool):
            errors.append(f"{name} must be boolean")
    return errors


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["create", "update", "complete", "cancel"],
            },
            "checklist_id": {"type": ["string", "null"]},
            "task_id": {"type": ["string", "null"]},
            "expected_revision": {"type": ["integer", "null"], "minimum": 1},
            "goal": {"type": ["string", "null"]},
            "steps": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "required": ["key", "title"],
                    "additionalProperties": False,
                },
            },
            "services": {
                "type": ["array", "null"],
                "items": {"type": "string"},
            },
            "material_commitment": {"type": ["boolean", "null"]},
            "unstable_success_criterion": {"type": ["boolean", "null"]},
            "requires_review": {"type": ["boolean", "null"]},
            "step_key": {"type": ["string", "null"]},
            "status": {
                "type": ["string", "null"],
                "enum": [
                    "pending",
                    "in_progress",
                    "completed",
                    "blocked",
                    "skipped",
                    "failed",
                    None,
                ],
            },
            "summary": {"type": ["string", "null"]},
            "error_message": {"type": ["string", "null"]},
            "title": {"type": ["string", "null"]},
            "reason": {"type": ["string", "null"]},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
)
class ManageTaskChecklistTool(Tool):
    @property
    def name(self) -> str:
        return "manage_task_checklist"

    @property
    def description(self) -> str:
        return (
            "Create and update the durable user-visible checklist for ordinary multi-step "
            "work. Use present_plan instead when work needs seven or more meaningful steps."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        return PermissionRequest(
            action="task.progress",
            resource="internal:task-progress",
            risk=Risk.LOCAL_WRITE,
            summary="Update visible task progress",
            reversible=True,
            redacted_parameters={"operation": str(params.get("operation") or "")},
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        operation = str(params.get("operation") or "")
        task_id = params.get("checklist_id") or params.get("task_id")
        if operation == "create":
            if not str(params.get("goal") or "").strip():
                errors.append("goal is required for create")
            if not isinstance(params.get("steps"), list):
                errors.append("steps are required for create")
            errors.extend(_create_review_metadata_errors(params))
        elif operation == "update":
            if not task_id:
                errors.append("checklist_id is required for update")
            if not str(params.get("step_key") or "").strip():
                errors.append("step_key is required for update")
            if not str(params.get("status") or "").strip():
                errors.append("status is required for update")
        elif operation in {"complete", "cancel"} and not task_id:
            errors.append(f"checklist_id is required for {operation}")
        return errors

    async def execute(self, **kwargs: Any) -> Any:
        if _db is None:
            return ToolResult.error("Task checklists are not ready yet.")
        conversation_id, run_id = _request_scope()
        if not conversation_id:
            return ToolResult.error("This task is not attached to a conversation.")
        operation = str(kwargs.get("operation") or "")
        task_id = str(kwargs.get("checklist_id") or kwargs.get("task_id") or "")

        try:
            if run_id is not None:
                run = _db.get_run(run_id)
                if run is None or str(run.get("conversation_id") or "") != conversation_id:
                    raise ValueError("This plan run does not belong to the current conversation.")
                if operation != "update":
                    raise ValueError(
                        "Approved plan runs use update only; their lifecycle is controlled by the host."
                    )
                if task_id and task_id != run_id:
                    raise ValueError("Use the current plan run ID when updating its progress.")
                if kwargs.get("title") is not None:
                    raise ValueError("Approved plan step titles are immutable.")
                task = _db.update_run_task_step(
                    run_id,
                    str(kwargs.get("step_key") or ""),
                    status=str(kwargs.get("status") or ""),
                    summary=kwargs.get("summary"),
                    error_message=kwargs.get("error_message"),
                )
                return _event(task)

            if operation == "create":
                if _db.get_conversation_review_gate(conversation_id) is not None:
                    raise ValueError(
                        "This work needs review first. Use present_plan and wait for approval."
                    )
                metadata_errors = _create_review_metadata_errors(kwargs)
                if metadata_errors:
                    raise ValueError("; ".join(metadata_errors))
                steps = kwargs.get("steps")
                services = {
                    str(service).strip().casefold()
                    for service in (kwargs.get("services") or [])
                    if str(service).strip()
                }
                review_reasons = []
                if isinstance(steps, list) and len(steps) >= 7:
                    review_reasons.append("seven_or_more_steps")
                if len(services) >= 2:
                    review_reasons.append("multiple_services")
                if bool(kwargs.get("material_commitment")):
                    review_reasons.append("material_commitment")
                if bool(kwargs.get("unstable_success_criterion")):
                    review_reasons.append("unstable_success_criterion")
                if bool(kwargs.get("requires_review")):
                    review_reasons.append("requires_review")
                if review_reasons:
                    _db.require_conversation_review(conversation_id, review_reasons)
                    raise ValueError(
                        "This work needs review first. Use present_plan and wait for approval."
                    )
                if _db.get_active_task(conversation_id) is not None:
                    raise ValueError("Finish or cancel the current task before creating another one.")
                task = _db.create_task_checklist(
                    conversation_id=conversation_id,
                    goal=str(kwargs.get("goal") or ""),
                    steps=steps if isinstance(steps, list) else [],
                )
                return _event(task)

            task = _db.get_task_checklist(task_id)
            if task is None:
                raise ValueError("Task checklist not found.")
            if str(task.get("conversation_id") or "") != conversation_id:
                raise ValueError("This checklist does not belong to the current conversation.")
            revision = kwargs.get("expected_revision")
            if revision is None:
                raise ValueError("expected_revision is required for checklist changes.")
            if operation == "update":
                task = _db.update_task_checklist(
                    task_id,
                    expected_revision=int(revision),
                    step_key=str(kwargs.get("step_key") or ""),
                    status=str(kwargs.get("status") or ""),
                    summary=kwargs.get("summary"),
                    error_message=kwargs.get("error_message"),
                    title=kwargs.get("title"),
                )
            elif operation == "complete":
                task = _db.complete_task_checklist(
                    task_id, expected_revision=int(revision)
                )
            elif operation == "cancel":
                task = _db.cancel_task_checklist(
                    task_id,
                    expected_revision=int(revision),
                    reason=kwargs.get("reason"),
                )
            else:
                raise ValueError(f"Unknown checklist operation: {operation}")
            return _event(task)
        except (TypeError, ValueError) as exc:
            return ToolResult.error(str(exc))

    def runtime_context_provider(self):
        async def provide(request: Any) -> RuntimeContextBlock:
            permission = request.metadata.get("permission_context", {})
            if not isinstance(permission, dict):
                permission = {}
            conversation_id = str(permission.get("conversation_id") or request.chat_id or "")
            task = _db.get_active_task(conversation_id) if _db is not None else None
            active = ""
            if task is not None:
                active = (
                    "\nCurrent authoritative task snapshot:\n"
                    + json.dumps(_renderer_task(task), ensure_ascii=False)
                )
            return RuntimeContextBlock(
                source="task-checklists",
                content=(
                    "For work with 3–6 meaningful dependent outcomes, call "
                    "manage_task_checklist(create) before starting, keep exactly one step "
                    "in_progress, and explicitly complete, block, fail, skip, or cancel the "
                    "checklist before ending. For 7 or more meaningful steps, use present_plan "
                    "and wait for review. On create, always populate services, "
                    "material_commitment, unstable_success_criterion, and requires_review; "
                    "two or more services or any true review flag also requires present_plan. "
                    "Never treat a successful tool call as proof that its outcome is complete."
                    + active
                ),
            )

        return provide
