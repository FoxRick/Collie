"""Tests for the ProfileStore and the remember tool."""

from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.memory.profile import ProfileStore
from collie_core.tools import memory as memory_tool_module
from collie_core.tools.memory import RememberTool, bind_profile_store


@pytest.fixture()
def store(tmp_path: Path) -> ProfileStore:
    db = CollieDB(tmp_path / "collie.db")
    s = ProfileStore(db, tmp_path / "workspace")
    yield s
    db.close()
    memory_tool_module._profile_store = None


def test_set_fact_regenerates_memory_md(store: ProfileStore) -> None:
    store.set("dietary", "vegetarian, allergic to peanuts")
    text = store.memory_file.read_text(encoding="utf-8")
    assert "Food & allergies" in text
    assert "allergic to peanuts" in text


def test_person_and_dates_render(store: ProfileStore) -> None:
    mom = store.add_person("Mom", relationship="mother",
                           preferences="gardening books, red wine")
    store.add_date("03-15", "Mom's birthday", recurring=True, person_id=mom["id"])
    text = store.memory_file.read_text(encoding="utf-8")
    assert "**Mom** (mother)" in text
    assert "Likes: gardening books, red wine" in text
    assert "**03-15**: Mom's birthday (Mom) — every year" in text


def test_add_person_merges_by_name(store: ProfileStore) -> None:
    store.add_person("Alex", relationship="partner")
    store.add_person("Alex", allergies="shellfish")
    people = store.list_people()
    assert len(people) == 1
    assert people[0]["relationship"] == "partner"
    assert people[0]["allergies"] == "shellfish"


def test_empty_memory_md(store: ProfileStore) -> None:
    store.regenerate_memory_md()
    text = store.memory_file.read_text(encoding="utf-8")
    assert "Nothing here yet" in text


@pytest.mark.asyncio
async def test_remember_tool_fact(store: ProfileStore) -> None:
    bind_profile_store(store)
    tool = RememberTool()
    result = await tool.execute(kind="fact", key="Wake Time", value="07:00")
    assert "Remembered" in str(result)
    assert store.get("wake_time") == "07:00"


@pytest.mark.asyncio
async def test_remember_tool_person_creates_birthday_date(store: ProfileStore) -> None:
    bind_profile_store(store)
    tool = RememberTool()
    await tool.execute(kind="person", name="Mom", relationship="mother",
                       birthday="03-15", gift_ideas="rose pruning set")
    person = store.find_person("Mom")
    assert person["gift_ideas"] == "rose pruning set"
    dates = store.list_dates()
    assert len(dates) == 1
    assert dates[0]["label"] == "Mom's birthday"
    assert dates[0]["recurring"] == 1

    # Remembering again must not duplicate the birthday date
    await tool.execute(kind="person", name="Mom", notes="lives nearby")
    assert len(store.list_dates()) == 1


@pytest.mark.asyncio
async def test_remember_tool_forget(store: ProfileStore) -> None:
    bind_profile_store(store)
    tool = RememberTool()
    await tool.execute(kind="fact", key="goals", value="learn Spanish")
    await tool.execute(kind="forget_fact", key="goals")
    assert store.get("goals") is None

    await tool.execute(kind="person", name="Sam")
    result = await tool.execute(kind="forget_person", name="Sam")
    assert "Forgot" in str(result)
    assert store.find_person("Sam") is None


@pytest.mark.asyncio
async def test_remember_tool_errors(store: ProfileStore) -> None:
    bind_profile_store(store)
    tool = RememberTool()
    result = await tool.execute(kind="fact")
    assert getattr(result, "is_error", False)
    result = await tool.execute(kind="nope")
    assert getattr(result, "is_error", False)
    result = await tool.execute(kind="forget_person", name="Ghost")
    assert getattr(result, "is_error", False)


@pytest.mark.asyncio
async def test_remember_tool_disabled_without_store(tmp_path: Path) -> None:
    memory_tool_module._profile_store = None
    assert RememberTool.enabled(None) is False
    tool = RememberTool()
    result = await tool.execute(kind="fact", key="a", value="b")
    assert getattr(result, "is_error", False)


def test_tool_loader_discovers_collie_tools(store: ProfileStore) -> None:
    import collie_core.tools as collie_tools
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.registry import ToolRegistry

    bind_profile_store(store)
    loader = ToolLoader(collie_tools)
    registry = ToolRegistry()

    class _Ctx:
        config = None
        workspace = "."

    registered = loader.load(_Ctx(), registry)
    assert "remember" in registered
