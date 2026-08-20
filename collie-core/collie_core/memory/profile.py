"""Collie structured profile memory.

``ProfileStore`` is the single write path for everything Collie remembers:
- profile facts (dietary, wake/sleep, location, medications, goals)
- people (name, relationship, birthday, allergies, preferences, gift ideas)
- important dates (birthdays, anniversaries, renewals)

Primary storage is SQLite (``collie.db``). After every change the store
regenerates ``~/.collie/workspace/MEMORY.md`` — a human-readable snapshot the
agent loop injects into LLM context (and the user can read in any editor).
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from collie_core.db import CollieDB

__all__ = ["ProfileStore", "PROFILE_KEYS"]

# MEMORY.md is injected into the LLM context as a bootstrap prompt, so any
# value that reaches it must be escaped — a value full of markdown or
# instructions must never become prompt directives.
_MEMORY_WRITE_LOCK = threading.Lock()


def _md_value(value: Any) -> str:
    """Flatten and escape a memory value for safe inclusion in MEMORY.md."""
    text = " ".join(str(value).split())
    text = text[:500]
    for char in ("\\", "`", "*", "_", "~", "[", "]", "#", ">", "|", "<"):
        text = text.replace(char, "\\" + char)
    return text


# Known profile keys shown in Settings → Memory with friendly labels.
PROFILE_KEYS: dict[str, str] = {
    "name": "Name",
    "dietary": "Food & allergies",
    "wake_time": "Wakes up",
    "sleep_time": "Goes to sleep",
    "location": "Lives in",
    "timezone": "Timezone",
    "medications": "Medications",
    "goals": "Current goals",
    "work": "Work",
    "family": "Family",
    "preferences": "Preferences",
    "notes": "Other notes",
}


class ProfileStore:
    """Structured profile memory backed by SQLite, mirrored to MEMORY.md."""

    def __init__(self, db: CollieDB, workspace: Path, version_store: Any = None) -> None:
        self.db = db
        self.workspace = workspace
        self.memory_file = workspace / "MEMORY.md"
        self._version_store = version_store

    # -- versioning (Gardener rollback rail) --------------------------------

    def _snapshot_memory(self) -> None:
        """Snapshot MEMORY.md before a regeneration; never breaks the write."""
        if self._version_store is None:
            return
        try:
            before = ""
            if self.memory_file.exists():
                before = self.memory_file.read_text(encoding="utf-8")
            after = self.memory_markdown()
            if before != after:
                self._version_store.snapshot(
                    "memory_profile", "MEMORY.md", before, after, source="user"
                )
        except Exception:
            # Versioning is a rollback rail — a failure must never block a
            # memory write the user asked for.
            logger.exception("memory version snapshot failed (swallowed)")

    # -- profile facts -------------------------------------------------------

    def _journal(self, kind: str, subject: str, action: str, value: Any = None) -> None:
        """Append a mutation record for the Settings -> Memory undo trail."""
        self.db.log_memory_journal(kind, subject, action, value)

    def get(self, key: str, default: Any = None) -> Any:
        return self.db.get_profile(key, default)

    def set(self, key: str, value: Any) -> None:
        existing = self.db.get_profile(key, None)
        self.db.set_profile(key, value)
        self._journal(
            "fact",
            key,
            "add" if existing is None or existing == "" else "update",
            value,
        )
        self._snapshot_memory()
        self.regenerate_memory_md()

    def delete(self, key: str) -> None:
        existing = self.db.get_profile(key, None)
        self.db.delete_profile(key)
        self._journal("fact", key, "delete", existing)
        self._snapshot_memory()
        self.regenerate_memory_md()

    def all(self) -> dict[str, Any]:
        return self.db.all_profile()

    # -- people ----------------------------------------------------------------

    def add_person(self, name: str, **fields: Any) -> dict[str, Any]:
        existing = self.db.find_person(name)
        if existing:
            self.db.update_person(existing["id"], **fields)
            person = self.db.get_person(existing["id"])
            self._journal("person", name, "update", fields)
        else:
            person = self.db.add_person(name, **fields)
            self._journal("person", name, "add", fields)
        self._snapshot_memory()
        self.regenerate_memory_md()
        return person  # type: ignore[return-value]

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return self.db.get_person(person_id)

    def find_person(self, name: str) -> dict[str, Any] | None:
        return self.db.find_person(name)

    def update_person(self, person_id: str, **fields: Any) -> None:
        person = self.db.get_person(person_id) or {}
        self.db.update_person(person_id, **fields)
        self._journal("person", person.get("name") or person_id, "update", fields)
        self._snapshot_memory()
        self.regenerate_memory_md()

    def delete_person(self, person_id: str) -> None:
        person = self.db.get_person(person_id) or {}
        self.db.delete_person(person_id)
        self._journal("person", person.get("name") or person_id, "delete", person)
        self._snapshot_memory()
        self.regenerate_memory_md()

    def list_people(self) -> list[dict[str, Any]]:
        return self.db.list_people()

    # -- important dates ----------------------------------------------------------

    def add_date(self, date: str, label: str, **kwargs: Any) -> dict[str, Any]:
        row = self.db.add_date(date, label, **kwargs)
        self._journal("date", label, "add", {"date": date, **kwargs})
        self._snapshot_memory()
        self.regenerate_memory_md()
        return row

    def list_dates(self) -> list[dict[str, Any]]:
        return self.db.list_dates()

    def update_date(self, date_id: str, **fields: Any) -> None:
        row = self.db.get_date(date_id) or {}
        self.db.update_date(date_id, **fields)
        self._journal("date", row.get("label") or date_id, "update", fields)
        self._snapshot_memory()
        self.regenerate_memory_md()

    def delete_date(self, date_id: str) -> None:
        row = self.db.get_date(date_id) or {}
        self.db.delete_date(date_id)
        self._journal("date", row.get("label") or date_id, "delete", row)
        self._snapshot_memory()
        self.regenerate_memory_md()

    # -- MEMORY.md generation --------------------------------------------------------

    def memory_markdown(self) -> str:
        """Render the full memory snapshot as markdown."""
        lines: list[str] = [
            "# What Collie Remembers",
            "",
            "_Auto-generated from Collie's memory. Edit in Settings → Memory._",
            "",
        ]

        profile = self.all()
        if profile:
            lines.append("## About you")
            for key, label in PROFILE_KEYS.items():
                if key in profile and profile[key] not in (None, ""):
                    lines.append(f"- **{label}**: {_md_value(profile[key])}")
            for key, value in profile.items():
                if key not in PROFILE_KEYS and value not in (None, ""):
                    lines.append(f"- **{key}**: {_md_value(value)}")
            lines.append("")

        people = self.list_people()
        if people:
            lines.append("## People you care about")
            for p in people:
                bits: list[str] = []
                if p.get("relationship"):
                    bits.append(_md_value(p["relationship"]))
                if p.get("birthday"):
                    bits.append(f"birthday {_md_value(p['birthday'])}")
                header = f"- **{_md_value(p['name'])}**" + (f" ({', '.join(bits)})" if bits else "")
                lines.append(header)
                for field, label in (
                    ("allergies", "Allergies"),
                    ("preferences", "Likes"),
                    ("gift_ideas", "Gift ideas"),
                    ("notes", "Notes"),
                ):
                    if p.get(field):
                        lines.append(f"  - {label}: {_md_value(p[field])}")
            lines.append("")

        dates = self.list_dates()
        if dates:
            lines.append("## Important dates")
            people_by_id = {p["id"]: p["name"] for p in people}
            for d in dates:
                who = people_by_id.get(d.get("person_id") or "")
                suffix = f" ({_md_value(who)})" if who else ""
                recur = " — every year" if d.get("recurring") else ""
                lines.append(
                    f"- **{_md_value(d['date'])}**: {_md_value(d['label'])}{suffix}{recur}"
                )
            lines.append("")

        if len(lines) == 4:
            lines.append("_Nothing here yet. Keep chatting and I'll remember what matters._")
            lines.append("")

        return "\n".join(lines)

    def regenerate_memory_md(self) -> None:
        # A unique tmp name + lock keeps concurrent writers (e.g. a worker
        # thread) from interleaving on the same file and replacing a partial
        # snapshot.
        with _MEMORY_WRITE_LOCK:
            self.workspace.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix="MEMORY.md.", suffix=".tmp", dir=self.workspace)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(self.memory_markdown())
                os.replace(tmp_name, self.memory_file)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(tmp_name)
