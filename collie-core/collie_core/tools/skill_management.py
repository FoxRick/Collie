"""Reviewed agent access to the same skill manager as the Skills screen."""

from __future__ import annotations

from typing import Any
from weakref import WeakValueDictionary

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.skills import SkillManager
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

_managers: WeakValueDictionary[str, SkillManager] = WeakValueDictionary()


def bind_skill_manager(manager: SkillManager) -> None:
    _managers[str(manager.workspace.resolve())] = manager


def skill_manager(workspace: Any) -> SkillManager | None:
    from pathlib import Path

    return _managers.get(str(Path(workspace).resolve()))


@tool_parameters({"type": "object", "properties": {"name": {"type": "string"}}})
class InspectSkillsTool(Tool):
    def __init__(self, manager: SkillManager | None):
        self.manager = manager

    @property
    def name(self) -> str:
        return "inspect_skills"

    @property
    def description(self) -> str:
        return "Inspect saved skills, deleted names, pending changes, or one skill and its revision/history."

    @property
    def read_only(self) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> InspectSkillsTool:
        return cls(skill_manager(ctx.workspace))

    async def execute(self, **kwargs: Any) -> Any:
        if self.manager is None:
            return ToolResult.error("Skills are not available yet.")
        try:
            return (
                self.manager.detail(kwargs["name"])
                if kwargs.get("name")
                else self.manager.inventory()
            )
        except ValueError as exc:
            return ToolResult.error(str(exc))


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["edit", "delete", "enable", "disable", "reset", "restore", "version"],
            },
            "revision": {
                "type": "string",
                "description": "Current revision from inspect_skills. Required except restore.",
            },
            "content": {
                "type": "string",
                "description": "Full Markdown including the existing YAML header for edit.",
            },
            "version": {
                "type": "integer",
                "description": "Restore instructions from before this recorded edit.",
            },
        },
        "required": ["name", "action"],
    }
)
class ManageSkillTool(InspectSkillsTool):
    @property
    def name(self) -> str:
        return "manage_skill"

    @property
    def description(self) -> str:
        return (
            "Edit, enable, disable, delete, restore, reset a built-in override, or restore earlier "
            "instructions. Inspect first. Only make changes requested by the user; every change "
            "requires approval. Changes wait until all current tasks finish."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        return PermissionRequest(
            action="capability.skill.manage",
            resource=str(params.get("name") or "skill"),
            risk=Risk.LOCAL_WRITE,
            summary=f"{params.get('action', 'Change')} skill {params.get('name', '')}",
            reversible=True,
            hard_approval=True,
            suggested_scope=Scope.ONCE,
            redacted_parameters=dict(params),
        )

    async def execute(self, **kwargs: Any) -> Any:
        if self.manager is None:
            return ToolResult.error("Skills are not available yet.")
        try:
            return self.manager.change(**kwargs, source="agent")
        except (OSError, ValueError) as exc:
            return ToolResult.error(str(exc))
