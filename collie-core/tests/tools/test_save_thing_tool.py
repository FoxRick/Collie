"""Tests for the ``save_thing`` tool (the user's "Your things")."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import collie_core.tools.artifacts as artifacts_module
from collie_core.things.store import ThingStore
from collie_core.tools.artifacts import SaveThingTool, bind_things
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.loader import ToolLoader
from nanobot.bus.outbound_events import ArtifactEvent, outbound_event_from_message
from nanobot.bus.queue import MessageBus
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)


@pytest.fixture
def scope(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    token = bind_workspace_scope(
        build_workspace_scope(root, "restricted", source_channel="websocket")
    )
    try:
        yield root
    finally:
        reset_workspace_scope(token)


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bus = MessageBus()
    store = ThingStore(root=tmp_path / "things")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(artifacts_module, "get_media_dir", lambda: media)
    bind_things(store=store, bus=bus)
    try:
        yield bus, store, media
    finally:
        bind_things(store=None, bus=None)


@pytest.fixture
def chat_ctx():
    ctx = RequestContext(channel="collie", chat_id="conv-1")
    with request_context(ctx):
        yield ctx


async def _run_tool(scope: Path, **kwargs: Any) -> Any:
    return await SaveThingTool().execute(**kwargs)


async def _next_outbound(bus: MessageBus) -> Any:
    return await asyncio.wait_for(bus.consume_outbound(), timeout=1)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_registers_thing_and_publishes_artifact_event(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "flyer.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="Dog walk flyer", path="flyer.png", kind="image")

    assert not result.is_error
    payload = json.loads(str(result))
    assert payload["title"] == "Dog walk flyer"
    assert payload["kind"] == "image"
    assert payload["conversation_id"] == "conv-1"

    records = store.list("conv-1")
    assert len(records) == 1
    assert records[0]["id"] == payload["thing_id"]
    assert records[0]["path"] == str((scope / "flyer.png").resolve())

    msg = await _next_outbound(bus)
    event = outbound_event_from_message(msg)
    assert isinstance(event, ArtifactEvent)
    assert event.artifact_id == payload["thing_id"]
    assert event.title == "Dog walk flyer"
    assert event.kind == "image"
    assert event.file_path == str((scope / "flyer.png").resolve())
    assert event.size_bytes == 8
    assert msg.channel == "collie"
    assert msg.chat_id == "conv-1"
    assert msg.content == "📎 Made: Dog walk flyer · Open"


async def test_kind_defaults_to_actual_for_unknown_file_kind(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "notes.txt").write_text("hello", encoding="utf-8")

    result = await _run_tool(scope, title="Notes", path="notes.txt", kind="file")

    assert not result.is_error
    assert json.loads(str(result))["kind"] == "document"
    event = outbound_event_from_message(await _next_outbound(bus))
    assert isinstance(event, ArtifactEvent) and event.kind == "document"


# ---------------------------------------------------------------------------
# validation failures
# ---------------------------------------------------------------------------


async def test_kind_mismatch_is_rejected(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "flyer.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="Dog walk flyer", path="flyer.png", kind="document")

    assert result.is_error
    assert "image" in str(result)
    assert store.list("conv-1") == []
    with pytest.raises(asyncio.TimeoutError):
        await _next_outbound(bus)


async def test_missing_file_is_rejected(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired

    result = await _run_tool(scope, title="Ghost", path="ghost.png", kind="image")

    assert result.is_error
    assert store.list("conv-1") == []
    with pytest.raises(asyncio.TimeoutError):
        await _next_outbound(bus)


async def test_directory_is_rejected(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "folder").mkdir()

    result = await _run_tool(scope, title="Folder", path="folder", kind="file")

    assert result.is_error
    assert store.list("conv-1") == []


async def test_path_outside_scope_is_rejected(scope: Path, wired, chat_ctx, tmp_path: Path) -> None:
    bus, store, _media = wired
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.docx").write_text("x", encoding="utf-8")

    result = await _run_tool(
        scope, title="Secret", path=str(outside / "secret.docx"), kind="document"
    )

    assert result.is_error
    assert "outside" in str(result).lower() or "allowed" in str(result).lower()
    assert store.list("conv-1") == []


async def test_media_dir_carve_out_allows_assistant_made_files(
    scope: Path, wired, chat_ctx
) -> None:
    bus, store, media = wired
    made = media / "generated" / "img_abc.png"
    made.parent.mkdir(parents=True)
    made.write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="Generated image", path=str(made), kind="image")

    assert not result.is_error
    assert store.list("conv-1")[0]["path"] == str(made.resolve())


async def test_bad_title_is_rejected(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "flyer.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="   ", path="flyer.png", kind="image")
    assert result.is_error

    result = await _run_tool(scope, title="x" * 200, path="flyer.png", kind="image")
    assert result.is_error

    assert store.list("conv-1") == []


async def test_invalid_kind_value_is_rejected(scope: Path, wired, chat_ctx) -> None:
    bus, store, _media = wired
    (scope / "flyer.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="Flyer", path="flyer.png", kind="movie")

    assert result.is_error
    assert store.list("conv-1") == []


async def test_no_request_context_is_rejected(scope: Path, wired) -> None:
    bus, store, _media = wired
    (scope / "flyer.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _run_tool(scope, title="Flyer", path="flyer.png", kind="image")

    assert result.is_error
    assert store.list("conv-1") == []


# ---------------------------------------------------------------------------
# permission + discovery
# ---------------------------------------------------------------------------


def test_permission_request_is_approval_free_and_reversible(scope: Path) -> None:
    request = SaveThingTool().permission_request(
        {"title": "Dog walk flyer", "path": "flyer.png", "kind": "image"}
    )

    assert request.action == "things.save"
    assert request.approval_free is True
    assert request.hard_approval is False
    assert request.reversible is True
    assert request.data_leaving_device == ()


def test_save_thing_tool_is_discoverable_via_loader() -> None:
    import collie_core.tools as collie_tools

    names = {tool_cls.__name__ for tool_cls in ToolLoader(collie_tools).discover()}
    assert "SaveThingTool" in names
