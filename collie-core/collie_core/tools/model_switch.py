"""Switch the active AI model from chat (``set_model`` tool).

The runtime binds one live switcher at boot (``CollieRuntime._switch_model``);
both the deterministic ``/model`` command and the agent's ``set_model`` tool
call through it. The change is persisted to settings and applied live to the
running loop, so the next turn uses the new model without a loop rebuild.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["SetModelTool", "bind_model_switcher", "model_switcher"]

ModelSwitcher = Callable[[str], Awaitable[dict[str, Any]]]

_switcher: ModelSwitcher | None = None


def bind_model_switcher(switcher: ModelSwitcher | None) -> None:
    """Bind the live runtime switcher (called once at boot)."""
    global _switcher
    _switcher = switcher


def model_switcher() -> ModelSwitcher | None:
    """Return the bound switcher, or ``None`` when no runtime is attached."""
    return _switcher


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": (
                    "Exact model ID to switch to, e.g. 'deepseek-v4-flash' or 'gpt-5.5'."
                ),
            },
        },
        "required": ["model"],
    }
)
class SetModelTool(Tool):
    """Switch the active AI model for the next messages."""

    @property
    def name(self) -> str:
        return "set_model"

    @property
    def description(self) -> str:
        return (
            "Switch the active AI model to a specific model ID (e.g. "
            "'deepseek-v4-flash' or 'gpt-5.5'). The change is saved and applies "
            "to the next messages. Use this when the user asks to change or "
            "switch the model."
        )

    @property
    def read_only(self) -> bool:
        return False

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        # Changing the active model mutates provider settings, so it stays
        # approval-gated (see mvp-agent-skill-runtime.md: provider/settings
        # operations never gain automatic or task-wide authority). The risk is
        # still LOCAL_WRITE and fully reversible, so no hard approval.
        return PermissionRequest(
            action="runtime.set_model",
            resource=str(params.get("model") or "model"),
            risk=Risk.LOCAL_WRITE,
            summary="Switch the active AI model",
            reversible=True,
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> SetModelTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("model") or "").strip()
        if not name:
            return self.error(
                "Which model should I switch to? Give me the exact model ID, "
                "e.g. 'deepseek-v4-flash'."
            )
        switcher = model_switcher()
        if switcher is None:
            return self.error("Model switching is not available in this session.")
        try:
            result = await switcher(name)
        except Exception as error:
            return self.error(f"I couldn't switch models: {error}")
        if not result.get("switched"):
            return self.error(str(result.get("error") or "I couldn't switch models."))
        model = str(result.get("model") or name)
        if result.get("applied"):
            return f"Done — the active model is now **{model}**. The next messages will use it."
        return f"Saved — I'll use **{model}** once a provider is connected."
