"""Tests for the Phase 3 life tools (Steps 35 + 39)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.tools.budget import BudgetTool
from collie_core.tools.contacts import ContactsTool
from collie_core.tools.documents import DocumentsTool
from collie_core.tools.health import HealthTool
from collie_core.tools.home import SmartHomeTool
from collie_core.tools.life_db import bind_life_db
from collie_core.tools.presentations import PresentationsTool
from collie_core.tools.shopping import ShoppingTool
from collie_core.tools.travel import TravelTool


@pytest.fixture()
def db(tmp_path: Path):
    database = CollieDB(tmp_path / "collie.db")
    bind_life_db(database)
    yield database
    bind_life_db(None)
    database.close()


# -- db layer ------------------------------------------------------------------


def test_shopping_db_roundtrip(db: CollieDB) -> None:
    row = db.add_shopping_item("Milk", category="Dairy", quantity="2L")
    assert row["checked"] == 0
    db.check_shopping_item_by_name("Milk", "Groceries")
    items = db.list_shopping_items()
    assert items[0]["checked"] == 1
    assert db.clear_checked_shopping_items() == 1
    assert db.list_shopping_items() == []


def test_expenses_and_budgets(db: CollieDB) -> None:
    db.add_expense(42.5, category="Groceries", spent_at="2026-07-02")
    db.add_expense(10.0, category="Groceries", spent_at="2026-07-15")
    db.add_expense(99.0, category="Fun", spent_at="2026-06-30")
    db.set_budget("Groceries", 300)
    by_cat = db.expenses_by_category("2026-07")
    assert by_cat == [{"category": "Groceries", "spent": 52.5}]
    assert db.list_budgets()[0]["monthly_limit"] == 300


def test_health_upsert_per_day(db: CollieDB) -> None:
    db.log_health("steps", 5000, logged_on="2026-07-18")
    db.log_health("steps", 8000, logged_on="2026-07-18")
    rows = db.health_logs_since("2026-07-01")
    assert len(rows) == 1
    assert rows[0]["value"] == 8000


# -- shopping tool ------------------------------------------------------------------


async def test_shopping_tool_flow(db: CollieDB) -> None:
    tool = ShoppingTool()
    result = await tool.execute(
        action="add",
        items=[
            {"name": "Milk", "category": "Dairy", "quantity": "2L"},
            {"name": "Apples", "category": "Produce"},
        ],
    )
    card = json.loads(result)
    assert card["card_type"] == "shopping_list"
    assert card["remaining"] == 2
    assert {i["name"] for i in card["categories"]["Dairy"]} == {"Milk"}

    result = await tool.execute(action="check", item="milk")
    card = json.loads(result)
    assert card["remaining"] == 1

    result = await tool.execute(action="clear_checked")
    card = json.loads(result)
    assert card["total"] == 1


async def test_shopping_tool_missing_item(db: CollieDB) -> None:
    tool = ShoppingTool()
    result = await tool.execute(action="check", item="caviar")
    assert "don't see" in result


# -- budget tool ---------------------------------------------------------------------


async def test_budget_tool_flow(db: CollieDB) -> None:
    tool = BudgetTool()
    await tool.execute(action="set_budget", category="Groceries", amount=300)
    result = await tool.execute(
        action="log_expense",
        amount=45.5,
        category="Groceries",
        description="weekly shop",
    )
    card = json.loads(result)
    assert card["card_type"] == "budget"
    groceries = next(c for c in card["categories"] if c["name"] == "Groceries")
    assert groceries["spent"] == 45.5
    assert groceries["budget"] == 300
    assert card["total_budget"] == 300


async def test_budget_default_date_and_summary_share_utc_month(
    db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local month boundary must not split the default write from its summary."""
    from datetime import datetime, timezone

    import collie_core.db as db_module
    import collie_core.tools.budget as budget_module

    class BoundaryDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 31, 16, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(db_module, "utc_now", lambda: "2026-07-31T16:30:00+00:00")
    monkeypatch.setattr(budget_module, "datetime", BoundaryDateTime)

    result = await BudgetTool().execute(action="log_expense", amount=12.5, category="Transport")
    card = json.loads(result)

    assert card["month"] == "2026-07"
    assert card["total_spent"] == 12.5
    assert db.expenses_by_category("2026-07") == [{"category": "Transport", "spent": 12.5}]
    assert db.expenses_by_category("2026-08") == []


async def test_budget_tool_needs_amount(db: CollieDB) -> None:
    tool = BudgetTool()
    result = await tool.execute(action="log_expense")
    assert "amount" in str(result).lower()


# -- health tool -------------------------------------------------------------------


async def test_health_tool_flow(db: CollieDB) -> None:
    tool = HealthTool()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await tool.execute(action="log", metric="steps", value=7500, date=today)
    await tool.execute(action="log", metric="sleep_hours", value=7.5, date=today)
    await tool.execute(action="log", metric="steps", value=4000, date=yesterday)
    await tool.execute(action="log", metric="water_cups", value=6, date=yesterday)
    result = await tool.execute(action="summary")
    card = json.loads(result)
    assert card["card_type"] == "health"
    assert card["steps"] == 7500
    assert card["sleep_hours"] == 7.5
    assert card["streak_days"] == 2
    assert len(card["grid"]) == 7

    habits = {h["key"]: h for h in card["habits"]}
    assert set(habits) == {"steps", "water_cups", "sleep_hours"}
    steps_days = habits["steps"]["days"]
    assert len(steps_days) == 7
    assert steps_days[-1] == 7500  # today, newest last
    assert steps_days[-2] == 4000  # yesterday
    assert steps_days[0] is None  # nothing logged 6 days ago
    assert habits["water_cups"]["days"][-2] == 6


async def test_health_tool_rejects_unknown_metric(db: CollieDB) -> None:
    tool = HealthTool()
    result = await tool.execute(action="log", metric="mood", value=5)
    assert "track" in str(result).lower()


# -- contacts tool ------------------------------------------------------------------


async def test_contacts_tool_flow(db: CollieDB) -> None:
    tool = ContactsTool()
    result = await tool.execute(
        action="upsert",
        name="Mom",
        relationship="mother",
        birthday="March 15",
        preferences="gardening books, red wine",
    )
    assert "remember" in result.lower()
    result = await tool.execute(action="find", name="Mom")
    assert "March 15" in result
    result = await tool.execute(action="gift_ideas", name="Mom")
    assert "gardening books" in result
    result = await tool.execute(
        action="upsert",
        name="Mom",
        gift_ideas="rose pruning shears",
    )
    assert "Updated" in result
    result = await tool.execute(action="list")
    assert "Mom" in result


async def test_contacts_tool_unknown_person(db: CollieDB) -> None:
    tool = ContactsTool()
    result = await tool.execute(action="find", name="Zorp")
    assert "don't know" in result


# -- travel tool --------------------------------------------------------------------


async def test_travel_itinerary_card() -> None:
    tool = TravelTool()
    result = await tool.execute(
        action="itinerary",
        destination="Barcelona",
        days=[
            {
                "label": "Day 1 — Sat",
                "summary": "Old town",
                "activities": [
                    {"time": "10:00", "title": "Sagrada Família", "kind": "sight"},
                    {"time": "13:00", "title": "Tapas lunch", "kind": "food"},
                ],
            },
            {"label": "Day 2 — Sun", "summary": "Beach day"},
        ],
    )
    card = json.loads(result)
    assert card["card_type"] == "travel"
    assert card["destination"] == "Barcelona"
    assert card["days"][0]["activities"][0]["icon"] == "📸"
    assert card["days"][1]["activities"] == []


async def test_travel_packing_list() -> None:
    tool = TravelTool()
    result = await tool.execute(
        action="packing_list",
        nights=5,
        trip_type=["beach", "rain"],
    )
    assert "Swimsuit" in result
    assert "Rain jacket" in result
    assert "× 6" in result


# -- MCP-guidance tools ----------------------------------------------------------------


async def test_home_documents_presentations_stubs() -> None:
    home = await SmartHomeTool().execute(action="lights", request="off")
    assert "Settings → Services" in home
    docs = await DocumentsTool().execute(action="find", query="taxes")
    assert "Settings → Services" in docs
    slides = await PresentationsTool().execute(action="create", topic="Q3")
    assert "Settings → Services" in slides


async def test_presentations_outline_works_offline() -> None:
    result = await PresentationsTool().execute(
        action="outline",
        topic="Dog Parks",
        slides=[
            {"title": "Why dog parks", "bullets": ["exercise", "socializing"]},
            {"title": "Best practices"},
        ],
    )
    assert "Slide 1: Why dog parks" in result
    assert "• exercise" in result
    assert "Slide 2: Best practices" in result


def test_home_tool_is_hidden_for_weekend_alpha() -> None:
    assert SmartHomeTool.enabled(None) is False
