"""Versioned artifact storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``artifact_versions``.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ArtifactsDomain:
    """Versioned artifact storage. Mixed into :class:`CollieDB` by composition, never a domain
    to domain call."""

    def snapshot_artifact(
        self,
        artifact_type: str,
        artifact_key: str,
        before_text: str,
        after_text: str,
        diff_text: str,
        evidence: Any = None,
        source: str = "user",
    ) -> dict[str, Any]:
        """Append a version row for an artifact edit; returns the new row."""
        with self._write_immediate() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM artifact_versions "
                "WHERE artifact_type = ? AND artifact_key = ?",
                (artifact_type, artifact_key),
            ).fetchone()
            version = int(row["v"] or 0) + 1
            version_id = new_id()
            conn.execute(
                "INSERT INTO artifact_versions (id, artifact_type, artifact_key, "
                "version, before_text, after_text, diff_text, evidence_json, "
                "source, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'applied', ?)",
                (
                    version_id,
                    artifact_type,
                    artifact_key,
                    version,
                    before_text,
                    after_text,
                    diff_text,
                    json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
                    source,
                    utc_now(),
                ),
            )
            return {
                "id": version_id,
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version": version,
                "status": "applied",
            }

    def latest_artifact_version(self, artifact_type: str, artifact_key: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) AS v FROM artifact_versions "
                "WHERE artifact_type = ? AND artifact_key = ?",
                (artifact_type, artifact_key),
            ).fetchone()
            return int(row["v"] or 0) if row else 0

    def list_artifact_versions(
        self,
        artifact_type: str | None = None,
        artifact_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List artifact versions, most recent first, with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if artifact_type:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        if artifact_key:
            clauses.append("artifact_key = ?")
            params.append(artifact_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._rows(
            f"SELECT * FROM artifact_versions {where} "
            "ORDER BY created_at DESC, version DESC LIMIT ?",
            tuple(params),
        )

    def get_artifact_version(self, version_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM artifact_versions WHERE id = ?", (version_id,))

    def mark_artifact_rolled_back(self, version_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE artifact_versions SET status = 'rolled_back' WHERE id = ?",
                (version_id,),
            )
