"""Versioned artifact store — snapshot, diff, and roll back Collie artifacts.

Every user-visible artifact edit (subagent files, ``VISION.md`` /
``AGENTS.md`` / ``MEMORY.md``, dream consolidations, Gardener applies) is
snapshotted into the ``artifact_versions`` table with a before/after text
pair and a unified diff. The one-action rollback restores the prior text
**only when the current content still matches the snapshotted ``after``
text** — a newer owner edit is never clobbered (the no-clobber guard).

The store is deliberately text-generic: reading the current artifact text
and writing the restored text is the caller's job (each artifact type has
its own on-disk location and re-sync rules). This keeps ``VersionStore``
independent of the workspace layout.
"""

from __future__ import annotations

import difflib
import threading
from typing import Any

from collie_core.db import CollieDB

__all__ = ["VersionStore", "VersionConflictError", "artifact_lock"]


# -- per-artifact lock registry ------------------------------------------------
# Rollback and apply (Gardener/Dream) each do a read-modify-write on the same
# artifact file from different threads (apply runs via ``asyncio.to_thread``).
# This shared lock serializes them so a rollback can't clobber a concurrent
# apply (or vice versa). Keys are ``(artifact_type, artifact_key)``; the
# registry lives here and is used directly by the apply/rollback handlers in
# ``ipc/server.py``.

_LOCKS_GUARD = threading.Lock()
_ARTIFACT_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def artifact_lock(artifact_type: str, key: str) -> threading.Lock:
    """Return the shared per-artifact lock for an artifact type + key."""
    with _LOCKS_GUARD:
        return _ARTIFACT_LOCKS.setdefault((artifact_type, key), threading.Lock())


class VersionConflictError(Exception):
    """Raised when a rollback would clobber newer content than the version."""


def make_diff(before: str, after: str, key: str) -> str:
    """Build a unified diff between two texts (empty when identical)."""
    return "".join(
        difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"{key} (before)",
            tofile=f"{key} (after)",
        )
    )


def _normalize_text(text: str) -> str:
    """Normalize an artifact text for equality comparison.

    Mirrors the gardener/runner apply normalization (a trailing ``\\n`` is
    always appended) plus CRLF handling: ``\\r\\n`` -> ``\\n`` and trailing
    newlines stripped. This prevents a whitespace-only difference (e.g. the
    stored ``after_text`` vs. a manual edit that adds a trailing blank line)
    from permanently refusing a rollback.
    """
    return (text or "").replace("\r\n", "\n").rstrip("\n")


class VersionStore:
    """Snapshot artifact edits and roll them back without clobbering."""

    def __init__(self, db: CollieDB) -> None:
        self.db = db

    # -- snapshot ------------------------------------------------------------

    def snapshot(
        self,
        artifact_type: str,
        key: str,
        current_text: str,
        new_text: str,
        *,
        evidence: Any = None,
        source: str = "user",
    ) -> str | None:
        """Record a version for an artifact edit; returns the version id.

        Returns ``None`` when the texts are identical (nothing changed).
        ``evidence`` is an optional JSON-serializable dict (e.g. Gardener
        trigger evidence: run ids, tool stats).
        """
        before = current_text or ""
        after = new_text or ""
        if before == after:
            return None
        row = self.db.snapshot_artifact(
            artifact_type,
            key,
            before,
            after,
            make_diff(before, after, key),
            evidence=evidence,
            source=source,
        )
        return str(row["id"])

    def latest_version_id(self, artifact_type: str, key: str) -> str | None:
        """Return the newest applied version id for an artifact, if any."""
        rows = self.db.list_artifact_versions(
            artifact_type=artifact_type, artifact_key=key, limit=1
        )
        if not rows:
            return None
        for row in rows:
            if row.get("status") == "applied":
                return str(row["id"])
        return None

    # -- rollback ------------------------------------------------------------

    def rollback(
        self,
        artifact_type: str,
        key: str,
        *,
        to_version: int | None = None,
        current_text: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a rollback to ``before_text`` of the target version.

        * ``to_version`` — the version to undo (defaults to the newest
          applied version of the artifact).
        * ``current_text`` — the artifact's current content, read by the
          caller before calling. When it differs from the target version's
          ``after_text`` the rollback is refused (never clobber newer
          owner edits). Trailing-newline / CRLF differences are normalized
          away so a whitespace-only change doesn't permanently block undo.

        The caller writes the returned ``restored_text`` to the artifact (and
        re-syncs as needed) and then calls :meth:`mark_rolled_back` with the
        returned ``version_id`` — never *before* the write succeeds.
        """
        rows = self.db.list_artifact_versions(
            artifact_type=artifact_type, artifact_key=key, limit=200
        )
        applied = [r for r in rows if r.get("status") == "applied"]
        if not applied:
            raise VersionConflictError("There's nothing to undo for this item yet.")
        target: dict[str, Any] | None = None
        if to_version is not None:
            target = next(
                (r for r in applied if int(r.get("version") or 0) == to_version),
                None,
            )
        else:
            target = max(applied, key=lambda r: int(r.get("version") or 0))
        if target is None:
            raise VersionConflictError(f"Version {to_version} was already undone or never existed.")

        after_text = target.get("after_text") or ""
        current = current_text if current_text is not None else ""
        if _normalize_text(current) != _normalize_text(after_text):
            raise VersionConflictError(
                "That edit has already been changed since — I won't overwrite "
                "the newer version. Undo the most recent change first."
            )

        restored = target.get("before_text") or ""
        return {
            "version_id": str(target["id"]),
            "artifact_type": artifact_type,
            "artifact_key": key,
            "version": int(target.get("version") or 0),
            "restored_text": restored,
            "status": "rolled_back",
        }

    def mark_rolled_back(self, version_id: str) -> None:
        """Mark a version row ``rolled_back`` (call after writing the file).

        Callers must only invoke this *after* the restored text has been
        written successfully — marking before the write would leave the row
        in ``rolled_back`` while the artifact still holds newer content if
        the write raises.
        """
        self.db.mark_artifact_rolled_back(str(version_id))
