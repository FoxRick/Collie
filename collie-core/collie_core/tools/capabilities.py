"""Safe workspace capability creation for Collie agents and skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.subagents.loader import get_subagent_loader
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_INSTRUCTIONS = 16_000


def _clean_skill_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not name or len(name) > 64 or not _SKILL_NAME.fullmatch(name):
        raise ValueError("Skill names must use lowercase letters, numbers, and single hyphens.")
    return name


def create_workspace_skill(
    workspace: Path,
    *,
    name: str,
    description: str,
    instructions: str,
) -> dict[str, Any]:
    """Validate and atomically install one Markdown-only workspace skill."""
    clean_name = _clean_skill_name(name)
    clean_description = " ".join(description.strip().split())
    clean_instructions = instructions.strip()
    if not clean_description:
        raise ValueError("A skill needs a short description.")
    if len(clean_description) > 1_024:
        raise ValueError("That skill description is too long.")
    if not clean_instructions:
        raise ValueError("A skill needs instructions.")
    if len(clean_instructions) > _MAX_INSTRUCTIONS:
        raise ValueError("Those skill instructions are too long.")

    skills_root = (workspace / "skills").resolve()
    target_dir = (skills_root / clean_name).resolve()
    target_dir.relative_to(skills_root)
    target = target_dir / "SKILL.md"
    if target.exists():
        raise ValueError(
            f"A skill called '{clean_name}' already exists. Edit it from Skills instead."
        )

    metadata = yaml.safe_dump(
        {"name": clean_name, "description": clean_description},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    rendered = f"---\n{metadata}\n---\n\n{clean_instructions}\n"
    target_dir.mkdir(parents=True, exist_ok=False)
    temporary = target_dir / "SKILL.md.tmp"
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(target)

    # Re-read through the production parser before reporting success.
    loader = SkillsLoader(workspace)
    parsed = loader.get_skill_metadata(clean_name)
    if not parsed or parsed.get("name") != clean_name:
        target.unlink(missing_ok=True)
        target_dir.rmdir()
        raise ValueError("The generated skill did not pass validation.")
    return {
        "name": clean_name,
        "description": clean_description,
        "source": "workspace",
        "path": str(target),
    }


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short name for the new specialist."},
            "description": {
                "type": "string",
                "description": "One-sentence description of when this specialist helps.",
            },
            "instructions": {
                "type": "string",
                "description": (
                    "Complete system instructions defining the specialist's role, workflow, "
                    "quality bar, and boundaries."
                ),
            },
            "execution_posture": {
                "type": "string",
                "enum": ["read_only", "inherit"],
                "description": "read_only for research/review; inherit for approved actions.",
            },
        },
        "required": ["name", "description", "instructions"],
    }
)
class CreateSubagentTool(Tool):
    """Create a specialist only after the user explicitly asks and approves."""

    @property
    def name(self) -> str:
        return "create_subagent"

    @property
    def description(self) -> str:
        return (
            "Create a reusable specialist agent. Use only when the user explicitly asks "
            "to create, make, or save an agent. Never create one merely because it might "
            "be useful. The user sees and approves the proposed definition before it is saved."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        instructions = str(params.get("instructions") or "")
        return PermissionRequest(
            action="capability.agent.create",
            resource=str(params.get("name") or "new agent"),
            risk=Risk.LOCAL_WRITE,
            summary=f"Create agent “{params.get('name') or 'Untitled'}”",
            reversible=True,
            suggested_scope=Scope.ONCE,
            hard_approval=True,
            redacted_parameters={
                "name": str(params.get("name") or ""),
                "description": str(params.get("description") or ""),
                "execution_posture": str(params.get("execution_posture") or "read_only"),
                "instructions_preview": instructions[:1_200],
            },
        )

    async def execute(self, **kwargs: Any) -> Any:
        loader = get_subagent_loader()
        if loader is None:
            return ToolResult.error("Agents are not available yet.")
        posture = str(kwargs.get("execution_posture") or "read_only")
        if posture not in {"read_only", "inherit"}:
            posture = "read_only"
        try:
            row = loader.create(
                str(kwargs.get("name") or ""),
                description=str(kwargs.get("description") or ""),
                system_prompt=str(kwargs.get("instructions") or ""),
                execution_posture=posture,
            )
        except ValueError as exc:
            return ToolResult.error(str(exc))
        return (
            f"Created agent “{row['name']}”. It is available in /agents and can be "
            f"started with /agent {row['name']} <task>."
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Lowercase hyphenated skill name, such as weekly-review.",
            },
            "description": {
                "type": "string",
                "description": "Specific description of when Collie should load this skill.",
            },
            "instructions": {
                "type": "string",
                "description": "Complete reusable Markdown workflow instructions.",
            },
        },
        "required": ["name", "description", "instructions"],
    }
)
class CreateSkillTool(Tool):
    """Create a Markdown-only skill inside Collie's workspace."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "create_skill"

    @property
    def description(self) -> str:
        return (
            "Create a reusable Markdown skill. Use only when the user explicitly asks "
            "to create, teach, or save a skill/workflow. Never create one proactively. "
            "The user sees and approves the proposed definition before it is installed."
        )

    @property
    def read_only(self) -> bool:
        return False

    @classmethod
    def create(cls, ctx: Any) -> CreateSkillTool:
        return cls(Path(str(ctx.workspace)))

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        instructions = str(params.get("instructions") or "")
        return PermissionRequest(
            action="capability.skill.create",
            resource=str(params.get("name") or "new skill"),
            risk=Risk.LOCAL_WRITE,
            summary=f"Create skill “{params.get('name') or 'untitled'}”",
            reversible=True,
            suggested_scope=Scope.ONCE,
            hard_approval=True,
            redacted_parameters={
                "name": str(params.get("name") or ""),
                "description": str(params.get("description") or ""),
                "instructions_preview": instructions[:1_200],
            },
        )

    async def execute(self, **kwargs: Any) -> Any:
        try:
            skill = create_workspace_skill(
                self._workspace,
                name=str(kwargs.get("name") or ""),
                description=str(kwargs.get("description") or ""),
                instructions=str(kwargs.get("instructions") or ""),
            )
        except (OSError, ValueError) as exc:
            return ToolResult.error(str(exc))
        return (
            f"Created skill “{skill['name']}”. It is available in /skills and can be "
            f"used with /skill {skill['name']} <request>."
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact skill name to load."},
        },
        "required": ["name"],
    }
)
class LoadSkillTool(Tool):
    """Load one discovered skill without exposing general filesystem access."""

    def __init__(self, workspace: Path) -> None:
        self._loader = SkillsLoader(workspace)

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "Load the full instructions for one available skill by exact name. "
            "Use when a request matches a skill or the user explicitly invokes /skill."
        )

    @property
    def read_only(self) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> LoadSkillTool:
        return cls(Path(str(ctx.workspace)))

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("name") or "").strip()
        content = self._loader.load_skill(name)
        if content is None:
            available = [entry["name"] for entry in self._loader.list_skills()]
            return ToolResult.error(
                f"Skill '{name}' was not found. Available skills: {', '.join(available) or 'none'}."
            )
        return content
