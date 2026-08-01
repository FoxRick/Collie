"""Tests for the Reminders tool (F024)."""

from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.tools.reminders import RemindersTool, bind_reminders_db


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    bind_reminders_db(d)
    yield d
    d.close()
    # Reset module-level state
    import collie_core.tools.reminders as mod
    mod._db = None


@pytest.mark.asyncio
async def test_create_reminder(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(
        action="create",
        text="Call Mom",
        due_at="2026-07-20T15:00:00",
        recurrence="weekly",
    )
    assert "Call Mom" in str(result)
    assert "repeating" in str(result).lower()

    reminders = db.list_reminders()
    assert len(reminders) == 1
    assert reminders[0]["text"] == "Call Mom"
    assert reminders[0]["recurrence"] == "weekly"


@pytest.mark.asyncio
async def test_create_without_due_adds_now(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="create", text="Quick task")
    assert "Quick task" in str(result)


@pytest.mark.asyncio
async def test_create_missing_text(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="create", text="")
    assert "need to know" in str(result).lower()


@pytest.mark.asyncio
async def test_list_empty(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="list")
    assert "don't have any" in str(result).lower()


@pytest.mark.asyncio
async def test_list_with_items(db: CollieDB) -> None:
    db.add_reminder("Buy milk", "2026-07-19T10:00:00")
    db.add_reminder("Dentist", "2026-07-22T14:00:00", recurrence="monthly")

    tool = RemindersTool()
    result = await tool.execute(action="list")
    text = str(result)
    assert "Buy milk" in text
    assert "Dentist" in text
    assert "monthly" in text


@pytest.mark.asyncio
async def test_complete(db: CollieDB) -> None:
    r = db.add_reminder("Test", "2026-07-20T12:00:00")
    tool = RemindersTool()
    result = await tool.execute(action="complete", reminder_id=r["id"])
    assert "Done" in str(result)

    # Should not appear in active reminders
    active = db.list_reminders()
    assert len(active) == 0

    all_reminders = db.list_reminders(include_completed=True)
    assert len(all_reminders) == 1


@pytest.mark.asyncio
async def test_delete(db: CollieDB) -> None:
    r = db.add_reminder("Delete me", "2026-07-20T12:00:00")
    tool = RemindersTool()
    result = await tool.execute(action="delete", reminder_id=r["id"])
    assert "Gone" in str(result)
    assert len(db.list_reminders(include_completed=True)) == 0


@pytest.mark.asyncio
async def test_snooze(db: CollieDB) -> None:
    r = db.add_reminder("Eat lunch", "2026-07-20T12:00:00")
    tool = RemindersTool()
    result = await tool.execute(
        action="snooze", reminder_id=r["id"], snooze_until="2026-07-20T13:00:00"
    )
    assert "Snoozed" in str(result)

    reminders = db.list_reminders()
    # Naive times are interpreted as local and normalized to aware UTC.
    stored = reminders[0]["snoozed_until"]
    import datetime as _dt

    local_tz = _dt.datetime.now().astimezone().tzinfo
    expected = (
        _dt.datetime(2026, 7, 20, 13, 0, tzinfo=local_tz)
        .astimezone(_dt.timezone.utc)
        .isoformat(timespec="seconds")
    )
    assert stored == expected


@pytest.mark.asyncio
async def test_snooze_without_until_uses_default(db: CollieDB) -> None:
    r = db.add_reminder("Snack", "2026-07-20T12:00:00")
    tool = RemindersTool()
    result = await tool.execute(action="snooze", reminder_id=r["id"])
    assert "Snoozed" in str(result)


@pytest.mark.asyncio
async def test_complete_missing_id(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="complete")
    assert "need the id" in str(result).lower()


@pytest.mark.asyncio
async def test_unknown_action(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="fly")
    assert "not sure" in str(result).lower()
