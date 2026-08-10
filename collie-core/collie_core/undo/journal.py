"""Shadow-copy undo journal for Collie's local file writes.

Before any write, ``LocalFilesTool`` snapshots the file's current bytes into a
per-conversation journal under the Collie home directory. A one-tap undo
restores those bytes (or removes a created file) — strictly scoped to files
Collie itself changed in that conversation.

Storage layout (``COLLIE_HOME`` or ``~/.collie``)::

    undo/<conversation_id>/manifest.json   — entries, newest first
    undo/<conversation_id>/<entry_id>.orig — pre-write snapshot bytes

Entries are append-only until undone; the journal sweeps entries older than
``_RETENTION_DAYS`` lazily on the next write.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collie_core.db import collie_home

_UNDO_DIR_NAME = "undo"
_MAX_ENTRY_BYTES = 1_000_000  # mirrors LocalFilesTool._MAX_FILE_BYTES
_RETENTION_DAYS = 7
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_conversation_id(conversation_id: str) -> str | None:
    """Return a filesystem-safe conversation id, or None when untrusted."""
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    if "/" in conversation_id or "\\" in conversation_id or conversation_id in {".", ".."}:
        return None
    return conversation_id


def _conversation_dir(conversation_id: str) -> Path:
    return collie_home() / _UNDO_DIR_NAME / _safe_conversation_id(conversation_id)  # type: ignore[arg-type]


def _manifest_path(conversation_dir: Path) -> Path:
    return conversation_dir / "manifest.json"


def _load_manifest(conversation_dir: Path) -> list[dict[str, Any]]:
    try:
        raw = _manifest_path(conversation_dir).read_text(encoding="utf-8")
        entries = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _save_manifest(conversation_dir: Path, entries: list[dict[str, Any]]) -> None:
    conversation_dir.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(conversation_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _sweep() -> None:
    """Drop entries older than the retention window (lazy, on write)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
    root = collie_home() / _UNDO_DIR_NAME
    if not root.is_dir():
        return
    for conversation_dir in root.iterdir():
        if not conversation_dir.is_dir():
            continue
        entries = _load_manifest(conversation_dir)
        kept: list[dict[str, Any]] = []
        for entry in entries:
            ts = entry.get("ts")
            try:
                timestamp = datetime.fromisoformat(str(ts))
            except (TypeError, ValueError):
                timestamp = None
            if timestamp is not None and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp is not None and timestamp < cutoff:
                shadow = conversation_dir / f"{entry.get('id')}.orig"
                shadow.unlink(missing_ok=True)
                continue
            kept.append(entry)
        if len(kept) != len(entries):
            _save_manifest(conversation_dir, kept)
        if not kept and not any(conversation_dir.iterdir()):
            shutil.rmtree(conversation_dir, ignore_errors=True)


def record_write(conversation_id: str, path: str | Path, operation: str) -> str | None:
    """Snapshot ``path`` before a write and return the entry id.

    Returns ``None`` when there is no trusted conversation id or the file is
    too large to snapshot (writes then simply have no undo entry). ``create``
    operations record a ``existed=False`` entry — undo removes the created
    file instead of restoring bytes.
    """
    safe_id = _safe_conversation_id(conversation_id)
    if safe_id is None:
        return None
    target = Path(path)
    if not isinstance(operation, str) or not operation:
        return None

    with _LOCK:
        _sweep()
        conversation_dir = _conversation_dir(safe_id)
        conversation_dir.mkdir(parents=True, exist_ok=True)
        existed = target.is_file()
        entry_id = uuid.uuid4().hex
        if existed:
            try:
                if target.stat().st_size > _MAX_ENTRY_BYTES:
                    return None
                shutil.copyfile(target, conversation_dir / f"{entry_id}.orig")
            except OSError:
                return None
        entry: dict[str, Any] = {
            "id": entry_id,
            "ts": _now_iso(),
            "path": str(target),
            "operation": operation,
            "existed": existed,
        }
        entries = _load_manifest(conversation_dir)
        entries.insert(0, entry)
        _save_manifest(conversation_dir, entries)
        return entry_id


def discard_write(conversation_id: str, entry_id: str) -> None:
    """Drop a journal entry that must not be undoable (e.g. the write failed).

    Removes the entry from the manifest and deletes its shadow copy. Safe to
    call for unknown entry ids — it is a no-op then.
    """
    safe_id = _safe_conversation_id(conversation_id)
    if safe_id is None or not isinstance(entry_id, str) or not entry_id:
        return
    conversation_dir = _conversation_dir(safe_id)
    with _LOCK:
        entries = _load_manifest(conversation_dir)
        kept = [entry for entry in entries if entry.get("id") != entry_id]
        if len(kept) == len(entries):
            return
        (conversation_dir / f"{entry_id}.orig").unlink(missing_ok=True)
        _save_manifest(conversation_dir, kept)
        if not kept and not any(conversation_dir.iterdir()):
            shutil.rmtree(conversation_dir, ignore_errors=True)


def pending_entries(conversation_id: str) -> list[dict[str, Any]]:
    """Return not-yet-undone journal entries for a conversation, newest first."""
    safe_id = _safe_conversation_id(conversation_id)
    if safe_id is None:
        return []
    return _load_manifest(_conversation_dir(safe_id))


def undo_entries(
    conversation_id: str, entry_ids: list[str] | None = None
) -> dict[str, list[dict[str, str]]]:
    """Restore journaled files for a conversation.

    ``entry_ids`` selects a subset (all entries when None). Restores the
    pre-write bytes for edited/overwritten files and removes created files.
    Consumed entries are dropped from the manifest. Returns ``undone`` and
    ``errors`` lists keyed by entry id.
    """
    safe_id = _safe_conversation_id(conversation_id)
    if safe_id is None:
        return {"undone": [], "errors": []}
    conversation_dir = _conversation_dir(safe_id)

    with _LOCK:
        entries = _load_manifest(conversation_dir)
        if entry_ids is not None:
            wanted = set(entry_ids)
            selected = [entry for entry in entries if entry.get("id") in wanted]
            remaining = [entry for entry in entries if entry.get("id") not in wanted]
        else:
            selected = entries
            remaining = []

        undone: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for entry in selected:
            entry_id = str(entry.get("id") or "")
            target = Path(str(entry.get("path") or ""))
            existed = bool(entry.get("existed"))
            shadow = conversation_dir / f"{entry_id}.orig"
            try:
                if existed:
                    if not shadow.is_file():
                        raise OSError("The safety copy is missing.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".collie-undo-{entry_id}{target.suffix}")
                    temporary.write_bytes(shadow.read_bytes())
                    os.replace(temporary, target)
                else:
                    target.unlink(missing_ok=True)
                shadow.unlink(missing_ok=True)
                undone.append({"id": entry_id, "path": str(target)})
            except OSError as exc:
                errors.append({"id": entry_id, "path": str(target), "message": str(exc)})
                remaining.append(entry)

        _save_manifest(conversation_dir, remaining)
        if not remaining and not any(conversation_dir.iterdir()):
            shutil.rmtree(conversation_dir, ignore_errors=True)
        return {"undone": undone, "errors": errors}
