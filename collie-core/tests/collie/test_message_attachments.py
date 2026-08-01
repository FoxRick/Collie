import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer


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
    assert db.schema_version == 10


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
            "attachments": [{
                "name": "notes.txt",
                "mime": "text/plain",
                "size": 17,
                "data_url": f"data:text/plain;base64,{encoded}",
            }],
        },
    )
    await asyncio.wait_for(runner_called.wait(), timeout=2)

    assert len(received_media) == 1
    assert Path(received_media[0]).read_text(encoding="utf-8") == "hello from a file"
    conversation_id = connection.frames[0]["data"]["conversation_id"]
    user_message = db.get_messages(conversation_id)[0]
    assert user_message["attachments"][0]["name"] == "notes.txt"
