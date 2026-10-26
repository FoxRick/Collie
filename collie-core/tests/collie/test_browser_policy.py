"""Controller race/authority and bounded file transfer regression checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from collie_core.browser.service import BrowserCommand, BrowserService, BrowserSession, origin
from collie_core.browser.transfers import LIMIT, download, selected_path
from collie_core.permissions.models import ExecutionContext
from collie_core.tools.browser import BrowserTool


def test_no_arbitrary_code_selectors_keys_or_paths_in_agent_schema():
    schema = BrowserTool().parameters
    assert not {"selector", "javascript", "file_path"} & schema["properties"].keys()
    assert not {"upload", "download"} & set(schema["properties"]["operation"]["enum"])
    for command in (
        {"operation": "click", "selector": "#x"},
        {"operation": "key", "key": "Enter"},
        {"operation": "evaluate", "text": "x"},
        {"operation": "open", "url": "x", "unknown": True},
    ):
        with pytest.raises(ValidationError):
            BrowserCommand.model_validate(command)
    for url in ("file:///secret", "http://user:secret@localhost", "edge://settings"):
        with pytest.raises(ValueError):
            origin(url)


@pytest.mark.asyncio
async def test_queued_action_does_not_restart_stopped_worker():
    auth = SimpleNamespace(authorize=None)
    service = BrowserService(auth)
    session = BrowserSession("owned", "chat", "run", "http://localhost")
    service.sessions[session.id] = session
    await session.lock.acquire()
    task = asyncio.create_task(
        service.execute(
            ExecutionContext(conversation_id="chat", run_id="run"),
            {"operation": "observe", "session_id": "owned"},
        )
    )
    await asyncio.sleep(0)
    await service.execute(
        ExecutionContext(conversation_id="chat"), {"operation": "close", "session_id": "owned"}
    )
    session.lock.release()
    with pytest.raises(ValueError, match="stopped"):
        await task
    assert session.process is None


@pytest.mark.asyncio
async def test_task_scope_rechecked_after_lock():
    service = BrowserService(None)
    session = BrowserSession("owned", "chat", "run", "http://localhost")
    service.sessions[session.id] = session
    await session.lock.acquire()
    task = asyncio.create_task(
        service.execute(
            ExecutionContext(conversation_id="chat", run_id="run"),
            {"operation": "observe", "session_id": "owned"},
        )
    )
    await asyncio.sleep(0)
    session.task_id = "new-run"
    session.lock.release()
    with pytest.raises(ValueError, match="another task"):
        await task
    assert session.process is None


def test_transfer_path_and_size_boundaries(tmp_path):
    for path in ("relative.txt", r"\\server\share\file.txt", str(tmp_path / "bad.exe")):
        with pytest.raises(ValueError):
            selected_path(path, upload=False)
    source = tmp_path / "large.txt"
    source.write_bytes(b"x" * (LIMIT + 1))
    with pytest.raises(ValueError, match="6 MB"):
        selected_path(str(source), upload=True)
    existing = tmp_path / "existing.txt"
    existing.write_text("preserve")
    with pytest.raises(ValueError, match="never overwrite"):
        selected_path(str(existing), upload=False)
    with pytest.raises(ValueError, match="destinations"):
        download(
            "https://unapproved.invalid/file.txt", str(tmp_path / "new.txt"), {"http://localhost"}
        )
    assert existing.read_text() == "preserve"


def test_streamed_download_limit_and_atomic_commit(tmp_path, monkeypatch):
    import io

    from collie_core.browser import transfers

    class Response(io.BytesIO):
        headers = {}

    class Opener:
        def __init__(self, content):
            self.content = content

        def open(self, *_args, **_kwargs):
            return Response(self.content)

    destination = tmp_path / "bounded.txt"
    monkeypatch.setattr(transfers, "build_opener", lambda *_args: Opener(b"x" * (LIMIT + 1)))
    with pytest.raises(ValueError, match="6 MB"):
        download("http://localhost/file.txt", str(destination), {"http://localhost"})
    assert not destination.exists()
    assert not list(tmp_path.glob("*.partial"))
    monkeypatch.setattr(transfers, "build_opener", lambda *_args: Opener(b"complete"))
    assert download("http://localhost/file.txt", str(destination), {"http://localhost"}) == 8
    assert destination.read_bytes() == b"complete"
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.asyncio
async def test_broker_cancellation_during_notification_clears_approval(tmp_path):
    from collie_core.db import CollieDB
    from collie_core.permissions.broker import ApprovalBroker
    from collie_core.permissions.evaluator import PermissionEvaluator

    db = CollieDB(tmp_path / "cancel.db")
    started = asyncio.Event()

    async def blocked_notification(event):
        if event["type"] == "approval_requested":
            started.set()
            await asyncio.Future()

    broker = ApprovalBroker(db, PermissionEvaluator(), blocked_notification)
    service = BrowserService(broker)
    session = BrowserSession("owned", "chat", "run", "http://localhost")
    pending = asyncio.create_task(
        service._authorize(
            ExecutionContext(conversation_id="chat", run_id="run"), session, {"operation": "click"}
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not db.list_pending_approvals()
        assert not broker._pending
    finally:
        db.close()


@pytest.mark.asyncio
async def test_destination_grants_are_bounded():
    service = BrowserService(None)
    session = BrowserSession("owned", "chat", "run", "http://localhost")
    session.destinations = {f"https://site{i}.invalid" for i in range(8)}
    service.sessions[session.id] = session
    with pytest.raises(ValueError, match="eight"):
        await service.execute(
            ExecutionContext(conversation_id="chat", run_id="run"),
            {
                "operation": "approve_destination",
                "session_id": "owned",
                "url": "https://ninth.invalid",
            },
        )
    assert session.process is None
