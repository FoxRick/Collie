"""Tests for update_custom_automation — plain-English routine editing."""

import pytest

from collie_core.automations.custom import create_custom_automation, update_custom_automation
from collie_core.db import CollieDB


@pytest.fixture()
def db(tmp_path):
    return CollieDB(tmp_path / "test.db")


def test_update_rewords_schedule_and_prompt(db: CollieDB) -> None:
    row = create_custom_automation(db, "water the plants every Monday at 9am", name="Plants")
    updated = update_custom_automation(db, str(row["id"]), "water the plants every Friday at 5pm")
    assert updated["schedule"] == "Fri 17:00"
    config = updated["action_config"]
    if isinstance(config, str):
        import json

        config = json.loads(config)
    assert "every Friday at 5pm" in config["prompt"]
    # next run was recomputed
    assert updated["next_run_at"]


def test_update_preserves_identity_and_enabled_state(db: CollieDB) -> None:
    row = create_custom_automation(db, "stretch every day at noon", name="Stretch")
    db.toggle_automation(str(row["id"]), False)
    before_id = str(row["id"])
    updated = update_custom_automation(db, before_id, "stretch every day at 3pm")
    assert str(updated["id"]) == before_id
    assert updated["enabled"] == 0  # paused stays paused


def test_update_rejects_builtin(db: CollieDB) -> None:
    from collie_core.automations.scheduler import seed_builtin_automations

    seed_builtin_automations(db)
    with pytest.raises(ValueError, match="Built-in"):
        update_custom_automation(db, "collie-morning-briefing", "brief me at noon instead")


def test_update_rejects_unclear_schedule(db: CollieDB) -> None:
    row = create_custom_automation(db, "jog every Tuesday at 7am", name="Jog")
    with pytest.raises(ValueError, match="couldn't work out"):
        update_custom_automation(db, str(row["id"]), "sometimes jog maybe")


def test_update_missing_routine(db: CollieDB) -> None:
    with pytest.raises(ValueError, match="no longer exists"):
        update_custom_automation(db, "nope-gone", "jog every Tuesday at 7am")
