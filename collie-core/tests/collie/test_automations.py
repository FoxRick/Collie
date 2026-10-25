"""Tests for the automation scheduler."""

import asyncio
from pathlib import Path

import pytest

from collie_core.automations.scheduler import (
    BUILTIN_AUTOMATIONS,
    AutomationScheduler,
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
