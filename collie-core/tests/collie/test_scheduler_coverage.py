"""Coverage backfill: scheduler tick paths, seeding, and recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from collie_core.automations.scheduler import (
    BUILTIN_AUTOMATIONS,
    AutomationScheduler,
    seed_builtin_automations,
)
from collie_core.db import CollieDB


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    database = CollieDB(tmp_path / "sched.db")
    yield database
    database.close()


def test_seed_builtins_once_and_not_resurrected(db: CollieDB) -> None:
    seed_builtin_automations(db)
    assert {a["id"] for a in db.list_automations()} == {a["id"] for a in BUILTIN_AUTOMATIONS}
    # A deletion must NOT be resurrected by a later seed.
    db.delete_automation("collie-morning-briefing")
    seed_builtin_automations(db)
    assert "collie-morning-briefing" not in {a["id"] for a in db.list_automations()}
    # clear_all resets the flag: fresh start re-seeds.
    db.clear_all()
    seed_builtin_automations(db)
    assert "collie-morning-briefing" in {a["id"] for a in db.list_automations()}


def test_toggle_automation_clears_next_run_on_enable(db: CollieDB) -> None:
    auto = db.add_automation(
        "Routine",
        schedule="09:00",
        action_type="briefing",
        action_config={"prompt": "hi"},
        enabled=False,
        next_run_at="2020-01-01T00:00:00+00:00",
    )
    db.toggle_automation(auto["id"], True)
    row = db.get_automation(auto["id"])
    assert row["next_run_at"] is None
    assert row["routine_status"] == "enabled"


async def test_tick_fires_due_automation_and_backfills(db: CollieDB) -> None:
    fired: list[str] = []

    async def runner(auto):
        fired.append(auto["id"])

    auto = db.add_automation(
        "Due now",
        schedule="00:00",
        action_type="briefing",
        action_config={"prompt": "go"},
        enabled=True,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    )
    scheduler = AutomationScheduler(db, runner=runner, poll_seconds=60)
    await scheduler._tick()
    # Claimed runs execute in a fire-and-forget task.
    await asyncio.sleep(0.1)
    assert fired == [auto["id"]]


async def test_tick_marks_needs_attention_for_broken_plan(db: CollieDB) -> None:
    fired: list[str] = []

    async def runner(auto):
        fired.append(auto["id"])

    auto = db.add_automation(
        "Broken plan",
        schedule="09:00",
        action_type="approved_plan",
        enabled=True,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    )
    scheduler = AutomationScheduler(db, runner=runner)
    await scheduler._tick()
    assert fired == []
    assert db.get_automation(auto["id"])["routine_status"] == "needs_attention"


async def test_tick_backfills_empty_next_run_at(db: CollieDB) -> None:
    auto = db.add_automation(
        "Fresh",
        schedule="09:00",
        action_type="briefing",
        action_config={"prompt": "go"},
        enabled=True,
    )
    scheduler = AutomationScheduler(db, runner=lambda auto: None)
    await scheduler._tick()
    row = db.get_automation(auto["id"])
    assert row["next_run_at"] is not None


async def test_tick_skips_future_and_handles_bad_schedule(db: CollieDB) -> None:
    fired: list[str] = []

    async def runner(auto):
        fired.append(auto["id"])

    future = db.add_automation(
        "Later",
        schedule="23:59",
        action_type="briefing",
        action_config={"prompt": "later"},
        enabled=True,
        next_run_at=(datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
    )
    bad = db.add_automation(
        "Malformed",
        schedule="not a schedule",
        action_type="briefing",
        action_config={"prompt": "x"},
        enabled=True,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    )
    scheduler = AutomationScheduler(db, runner=runner)
    await scheduler._tick()
    assert fired == []
    assert db.get_automation(bad["id"])["routine_status"] == "needs_attention"
    assert db.get_automation(future["id"])["next_run_at"] is not None


def test_structured_schedule_guards_malformed_json(db: CollieDB) -> None:
    auto = db.add_automation(
        "Weird json",
        schedule="09:00",
        action_type="briefing",
        action_config={"prompt": "x"},
        enabled=True,
        schedule_json='{"kind": "daily"',  # missing time + broken json
    )
    scheduler = AutomationScheduler(db, runner=lambda auto: None)
    assert scheduler._structured_schedule(auto) is None
    # Missing keys inside otherwise-valid JSON must not raise either.
    db.update_automation(auto["id"], schedule_json='{"kind": "daily"}')
    assert scheduler._structured_schedule(db.get_automation(auto["id"])) is None


def test_recover_stale_runs_covers_queued(db: CollieDB) -> None:
    created = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with db._write() as conn:
        conn.execute(
            "INSERT INTO runs (id, routine_id, trigger_type, scheduled_for, status, "
            "idempotency_key, created_at) VALUES (?, ?, 'schedule', ?, 'queued', 'k1', ?)",
            ("run-stale", "routine-x", created, created),
        )
    recovered = db.recover_stale_runs(stale_before=cutoff)
    assert recovered == 1
    rows = db.list_runs(limit=5)
    assert rows[0]["status"] == "interrupted"
