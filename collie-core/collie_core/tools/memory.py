"""Memory tool: lets Collie remember facts, people, and important dates.

The LLM calls this when the user shares something worth keeping
("I'm allergic to peanuts", "my mom's birthday is March 15"). Data lands in
the SQLite-backed ProfileStore, which regenerates MEMORY.md for context
injection on future turns.
"""

from __future__ import annotations

import re
from typing import Any

from collie_core.memory.names import name_candidate
from collie_core.memory.profile import PROFILE_KEYS, ProfileStore
from collie_core.permissions.models import PermissionRequest, Risk
from nanobot.agent.tools.base import Tool, tool_parameters

_PERSON_FIELDS = frozenset(
    {"relationship", "birthday", "allergies", "preferences", "gift_ideas", "notes"}
)

# When a sentence slips into the name slot, route it to a more honest key.
_PREFERENCE_MARKERS = re.compile(
    r"\b(prefer|prefers|preferred|like|likes|dislike|dislikes|"
    r"enjoy|enjoys|hate|hates|wants?|needs?)\b",
    re.IGNORECASE,
)

_profile_store: ProfileStore | None = None


def bind_profile_store(store: ProfileStore) -> None:
    """Called once by the Collie runtime before tools are loaded."""
    global _profile_store
    _profile_store = store


def _store() -> ProfileStore | None:
    return _profile_store


@tool_parameters(
    {
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
                    "For kind=fact/forget_fact: which fact. Use 'name' ONLY for the "
                    "user's actual name (a short label, e.g. 'Rick'). Likes, dislikes, "
                    "and communication style belong under 'preferences'. Known keys: "
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
    }
)
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

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        """Additive memory writes are approval-free; forgets and rewrites ask.

        New facts, people, and dates are reversible local writes and land
        silently (they are journaled for undo in Settings). Forgetting, or
        overwriting a memory that already holds a different value, stays an
        explicit approval — that is a rewrite in disguise, not an addition.
        """
        kind = str(params.get("kind") or "").strip()
        store = _store()

        if kind in ("forget_fact", "forget_person"):
            return PermissionRequest(
                action="memory.forget",
                resource="profile",
                risk=Risk.LOCAL_WRITE,
                summary="Forget a memory",
                reversible=False,
                approval_free=False,
            )

        if kind == "fact":
            key = (params.get("key") or "").strip().lower().replace(" ", "_")
            value = str(params.get("value") or "").strip()
            existing = store.get(key) if store is not None else None
            conflict = existing not in (None, "") and str(existing) != value
        elif kind == "person":
            name = (params.get("name") or "").strip()
            existing = store.find_person(name) if store is not None else None
            conflict = self._person_conflicts(existing, params)
        elif kind == "date":
            label = (params.get("label") or "").strip()
            date = str(params.get("date") or "").strip()
            existing = None
            if store is not None:
                existing = next((d for d in store.list_dates() if d.get("label") == label), None)
            conflict = existing is not None and str(existing.get("date") or "") != date
        else:
            # Unknown kinds fail closed: ask rather than auto-approve.
            conflict = True

        return PermissionRequest(
            action="memory.write",
            resource="profile",
            risk=Risk.LOCAL_WRITE,
            summary="Remember something new"
            if not conflict
            else "Update a memory you already have",
            reversible=True,
            approval_free=not conflict,
        )

    @staticmethod
    def _person_conflicts(existing: dict[str, Any] | None, params: dict[str, Any]) -> bool:
        """A person write conflicts when it changes an existing stored field."""
        if existing is None:
            return False
        for field in _PERSON_FIELDS:
            incoming = (params.get(field) or "").strip()
            stored = str(existing.get(field) or "").strip()
            if incoming and stored and incoming != stored:
                return True
        return False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _store() is not None

    @classmethod
    def create(cls, ctx: Any) -> RememberTool:
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
            if key == "name":
                # A sentence in the name slot must never become the user's
                # Name. Keep the data under a more honest key instead.
                candidate = name_candidate(value)
                if candidate is None:
                    target = (
                        "preferences" if _PREFERENCE_MARKERS.search(value) else "notes"
                    )
                    store.set(target, value)
                    return (
                        f"That looked like a preference, not a name — I saved it under "
                        f"{target} instead. (Say 'my name is …' to set your name.)"
                    )
                value = candidate
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
                for k in (
                    "relationship",
                    "birthday",
                    "allergies",
                    "preferences",
                    "gift_ideas",
                    "notes",
                )
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
                    store.add_date(birthday, label, recurring=True, person_id=person["id"])
                else:
                    store.update_date(existing["id"], date=birthday, label=label, recurring=True)
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
            store.add_date(
                date, label, recurring=bool(kwargs.get("recurring")), person_id=person_id
            )
            return f"Remembered: {label} on {date}"

        return self.error(f"Unknown kind: {kind!r}")
