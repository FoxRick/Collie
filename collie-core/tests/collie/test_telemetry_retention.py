"""Retention sweep for the append-only telemetry and journal tables."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from collie_core.db import CollieDB


@pytest.fixture
def db(tmp_path: Path) -> Iterator[CollieDB]:
    database = CollieDB(tmp_path / "collie.db")
    try:
        yield database
    finally:
        from collie_core.telemetry.recorder import RunRecorder

        recorder = RunRecorder.active_for(database)
        if recorder is not None:
            recorder.shutdown()
        database.close()


def _stamp(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _record_turn(db: CollieDB, turn_id: str, *, days_ago: int, with_tool: bool = False) -> None:
    db.record_turn_event(
        turn_id=turn_id, turn_kind="chat", status="ok", started_at=_stamp(days_ago)
    )
    if with_tool:
        db.record_tool_event(
            tool_id=f"{turn_id}-tool",
            turn_id=turn_id,
            tool_name="search",
            started_at=_stamp(days_ago),
        )


def test_sweep_drops_events_past_the_window_and_keeps_the_rest(db: CollieDB) -> None:
    _record_turn(db, "old", days_ago=120, with_tool=True)
    _record_turn(db, "recent", days_ago=10, with_tool=True)

    removed = db.sweep_telemetry()

    assert removed["turn_events"] == 1
    assert removed["tool_events"] == 1
    assert [row["id"] for row in db.list_turn_events()] == ["recent"]
    assert [row["id"] for row in db._rows("SELECT id FROM tool_events")] == ["recent-tool"]


def test_sweep_drops_journal_entries_past_the_window(db: CollieDB) -> None:
    with db._write() as conn:
        conn.execute(
            "INSERT INTO memory_journal (kind, subject, action, value, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("fact", "stale", "add", None, _stamp(120)),
        )
    db.log_memory_journal("fact", "fresh", "add", "value")

    removed = db.sweep_telemetry()

    assert removed["memory_journal"] == 1
    assert [row["subject"] for row in db.list_memory_journal()] == ["fresh"]


def test_sweep_keeps_only_the_newest_versions_per_artifact(db: CollieDB) -> None:
    for index in range(25):
        db.snapshot_artifact("subagent", "helper.md", f"before {index}", f"after {index}", "")
    db.snapshot_artifact("vision", "VISION.md", "before", "after", "")

    removed = db.sweep_telemetry()

    assert removed["artifact_versions"] == 5
    kept = db._rows(
        "SELECT artifact_key, version FROM artifact_versions ORDER BY artifact_key, version"
    )
    helper_versions = [row["version"] for row in kept if row["artifact_key"] == "helper.md"]
    assert helper_versions == list(range(6, 26))
    assert [row["artifact_key"] for row in kept].count("VISION.md") == 1


def test_sweep_leaves_recent_data_untouched(db: CollieDB) -> None:
    _record_turn(db, "recent", days_ago=1, with_tool=True)
    db.log_memory_journal("fact", "fresh", "add", "value")

    removed = db.sweep_telemetry()

    assert removed == {
        "tool_events": 0,
        "turn_events": 0,
        "memory_journal": 0,
        "artifact_versions": 0,
    }
    assert db.list_turn_events()
    assert db.list_memory_journal()


def test_sweep_leaves_conversations_intact_when_it_drops_their_turns(db: CollieDB) -> None:
    conversation = db.create_conversation(title="Stay put")
    db.record_turn_event(
        turn_id="old-turn",
        conversation_id=str(conversation["id"]),
        turn_kind="chat",
        status="ok",
        started_at=_stamp(200),
    )

    db.sweep_telemetry()

    assert db.get_conversation(str(conversation["id"])) is not None
    assert db.list_turn_events() == []


@pytest.mark.parametrize("overrides", [{"retention_days": 0}, {"max_artifact_versions": 0}])
def test_sweep_rejects_non_positive_bounds(db: CollieDB, overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        db.sweep_telemetry(**overrides)
