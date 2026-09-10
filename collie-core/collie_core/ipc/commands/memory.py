"""Memory, people, dates and Dream commands.

Handlers for the ``memory`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection

from collie_core.db import collie_home

_PERSON_FIELDS = frozenset(
    {"relationship", "birthday", "allergies", "preferences", "gift_ideas", "notes"}
)


class MemoryCommands:
    """Memory, people, dates and Dream commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_get_profile(self, connection: ServerConnection, frame: dict) -> dict:
        return {"profile": self.db.all_profile()}

    async def _cmd_get_memory_journal(self, connection: ServerConnection, frame: dict) -> dict:
        """Recent memory mutations (Settings -> Memory -> Recent activity)."""
        limit = frame.get("limit")
        try:
            limit = int(limit) if limit is not None else 50
        except (TypeError, ValueError):
            limit = 50
        return {"entries": self.db.list_memory_journal(limit=max(1, min(limit, 500)))}

    async def _cmd_run_dream(self, connection: ServerConnection, frame: dict) -> dict:
        """Manual trigger: run one Dream consolidation pass now."""
        if self._dream_runner is None:
            raise ValueError("The memory review isn't available right now.")
        outcome = await self._dream_runner()
        return dict(outcome or {})

    async def _cmd_get_dream_history(self, connection: ServerConnection, frame: dict) -> dict:
        """Past Dream consolidations (memory_dream versions), newest first."""
        versions = await asyncio.to_thread(
            self.db.list_artifact_versions,
            artifact_type="memory_dream",
            limit=50,
        )
        return {"versions": versions}

    async def _cmd_get_dream_pending(self, connection: ServerConnection, frame: dict) -> dict:
        """Pending Dream proposal state (Settings → Memory self-review)."""
        from collie_core.memory.dream import get_dream_pending

        return await asyncio.to_thread(
            get_dream_pending,
            workspace=collie_home() / "workspace",
        )

    async def _cmd_apply_dream_proposal(self, connection: ServerConnection, frame: dict) -> dict:
        """Approve the pending Dream proposal: re-validate, write, version."""
        from collie_core.memory.dream import apply_dream_proposal
        from collie_core.versions import VersionStore, artifact_lock

        def _apply() -> dict:
            # Same-artifact serialization as the rollback path (memory_dream /
            # MEMORY.md). Lock held on the worker thread; no await inside the
            # lock, so a concurrent rollback can't clobber the write.
            with artifact_lock("memory_dream", "MEMORY.md"):
                return apply_dream_proposal(
                    workspace=collie_home() / "workspace",
                    version_store=VersionStore(self.db),
                )

        return await asyncio.to_thread(_apply)

    async def _cmd_dismiss_dream_proposal(self, connection: ServerConnection, frame: dict) -> dict:
        """Dismiss the pending Dream proposal without applying it."""
        from collie_core.memory.dream import dismiss_dream_proposal

        return await asyncio.to_thread(
            dismiss_dream_proposal,
            workspace=collie_home() / "workspace",
        )

    async def _cmd_set_profile_memory(self, connection: ServerConnection, frame: dict) -> dict:
        key = str(frame.get("key") or "").strip()
        value = str(frame.get("value") or "").strip()
        if not key:
            raise ValueError("A memory key is required")
        if value:
            self._memory().set(key, value)
        else:
            self._memory().delete(key)
        return {"profile": self._memory().all()}

    async def _cmd_delete_profile_memory(self, connection: ServerConnection, frame: dict) -> dict:
        key = str(frame.get("key") or "").strip()
        if not key:
            raise ValueError("A memory key is required")
        self._memory().delete(key)
        return {"profile": self._memory().all()}

    async def _cmd_add_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("A person's name is required")
        person = self._memory().add_person(
            name,
            **{
                key: value
                for key, value in fields.items()
                if key in _PERSON_FIELDS and value not in (None, "")
            },
        )
        return {"person": person}

    async def _cmd_update_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        person_id = str(frame.get("person_id") or "").strip()
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        if not person_id:
            raise ValueError("A person is required")
        self._memory().update_person(person_id, **fields)
        return {"person": self._memory().get_person(person_id)}

    async def _cmd_delete_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        person_id = str(frame.get("person_id") or "").strip()
        if not person_id:
            raise ValueError("A person is required")
        self._memory().delete_person(person_id)
        return {"deleted": True}

    async def _cmd_add_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date = str(frame.get("date") or "").strip()
        label = str(frame.get("label") or "").strip()
        if not date or not label:
            raise ValueError("A date and label are required")
        entry = self._memory().add_date(
            date,
            label,
            recurring=bool(frame.get("recurring")),
        )
        return {"date": entry}

    async def _cmd_update_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date_id = str(frame.get("date_id") or "").strip()
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        if not date_id:
            raise ValueError("A date is required")
        self._memory().update_date(date_id, **fields)
        return {"dates": self._memory().list_dates()}

    async def _cmd_delete_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date_id = str(frame.get("date_id") or "").strip()
        if not date_id:
            raise ValueError("A date is required")
        self._memory().delete_date(date_id)
        return {"deleted": True}

    async def _cmd_get_people(self, connection: ServerConnection, frame: dict) -> dict:
        return {"people": self.db.list_people()}

    async def _cmd_get_dates(self, connection: ServerConnection, frame: dict) -> dict:
        return {"dates": self.db.list_dates()}
