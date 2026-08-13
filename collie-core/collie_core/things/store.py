"""Durable index of the user's "Your things".

One JSON file per conversation under the instance ``things`` directory
(``~/.collie/things/<conversation_id>.json``), mirroring the sidecar-metadata
pattern already used for generated media in ``nanobot.utils.artifacts`` — no
SQLite, no new tables. The renderer hydrates the panel from this index on
reconnect; live updates arrive as ``ArtifactEvent`` bus messages.

Records are plain dicts so the IPC layer can broadcast them verbatim:

``{"id", "title", "kind", "path", "size_bytes", "created_at", "status", "version"}``
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_runtime_subdir

__all__ = ["ThingStore"]

# Conversation ids are Collie-generated keys ("conv_…"); the regex keeps a
# conversation from ever smuggling path separators into the index filename.
_SAFE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

ThingRecord = dict[str, Any]


class ThingStore:
    """JSON-per-conversation thing index with atomic writes."""

    def __init__(self, root: Path | None = None) -> None:
        self._dir = Path(root) if root is not None else get_runtime_subdir("things")
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The directory holding the per-conversation index files."""
        return self._dir

    def register(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
        title: str,
        kind: str,
        path: str,
        size_bytes: int,
        created_at: float,
        status: str = "new",
        version: int = 1,
    ) -> ThingRecord:
        """Insert or update one thing record for a conversation.

        A re-registered ``artifact_id`` replaces the existing record in place
        (the slot keeps its position); a new id is prepended so ``list``
        returns newest first. The file is written atomically (tmp + rename).
        """
        record: ThingRecord = {
            "id": artifact_id,
            "title": title,
            "kind": kind,
            "path": path,
            "size_bytes": size_bytes,
            "created_at": created_at,
            "status": status,
            "version": version,
        }
        records = self._load(conversation_id)
        for index, existing in enumerate(records):
            if existing.get("id") == artifact_id:
                records[index] = record
                break
        else:
            records.insert(0, record)
        self._save(conversation_id, records)
        return record

    def list(self, conversation_id: str) -> list[ThingRecord]:
        """All things for a conversation, newest first (empty for unknown)."""
        return self._load(conversation_id)

    def get(self, conversation_id: str, thing_id: str) -> ThingRecord | None:
        """One registered thing by id, or ``None``. Safe for unknown ids."""
        for record in self._load(conversation_id):
            if record.get("id") == thing_id:
                return record
        return None

    def delete(self, conversation_id: str) -> bool:
        """Drop the whole thing index for a conversation.

        Metadata-only: the deliverables themselves stay on disk (they are
        the user's files — Collie never deletes them), and deleting a
        conversation must not make them unreachable by path. Returns True
        when an index file existed and was removed.
        """
        path = self._index_path(conversation_id)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    # -- internals ---------------------------------------------------------

    def _index_path(self, conversation_id: str) -> Path:
        if not _SAFE_CONVERSATION_ID.match(conversation_id or ""):
            raise ValueError(f"conversation_id {conversation_id!r} is not safe for an index file")
        return self._dir / f"{conversation_id}.json"

    def _load(self, conversation_id: str) -> list[ThingRecord]:
        path = self._index_path(conversation_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records = payload.get("things", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            return []
        return [r for r in records if isinstance(r, dict)]

    def _save(self, conversation_id: str, records: list[ThingRecord]) -> None:
        path = self._index_path(conversation_id)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"things": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
