"""Tests for the starter conversation (greeting seed, name capture, commands)."""

from __future__ import annotations

from pathlib import Path

import pytest

from collie_core.commands import CommandController, parse_command
from collie_core.db import CollieDB
from collie_core.memory.profile import PROFILE_KEYS, ProfileStore
from collie_core.onboarding import (
    STARTER_GREETING,
    capture_starter_name,
    ensure_starter_conversation,
    is_starter_conversation,
)


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    instance = CollieDB(tmp_path / "collie.db")
    yield instance
    instance.close()


@pytest.fixture()
def profile(db: CollieDB, tmp_path: Path) -> ProfileStore:
    return ProfileStore(db, tmp_path / "workspace")


def test_profile_keys_include_name() -> None:
    assert PROFILE_KEYS["name"] == "Name"


def test_ensure_starter_creates_and_seeds_greeting(db: CollieDB) -> None:
    result = ensure_starter_conversation(db)
    conv_id = str(result["conversation"]["id"])
    assert result["greeted"] is True
    assert result["conversation"]["title"] == "Getting started"
    assert result["greeting"] is not None
    messages = db.get_messages(conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert "Hey, welcome! I'm Collie" in messages[0]["content"]
    assert STARTER_GREETING in messages[0]["content"]
    # The conversation is tracked so later calls are idempotent.
    assert is_starter_conversation(db, conv_id)


def test_ensure_starter_is_idempotent_no_second_greeting(db: CollieDB) -> None:
    first = ensure_starter_conversation(db)
    second = ensure_starter_conversation(db)
    assert second["greeted"] is False
    assert second["greeting"] is None
    assert second["conversation"]["id"] == first["conversation"]["id"]
    assert len(db.get_messages(str(first["conversation"]["id"]))) == 1


def test_ensure_starter_recreates_after_delete(db: CollieDB) -> None:
    first = ensure_starter_conversation(db)
    db.delete_conversation(str(first["conversation"]["id"]))
    second = ensure_starter_conversation(db)
    assert second["greeted"] is True
    assert second["conversation"]["id"] != first["conversation"]["id"]


def test_ensure_starter_reuses_empty_conversation(db: CollieDB) -> None:
    empty = db.create_conversation(title="New chat")
    result = ensure_starter_conversation(db, reuse_conversation_id=str(empty["id"]))
    assert result["conversation"]["id"] == empty["id"]
    assert result["greeted"] is True
    # A conversation with messages is never reused.
    busy = db.create_conversation(title="Busy")
    db.add_message(str(busy["id"]), "user", "hello")
    fresh = ensure_starter_conversation(db, reuse_conversation_id=str(busy["id"]))
    assert fresh["conversation"]["id"] != busy["id"]


def test_capture_starter_name_stores_first_reply(db: CollieDB, profile: ProfileStore) -> None:
    starter = ensure_starter_conversation(db)
    conv_id = str(starter["conversation"]["id"])
    assert capture_starter_name(db, profile, conv_id, "Rick") is True
    assert profile.get("name") == "Rick"
    # MEMORY.md carries the name for the model's bootstrap context.
    assert "Name" in profile.memory_file.read_text(encoding="utf-8")
    # Second reply does not overwrite the name.
    assert capture_starter_name(db, profile, conv_id, "Not Rick") is False
    assert profile.get("name") == "Rick"


def test_capture_starter_name_only_in_starter_thread(db: CollieDB, profile: ProfileStore) -> None:
    other = db.create_conversation(title="Other")
    assert capture_starter_name(db, profile, str(other["id"]), "Rick") is False
    assert profile.get("name") is None


def test_capture_starter_name_never_forces_long_or_empty(
    db: CollieDB, profile: ProfileStore
) -> None:
    starter = ensure_starter_conversation(db)
    conv_id = str(starter["conversation"]["id"])
    assert capture_starter_name(db, profile, conv_id, "") is False
    assert capture_starter_name(db, profile, conv_id, "   ") is False
    long_reply = "I'd rather not say right now, but you can call me later maybe, once we know each other better"
    assert len(long_reply) > 64
    assert capture_starter_name(db, profile, conv_id, long_reply) is False
    assert profile.get("name") is None
    # A multi-line answer is not a name either.
    assert capture_starter_name(db, profile, conv_id, "Rick\nand also Sam") is False


def test_capture_starter_name_requires_only_greeting_before_reply(
    db: CollieDB, profile: ProfileStore
) -> None:
    starter = ensure_starter_conversation(db)
    conv_id = str(starter["conversation"]["id"])
    db.add_message(conv_id, "user", "Hi")
    # The thread already has a real user message → this is not the first reply.
    assert capture_starter_name(db, profile, conv_id, "Rick") is False
    assert profile.get("name") is None


def test_parse_command_recognizes_get_started() -> None:
    assert parse_command("/get-started") == ("get-started", "")
    assert parse_command("/start") == ("start", "")


def _controller() -> CommandController:
    return CommandController(
        workspace=Path("/tmp"),
        subagent_loader=None,
        loop_provider=lambda: None,
        status_provider=lambda: {"model": "test"},
    )


@pytest.mark.asyncio
async def test_desktop_get_started_returns_starter_flag() -> None:
    result = await _controller().execute(
        "/get-started", session_key="s", origin="desktop"
    )
    assert result == {"handled": True, "starter_conversation": True, "content": ""}


@pytest.mark.asyncio
async def test_desktop_start_returns_starter_flag() -> None:
    result = await _controller().execute("/start", session_key="s", origin="desktop")
    assert result is not None
    assert result["starter_conversation"] is True


@pytest.mark.asyncio
async def test_messenger_start_keeps_command_menu() -> None:
    result = await _controller().execute("/start", session_key="s", origin="telegram")
    assert result is not None
    assert result.get("starter_conversation") is None
    assert "Here are my commands:" in str(result["content"])


@pytest.mark.asyncio
async def test_messenger_get_started_keeps_command_menu() -> None:
    result = await _controller().execute("/get-started", session_key="s", origin="telegram")
    assert result is not None
    assert result.get("starter_conversation") is None
    assert "Getting started lives in the Collie desktop app" in str(result["content"])
