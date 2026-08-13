import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer, _safe_preview_data_url


def test_message_attachments_roundtrip(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation()
    attachments = [{"name": "notes.txt", "mime": "text/plain", "size": 12}]

    created = db.add_message(
        conversation["id"],
        "user",
        "Summarize this",
        attachments=attachments,
    )
    stored = db.get_messages(conversation["id"])[0]

    assert created["attachments"] == attachments
    assert stored["attachments"] == attachments
    assert db.schema_version == 14


@pytest.mark.asyncio
async def test_attachment_only_chat_reaches_agent_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    received_media: list[str] = []
    runner_called = asyncio.Event()

    async def runner(content, *, conversation_id, on_stream, on_progress, media):
        assert content == ""
        received_media.extend(media)
        runner_called.set()
        return SimpleNamespace(content="I read the file.")

    class Connection:
        def __init__(self) -> None:
            self.frames: list[dict] = []

        async def send(self, payload: str) -> None:
            self.frames.append(json.loads(payload))

    db = CollieDB(tmp_path / "collie.db")
    server = CollieIPCServer(db, chat_runner=runner)
    connection = Connection()
    encoded = base64.b64encode(b"hello from a file").decode("ascii")

    await server._cmd_chat(
        connection,
        {
            "id": "attach-1",
            "content": "",
            "attachments": [
                {
                    "name": "notes.txt",
                    "mime": "text/plain",
                    "size": 17,
                    "data_url": f"data:text/plain;base64,{encoded}",
                }
            ],
        },
    )
    await asyncio.wait_for(runner_called.wait(), timeout=2)

    assert len(received_media) == 1
    assert Path(received_media[0]).read_text(encoding="utf-8") == "hello from a file"
    conversation_id = connection.frames[0]["data"]["conversation_id"]
    user_message = db.get_messages(conversation_id)[0]
    assert user_message["attachments"][0]["name"] == "notes.txt"


@pytest.mark.asyncio
async def test_safe_image_preview_roundtrips_through_chat_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    runner_called = asyncio.Event()

    async def runner(content, *, conversation_id, on_stream, on_progress, media):
        runner_called.set()
        return SimpleNamespace(content="I see the screenshot.")

    class Connection:
        def __init__(self) -> None:
            self.frames: list[dict] = []

        async def send(self, payload: str) -> None:
            self.frames.append(json.loads(payload))

    db = CollieDB(tmp_path / "collie.db")
    server = CollieIPCServer(db, chat_runner=runner)
    connection = Connection()
    image_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
        "AAAAASUVORK5CYII="
    )
    preview_data_url = f"data:image/png;base64,{image_base64}"

    await server._cmd_chat(
        connection,
        {
            "id": "image-1",
            "content": "What is in this?",
            "attachments": [
                {
                    "name": "screenshot.png",
                    "mime": "image/png",
                    "size": 68,
                    "data_url": preview_data_url,
                    "preview_data_url": preview_data_url,
                }
            ],
        },
    )
    await asyncio.wait_for(runner_called.wait(), timeout=2)

    conversation_id = connection.frames[0]["data"]["conversation_id"]
    echoed_attachment = connection.frames[0]["data"]["message"]["attachments"][0]
    user_message = db.get_messages(conversation_id)[0]
    assert echoed_attachment["preview_data_url"] == preview_data_url
    assert user_message["attachments"][0]["name"] == "screenshot.png"
    assert user_message["attachments"][0]["preview_data_url"] == preview_data_url

    restored = await server._cmd_get_messages(
        connection,
        {"conversation_id": conversation_id},
    )
    assert restored["messages"][0]["attachments"][0]["preview_data_url"] == preview_data_url


@pytest.mark.parametrize(
    ("mime", "preview_data_url"),
    [
        ("image/png", "data:image/png;base64,%%%"),
        (
            "image/png",
            "data:image/png;base64," + base64.b64encode(b"x" * (256 * 1024 + 1)).decode("ascii"),
        ),
        ("image/png", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="),
        ("text/plain", "data:image/png;base64,iVBORw0KGgo="),
    ],
    ids=["malformed", "oversized", "unsafe-preview-mime", "non-image"],
)
def test_unsafe_attachment_previews_are_omitted(
    mime: str,
    preview_data_url: str,
) -> None:
    attachment = {"mime": mime, "preview_data_url": preview_data_url}

    assert _safe_preview_data_url(attachment) is None


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp", "image/gif"])
def test_safe_raster_preview_mimes_are_accepted(mime: str) -> None:
    preview = f"data:{mime};base64,eA=="

    assert _safe_preview_data_url({"mime": mime, "preview_data_url": preview}) == preview


def test_rasterized_png_preview_is_accepted_for_another_safe_image_mime() -> None:
    preview = "data:image/png;base64,eA=="

    assert _safe_preview_data_url({"mime": "image/jpeg", "preview_data_url": preview}) == preview
