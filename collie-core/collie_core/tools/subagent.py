"""Call a named subagent from chat (Step 38, F046).

"Ask my Trip Planner to plan Barcelona" → the model calls this tool, Collie
spawns the engine's background subagent with the specialist's system prompt,
and the result is announced back into the conversation when it finishes.
"""

from __future__ import annotations

from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.subagents.loader import get_subagent_loader
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.security.workspace_access import current_workspace_scope

__all__ = ["CallSubagentTool"]


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The subagent's name, e.g. 'Trip Planner'.",
            },
            "task": {
                "type": "string",
                "description": "What the subagent should do, with all needed details.",
            },
        },
        "required": ["name", "task"],
    }
)
class CallSubagentTool(Tool):
    """Hand a task to one of the user's specialized assistants."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "call_subagent"

    @property
    def description(self) -> str:
        return (
            "Hand a task to one of the user's specialized assistants "
            "(created in Settings → Subagents). Use it when the user names "
            "one ('ask my Trip Planner...') or when a task clearly matches "
            "a specialist. It works in the background and reports back."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        return PermissionRequest(
            action="subagent.spawn",
            resource=str(params.get("name") or "subagent"),
            risk=Risk.LOCAL_WRITE,
            summary=f"Ask {params.get('name') or 'a subagent'} to help",
            reversible=True,
            suggested_scope=Scope.RUN,
            redacted_parameters={"name": str(params.get("name") or "")},
            approval_free=True,
            approve_for_me=True,
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> CallSubagentTool:
        return cls(manager=ctx.subagent_manager)

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("name") or "").strip()
        task = str(kwargs.get("task") or "").strip()
        if not task:
            return ToolResult.error("The subagent needs a task to chew on.")

        loader = get_subagent_loader()
        if loader is None:
            return ToolResult.error("Subagents aren't set up yet.")
        subagent = loader.find(name)
        if subagent is None:
            names = [str(r["name"]) for r in loader.db.list_subagents()]
            if names:
                return (
                    f"I don't have a helper called '{name}'. My helpers are: "
                    f"{', '.join(names)}. Or make a new one in "
                    "Settings → Subagents!"
                )
            return (
                f"I don't have a helper called '{name}' yet — no subagents "
                "exist. The user can create one in Settings → Subagents."
            )

        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("No model is awake to run the subagent.")

        posture = str(subagent.get("execution_posture") or "read_only")
        session_key = request_ctx.session_key or (f"{request_ctx.channel}:{request_ctx.chat_id}")
        if hasattr(self._manager, "get_running_statuses_by_session"):
            running_statuses = self._manager.get_running_statuses_by_session(session_key)
            running = len(running_statuses)
        else:
            running_statuses = []
            running = self._manager.get_running_count()
        limit = min(3, self._manager.max_concurrent_subagents)
        if running >= limit:
            return (
                f"My helpers are busy right now ({running}/{limit} busy). "
                "Let one finish before calling in another."
            )
        operator_running = any(
            item.get("execution_posture") == "inherit" for item in running_statuses
        )
        if posture == "inherit" and running:
            return "Operator needs an exclusive turn. Let the other specialists finish first."
        if posture == "read_only" and operator_running:
            return "Operator is acting right now. Let it finish before starting another specialist."

        composite = (
            f'You are acting as "{subagent["name"]}", one of the user\'s '
            "specialized assistants. Follow this role exactly:\n\n"
            f"{subagent['system_prompt']}\n\n---\n\n"
            f"The task:\n{task}"
        )
        ws = current_workspace_scope()
        # Subagents must not inherit the parent run's run-scoped approvals or
        # blanket "allow for this run": only the subagent's own calls may be
        # matched against run rules, and those rules never travel with it.
        permission_context = dict(
            request_ctx.metadata.get("permission_context", {})
            if isinstance(request_ctx.metadata, dict)
            else {}
        )
        for inherited_key in ("run_id", "approve_all_for_run", "plan_id", "plan_version"):
            permission_context.pop(inherited_key, None)
        return await self._manager.spawn(
            task=composite,
            runtime=request_ctx.runtime,
            label=str(subagent["name"]),
            origin_channel=request_ctx.channel,
            origin_chat_id=request_ctx.chat_id,
            session_key=session_key,
            origin_message_id=request_ctx.message_id,
            permission_context=permission_context,
            execution_posture=posture,
            workspace_scope=ws,
        )
