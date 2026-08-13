"""Tests for the Collie SQLite storage layer."""

from pathlib import Path

import pytest

from collie_core.db import CollieDB


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    d.close()


def test_schema_created(db: CollieDB) -> None:
    assert db.schema_version == 14


def test_migration_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "collie.db"
    d1 = CollieDB(path)
    d1.set_setting("provider.name", "openai")
    d1.close()
    d2 = CollieDB(path)
    assert d2.get_setting("provider.name") == "openai"
    assert d2.schema_version == 14
    d2.close()


def test_incremental_migrations_v1_through_latest(tmp_path: Path) -> None:
    """Every schema version must upgrade cleanly to the latest, preserving data."""
    import sqlite3

    import collie_core.db as db_mod

    latest = len(db_mod._MIGRATIONS)
    for target in range(1, latest + 1):
        path = tmp_path / f"v{target}.db"
        conn = sqlite3.connect(path)
        conn.executescript(db_mod._SCHEMA_V1)
        for migration in db_mod._MIGRATIONS[1:target]:
            conn.executescript(migration)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) "
            "VALUES ('c1', 'Keep me', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        upgraded = CollieDB(path)
        assert upgraded.schema_version == latest, f"v{target} did not reach v{latest}"
        assert upgraded.get_conversation("c1")["title"] == "Keep me"
        upgraded.close()


def test_migration_failure_rolls_back_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that fails halfway must leave the version and schema intact."""
    import sqlite3

    import collie_core.db as db_mod

    path = tmp_path / "collie.db"
    CollieDB(path).close()  # a healthy v10 database

    monkeypatch.setattr(
        db_mod,
        "_MIGRATIONS",
        list(db_mod._MIGRATIONS)
        + ["CREATE TABLE half_done (x INTEGER);\nINSERT INTO missing_table VALUES (1);"],
    )
    with pytest.raises(sqlite3.OperationalError):
        CollieDB(path)

    # The failed migration rolled everything back: version still at the
    # pre-failure latest and the partial table is gone, so a normal boot
    # migrates cleanly again.
    conn = sqlite3.connect(path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        half = conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone()
    finally:
        conn.close()
    assert version == len(db_mod._MIGRATIONS) - 1
    assert half is None
    monkeypatch.undo()
    fresh = CollieDB(path)
    assert fresh.schema_version == len(db_mod._MIGRATIONS)
    fresh.close()


def test_v8_removes_only_legacy_system_subagent_allow(tmp_path: Path) -> None:
    """The V8→V9 upgrade keeps its V8 rule-cleanup behavior and adds task_state."""
    import sqlite3

    import collie_core.db as db_mod

    path = tmp_path / "collie.db"
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:7]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (7)")
    for row in (
        ("legacy", "subagent.spawn", "*", "allow", "global", "system"),
        ("user", "subagent.spawn", "*", "allow", "global", "user"),
        ("near-match", "subagent.spawn", "researcher", "allow", "global", "system"),
    ):
        conn.execute(
            "INSERT INTO approval_rules (id, action, resource_pattern, effect, scope_type, "
            "scope_value, created_by, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, '2026-01-01', '2026-01-01')",
            row,
        )
    conn.commit()
    conn.close()

    migrated = CollieDB(path)
    rules = {row["id"] for row in migrated.list_approval_rules()}
    assert "legacy" not in rules
    assert "user" in rules
    assert "near-match" in rules
    assert migrated._row("SELECT task_state FROM messages LIMIT 1") is None
    columns = {row["name"] for row in migrated._rows("PRAGMA table_info(messages)")}
    assert "task_state" in columns
    assert migrated.schema_version == len(db_mod._MIGRATIONS)
    migrated.close()


def test_v9_upgrade_adds_plan_change_terminal_message_id(tmp_path: Path) -> None:
    """Existing V9 plan-change rows upgrade without being recreated or lost."""
    import sqlite3

    import collie_core.db as db_mod

    path = tmp_path / "v9.db"
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:9]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (9)")
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) "
        "VALUES ('c1', 'Keep me', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO plans (id, conversation_id, version, title, goal, plan_json, "
        "plan_hash, status, created_at, updated_at) VALUES "
        "('p1', 'c1', 1, 'Plan', 'Keep it', '{}', 'hash', 'approved', "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO runs (id, trigger_type, idempotency_key, status, conversation_id, "
        "plan_id, plan_version, created_at) VALUES "
        "('r1', 'manual', 'keep-me', 'running', 'c1', 'p1', 1, "
        "'2026-01-01')"
    )
    conn.execute(
        "INSERT INTO plan_change_requests (run_id, conversation_id, plan_id, plan_version, "
        "reason, status, requested_at) VALUES "
        "('r1', 'c1', 'p1', 1, 'Change it', 'requested', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    upgraded = CollieDB(path)
    try:
        request = upgraded.get_plan_change_request("r1")
        columns = {row["name"] for row in upgraded._rows("PRAGMA table_info(plan_change_requests)")}
        assert upgraded.schema_version == len(db_mod._MIGRATIONS)
        assert "terminal_message_id" in columns
        assert request is not None
        assert request["reason"] == "Change it"
        assert request["terminal_message_id"] is None
    finally:
        upgraded.close()


def test_settings_roundtrip(db: CollieDB) -> None:
    db.set_setting("agent.timezone", "Europe/Berlin")
    db.set_setting("nested", {"a": 1, "b": [1, 2]})
    assert db.get_setting("agent.timezone") == "Europe/Berlin"
    assert db.get_setting("nested") == {"a": 1, "b": [1, 2]}
    assert db.get_setting("missing", "fallback") == "fallback"
    db.set_setting("agent.timezone", "UTC")
    assert db.get_setting("agent.timezone") == "UTC"
    db.delete_setting("agent.timezone")
    assert db.get_setting("agent.timezone") is None
    assert "nested" in db.all_settings()


def test_conversations_and_messages(db: CollieDB) -> None:
    conv = db.create_conversation("Trip planning", project_path="C:\\work\\trips")
    assert db.get_conversation(conv["id"])["title"] == "Trip planning"
    assert db.get_conversation(conv["id"])["project_path"] == "C:\\work\\trips"
    db.set_conversation_project(conv["id"], "C:\\work\\paris")
    assert db.get_conversation(conv["id"])["project_path"] == "C:\\work\\paris"

    db.add_message(conv["id"], "user", "plan a trip to Paris")
    db.add_message(
        conv["id"],
        "assistant",
        "Here's your itinerary!",
        card_type="TravelCard",
        card_data={"days": 3},
    )
    msgs = db.get_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["card_type"] == "TravelCard"
    assert msgs[1]["card_data"] == {"days": 3}

    results = db.search_messages("paris")
    assert len(results) == 1
    assert results[0]["conversation_title"] == "Trip planning"

    db.rename_conversation(conv["id"], "Paris trip")
    assert db.get_conversation(conv["id"])["title"] == "Paris trip"

    db.archive_conversation(conv["id"])
    assert db.list_conversations() == []
    assert len(db.list_conversations(include_archived=True)) == 1

    db.delete_conversation(conv["id"])
    assert db.get_conversation(conv["id"]) is None
    assert db.get_messages(conv["id"]) == []


def test_message_limit(db: CollieDB) -> None:
    conv = db.create_conversation()
    for i in range(10):
        db.add_message(conv["id"], "user", f"msg {i}")
    assert len(db.get_messages(conv["id"], limit=3)) == 3
    assert db.get_messages(conv["id"], limit=3)[-1]["content"] == "msg 9"


def test_profile(db: CollieDB) -> None:
    db.set_profile("dietary", "vegetarian, peanut allergy")
    db.set_profile("wake_time", "07:00")
    assert db.get_profile("dietary") == "vegetarian, peanut allergy"
    assert db.all_profile()["wake_time"] == "07:00"
    db.delete_profile("wake_time")
    assert db.get_profile("wake_time") is None


def test_people(db: CollieDB) -> None:
    mom = db.add_person(
        "Mom", relationship="mother", birthday="03-15", preferences="gardening books, red wine"
    )
    assert db.find_person("mom")["id"] == mom["id"]
    db.update_person(mom["id"], gift_ideas="rose pruning set")
    assert db.get_person(mom["id"])["gift_ideas"] == "rose pruning set"
    assert len(db.list_people()) == 1
    db.delete_person(mom["id"])
    assert db.list_people() == []


def test_important_dates_cascade(db: CollieDB) -> None:
    alex = db.add_person("Alex", relationship="partner")
    db.add_date("07-03", "Alex's birthday", recurring=True, person_id=alex["id"])
    assert len(db.list_dates()) == 1
    db.delete_person(alex["id"])
    assert db.list_dates() == []


def test_reminders(db: CollieDB) -> None:
    r = db.add_reminder("water the plants", "2026-07-20T09:00:00+00:00")
    assert len(db.list_reminders()) == 1
    db.snooze_reminder(r["id"], "2026-07-21T09:00:00+00:00")
    db.complete_reminder(r["id"])
    assert db.list_reminders() == []
    assert len(db.list_reminders(include_completed=True)) == 1


def test_automations(db: CollieDB) -> None:
    a = db.add_automation(
        "Morning Briefing",
        schedule="0 7 * * *",
        action_type="briefing",
        action_config={"include": ["weather", "calendar"]},
    )
    assert db.list_automations(enabled_only=True)[0]["name"] == "Morning Briefing"
    db.toggle_automation(a["id"], False)
    assert db.list_automations(enabled_only=True) == []
    db.mark_automation_run(a["id"])
    assert db.list_automations()[0]["last_run"] is not None
    db.delete_automation(a["id"])
    assert db.list_automations() == []


def test_services(db: CollieDB) -> None:
    db.upsert_service("gmail", name="Gmail", provider="google", status="disconnected")
    db.upsert_service(
        "gmail",
        name="Gmail",
        provider="google",
        status="connected",
        account_info="user@example.com",
    )
    svc = db.get_service("gmail")
    assert svc["status"] == "connected"
    assert svc["connected_at"] is not None
    db.delete_service("gmail")
    assert db.list_services() == []


def test_subagents(db: CollieDB) -> None:
    s = db.upsert_subagent(
        "Trip Planner",
        description="plans trips",
        system_prompt="You are a travel expert.",
        filename="trip-planner.md",
    )
    db.upsert_subagent(
        "Trip Planner",
        subagent_id=s["id"],
        description="plans amazing trips",
        system_prompt="You are a travel expert.",
        filename="trip-planner.md",
    )
    subs = db.list_subagents()
    assert len(subs) == 1
    assert subs[0]["description"] == "plans amazing trips"
    db.delete_subagent(s["id"])
    assert db.list_subagents() == []


def test_providers_and_usage(db: CollieDB) -> None:
    db.upsert_provider(
        "openai-oauth", name="ChatGPT", auth_type="oauth", model="gpt-5.6", is_default=True
    )
    db.upsert_provider("anthropic-key", name="Claude", auth_type="api_key")
    assert db.default_provider()["id"] == "openai-oauth"

    db.set_default_provider("anthropic-key")
    assert db.default_provider()["id"] == "anthropic-key"
    assert db.get_provider("openai-oauth")["is_default"] == 0

    db.record_usage("openai-oauth", messages=1, tokens=500)
    db.record_usage("openai-oauth", messages=2, tokens=1500)
    usage = db.usage_this_month("openai-oauth")
    assert usage == {"messages": 3, "tokens": 2000}
    assert db.usage_this_month()["messages"] == 3

    db.delete_provider("anthropic-key")
    assert db.default_provider()["id"] == "openai-oauth"


def test_export_and_clear(db: CollieDB) -> None:
    conv = db.create_conversation("hello")
    db.add_message(conv["id"], "user", "hi")
    db.set_profile("dietary", "vegan")
    db.add_person("Sam")
    data = db.export_all()
    assert data["schema_version"] == 14
    assert len(data["conversations"]) == 1
    assert data["profile"] == {"dietary": "vegan"}

    db.clear_all()
    assert db.list_conversations(include_archived=True) == []
    assert db.all_profile() == {}
    assert db.list_people() == []


def test_concurrent_access(db: CollieDB) -> None:
    import threading

    conv = db.create_conversation("threads")
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(20):
                db.add_message(conv["id"], "user", f"t{n}-{i}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(db.get_messages(conv["id"])) == 100
