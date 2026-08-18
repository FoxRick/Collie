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


# -- natural-language due times -------------------------------------------------

def _local(month: int, day: int, hour: int = 0, minute: int = 0, year: int = 2026):
    """A local-tz aware datetime (naive input interpreted as local, like the tool)."""
    import datetime as _dt

    return (
        _dt.datetime(year, month, day, hour, minute)
        .astimezone()
        .astimezone(_dt.timezone.utc)
        .isoformat(timespec="seconds")
    )


def test_nl_due_accepts_space_separated_iso() -> None:
    from collie_core.tools.reminders import _normalize_due

    assert _normalize_due("2026-07-20 15:00") == _local(7, 20, 15)


def test_nl_due_accepts_tomorrow_at_clock() -> None:
    from collie_core.tools.reminders import _normalize_due

    result = _normalize_due("tomorrow at 3pm")
    import datetime as _dt

    expected_date = _dt.datetime.now().astimezone().date() + _dt.timedelta(days=1)
    assert result == _local(expected_date.month, expected_date.day, 15)


def test_nl_due_accepts_tonight_without_clock() -> None:
    from collie_core.tools.reminders import _normalize_due

    result = _normalize_due("tonight")
    import datetime as _dt

    today = _dt.datetime.now().astimezone().date()
    assert result == _local(today.month, today.day, 20)


def test_nl_due_accepts_in_duration() -> None:
    import datetime as _dt

    from collie_core.tools.reminders import _normalize_due

    before = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=2)
    result = _normalize_due("in 2 hours")
    after = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=2)
    parsed = _dt.datetime.fromisoformat(result)
    # Tolerate the second tick between the window snapshots.
    assert before - _dt.timedelta(seconds=2) <= parsed <= after + _dt.timedelta(seconds=2)


def test_nl_due_accepts_next_weekday() -> None:
    from collie_core.tools.reminders import _normalize_due

    result = _normalize_due("next monday 9am")
    import datetime as _dt

    today = _dt.datetime.now().astimezone().date()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    expected = today + _dt.timedelta(days=days_ahead)
    assert result == _local(expected.month, expected.day, 9, year=expected.year)


def test_nl_due_accepts_bare_clock() -> None:
    from collie_core.tools.reminders import _normalize_due

    result = _normalize_due("3pm")
    import datetime as _dt

    today = _dt.datetime.now().astimezone().date()
    # "3pm" means today at 15:00, or tomorrow when that moment already passed.
    assert result in {
        _local(today.month, today.day, 15),
        _local(today.month, today.day + 1, 15, year=today.year),
    }


def test_nl_due_accepts_dateutil_style() -> None:
    from collie_core.tools.reminders import _normalize_due

    assert _normalize_due("July 20, 2026 at 3pm") == _local(7, 20, 15)


def test_nl_due_rejects_gibberish_with_guidance() -> None:
    from collie_core.tools.reminders import _normalize_due

    with pytest.raises(ValueError, match="didn't parse"):
        _normalize_due("sometime soonish")


@pytest.mark.asyncio
async def test_create_reminder_with_natural_language_due(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(action="create", text="Water plants", due_at="tomorrow at 9am")
    assert "Water plants" in str(result)
    assert "didn't parse" not in str(result)

    reminders = db.list_reminders()
    assert len(reminders) == 1
    assert reminders[0]["text"] == "Water plants"
    # Stored as aware UTC, not the raw phrase.
    import datetime as _dt

    stored = _dt.datetime.fromisoformat(reminders[0]["due_at"])
    assert stored.tzinfo is not None


@pytest.mark.asyncio
async def test_snooze_with_natural_language_until(db: CollieDB) -> None:
    r = db.add_reminder("Nap", "2026-07-20T12:00:00")
    tool = RemindersTool()
    result = await tool.execute(
        action="snooze", reminder_id=r["id"], snooze_until="in 1 hour"
    )
    assert "Snoozed" in str(result)


def test_nl_due_tolerates_sentence_punctuation() -> None:
    """Models wrap due strings in commas/periods — those must not break parsing."""
    import datetime as _dt

    from collie_core.tools.reminders import _normalize_due

    today = _dt.datetime.now().astimezone().date()
    tomorrow = today + _dt.timedelta(days=1)
    assert _normalize_due("tomorrow, 3pm") == _local(tomorrow.month, tomorrow.day, 15)
    assert _normalize_due("tomorrow at 3pm.") == _local(tomorrow.month, tomorrow.day, 15)
    assert _normalize_due("tomorrow, 3pm.") == _local(tomorrow.month, tomorrow.day, 15)
