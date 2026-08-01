"""Contacts tool: the user's people.

Backed by the same ``people`` table as memory — find someone, remember
details about them, and suggest gifts from stored preferences.
"""

from __future__ import annotations

from typing import Any

from collie_core.tools.life_db import life_db
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["ContactsTool"]


def _person_lines(person: dict[str, Any]) -> str:
    lines = [f"{person['name']}"]
    for key, label in (
        ("relationship", "Relationship"),
        ("birthday", "Birthday"),
        ("allergies", "Allergies"),
        ("preferences", "Likes"),
        ("gift_ideas", "Gift ideas"),
        ("notes", "Notes"),
    ):
        if person.get(key):
            lines.append(f"  {label}: {person[key]}")
    return "\n".join(lines)


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["find", "list", "upsert", "gift_ideas"],
            "description": "find a person, list everyone, add/update details, "
                           "or pull gift ideas for someone.",
        },
        "name": {"type": "string", "description": "The person's name."},
        "relationship": {"type": "string", "description": "e.g. mom, partner, boss."},
        "birthday": {"type": "string", "description": "e.g. 'March 15' or '1990-03-15'."},
        "allergies": {"type": "string"},
        "preferences": {"type": "string", "description": "What they like."},
        "gift_ideas": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["action"],
})
class ContactsTool(Tool):
    """The user's people — find, remember, suggest gifts."""

    @property
    def name(self) -> str:
        return "contacts"

    @property
    def description(self) -> str:
        return (
            "Work with the user's people: look someone up, list everyone, "
            "save details (relationship, birthday, allergies, likes, gift "
            "ideas), or fetch gift ideas for a person."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return life_db() is not None

    @classmethod
    def create(cls, ctx: Any) -> "ContactsTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        db = life_db()
        if db is None:
            return self.error("Contacts aren't available right now.")

        action = str(kwargs.get("action") or "").strip().lower()
        name = str(kwargs.get("name") or "").strip()

        if action == "list":
            people = db.list_people()
            if not people:
                return (
                    "I don't know anyone yet! Tell me about your people and "
                    "I'll remember them."
                )
            return "\n\n".join(_person_lines(p) for p in people)

        if action == "find":
            if not name:
                return self.error("Who should I look up?")
            person = db.find_person(name)
            if person is None:
                return f"I don't know {name} yet — tell me about them!"
            return _person_lines(person)

        if action == "upsert":
            if not name:
                return self.error("Whose details am I saving?")
            fields = {
                key: str(kwargs[key]).strip()
                for key in ("relationship", "birthday", "allergies",
                            "preferences", "gift_ideas", "notes")
                if kwargs.get(key)
            }
            person = db.find_person(name)
            if person is None:
                db.add_person(name, **fields)
                return f"Got it — I'll remember {name}! 🦴"
            db.update_person(person["id"], **fields)
            return f"Updated what I know about {name}!"

        if action == "gift_ideas":
            if not name:
                return self.error("Gift ideas for whom?")
            person = db.find_person(name)
            if person is None:
                return f"I don't know {name} yet — tell me about them first!"
            bits = []
            if person.get("gift_ideas"):
                bits.append(f"Saved gift ideas: {person['gift_ideas']}")
            if person.get("preferences"):
                bits.append(f"They like: {person['preferences']}")
            if person.get("birthday"):
                bits.append(f"Birthday: {person['birthday']}")
            if not bits:
                return (
                    f"I don't have gift clues for {name} yet. What do they "
                    "like? I'll remember for next time."
                )
            return "\n".join(bits)

        return self.error(
            f"Not sure what to do with action '{action}'. Try find, list, "
            "upsert, or gift_ideas."
        )
