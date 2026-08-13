"""Versioned artifact store tests (Gardener Foundations PR 2).

Covers the ``artifact_versions`` schema (V14), the ``VersionStore`` API
(snapshot/diff/rollback + no-clobber guard), the write-path wiring
(subagent loader, ProfileStore, workspace file writes) and the IPC
surface (list_versions / rollback_artifact).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.memory.profile import ProfileStore
from collie_core.subagents.loader import SubagentLoader
from collie_core.versions import VersionConflictError, VersionStore


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    d.close()


class _FakeConn:
    """Minimal stand-in for a ServerConnection (direct handler calls)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


# -- schema -------------------------------------------------------------------


def test_v14_creates_artifact_versions_on_fresh_db(db: CollieDB) -> None:
    assert db.schema_version == 14
    rows = db._rows(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_versions'"
    )
    assert len(rows) == 1
    columns = {row["name"] for row in db._rows("PRAGMA table_info(artifact_versions)")}
    assert {
        "id",
        "artifact_type",
        "artifact_key",
        "version",
        "before_text",
        "after_text",
        "diff_text",
        "evidence_json",
        "source",
        "status",
        "created_at",
    } <= columns


def test_v13_db_upgrades_to_v14_preserving_data(tmp_path: Path) -> None:
    import collie_core.db as db_mod

    path = tmp_path / "v13.db"
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:13]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (13)")
    conn.commit()
    conn.close()

    upgraded = CollieDB(path)
    try:
        assert upgraded.schema_version == 14
        assert upgraded._rows(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_versions'"
        )
    finally:
        upgraded.close()


# -- DB methods ----------------------------------------------------------------


def test_snapshot_versions_are_monotonic_per_artifact(db: CollieDB) -> None:
    r1 = db.snapshot_artifact("subagent", "researcher.md", "", "v1", "- +v1")
    r2 = db.snapshot_artifact("subagent", "researcher.md", "v1", "v2", "- +v2")
    r3 = db.snapshot_artifact("subagent", "analyst.md", "", "a1", "- +a1")
    assert (r1["artifact_type"], r1["artifact_key"], r1["version"]) == (
        "subagent",
        "researcher.md",
        1,
    )
    assert (r2["artifact_type"], r2["artifact_key"], r2["version"]) == (
        "subagent",
        "researcher.md",
        2,
    )
    assert (r3["artifact_type"], r3["artifact_key"], r3["version"]) == ("subagent", "analyst.md", 1)
    assert db.latest_artifact_version("subagent", "researcher.md") == 2

    rows = db.list_artifact_versions(artifact_type="subagent", artifact_key="researcher.md")
    assert [r["version"] for r in rows] == [2, 1]
    assert rows[0]["status"] == "applied"
    assert rows[0]["source"] == "user"

    got = db.get_artifact_version(r2["id"])
    assert got is not None and got["after_text"] == "v2"
    db.mark_artifact_rolled_back(r2["id"])
    assert db.get_artifact_version(r2["id"])["status"] == "rolled_back"


def test_snapshot_unique_constraint_per_version(db: CollieDB) -> None:
    """(type, key, version) is unique — enforced at the DB level."""
    r1 = db.snapshot_artifact("vision", "VISION.md", "", "a", "- +a")
    # The API auto-increments, so a collision requires a direct insert.
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO artifact_versions (id, artifact_type, artifact_key, "
            "version, before_text, after_text, diff_text, source, status, "
            "created_at) VALUES (?, ?, ?, ?, '', '', '', 'user', 'applied', ?)",
            ("dup", "vision", "VISION.md", r1["version"], "2026-01-01"),
        )
    # Clear the failed transaction so later statements succeed.
    db._conn.rollback()
    # Same type, same version, different key is fine; different type same key fine.
    db.snapshot_artifact("vision", "AGENTS.md", "", "a", "- +a")


# -- VersionStore --------------------------------------------------------------


def test_snapshot_returns_id_and_diff(db: CollieDB) -> None:
    store = VersionStore(db)
    vid = store.snapshot("agents", "AGENTS.md", "old", "new", source="user")
    assert vid is not None
    row = db.get_artifact_version(vid)
    assert row is not None
    assert row["before_text"] == "old"
    assert row["after_text"] == "new"
    assert "-old" in row["diff_text"] and "+new" in row["diff_text"]

    assert store.snapshot("agents", "AGENTS.md", "new", "new") is None


def test_rollback_restores_before_text(db: CollieDB) -> None:
    store = VersionStore(db)
    store.snapshot("subagent", "researcher.md", "", "v1")
    store.snapshot("subagent", "researcher.md", "v1", "v2")

    result = store.rollback("subagent", "researcher.md", current_text="v2")
    assert result["version"] == 2
    assert result["restored_text"] == "v1"
    assert db.get_artifact_version(result["version_id"])["status"] == "rolled_back"

    # Next rollback targets the remaining applied version.
    result = store.rollback("subagent", "researcher.md", current_text="v1")
    assert result["version"] == 1
    assert result["restored_text"] == ""

    with pytest.raises(VersionConflictError):
        store.rollback("subagent", "researcher.md", current_text="anything")


def test_rollback_no_clobber_guard(db: CollieDB) -> None:
    store = VersionStore(db)
    store.snapshot("agents", "AGENTS.md", "old", "new")
    # The file was edited again after the snapshot -> refuse.
    with pytest.raises(VersionConflictError):
        store.rollback("agents", "AGENTS.md", current_text="newer edit")
    # Still applied (nothing was marked).
    assert (
        db.get_artifact_version(store.latest_version_id("agents", "AGENTS.md") or "")["status"]
        == "applied"
    )


def test_rollback_targets_specific_version(db: CollieDB) -> None:
    store = VersionStore(db)
    store.snapshot("agents", "AGENTS.md", "", "a")
    store.snapshot("agents", "AGENTS.md", "a", "b")
    store.snapshot("agents", "AGENTS.md", "b", "c")
    # Undoing an older version while a newer edit sits on top refuses: current
    # text ("c") no longer matches the older version's after_text ("b").
    with pytest.raises(VersionConflictError):
        store.rollback("agents", "AGENTS.md", to_version=2, current_text="c")
    # Undo the latest first, then the older one becomes undoable.
    result = store.rollback("agents", "AGENTS.md", current_text="c")
    assert result["version"] == 3
    assert result["restored_text"] == "b"
    result = store.rollback("agents", "AGENTS.md", to_version=2, current_text="b")
    assert result["version"] == 2
    assert result["restored_text"] == "a"
    # Undoing an already-rolled-back version is refused.
    with pytest.raises(VersionConflictError):
        store.rollback("agents", "AGENTS.md", to_version=2, current_text="a")


# -- subagent loader wiring ------------------------------------------------------


def test_subagent_edit_versions_and_rollback_restores_file_and_db(
    tmp_path: Path, db: CollieDB
) -> None:
    workspace = tmp_path / "workspace"
    loader = SubagentLoader(workspace, db)
    created = loader.create("Researcher", "researches", "You research things.")
    filename = created["filename"]
    versions = db.list_artifact_versions(artifact_type="subagent", artifact_key=filename)
    assert len(versions) == 1
    assert versions[0]["before_text"] == ""
    assert "You research things." in versions[0]["after_text"]

    loader.update(created["id"], system_prompt="You research very carefully.")
    versions = db.list_artifact_versions(artifact_type="subagent", artifact_key=filename)
    assert len(versions) == 2
    assert versions[0]["before_text"].strip().endswith("You research things.")

    # Roll back the update: file restored, DB row in sync via sync().
    target = workspace / "subagents" / filename
    current = target.read_text(encoding="utf-8")
    result = VersionStore(db).rollback("subagent", filename, current_text=current)
    target.write_text(result["restored_text"], encoding="utf-8")
    loader.sync()
    row = loader.find("Researcher")
    assert row is not None
    assert "You research things." in row["system_prompt"]

    # Delete snapshots an empty after; rollback restores the file + row.
    loader.delete(created["id"])
    assert not target.exists()
    versions = db.list_artifact_versions(artifact_type="subagent", artifact_key=filename)
    assert versions[0]["after_text"] == ""
    result = VersionStore(db).rollback("subagent", filename, current_text="")
    target.write_text(result["restored_text"], encoding="utf-8")
    loader.sync()
    assert target.exists()
    assert loader.find("Researcher") is not None


def test_subagent_sync_is_not_versioned(tmp_path: Path, db: CollieDB) -> None:
    """Reconciliation (sync) must not create version rows — only user edits."""
    workspace = tmp_path / "workspace"
    loader = SubagentLoader(workspace, db)
    loader.create("Analyst", "analyzes", "You analyze.")
    before = len(db.list_artifact_versions(artifact_type="subagent"))
    loader.sync()
    assert len(db.list_artifact_versions(artifact_type="subagent")) == before


# -- ProfileStore wiring ---------------------------------------------------------


def test_profile_edit_versions_memory_md(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    store = ProfileStore(db, workspace, version_store=VersionStore(db))
    store.regenerate_memory_md()
    store.set("dietary", "vegan")
    versions = db.list_artifact_versions(artifact_type="memory_profile", artifact_key="MEMORY.md")
    assert len(versions) == 1
    assert "vegan" in versions[0]["after_text"]
    # The before snapshot is the prior generated file (the empty-state text).
    assert "Nothing here yet" in versions[0]["before_text"]

    # Rollback restores the prior MEMORY.md text.
    result = VersionStore(db).rollback(
        "memory_profile", "MEMORY.md", current_text=versions[0]["after_text"]
    )
    (workspace / "MEMORY.md").write_text(result["restored_text"], encoding="utf-8")
    assert "vegan" not in (workspace / "MEMORY.md").read_text(encoding="utf-8")


def test_profile_edit_without_version_store_still_works(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    store = ProfileStore(db, workspace)
    store.set("location", "Berlin")
    assert "Berlin" in (workspace / "MEMORY.md").read_text(encoding="utf-8")


# -- IPC surface -----------------------------------------------------------------


def _make_server(tmp_path: Path, db: CollieDB) -> CollieIPCServer:
    workspace = tmp_path / "workspace"
    loader = SubagentLoader(workspace, db)
    srv = CollieIPCServer(db, port=0, subagent_loader=loader)
    srv._test_loader = loader  # type: ignore[attr-defined]
    return srv


async def test_ipc_list_versions_and_rollback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path))
    db = CollieDB(tmp_path / "collie.db")
    srv = _make_server(tmp_path, db)
    try:
        loader: SubagentLoader = srv._test_loader  # type: ignore[attr-defined]
        created = loader.create("Researcher", "researches", "You research things.")
        filename = created["filename"]

        conn = _FakeConn()
        result = await srv._cmd_list_versions(conn, {"artifact_type": "subagent"})
        versions = result["versions"]
        assert len(versions) == 1
        assert versions[0]["artifact_key"] == filename

        # Update the subagent (v2), then roll it back through the IPC path.
        loader.update(created["id"], system_prompt="You research very carefully.")
        target = tmp_path / "workspace" / "subagents" / filename
        current = target.read_text(encoding="utf-8")
        assert "very carefully" in current
        rolled = await srv._cmd_rollback_artifact(
            conn,
            {
                "version_id": db.list_artifact_versions(
                    artifact_type="subagent", artifact_key=filename, limit=1
                )[0]["id"]
            },
        )
        assert rolled["rolled_back"] is True
        assert "very carefully" not in target.read_text(encoding="utf-8")
        assert "You research things." in target.read_text(encoding="utf-8")
        # DB row is back in sync with the restored file.
        assert loader.find("Researcher") is not None

        # A newer owner edit must be refused (no-clobber guard).
        create_version = db.list_artifact_versions(
            artifact_type="subagent", artifact_key=filename, limit=2
        )[1]["id"]
        target.write_text("newer hand edit", encoding="utf-8")
        with pytest.raises(ValueError, match="newer"):
            await srv._cmd_rollback_artifact(conn, {"version_id": create_version})

        # Restore the file to the snapshotted state; undoing the create now
        # removes the artifact again.
        create_row = db.get_artifact_version(create_version)
        target.write_text(create_row["after_text"], encoding="utf-8")
        rolled = await srv._cmd_rollback_artifact(conn, {"version_id": create_version})
        assert rolled["rolled_back"] is True
        assert not target.exists()
        assert loader.find("Researcher") is None

        with pytest.raises(ValueError):
            await srv._cmd_rollback_artifact(conn, {"version_id": "missing-id"})
    finally:
        db.close()


async def test_ipc_write_file_versions_suggest_apply_path(tmp_path: Path, monkeypatch) -> None:
    """The suggest-card apply path (_cmd_write_file) versions VISION/AGENTS."""
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path))
    db = CollieDB(tmp_path / "collie.db")
    srv = _make_server(tmp_path, db)
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENTS.md").write_text("old about me", encoding="utf-8")
        conn = _FakeConn()
        result = await srv._cmd_write_file(conn, {"path": "AGENTS.md", "content": "new about me"})
        assert result["saved"] is True
        assert result["version_id"] is not None
        assert "-old about me" in (result["diff_text"] or "")
        rows = db.list_artifact_versions(artifact_type="agents", artifact_key="AGENTS.md")
        assert len(rows) == 1

        # Writing the same content again creates no version.
        result = await srv._cmd_write_file(conn, {"path": "AGENTS.md", "content": "new about me"})
        assert result["version_id"] is None
        assert len(db.list_artifact_versions(artifact_type="agents")) == 1

        # Non-artifact files are written but not versioned.
        result = await srv._cmd_write_file(conn, {"path": "notes.txt", "content": "hello"})
        assert result["saved"] is True and result["version_id"] is None
    finally:
        db.close()
