from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.memory.profile import ProfileStore


@pytest.fixture()
def memory(tmp_path: Path):
    db = CollieDB(tmp_path / "collie.db")
    store = ProfileStore(db, tmp_path / "workspace")
    yield db, store
    db.close()


def test_structured_edits_regenerate_memory_markdown(memory) -> None:
    db, store = memory
    store.set("goals", "Ship the beta")
    person = store.add_person("Alex", relationship="partner")
    date = store.add_date("07-30", "Launch day", recurring=False)

    store.update_person(person["id"], preferences="Strong coffee")
    store.update_date(date["id"], label="Beta launch", recurring=True)

    markdown = (store.workspace / "MEMORY.md").read_text(encoding="utf-8")
    assert "**Current goals**: Ship the beta" in markdown
    assert "Likes: Strong coffee" in markdown
    assert "**07-30**: Beta launch — every year" in markdown
    assert db.list_dates()[0]["recurring"] == 1


@pytest.mark.asyncio
async def test_memory_ipc_commands_use_profile_store(memory) -> None:
    db, store = memory
    server = CollieIPCServer(db, profile_store=store)
    await server._cmd_set_profile_memory(None, {"key": "work", "value": "Designer"})  # type: ignore[arg-type]
    assert store.get("work") == "Designer"

    person_result = await server._cmd_add_person_memory(  # type: ignore[arg-type]
        None,
        {"fields": {"name": "Sam", "relationship": "friend"}},
    )
    person = person_result["person"]
    assert store.get_person(person["id"])["relationship"] == "friend"
    await server._cmd_update_person_memory(  # type: ignore[arg-type]
        None,
        {"person_id": person["id"], "fields": {"relationship": "best friend"}},
    )
    assert store.get_person(person["id"])["relationship"] == "best friend"

    date_result = await server._cmd_add_date_memory(  # type: ignore[arg-type]
        None,
        {"date": "07-30", "label": "Launch day", "recurring": True},
    )
    assert date_result["date"]["recurring"] == 1
    assert store.list_dates()[0]["label"] == "Launch day"

    await server._cmd_delete_profile_memory(None, {"key": "work"})  # type: ignore[arg-type]
    await server._cmd_delete_person_memory(None, {"person_id": person["id"]})  # type: ignore[arg-type]
    assert store.get("work") is None
    assert store.list_people() == []
