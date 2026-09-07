"""Local count durability, privacy boundary, and retry window."""
from datetime import UTC, datetime, timedelta

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer


def test_no_collection_without_packaged_launcher(tmp_path, monkeypatch):
    monkeypatch.delenv("COLLIE_PRODUCT_METRICS", raising=False)
    with CollieDB(tmp_path / "db") as db:
        db.record_turn_event(turn_id="dev", started_at=datetime.now(UTC).isoformat())
        assert db.product_metrics() == {}
        assert db._rows("SELECT * FROM product_metrics_daily") == []


def test_counters_count_starts_once_classify_and_survive_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLIE_PRODUCT_METRICS", "1")
    now = datetime.now(UTC).isoformat()
    path = tmp_path / "db"
    with CollieDB(path) as db:
        for kind in ("chat", "plan", "routine", "cron", "automation", "subagent"):
            db.record_turn_event(turn_id=kind, turn_kind=kind, started_at=now)
            db.record_turn_event(turn_id=kind, turn_kind=kind, started_at=now)
            db.record_turn_event(turn_id=kind, status="ok")
            db.record_tool_event(tool_id=kind, turn_id=kind, tool_name="private name",
                                 input_summary="secret input", started_at=now)
            db.record_tool_event(tool_id=kind, turn_id=kind, tool_name="private name", status="ok")
        db.record_tool_event(tool_id="blocked", turn_id="chat", tool_name="private",
                             status="denied", started_at=now, finished_at=now)
        # A finish-only record does not invent a run start.
        db.record_turn_event(turn_id="missing-start", status="error")
        snapshot = db.product_metrics()
        assert snapshot["days"] == [{"day": now[:10], "runs": 5,
                                    "interactive_runs": 2, "tool_calls": 7}]
        assert "private" not in str(snapshot) and "secret" not in str(snapshot)
        with db._write() as conn:
            conn.execute("DELETE FROM turn_events")
        assert db.product_metrics() == snapshot
    with CollieDB(path) as db:
        assert db.product_metrics() == snapshot
        exported = db.export_all()
        assert "product_metrics_source" not in exported
        assert "product_metrics_daily" not in exported
        db.clear_all()
        fresh = db.product_metrics()
        assert fresh["days"] == []
        assert fresh["source_id"] != snapshot["source_id"]


def test_utc_event_day_and_bounded_offline_window(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLIE_PRODUCT_METRICS", "1")
    now = datetime.now(UTC)
    with CollieDB(tmp_path / "db") as db:
        for age in range(40):
            stamp = (now - timedelta(days=age)).isoformat()
            db.record_turn_event(turn_id=str(age), started_at=stamp)
        rows = db.product_metrics()["days"]
        assert len(rows) == 35
        assert rows[0]["day"] == (now - timedelta(days=34)).date().isoformat()
        assert len(db._rows("SELECT * FROM product_metrics_daily")) == 35


@pytest.mark.asyncio
async def test_ipc_returns_only_daily_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLIE_PRODUCT_METRICS", "1")
    with CollieDB(tmp_path / "db") as db:
        db.record_turn_event(turn_id="private", session_key="private conversation",
                             started_at=datetime.now(UTC).isoformat())
        # Exercise the actual command handler without opening a network listener.
        server = object.__new__(CollieIPCServer)
        server.db = db
        result = await server._cmd_get_product_metrics(None, {})
        assert set(result) == {"source_id", "days"}
        assert result["days"][0]["runs"] == 1
        assert "private" not in str(result)
