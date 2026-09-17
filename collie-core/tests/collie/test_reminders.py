"""Tests for the Reminders tool (F024)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.routines.schedule import next_recurrence, validate_recurrence
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
    result = await tool.execute(action="snooze", reminder_id=r["id"], snooze_until="in 1 hour")
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


# -- recurrence ----------------------------------------------------------------


class _FakeIPC:
    def __init__(self) -> None:
        self.broadcasts: list[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.broadcasts.append(payload)


def _runtime_for(db: CollieDB):
    from collie_core.runtime import CollieRuntime

    runtime = CollieRuntime.__new__(CollieRuntime)
    runtime.db = db
    runtime.ipc = _FakeIPC()
    return runtime


def test_reschedule_reminder_clears_snooze_and_keeps_it_active(db: CollieDB) -> None:
    r = db.add_reminder("Daily", "2020-01-01T09:00:00+00:00", recurrence="daily")
    db.snooze_reminder(r["id"], "2020-01-01T10:00:00+00:00")

    assert db.reschedule_reminder(r["id"], "2026-07-21T09:00:00+00:00") is True

    row = db.list_reminders()[0]
    assert row["due_at"] == "2026-07-21T09:00:00+00:00"
    assert row["snoozed_until"] is None
    assert row["completed"] == 0


@pytest.mark.asyncio
async def test_recurring_reminder_reschedules_instead_of_completing(db, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_TIMEZONE", "UTC")
    db.add_reminder("Stretch", "2020-01-01T09:00:00+00:00", recurrence="daily")
    runtime = _runtime_for(db)

    await runtime._deliver_due_reminders()

    active = db.list_reminders()
    assert len(active) == 1
    assert active[0]["completed"] == 0
    following = datetime.fromisoformat(active[0]["due_at"])
    now = datetime.now(UTC)
    assert following > now
    assert following.hour == 9 and following.minute == 0
    assert following - now <= timedelta(days=1)
    # The notification still went out, twice (message + automation).
    assert len(runtime.ipc.broadcasts) == 2


@pytest.mark.asyncio
async def test_non_recurring_reminder_is_completed(db, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_TIMEZONE", "UTC")
    db.add_reminder("One-off", "2020-01-01T09:00:00+00:00")
    runtime = _runtime_for(db)

    await runtime._deliver_due_reminders()

    assert db.list_reminders() == []
    assert db.list_reminders(include_completed=True)[0]["completed"] == 1
    assert len(runtime.ipc.broadcasts) == 2


@pytest.mark.asyncio
async def test_unusable_recurrence_completes_without_stopping_the_checker(db, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_TIMEZONE", "UTC")
    db.add_reminder("Broken", "2020-01-01T09:00:00+00:00", recurrence="every so often")
    db.add_reminder("Still works", "2020-01-01T10:00:00+00:00", recurrence="daily")
    runtime = _runtime_for(db)

    await runtime._deliver_due_reminders()

    rows = {row["text"]: row for row in db.list_reminders(include_completed=True)}
    assert rows["Broken"]["completed"] == 1
    assert rows["Still works"]["completed"] == 0
    # Both were delivered: the broken one once, the repeating one still scheduled.
    assert len(runtime.ipc.broadcasts) == 4


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("daily", datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
        ("weekdays", datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
        ("weekly", datetime(2026, 7, 27, 9, 0, tzinfo=UTC)),
        ("monthly", datetime(2026, 8, 20, 9, 0, tzinfo=UTC)),
        ("0 9 * * *", datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
    ],
)
def test_next_recurrence_expected_next_time(rule: str, expected: datetime) -> None:
    # 2026-07-20 is a Monday, so "weekly" lands the following Monday and
    # "monthly" keeps the 20th.
    anchor = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    after = datetime(2026, 7, 20, 9, 30, tzinfo=UTC)
    assert next_recurrence(rule, anchor, after=after, timezone_name="UTC") == expected


def test_next_recurrence_matches_routines_dst_spring_forward() -> None:
    # US DST starts Sun 2026-03-08: 02:30 doesn't exist, so the reminder snaps
    # to the first valid instant (03:00 EDT), exactly like the routines scheduler.
    anchor = datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    after = datetime(2026, 3, 7, 8, 0, tzinfo=UTC)
    assert next_recurrence(
        "daily", anchor, after=after, timezone_name="America/New_York"
    ) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


def test_next_recurrence_returns_none_for_unusable_rule() -> None:
    anchor = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    assert next_recurrence("every so often", anchor, timezone_name="UTC") is None


def test_validate_recurrence_accepts_named_kinds_and_cron() -> None:
    assert validate_recurrence("Daily") == "daily"
    assert validate_recurrence("0 9 * * 1-5") == "0 9 * * 1-5"


def test_validate_recurrence_rejects_unknown_rule() -> None:
    with pytest.raises(ValueError, match="cron"):
        validate_recurrence("every so often")


@pytest.mark.asyncio
async def test_create_with_invalid_recurrence_is_rejected(db: CollieDB) -> None:
    tool = RemindersTool()
    result = await tool.execute(
        action="create",
        text="Yoga",
        due_at="2026-07-20T15:00:00",
        recurrence="every so often",
    )
    assert "cron" in str(result).lower()
    assert db.list_reminders() == []
