"""Collaboration transport commands.

Handlers for the ``collaboration`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import os
from pathlib import Path

from websockets.asyncio.server import ServerConnection

from collie_core.ipc.command_support import _bounded_list_limit


class CollaborationCommands:
    """Collaboration transport commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_collaboration_status(self, connection: ServerConnection, frame: dict) -> dict:
        if self._collaboration_store is None:
            return {"available": False}
        session_id = str(frame.get("session_id") or "")
        bound = bool(self._collaboration_store.is_bound)
        return {
            "available": True,
            "signed_in": bound,
            "cursor": self._collaboration_store.cursor(session_id) if bound and session_id else 0,
            "pending": self._collaboration_store.pending(
                _bounded_list_limit(frame.get("limit"), default=100) or 100
            )
            if bound
            else [],
            "archives": self._archive_manager.list() if self._archive_manager and bound else [],
        }

    async def _cmd_collaboration_bind_identity(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        expected = str(os.environ.get("COLLIE_IDENTITY_BIND_TOKEN") or "")
        supplied = str(frame.get("bind_token") or "")
        if (
            not expected
            or not supplied
            or not __import__("hmac").compare_digest(expected, supplied)
        ):
            raise ValueError("Shared-session identity binding was not authorized.")
        if self._collaboration_identity_binder is None:
            raise ValueError("Shared sessions are unavailable.")
        account_id = str(frame.get("account_id") or "")
        device_id = str(frame.get("device_id") or "")
        if bool(account_id) != bool(device_id):
            raise ValueError("Account and device identity must be bound together.")
        self._collaboration_identity_binder(account_id, device_id)
        return {"bound": bool(account_id)}

    async def _cmd_collaboration_queue_event(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None:
            raise ValueError("Shared sessions are unavailable.")
        return self._collaboration_store.queue_event(
            str(frame.get("session_id") or ""), dict(frame.get("event") or {})
        )

    async def _cmd_collaboration_cache_bootstrap(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None:
            raise ValueError("Shared sessions are unavailable.")
        snapshot = frame.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("A collaboration bootstrap snapshot is required.")
        self._collaboration_store.cache_bootstrap(snapshot)
        return {"cached": True}

    async def _cmd_collaboration_get_cached_bootstrap(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None:
            raise ValueError("Shared sessions are unavailable.")
        return {"snapshot": self._collaboration_store.cached_bootstrap()}

    async def _cmd_collaboration_set_routine_delivery(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None or not self._collaboration_store.is_bound:
            raise ValueError("Sign in before sharing a routine result.")
        routine_id = str(frame.get("routine_id") or "")
        delivery = frame.get("shared_delivery")
        if delivery is None:
            return {"routine": self.db.set_routine_shared_delivery(routine_id, None)}
        if not isinstance(delivery, dict):
            raise ValueError("Shared routine delivery must be an object.")
        session_id = str(delivery.get("session_id") or "")
        revision = int(delivery.get("audience_revision") or 0)
        creator = str(delivery.get("creator_account_id") or "")
        if not session_id or revision < 1 or creator != self._collaboration_store.account_id:
            raise ValueError("Shared routine delivery identity or audience is invalid.")
        normalized = {
            "session_id": session_id,
            "audience_revision": revision,
            "creator_account_id": creator,
        }
        return {"routine": self.db.set_routine_shared_delivery(routine_id, normalized)}

    async def _cmd_collaboration_mark_routine_delivery(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        event_id = str(frame.get("event_id") or "")
        status = str(frame.get("status") or "")
        if status not in {"acknowledged", "rejected", "pending"}:
            raise ValueError("Invalid routine delivery status.")
        error = str(frame.get("error") or "")[:300] or None
        self.db.record_routine_shared_delivery(
            routine_id, status=status, event_id=event_id or None, error=error
        )
        if event_id and self._collaboration_store is not None and status != "pending":
            self._collaboration_store.settle_outbox(event_id, status, error)
        return {"recorded": True}

    async def _cmd_collaboration_apply_page(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None:
            raise ValueError("Shared sessions are unavailable.")
        session_id = str(frame.get("session_id") or "")
        cursor = self._collaboration_store.apply_page(
            session_id,
            list(frame.get("events") or []),
            next_cursor=int(frame.get("next_cursor") or 0),
        )
        return {"cursor": cursor}

    async def _cmd_collaboration_list_messages(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_store is None:
            raise ValueError("Shared sessions are unavailable.")
        session_id = str(frame.get("session_id") or "")
        through = frame.get("through")
        return {
            "messages": self._collaboration_store.materialized_messages(
                session_id, through=int(through) if through is not None else None
            )
        }

    async def _cmd_collaboration_write_archive(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._archive_manager is None:
            raise ValueError("Local archives are unavailable.")
        attachments = {
            str(item["file_id"]): Path(str(item["path"]))
            for item in list(frame.get("attachments") or [])
        }
        return self._archive_manager.write(
            str(frame.get("manifest_json") or ""),
            str(frame.get("digest") or ""),
            attachments=attachments,
        )

    async def _cmd_collaboration_export_archive(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._archive_manager is None:
            raise ValueError("Local archives are unavailable.")
        output = self._archive_manager.export(
            Path(str(frame.get("archive_path") or "")), Path(str(frame.get("destination") or ""))
        )
        return {"path": str(output)}

    async def _cmd_collaboration_import_archive(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._archive_manager is None:
            raise ValueError("Local archives are unavailable.")
        return self._archive_manager.import_bundle(Path(str(frame.get("source") or "")))

    async def _cmd_collaboration_run_shared(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._shared_chat_runner is None:
            raise ValueError("Shared execution is unavailable.")
        return await self._shared_chat_runner(
            content=str(frame.get("content") or ""),
            claim=dict(frame.get("claim") or {}),
            published_history=list(frame.get("published_history") or []),
            mode=str(frame.get("mode") or "shared"),
        )

    async def _cmd_collaboration_control_run(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._collaboration_run_controller is None:
            raise ValueError("Shared execution is unavailable.")
        return await self._collaboration_run_controller(
            run_id=str(frame.get("run_id") or ""),
            action=str(frame.get("action") or ""),
            lease_token=str(frame.get("lease_token") or ""),
            lease_expires_at=str(frame.get("lease_expires_at") or ""),
        )
