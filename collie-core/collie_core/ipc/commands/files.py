"""Workspace file and version commands.

Handlers for the ``files`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection


class FileCommands:
    """Workspace file and version commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_read_file(self, connection: ServerConnection, frame: dict) -> dict:
        file_path = self._resolve_workspace_path(str(frame.get("path") or ""))
        if not file_path.exists():
            return {"content": ""}
        return {"content": file_path.read_text(encoding="utf-8")}

    async def _cmd_undo_file_changes(self, connection: ServerConnection, frame: dict) -> dict:
        """One-tap undo: restore files Collie changed in a conversation.

        Restores the pre-write bytes (or removes created files) from the
        shadow journal. Scope is strictly the entries recorded for this
        conversation; ``entry_ids`` narrows to a subset (all when omitted).
        """
        from collie_core.undo.journal import undo_entries

        conversation_id = str(frame.get("conversation_id") or "")
        raw_ids = frame.get("entry_ids")
        if raw_ids is None:
            # Field omitted: undo every journaled entry for this conversation.
            entry_ids = None
        elif isinstance(raw_ids, list):
            # Explicit list (even empty) selects exactly those entries — an
            # empty list is a deliberate no-op, never a blanket undo.
            entry_ids = [str(entry_id) for entry_id in raw_ids if str(entry_id)]
        else:
            entry_ids = None
        return undo_entries(conversation_id, entry_ids)

    async def _cmd_write_file(self, connection: ServerConnection, frame: dict) -> dict:
        file_path = self._resolve_workspace_path(str(frame.get("path") or ""))
        content = str(frame.get("content") or "")
        artifact = self._classify_workspace_artifact(file_path)
        if artifact is not None:
            # Serialize with any concurrent rollback/apply on this artifact.
            from collie_core.versions import VersionStore, artifact_lock, make_diff

            with artifact_lock(artifact[0], artifact[1]):
                file_path.parent.mkdir(parents=True, exist_ok=True)
                before = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
                version_id: str | None = None
                diff_text: str | None = None
                if before != content:
                    version_id = VersionStore(self.db).snapshot(
                        artifact[0], artifact[1], before, content, source="user"
                    )
                    if version_id is not None:
                        diff_text = make_diff(before, content, artifact[1])
                file_path.write_text(content, encoding="utf-8")
                return {
                    "saved": True,
                    "version_id": version_id,
                    "diff_text": diff_text,
                }
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return {
            "saved": True,
            "version_id": None,
            "diff_text": None,
        }

    async def _cmd_list_versions(self, connection: ServerConnection, frame: dict) -> dict:
        """List artifact versions (most recent first) — read-only rollback rail."""
        artifact_type = str(frame.get("artifact_type") or "") or None
        artifact_key = str(frame.get("artifact_key") or "") or None
        limit = frame.get("limit")
        try:
            limit = int(limit) if limit is not None else 100
        except (TypeError, ValueError):
            limit = 100
        versions = await asyncio.to_thread(
            self.db.list_artifact_versions,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
            limit=max(1, min(limit, 500)),
        )
        return {"versions": versions}

    async def _cmd_rollback_artifact(self, connection: ServerConnection, frame: dict) -> dict:
        """Undo one artifact version (no-clobber guarded) and re-sync state."""
        from collie_core.versions import VersionConflictError, VersionStore, artifact_lock

        version_id = str(frame.get("version_id") or "")
        row = self.db.get_artifact_version(version_id)
        if row is None:
            raise ValueError("I can't find that change — it may have been cleaned up.")
        artifact_type = str(row["artifact_type"])
        key = str(row["artifact_key"])
        target = self._artifact_target(artifact_type, key)
        # Serialize with any concurrent Gardener/Dream apply on the same
        # artifact. Read -> validate -> write -> mark must be atomic against a
        # concurrent apply, otherwise a newer edit could be clobbered between
        # the read and the write. The mark is covered too so a failed write
        # leaves the row applied (artifact unchanged) — never desynced.
        with artifact_lock(artifact_type, key):
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            try:
                result = VersionStore(self.db).rollback(
                    artifact_type,
                    key,
                    to_version=int(row["version"]),
                    current_text=current,
                )
            except VersionConflictError as exc:
                raise ValueError(str(exc)) from exc
            restored = result["restored_text"]
            if restored:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(restored, encoding="utf-8")
            elif target.exists():
                target.unlink()
            VersionStore(self.db).mark_rolled_back(result["version_id"])
        # A subagent rollback also restores the database row (or renames it
        # back): the loader reconciles disk -> DB. Done outside the lock — it
        # only reads the file we just wrote and reconciles the DB mirror.
        if artifact_type == "subagent" and self._subagent_loader is not None:
            await asyncio.to_thread(self._subagent_loader.sync)
        return {
            "rolled_back": True,
            "version_id": result["version_id"],
            "artifact_type": artifact_type,
            "artifact_key": key,
            "version": result["version"],
        }
