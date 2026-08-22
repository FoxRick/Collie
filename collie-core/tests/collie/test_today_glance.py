"""Tests for the Today-at-a-glance card payload (morning briefing)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.tools.today_glance import attach_today_glance, today_glance_card


@pytest.fixture()
def db(tmp_path: Path):
    database = CollieDB(tmp_path / "collie.db")
    yield database
    database.close()


def _due_in_hours(hours: float) -> str:
    return (
        datetime.now(timezone.utc)  # noqa: UP017
        + timedelta(hours=hours)
    ).isoformat(timespec="seconds")


def test_payload_none_without_weather_or_reminders(db: CollieDB) -> None:
    # No location in profile, no reminders -> nothing to show.
    assert today_glance_card(db) is None


def test_reminders_within_24h_are_included_and_capped(db: CollieDB) -> None:
    db.add_reminder("Call the dentist", _due_in_hours(3))
    db.add_reminder("Take out trash", _due_in_hours(20))
    db.add_reminder("Far away thing", _due_in_hours(72))
    for i in range(5):
        db.add_reminder(f"extra {i}", _due_in_hours(4 + i))

    payload = today_glance_card(db)
    assert payload is not None
    assert payload["card_type"] == "today_glance"
    texts = [item["text"] for item in payload["reminders"]]
    assert "Far away thing" not in texts  # outside the 24h horizon
    assert len(texts) <= 3  # capped


def test_past_and_completed_reminders_excluded(db: CollieDB) -> None:
    rid = db.add_reminder("Already done", _due_in_hours(-1))["id"]
    db.complete_reminder(str(rid))
    db.add_reminder("Still pending but way later", _due_in_hours(48))

    payload = today_glance_card(db)
    assert payload is None  # nothing due within 24h


async def test_attach_today_glance_persists_and_broadcasts(db: CollieDB) -> None:
    conv = db.create_conversation(title="🔔 Morning Briefing")
    db.add_reminder("Water the plants", _due_in_hours(5))

    class FakeIPC:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def broadcast(self, payload: dict) -> None:
            self.sent.append(payload)

    ipc = FakeIPC()
    await attach_today_glance(db, conv["id"], ipc)

    assert len(ipc.sent) == 1
    message = ipc.sent[0]["message"]
    assert message["role"] == "assistant"
    data = (
        json.loads(message["card_data"])
        if isinstance(message["card_data"], str)
        else message["card_data"]
    )
    assert data["reminders"][0]["text"] == "Water the plants"

    stored = db.get_messages(conv["id"])
    assert len(stored) == 1
