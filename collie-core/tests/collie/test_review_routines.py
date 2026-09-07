from datetime import UTC, datetime, time

import pytest

from collie_core.automations.scheduler import seed_builtin_automations
from collie_core.db import CollieDB
from collie_core.routines.models import Schedule
from collie_core.routines.schedule import next_occurrence


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "America/New_York", "Europe/Berlin"])
def test_new_builtins_use_each_users_timezone(tmp_path, monkeypatch, zone):
    monkeypatch.setenv("COLLIE_TIMEZONE", zone)
    with CollieDB(tmp_path / "collie.db") as db:
        seed_builtin_automations(db)
        assert {r["timezone"] for r in db.list_automations()} == {zone}
        db.update_automation("collie-morning-briefing", timezone="Pacific/Auckland")
        seed_builtin_automations(db)
        assert db.get_automation("collie-morning-briefing")["timezone"] == "Pacific/Auckland"


def test_fall_back_never_returns_an_instant_before_after():
    schedule = Schedule(kind="daily", time=time(1, 30), timezone="America/New_York")
    after = datetime(2026, 11, 1, 6, 15, tzinfo=UTC)
    # Run once at the first 01:30; do not replay it during the repeated hour.
    assert next_occurrence(schedule, after) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_spring_forward_snaps_gap_time_to_first_valid_instant():
    # US DST starts Sun 2026-03-08 at 02:00 EST → 03:00 EDT, so 02:30 is
    # nonexistent. The routine must fire once at 03:00 EDT (07:00 UTC), not
    # an hour late at 07:30 UTC (03:30 local).
    schedule = Schedule(kind="daily", time=time(2, 30), timezone="America/New_York")
    after = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
    assert next_occurrence(schedule, after) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize("trigger", ["manual", "retry", "schedule"])
def test_routine_summary_tracks_terminal_run_results(tmp_path, trigger):
    with CollieDB(tmp_path / "collie.db") as db:
        routine = db.add_automation("Review", schedule="08:00")
        run = db.create_run(
            trigger_type=trigger, idempotency_key="review", routine_id=routine["id"]
        )
        db.transition_run(run["id"], "running")
        db.transition_run(run["id"], "completed")
        completed = db.get_automation(routine["id"])
        assert completed["last_success_at"]
        db.transition_run(run["id"], "completed")
        assert db.get_automation(routine["id"])["last_success_at"] == completed["last_success_at"]
        retry = db.create_run(
            trigger_type="retry", idempotency_key="retry", routine_id=routine["id"]
        )
        db.transition_run(retry["id"], "failed", error_message="Could not connect")
        assert db.get_automation(routine["id"])["last_failure_at"]
        assert db.get_automation(routine["id"])["last_success_at"] == completed["last_success_at"]


@pytest.mark.asyncio
async def test_timezone_edit_recomputes_without_losing_recurrence(tmp_path):
    import json
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from collie_core.automations.custom import create_custom_automation, update_custom_automation
    from collie_core.ipc.server import CollieIPCServer

    with CollieDB(tmp_path / "collie.db") as db:
        row = create_custom_automation(
            db, "weekdays at 8am remind me to stretch", timezone_name="Asia/Shanghai"
        )
        server = SimpleNamespace(db=db)
        result = await CollieIPCServer._cmd_update_routine(
            server, None, {"routine_id": row["id"], "updates": {"timezone": "America/New_York"}}
        )
        changed = result["routine"]
        structured = json.loads(changed["schedule_json"])
        assert structured["kind"] == "weekdays"
        assert structured["timezone"] == "America/New_York"
        upcoming = datetime.fromisoformat(changed["next_run_at"]).astimezone(
            ZoneInfo("America/New_York")
        )
        assert upcoming.hour == 8
        assert upcoming.weekday() < 5
        with pytest.raises(ValueError):
            await CollieIPCServer._cmd_update_routine(
                server, None, {"routine_id": row["id"], "updates": {"timezone": "Invalid/Timezone"}}
            )
        assert db.get_automation(row["id"])["timezone"] == "America/New_York"
        edited = update_custom_automation(db, row["id"], "weekdays at 9am remind me to stretch")
        assert edited["timezone"] == "America/New_York"


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        ("2026-03-07T15:00:00+00:00", "2026-03-08T12:00:00+00:00"),
        ("2026-10-31T15:00:00+00:00", "2026-11-01T13:00:00+00:00"),
    ],
)
def test_local_morning_time_survives_dst_changes(after, expected):
    schedule = Schedule(kind="daily", time=time(8), timezone="America/New_York")
    assert next_occurrence(schedule, datetime.fromisoformat(after)) == datetime.fromisoformat(
        expected
    )
