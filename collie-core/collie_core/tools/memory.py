"""Memory tool: lets Collie remember facts, people, and important dates.

The LLM calls this when the user shares something worth keeping
("I'm allergic to peanuts", "my mom's birthday is March 15"). Data lands in
the SQLite-backed ProfileStore, which regenerates MEMORY.md for context
injection on future turns.
"""

from __future__ import annotations

from typing import Any

from collie_core.memory.profile import PROFILE_KEYS, ProfileStore
from nanobot.agent.tools.base import Tool, tool_parameters

_profile_store: ProfileStore | None = None


def bind_profile_store(store: ProfileStore) -> None:
    """Called once by the Collie runtime before tools are loaded."""
    global _profile_store
    _profile_store = store


def _store() -> ProfileStore | None:
    return _profile_store


@tool_parameters({
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["fact", "person", "date", "forget_fact", "forget_person"],
            "description": (
                "What to remember: 'fact' about the user, 'person' they care about, "
                "'date' that matters, or forget_* to remove."
            ),
        },
        "key": {
            "type": "string",
            "description": (
                "For kind=fact/forget_fact: which fact. Known keys: "
                + ", ".join(PROFILE_KEYS)
                + ". Free-form keys are allowed."
            ),
        },
        "value": {
            "type": "string",
            "description": "For kind=fact: the fact itself, phrased briefly.",
        },
        "name": {
            "type": "string",
            "description": "For kind=person/forget_person/date: the person's name.",
        },
        "relationship": {"type": "string", "description": "e.g. mother, partner, boss"},
        "birthday": {"type": "string", "description": "MM-DD or YYYY-MM-DD"},
        "allergies": {"type": "string"},
        "preferences": {"type": "string", "description": "Likes/dislikes worth remembering"},
        "gift_ideas": {"type": "string"},
        "notes": {"type": "string"},
        "date": {"type": "string", "description": "For kind=date: MM-DD or YYYY-MM-DD"},
        "label": {"type": "string", "description": "For kind=date: what this date is"},
        "recurring": {"type": "boolean", "description": "For kind=date: repeats yearly"},
    },
    "required": ["kind"],
})
class RememberTool(Tool):
    """Persist long-term memory into the Collie profile store."""

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Remember something about the user for future conversations: a personal "
            "fact (dietary needs, schedule, location, goals), a person they care "
            "about (with birthday, preferences, gift ideas), or an important date. "
            "Use this whenever the user shares lasting personal information. "
            "Also supports forgetting with kind=forget_fact / forget_person."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _store() is not None

    @classmethod
    def create(cls, ctx: Any) -> "RememberTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        store = _store()
        if store is None:
            return self.error("Memory is not available right now.")
        kind = str(kwargs.get("kind") or "").strip()

        if kind == "fact":
            key = (kwargs.get("key") or "").strip().lower().replace(" ", "_")
            value = (kwargs.get("value") or "").strip()
            if not key or not value:
                return self.error("kind=fact needs both 'key' and 'value'.")
            store.set(key, value)
            return f"Remembered: {key} = {value}"

        if kind == "forget_fact":
            key = (kwargs.get("key") or "").strip().lower().replace(" ", "_")
            if not key:
                return self.error("kind=forget_fact needs 'key'.")
            store.delete(key)
            return f"Forgot: {key}"

        if kind == "person":
            name = (kwargs.get("name") or "").strip()
            if not name:
                return self.error("kind=person needs 'name'.")
            fields = {
                k: v.strip()
                for k in ("relationship", "birthday", "allergies",
                          "preferences", "gift_ideas", "notes")
                if isinstance((v := kwargs.get(k)), str) and v.strip()
            }
            person = store.add_person(name, **fields)
            extra = f" ({', '.join(f'{k}: {v}' for k, v in fields.items())})" if fields else ""
            birthday = fields.get("birthday")
            if birthday:
                label = f"{name}'s birthday"
                # Re-remembering a birthday must update the stored date row,
                # not stack a stale duplicate.
                existing = next(
                    (
                        d
                        for d in store.list_dates()
                        if d.get("person_id") == person["id"] and d.get("label") == label
                    ),
                    None,
                )
                if existing is None:
                    store.add_date(birthday, label, recurring=True,
                                   person_id=person["id"])
                else:
                    store.update_date(
                        existing["id"], date=birthday, label=label, recurring=True
                    )
            return f"Remembered {name}{extra}"

        if kind == "forget_person":
            name = (kwargs.get("name") or "").strip()
            if not name:
                return self.error("kind=forget_person needs 'name'.")
            person = store.find_person(name)
            if not person:
                return self.error(f"I don't have anyone named {name} in memory.")
            # Their important dates belong to them — clear them too.
            for date_row in store.list_dates():
                if date_row.get("person_id") == person["id"]:
                    store.delete_date(date_row["id"])
            store.delete_person(person["id"])
            return f"Forgot {name}."

        if kind == "date":
            date = (kwargs.get("date") or "").strip()
            label = (kwargs.get("label") or "").strip()
            if not date or not label:
                return self.error("kind=date needs 'date' and 'label'.")
            person_id = None
            name = (kwargs.get("name") or "").strip()
            if name:
                person = store.find_person(name)
                person_id = person["id"] if person else None
            store.add_date(date, label, recurring=bool(kwargs.get("recurring")),
                           person_id=person_id)
            return f"Remembered: {label} on {date}"

        return self.error(f"Unknown kind: {kind!r}")
