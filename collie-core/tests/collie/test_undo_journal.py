"""Undo journal: shadow-copy snapshots + one-tap restore for local file writes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import collie_core.undo.journal as journal
from collie_core.db import collie_home


@pytest.fixture
def undo_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(home))
    return home


def test_record_and_undo_restores_original_bytes(undo_home: Path) -> None:
    target = undo_home / "notes.md"
    target.write_text("original", encoding="utf-8")

    entry = journal.record_write("conv-1", target, "overwrite")
    assert entry is not None
    target.write_text("changed by collie", encoding="utf-8")

    result = journal.undo_entries("conv-1")
    assert [item["id"] for item in result["undone"]] == [entry]
    assert result["errors"] == []
    assert target.read_text(encoding="utf-8") == "original"
    assert journal._load_manifest(journal._conversation_dir("conv-1")) == []


def test_create_undo_removes_created_file(undo_home: Path) -> None:
    target = undo_home / "new.md"
    entry = journal.record_write("conv-1", target, "create")
    assert entry is not None
    target.write_text("created by collie", encoding="utf-8")

    result = journal.undo_entries("conv-1")
    assert len(result["undone"]) == 1
    assert not target.exists()


def test_undo_subset_then_rest(undo_home: Path) -> None:
    first = undo_home / "a.txt"
    second = undo_home / "b.txt"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    journal.record_write("conv-1", first, "overwrite")
    entry_b = journal.record_write("conv-1", second, "overwrite")
    first.write_text("A2", encoding="utf-8")
    second.write_text("B2", encoding="utf-8")

    result = journal.undo_entries("conv-1", [str(entry_b)])
    assert [item["id"] for item in result["undone"]] == [entry_b]
    assert second.read_text(encoding="utf-8") == "B"
    assert first.read_text(encoding="utf-8") == "A2"

    journal.undo_entries("conv-1")
    assert first.read_text(encoding="utf-8") == "A"
    assert journal._load_manifest(journal._conversation_dir("conv-1")) == []


def test_unknown_entry_ids_are_ignored(undo_home: Path) -> None:
    target = undo_home / "a.txt"
    target.write_text("A", encoding="utf-8")
    journal.record_write("conv-1", target, "overwrite")
    target.write_text("A2", encoding="utf-8")

    result = journal.undo_entries("conv-1", ["does-not-exist"])
    assert result["undone"] == []
    assert target.read_text(encoding="utf-8") == "A2"


def test_missing_conversation_id_skips_journaling(undo_home: Path) -> None:
    target = undo_home / "x.txt"
    target.write_text("x", encoding="utf-8")
    assert journal.record_write("", target, "overwrite") is None
    assert journal.record_write("../evil", target, "overwrite") is None
    assert journal.record_write("..", target, "overwrite") is None
    assert journal._safe_conversation_id("") is None
    assert journal._safe_conversation_id("../evil") is None
    assert journal._safe_conversation_id("..") is None


def test_missing_shadow_reports_error_and_keeps_entry(undo_home: Path) -> None:
    target = undo_home / "m.txt"
    target.write_text("orig", encoding="utf-8")
    entry = journal.record_write("conv-1", target, "overwrite")
    (collie_home() / "undo" / "conv-1" / f"{entry}.orig").unlink()
    target.write_text("changed", encoding="utf-8")

    result = journal.undo_entries("conv-1")
    assert result["undone"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["id"] == entry
    # Entry survives so a later repair attempt stays possible.
    assert [item["id"] for item in journal._load_manifest(journal._conversation_dir("conv-1"))] == [
        entry
    ]


def test_retention_sweep_drops_expired_entries(undo_home: Path) -> None:
    target = undo_home / "old.txt"
    target.write_text("old", encoding="utf-8")
    old_entry = journal.record_write("conv-1", target, "overwrite")

    conversation_dir = collie_home() / "undo" / "conv-1"
    manifest_path = conversation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expired = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    manifest[0]["ts"] = expired
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    keeper = undo_home / "keep.txt"
    keeper.write_text("keep", encoding="utf-8")
    keeper_entry = journal.record_write("conv-1", keeper, "overwrite")

    remaining = journal._load_manifest(journal._conversation_dir("conv-1"))
    assert [item["id"] for item in remaining] == [keeper_entry]
    assert not (conversation_dir / f"{old_entry}.orig").exists()


def test_undo_restores_paths_with_missing_parent(undo_home: Path) -> None:
    target = undo_home / "sub" / "nested.md"
    target.parent.mkdir()
    target.write_text("orig", encoding="utf-8")
    entry = journal.record_write("conv-1", target, "overwrite")
    target.write_text("changed", encoding="utf-8")
    target.unlink()
    target.parent.rmdir()

    result = journal.undo_entries("conv-1")
    assert [item["id"] for item in result["undone"]] == [entry]
    assert target.read_text(encoding="utf-8") == "orig"


def test_discard_write_removes_entry_and_shadow(undo_home: Path) -> None:
    target = undo_home / "notes.md"
    target.write_text("original", encoding="utf-8")
    entry = journal.record_write("conv-1", target, "overwrite")

    journal.discard_write("conv-1", str(entry))
    assert journal._load_manifest(journal._conversation_dir("conv-1")) == []
    conversation_dir = undo_home / "undo" / "conv-1"
    assert not (conversation_dir / f"{entry}.orig").exists()


def test_discard_write_keeps_other_entries(undo_home: Path) -> None:
    first = undo_home / "a.md"
    first.write_text("a", encoding="utf-8")
    first_entry = journal.record_write("conv-1", first, "overwrite")
    second = undo_home / "b.md"
    second.write_text("b", encoding="utf-8")
    second_entry = journal.record_write("conv-1", second, "overwrite")

    journal.discard_write("conv-1", str(first_entry))
    remaining = journal._load_manifest(journal._conversation_dir("conv-1"))
    assert [item["id"] for item in remaining] == [second_entry]


def test_discard_write_unknown_entry_is_noop(undo_home: Path) -> None:
    target = undo_home / "notes.md"
    target.write_text("original", encoding="utf-8")
    entry = journal.record_write("conv-1", target, "overwrite")

    journal.discard_write("conv-1", "does-not-exist")
    assert [item["id"] for item in journal._load_manifest(journal._conversation_dir("conv-1"))] == [
        entry
    ]
