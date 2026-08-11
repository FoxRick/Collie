"""Tools that let Collie propose changes to AGENTS.md / VISION.md.

When the LLM notices something worth persisting about the user or its own
behavior, it calls `suggest_about_me` or `suggest_personality`. Results are
rendered as inline cards in the chat. The user approves (file is updated
and takes effect immediately) or dismisses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters

_workspace: Path | None = None


def bind_suggest_workspace(workspace: Path) -> None:
    global _workspace
    _workspace = workspace


SUGGEST_ABOUT_ME_DESCRIPTION = (
    "Propose an edit to About Me (AGENTS.md). Use this when you learn a new "
    "fact, preference, or routine about the user, or when the user asks you "
    "to update what you know about them. Write the full new content for the "
    "file — this will replace everything currently in AGENTS.md. The edit "
    "appears as a card the user approves or dismisses."
)

SUGGEST_PERSONALITY_DESCRIPTION = (
    "Propose an edit to your personality (VISION.md). Use this when the user "
    "asks you to adjust your tone, style, or behavior, or when you notice a "
    "change would help. Write the full new content for the file — this will "
    "replace everything currently in VISION.md. The edit appears as a card "
    "the user approves or dismisses."
)

COMMON_PARAMS = {
    "type": "object",
    "properties": {
        "suggestion": {
            "type": "string",
            "description": "The full new file content. Can be any length, with headings, formatting, bullet points — whatever makes sense.",
        },
        "reasoning": {
            "type": "string",
            "description": "Briefly explain the change (1 sentence). Shown on the approval card.",
        },
    },
    "required": ["suggestion", "reasoning"],
}


@tool_parameters(COMMON_PARAMS)
class SuggestAboutMeTool(Tool):
    """Propose an edit to AGENTS.md (About the User)."""

    @property
    def name(self) -> str:
        return "suggest_about_me"

    @property
    def description(self) -> str:
        return SUGGEST_ABOUT_ME_DESCRIPTION

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _workspace is not None

    @classmethod
    def create(cls, ctx: Any) -> SuggestAboutMeTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        suggestion = (kwargs.get("suggestion") or "").strip()
        reasoning = (kwargs.get("reasoning") or "").strip()
        if not suggestion or not reasoning:
            return self.error("Both 'suggestion' and 'reasoning' are required.")
        return json.dumps(
            {
                "card_type": "profile_suggestion",
                "file": "AGENTS.md",
                "label": "About Me",
                "suggestion": suggestion,
                "reasoning": reasoning,
            }
        )


@tool_parameters(COMMON_PARAMS)
class SuggestPersonalityTool(Tool):
    """Propose an edit to VISION.md (Collie's Personality)."""

    @property
    def name(self) -> str:
        return "suggest_personality"

    @property
    def description(self) -> str:
        return SUGGEST_PERSONALITY_DESCRIPTION

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _workspace is not None

    @classmethod
    def create(cls, ctx: Any) -> SuggestPersonalityTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        suggestion = (kwargs.get("suggestion") or "").strip()
        reasoning = (kwargs.get("reasoning") or "").strip()
        if not suggestion or not reasoning:
            return self.error("Both 'suggestion' and 'reasoning' are required.")
        return json.dumps(
            {
                "card_type": "profile_suggestion",
                "file": "VISION.md",
                "label": "Collie's Personality",
                "suggestion": suggestion,
                "reasoning": reasoning,
            }
        )
