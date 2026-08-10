"""Tests for the per-conversation "Your things" index (ThingStore)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from collie_core.things.store import ThingStore

RECORD_KW: dict[str, Any] = dict(
    title="Dog walk flyer",
    kind="image",
    path="/tmp/flyer.png",
    size_bytes=2048,
    created_at=1_720_000_000.0,
)


@pytest.fixture
def store(tmp_path: Path) -> ThingStore:
    return ThingStore(root=tmp_path / "things")


def test_register_and_list_roundtrip(store: ThingStore) -> None:
    record = store.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)

    assert record["id"] == "th_a"
    assert record["title"] == "Dog walk flyer"

    listed = store.list("conv-1")
    assert len(listed) == 1
    assert listed[0] == record


def test_new_things_are_prepended_newest_first(store: ThingStore) -> None:
    store.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)
    store.register(
        conversation_id="conv-1",
        artifact_id="th_b",
        **{**RECORD_KW, "title": "Barcelona trip plan", "kind": "document"},
    )

    ids = [record["id"] for record in store.list("conv-1")]
    assert ids == ["th_b", "th_a"]


def test_re_registered_id_replaces_in_place(store: ThingStore) -> None:
    store.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)
    store.register(conversation_id="conv-1", artifact_id="th_b", **{**RECORD_KW, "title": "Second"})
    store.register(
        conversation_id="conv-1",
        artifact_id="th_a",
        **{**RECORD_KW, "title": "Flyer v2", "status": "updated", "version": 2},
    )

    records = store.list("conv-1")
    assert len(records) == 2
    assert records[1]["id"] == "th_a"  # slot position kept
    assert records[1]["title"] == "Flyer v2"
    assert records[1]["version"] == 2


def test_conversations_are_isolated(store: ThingStore) -> None:
    store.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)

    assert store.list("conv-2") == []
    assert store.list("conv-1")[0]["id"] == "th_a"


def test_unknown_conversation_lists_empty(store: ThingStore) -> None:
    assert store.list("conv-ghost") == []


def test_unsafe_conversation_id_raises(store: ThingStore) -> None:
    with pytest.raises(ValueError, match="not safe"):
        store.register(conversation_id="../../etc/passwd", artifact_id="th_a", **RECORD_KW)
    with pytest.raises(ValueError, match="not safe"):
        store.list("conv with spaces")


def test_index_survives_store_reload(tmp_path: Path) -> None:
    root = tmp_path / "things"
    first = ThingStore(root=root)
    first.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)

    reloaded = ThingStore(root=root)
    assert reloaded.list("conv-1")[0]["id"] == "th_a"

    # And the on-disk shape is a {"things": [...]} document.
    payload = json.loads((root / "conv-1.json").read_text(encoding="utf-8"))
    assert payload["things"][0]["title"] == "Dog walk flyer"


def test_corrupt_index_file_is_treated_as_empty(tmp_path: Path) -> None:
    root = tmp_path / "things"
    root.mkdir()
    (root / "conv-1.json").write_text("{not json", encoding="utf-8")

    store = ThingStore(root=root)
    assert store.list("conv-1") == []
    store.register(conversation_id="conv-1", artifact_id="th_a", **RECORD_KW)
    assert store.list("conv-1")[0]["id"] == "th_a"
