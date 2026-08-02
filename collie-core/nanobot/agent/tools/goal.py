"""Session-scoped sustained-goal tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.goal_permission import goal_mutation_allowed
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import (
    current_request_context,
    current_request_session_key,
)
from nanobot.bus.runtime_events import GoalStateChanged, RuntimeEventContext
from nanobot.runtime_context import RuntimeContextBlock
from nanobot.session.goal_state import (
    GOAL_STATE_KEY,
    MAX_GOAL_OBJECTIVE_CHARS,
    discard_legacy_goal_state_key,
    goal_state_raw,
    goal_state_runtime_lines,
    parse_goal_state,
    trusted_goal_start_requested,
)
from nanobot.session.turn_continuation import reset_goal_continuation_rounds
from nanobot.utils.prompt_templates import render_template

_MAX_SUMMARY_CHARS = 120
_TERMINAL_STATUS = {
    "complete": "completed",
    "cancel": "cancelled",
    "block": "blocked",
}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _goal_response(metadata: dict[str, Any]) -> dict[str, Any]:
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict):
        return {"active": False, "status": "none"}
    return {"active": goal.get("status") == "active", **goal}


def _summary(objective: str, supplied: str | None) -> str:
    value = str(supplied or "").strip()
    if not value:
        value = " ".join(objective.split())
    return value[:_MAX_SUMMARY_CHARS].rstrip()


class _GoalTool(Tool):
    _plugin_discoverable = False

    def __init__(self, sessions: Any, runtime_events: Any | None = None) -> None:
        self._sessions = sessions
        self._runtime_events = runtime_events

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.sessions is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(ctx.sessions, ctx.runtime_events)

    @property
    def exclusive(self) -> bool:
        return not self.read_only

    def _session(self) -> Any | ToolResult:
        key = current_request_session_key()
        if not key:
            return ToolResult.error("Goal tools require an active chat session.")
        return self._sessions.get_or_create(key)

    @staticmethod
    def _trusted_start() -> bool:
        request = current_request_context()
        return trusted_goal_start_requested(request.metadata if request else None)

    async def _persist(self, session: Any) -> None:
        session.updated_at = datetime.now()
        self._sessions.save(session)
        request = current_request_context()
        publish = getattr(self._runtime_events, "publish", None)
        if request is None or not callable(publish):
            return
        await publish(GoalStateChanged(
            context=RuntimeEventContext(
                channel=request.channel,
                chat_id=request.chat_id,
                session_key=session.key,
                metadata=dict(request.metadata or {}),
            ),
            session_metadata=dict(session.metadata),
        ))


@tool_parameters({
    "type": "object",
    "properties": {
        "objective": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_GOAL_OBJECTIVE_CHARS,
            "description": "Durable, self-contained objective and completion criteria.",
        },
        "ui_summary": {
            "type": "string",
            "maxLength": _MAX_SUMMARY_CHARS,
            "description": "Optional short display label; never carries required constraints.",
        },
    },
    "required": ["objective"],
    "additionalProperties": False,
})
class CreateGoalTool(_GoalTool):
    """Create a sustained goal for a trusted explicit ``/goal`` turn."""

    _plugin_discoverable = True

    @property
    def name(self) -> str:
        return "create_goal"

    @property
    def description(self) -> str:
        return (
            "Persist a sustained goal for this chat. Use only when the current turn was "
            "explicitly started with /goal; ordinary requests must not create goals."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        return PermissionRequest(
            action="goal.create",
            resource="current-conversation",
            risk=Risk.LOCAL_WRITE,
            summary="Set this one-time goal",
            reversible=True,
            approval_free=True,
            approve_for_me=True,
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        if not str(params.get("objective") or "").strip():
            errors.append("objective must not be blank")
        return errors

    async def execute(self, objective: str, ui_summary: str | None = None) -> str:
        if not goal_mutation_allowed() or not self._trusted_start():
            return ToolResult.error("create_goal is allowed only during a trusted explicit /goal turn.")
        session = self._session()
        if isinstance(session, ToolResult):
            return session
        current = parse_goal_state(goal_state_raw(session.metadata))
        if isinstance(current, dict) and current.get("status") == "active":
            return ToolResult.error(
                "This chat already has an active goal; use update_goal(action='replace') "
                "during an explicit /goal turn."
            )
        objective = objective.strip()
        if not objective:
            return ToolResult.error("Goal objective must not be blank.")
        now = datetime.now(timezone.utc).isoformat()
        session.metadata[GOAL_STATE_KEY] = {
            "version": 1,
            "status": "active",
            "objective": objective,
            "ui_summary": _summary(objective, ui_summary),
            "created_at": now,
            "updated_at": now,
        }
        discard_legacy_goal_state_key(session.metadata)
        reset_goal_continuation_rounds(session.metadata)
        await self._persist(session)
        return _json(_goal_response(session.metadata))

    def runtime_context_provider(self):
        async def provide(request: Any) -> RuntimeContextBlock | None:
            session_key = request.session_key
            session = self._sessions.get_or_create(session_key) if session_key else None
            active = bool(session and _goal_response(session.metadata).get("active"))
            explicit_start = trusted_goal_start_requested(request.metadata)
            if not active and not explicit_start:
                return None
            guidance = render_template(
                "agent/goal_runtime.md",
                goal_start_requested=explicit_start,
                goal_active=active,
                strip=True,
            )
            state_lines = goal_state_runtime_lines(session.metadata) if session else []
            content = "\n\n".join(part for part in ("\n".join(state_lines), guidance) if part)
            return RuntimeContextBlock(source="goal", content=content)

        return provide


@tool_parameters({
    "type": "object",
    "properties": {},
    "additionalProperties": False,
})
class GetGoalTool(_GoalTool):
    """Read the current session's sustained-goal state."""

    _plugin_discoverable = True

    @property
    def name(self) -> str:
        return "get_goal"

    @property
    def description(self) -> str:
        return "Read this chat's current sustained-goal status. This never changes goal state."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> str:
        session = self._session()
        if isinstance(session, ToolResult):
            return session
        return _json(_goal_response(session.metadata))


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["complete", "cancel", "block", "replace"],
            "description": "Transition for the already-active goal.",
        },
        "objective": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_GOAL_OBJECTIVE_CHARS,
            "description": "Required only when action is replace.",
        },
        "ui_summary": {
            "type": "string",
            "maxLength": _MAX_SUMMARY_CHARS,
            "description": "Optional replacement display label.",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
})
class UpdateGoalTool(_GoalTool):
    """Transition or explicitly replace an active sustained goal."""

    _plugin_discoverable = True

    @property
    def name(self) -> str:
        return "update_goal"

    @property
    def description(self) -> str:
        return (
            "Transition an already-active goal: complete, cancel, block, or replace. "
            "Replacement additionally requires a trusted explicit /goal turn."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "update")
        return PermissionRequest(
            action=f"goal.{action}",
            resource="current-conversation",
            risk=Risk.LOCAL_WRITE,
            summary="Update this one-time goal",
            reversible=True,
            approval_free=True,
            approve_for_me=True,
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        if params.get("action") == "replace" and not str(params.get("objective") or "").strip():
            errors.append("objective is required when action='replace'")
        return errors

    async def execute(
        self,
        action: str,
        objective: str | None = None,
        ui_summary: str | None = None,
    ) -> str:
        if not goal_mutation_allowed():
            return ToolResult.error("Goal mutation is not authorized for this turn.")
        session = self._session()
        if isinstance(session, ToolResult):
            return session
        current = parse_goal_state(goal_state_raw(session.metadata))
        if not isinstance(current, dict) or current.get("status") != "active":
            return ToolResult.error("This chat does not have an active goal to update.")

        now = datetime.now(timezone.utc).isoformat()
        if action == "replace":
            if not self._trusted_start():
                return ToolResult.error(
                    "Replacing a goal is allowed only during a trusted explicit /goal turn."
                )
            replacement = str(objective or "").strip()
            if not replacement:
                return ToolResult.error("Replacement objective must not be blank.")
            session.metadata[GOAL_STATE_KEY] = {
                "version": 1,
                "status": "active",
                "objective": replacement,
                "ui_summary": _summary(replacement, ui_summary),
                "created_at": now,
                "updated_at": now,
            }
        else:
            status = _TERMINAL_STATUS[action]
            updated = dict(current)
            updated["status"] = status
            updated["updated_at"] = now
            updated[f"{status}_at"] = now
            session.metadata[GOAL_STATE_KEY] = updated
        discard_legacy_goal_state_key(session.metadata)
        reset_goal_continuation_rounds(session.metadata)
        await self._persist(session)
        return _json(_goal_response(session.metadata))
