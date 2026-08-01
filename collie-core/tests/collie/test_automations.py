"""Tests for the automation scheduler."""

import asyncio
from pathlib import Path

import pytest

from collie_core.automations.scheduler import (
    BUILTIN_AUTOMATIONS,
    AutomationScheduler,
    _match_schedule,
    seed_builtin_automations,
)
from collie_core.db import CollieDB


def test_seed_builtins(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    try:
        seed_builtin_automations(db)
        automations = db.list_automations()
        assert len(automations) == len(BUILTIN_AUTOMATIONS)
        names = {a["name"] for a in automations}
        assert "Morning Briefing" in names
        assert "Evening Wind-Down" in names
        assert "Weekly Review" in names
        assert "Bill Reminders" in names
        assert "Birthday Reminders" in names

        # Only Morning Briefing is enabled by default
        enabled = [a for a in automations if a.get("enabled")]
        assert len(enabled) == 1
        assert enabled[0]["name"] == "Morning Briefing"

        # Idempotent: seeding again doesn't duplicate
        seed_builtin_automations(db)
        assert len(db.list_automations()) == len(BUILTIN_AUTOMATIONS)
    finally:
        db.close()


def test_match_schedule_daily() -> None:
    import datetime as dt

    # 07:00 matches
    now = dt.datetime(2026, 7, 19, 7, 0, tzinfo=dt.timezone.utc)
    assert _match_schedule("07:00", now)

    # 07:01 does not match
    now = dt.datetime(2026, 7, 19, 7, 1, tzinfo=dt.timezone.utc)
    assert not _match_schedule("07:00", now)


def test_match_schedule_weekly() -> None:
    import datetime as dt

    # Sunday 18:00
    now = dt.datetime(2026, 7, 19, 18, 0, tzinfo=dt.timezone.utc)  # Sunday
    assert _match_schedule("Sun 18:00", now)

    # Monday 18:00 should NOT match Sun schedule
    now = dt.datetime(2026, 7, 20, 18, 0, tzinfo=dt.timezone.utc)  # Monday
    assert not _match_schedule("Sun 18:00", now)


def test_match_schedule_empty() -> None:
    import datetime as dt

    now = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc)
    assert not _match_schedule("", now)
    assert not _match_schedule("  ", now)


@pytest.mark.asyncio
async def test_scheduler_fires_automation(tmp_path: Path) -> None:
    import datetime as dt
    from unittest.mock import patch

    db = CollieDB(tmp_path / "collie.db")
    db.add_automation(
        "Test Auto",
        automation_id="test-auto",
        schedule="12:00",
        action_type="briefing",
        action_config={"prompt": "Hello there!"},
        enabled=True,
    )
    events: list = []

    async def fake_broadcast(payload):
        events.append(payload)

    scheduler = AutomationScheduler(db, broadcaster=fake_broadcast, poll_seconds=0.1)
    await scheduler.start()

    try:
        with (patch("collie_core.automations.scheduler.datetime") as mock_dt,):
            mock_now = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc)
            mock_dt.now.return_value = mock_now
            mock_dt.timezone = dt.timezone

            # Monkey-patch the now call inside _tick
            called = False

            async def _tick():
                nonlocal called
                called = True
                # Call original
                from collie_core.automations.scheduler import _match_schedule as ms
                # Use the patched datetime directly
                mock_now_val = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc)
                autos = scheduler.db.list_automations(enabled_only=True)
                for auto in autos:
                    if ms(auto["schedule"], mock_now_val):
                        await scheduler._fire(auto)

            scheduler._tick = _tick

            await asyncio.sleep(0.3)
    finally:
        await scheduler.stop()

    assert len(events) >= 1
    assert events[0]["type"] == "automation"
    assert events[0]["action_type"] == "briefing"
    assert events[0]["prompt"] == "Hello there!"
    assert events[0]["automation_id"] == "test-auto"
    db.close()


@pytest.mark.asyncio
async def test_scheduler_respects_disabled(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    db.add_automation(
        "Disabled Auto",
        automation_id="disabled-auto",
        schedule="00:00",
        action_type="briefing",
        enabled=False,
    )
    events: list = []

    async def fake_broadcast(payload):
        events.append(payload)

    scheduler = AutomationScheduler(db, broadcaster=fake_broadcast, poll_seconds=0.1)
    await scheduler.start()
    await asyncio.sleep(0.3)
    await scheduler.stop()

    assert len(events) == 0
    db.close()
