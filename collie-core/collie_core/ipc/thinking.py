"""Thinking states (spec §3.10, F082-F092).

Maps engine activity (tool calls, streaming, errors) to the friendly
phrases shown in the ThinkingBar and mirrored by the desktop pet.
"""

from __future__ import annotations

__all__ = ["PHRASES", "phrase_for_state", "thinking_state_for_tool"]

# state -> (phrase, pet animation)
PHRASES: dict[str, tuple[str, str]] = {
    "searching": ("Looking through reliable sources…", "walk"),
    "planning": ("Mapping out the next steps…", "working"),
    "fetching": ("Getting what I need…", "walk"),
    "generating": ("Writing your answer…", "working"),
    "processing": ("Working through it…", "working"),
    "summarizing": ("Sorting out the important bits…", "working"),
    "remembering": ("Saving that detail for later…", "working"),
    "recovering": ("Let me try that again…", "concerned"),
    "done": ("All done!", "happy"),
    "error": ("I hit a snag, but I'm still here.", "concerned"),
    "idle": ("Ready when you are.", "idle"),
    "startup": ("Just getting ready — one moment.", "working"),
    "pantry": ("Checking the pantry…", "walk"),
    "mapping": ("Looking over the map…", "walk"),
    "calendar": ("Checking your calendar…", "working"),
    "mail": ("Sorting through the mail…", "working"),
    "buddy": ("A specialist is working on it…", "working"),
}

_TOOL_STATES: dict[str, str] = {
    "web_search": "searching",
    "web_fetch": "fetching",
    "remember": "remembering",
    "message": "generating",
    "cron": "planning",
    "image_generation": "generating",
    "recipes": "pantry",
    "travel": "mapping",
    "calendar": "calendar",
    "email": "mail",
    "news": "searching",
    "weather": "fetching",
    "call_subagent": "buddy",
    "shopping_list": "planning",
    "budget": "summarizing",
    "health": "processing",
    "contacts": "remembering",
    "reminders": "planning",
}

PET_V2_STATE_BY_THINKING_STATE = {
    "searching": "review", "planning": "working", "fetching": "review",
    "generating": "working", "processing": "working", "summarizing": "working",
    "remembering": "working", "recovering": "working", "done": "completion",
    "error": "error", "idle": "idle", "startup": "working", "pantry": "review",
    "mapping": "review", "calendar": "working", "mail": "working", "buddy": "review",
}


def thinking_state_for_tool(tool_name: str) -> str:
    """Map a tool name to a thinking state key."""
    name = (tool_name or "").lower()
    if name in _TOOL_STATES:
        return _TOOL_STATES[name]
    if name.startswith("mcp_"):
        return "fetching"
    return "processing"


def phrase_for_state(state: str) -> dict[str, str]:
    """Return the ThinkingBar payload for a state key."""
    phrase, animation = PHRASES.get(state, PHRASES["processing"])
    return {
        "state": state,
        "phrase": phrase,
        "pet_animation": PET_V2_STATE_BY_THINKING_STATE.get(state, animation),
    }
