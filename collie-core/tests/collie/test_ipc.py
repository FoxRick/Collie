"""Tests for the Collie IPC server (real WebSocket round-trips)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest
import websockets

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.ipc.thinking import phrase_for_state, thinking_state_for_tool
from collie_core.permissions.broker import ApprovalBroker
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, PermissionRequest, Risk
from collie_core.permissions.store import PermissionStore
from nanobot.security.workspace_access import (
    clear_live_local_file_scope,
    live_local_file_scope,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeOutbound:
    def __init__(self, content: str):
        self.content = content


async def fake_chat_runner(content: str, *, conversation_id: str, on_stream, on_progress):
    await on_progress("", tool_events=[{"phase": "start", "name": "web_search"}])
    for chunk in ("Hi! ", "Here you go."):
        await on_stream(chunk)
    return FakeOutbound("Hi! Here you go.")


@pytest.fixture()
async def server(tmp_path: Path):
    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        on_set_api_key=lambda provider, key: captured_keys.append((provider, key)),
        status_provider=lambda: {"configured": True, "model": "test-model"},
    )
    captured_keys: list[tuple[str, str]] = []
    srv.captured_keys = captured_keys  # type: ignore[attr-defined]
    await srv.start()
    yield srv
    await srv.stop()
    db.close()


async def _connect(srv: CollieIPCServer):
    ws = await websockets.connect(f"ws://127.0.0.1:{srv.port}")
    ready = json.loads(await ws.recv())
    assert ready["type"] == "ready"
    assert "getting ready" in ready["phrase"]
    return ws


async def _send(ws, **frame: Any) -> None:
    await ws.send(json.dumps(frame))


async def _recv_until(ws, kind: str, timeout: float = 5.0) -> dict:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if frame["type"] == kind:
            return frame


async def _wait_for_thread_event(event: threading.Event) -> None:
    for _ in range(200):
        if event.is_set():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("OAuth worker did not reach the expected point")


class _RecordingConnection:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.frames.append(json.loads(raw))


@pytest.mark.asyncio
async def test_ping_and_status(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await _send(ws, type="ping", id="1")
    reply = await _recv_until(ws, "ok")
    assert reply["id"] == "1"
    assert reply["data"]["pong"] is True

    await _send(ws, type="get_status", id="2")
    reply = await _recv_until(ws, "ok")
    assert reply["data"]["model"] == "test-model"
    await ws.close()


@pytest.mark.asyncio
async def test_unknown_and_invalid_frames(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await ws.send("not json")
    err = await _recv_until(ws, "error")
    assert "invalid JSON" in err["message"]

    await _send(ws, type="rob_a_bank", id="9")
    err = await _recv_until(ws, "error")
    assert "unknown command" in err["message"]
    await ws.close()


@pytest.mark.asyncio
async def test_conversation_crud(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await _send(ws, type="new_conversation", id="1", title="Groceries")
    conv = (await _recv_until(ws, "ok"))["data"]

    await _send(ws, type="list_conversations", id="2")
    convs = (await _recv_until(ws, "ok"))["data"]["conversations"]
    assert [c["title"] for c in convs] == ["Groceries"]

    await _send(ws, type="rename_conversation", id="3",
                conversation_id=conv["id"], title="Weekly shop")
    await _recv_until(ws, "ok")

    await _send(ws, type="delete_conversation", id="4", conversation_id=conv["id"])
    await _recv_until(ws, "ok")

    await _send(ws, type="list_conversations", id="5")
    convs = (await _recv_until(ws, "ok"))["data"]["conversations"]
    assert convs == []
    await ws.close()


@pytest.mark.asyncio
async def test_settings_roundtrip(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await _send(ws, type="set_setting", id="1", key="provider.name", value="openrouter")
    await _recv_until(ws, "ok")
    await _send(ws, type="get_settings", id="2")
    settings = (await _recv_until(ws, "ok"))["data"]["settings"]
    assert settings["provider.name"] == "openrouter"
    await ws.close()


@pytest.mark.asyncio
async def test_set_file_access_scope_applies_live_override(
    server: CollieIPCServer, tmp_path: Path
) -> None:
    ws = await _connect(server)
    await _send(ws, type="new_conversation", id="1")
    conv = (await _recv_until(ws, "ok"))["data"]

    granted = tmp_path / "granted"
    granted.mkdir()
    await _send(
        ws,
        type="set_file_access_scope",
        id="2",
        conversation_id=conv["id"],
        file_access_scope={"mode": "chosen_folders", "roots": [str(granted)]},
    )
    data = (await _recv_until(ws, "ok"))["data"]
    assert data["applied"] is True
    assert data["file_access_scope"] == {
        "mode": "chosen_folders",
        "roots": [str(granted.resolve())],
    }
    try:
        assert live_local_file_scope(conv["id"]) == ((granted.resolve(),), False)
    finally:
        clear_live_local_file_scope(conv["id"])
    await ws.close()


@pytest.mark.asyncio
async def test_set_file_access_scope_requires_existing_conversation(
    server: CollieIPCServer,
) -> None:
    ws = await _connect(server)
    await _send(
        ws,
        type="set_file_access_scope",
        id="1",
        conversation_id="missing-conversation",
        file_access_scope={"mode": "selected_folder"},
    )
    error = await _recv_until(ws, "error")
    assert "no longer exists" in error["message"]
    await ws.close()


@pytest.mark.asyncio
async def test_set_api_key_never_persisted(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await _send(ws, type="set_api_key", id="1", provider="openai", key="sk-secret")
    await _recv_until(ws, "ok")
    assert server.captured_keys == [("openai", "sk-secret")]  # type: ignore[attr-defined]
    assert "sk-secret" not in json.dumps(server.db.all_settings())
    await ws.close()


@pytest.mark.asyncio
async def test_repeated_secret_injection_matches_custom_provider_case_insensitively(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    db.upsert_provider(
        "api-Phase 9 Local",
        name="Phase 9 Local",
        auth_type="api-key",
        model="phase9-local-model",
        runtime_name="custom",
        protocol="openai",
        api_base="http://127.0.0.1:39080/v1",
        secret_name="Phase 9 Local",
        is_default=True,
    )
    original = db.get_provider("api-Phase 9 Local")
    injected: list[tuple[str, str]] = []
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        on_set_api_key=lambda provider, key: injected.append((provider, key)),
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        for request_id in ("restart-1", "restart-2"):
            await _send(
                ws,
                type="set_api_key",
                id=request_id,
                provider="phase 9 local",
                key="restart-secret",
            )
            await _recv_until(ws, "ok")

        assert db.list_providers() == [original]
        assert db.default_provider() == original
        assert injected == [
            ("phase 9 local", "restart-secret"),
            ("phase 9 local", "restart-secret"),
        ]
        assert "restart-secret" not in json.dumps(db.all_settings())
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_repeated_oauth_click_replaces_only_matching_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.providers import auth as collie_auth

    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(db)
    connection = _RecordingConnection()
    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    attempts: list[Any] = []
    commits: list[int] = []

    class FakeAttempt:
        def __init__(self, provider: str) -> None:
            self.provider = provider
            self.index = len(attempts)
            self.cancelled = False
            attempts.append(self)

        def run(self) -> dict[str, Any]:
            started[self.index].set()
            assert release[self.index].wait(2)
            return {"provider": self.provider, "signed_in": True}

        def cancel(self) -> None:
            self.cancelled = True

        def commit(self) -> bool:
            if self.cancelled:
                return False
            commits.append(self.index)
            return True

        def discard(self) -> None:
            return None

    monkeypatch.setattr(collie_auth, "OAuthLoginAttempt", FakeAttempt)

    await srv._cmd_oauth_login(connection, {"id": "first", "provider": "chatgpt"})  # type: ignore[arg-type]
    await _wait_for_thread_event(started[0])
    await srv._cmd_oauth_login(connection, {"id": "second", "provider": "chatgpt"})  # type: ignore[arg-type]
    await _wait_for_thread_event(started[1])
    await asyncio.sleep(0)

    assert srv._oauth_attempts["chatgpt"].generation == 2
    stale_cancel = await srv._cmd_cancel_oauth(
        connection,  # type: ignore[arg-type]
        {"provider": "chatgpt", "generation": 1},
    )
    assert stale_cancel == {"cancelled": False}
    release[0].set()
    await asyncio.sleep(0.02)
    assert srv._oauth_attempts["chatgpt"].generation == 2

    release[1].set()
    for _ in range(200):
        if "chatgpt" not in srv._oauth_attempts:
            break
        await asyncio.sleep(0.005)

    assert "chatgpt" not in srv._oauth_attempts
    assert commits == [1]
    assert db.get_setting("provider.auth") == "chatgpt-oauth"
    assert db.default_provider()["id"] == "oauth-chatgpt"
    first = next(frame for frame in connection.frames if frame.get("id") == "first")
    second = next(frame for frame in connection.frames if frame.get("id") == "second")
    assert first["type"] == "error"
    assert second["type"] == "ok"
    assert second["data"]["generation"] == 2
    db.close()


@pytest.mark.asyncio
async def test_cancel_oauth_discards_late_completion_and_provider_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.providers import auth as collie_auth

    db = CollieDB(tmp_path / "collie.db")
    db.set_setting("provider.auth", "api-key")
    db.set_setting("provider.name", "deepseek")
    srv = CollieIPCServer(db)
    connection = _RecordingConnection()
    started = threading.Event()
    release = threading.Event()
    commits: list[bool] = []

    class FakeAttempt:
        def __init__(self, provider: str) -> None:
            self.provider = provider
            self.cancelled = False

        def run(self) -> dict[str, Any]:
            started.set()
            assert release.wait(2)
            return {"provider": self.provider, "signed_in": True}

        def cancel(self) -> None:
            self.cancelled = True

        def commit(self) -> bool:
            commits.append(True)
            return not self.cancelled

        def discard(self) -> None:
            return None

    monkeypatch.setattr(collie_auth, "OAuthLoginAttempt", FakeAttempt)

    await srv._cmd_oauth_login(connection, {"id": "login", "provider": "claude"})  # type: ignore[arg-type]
    await _wait_for_thread_event(started)
    cancelled = await srv._cmd_cancel_oauth(
        connection,  # type: ignore[arg-type]
        {"provider": "claude", "generation": 1},
    )
    assert cancelled == {
        "cancelled": True,
        "attempt_id": "claude:1",
        "generation": 1,
    }
    release.set()
    await asyncio.sleep(0.05)

    assert "claude" not in srv._oauth_attempts
    assert commits == []
    assert db.get_setting("provider.auth") == "api-key"
    assert db.get_setting("provider.name") == "deepseek"
    assert any(
        frame.get("id") == "login" and frame["type"] == "error"
        for frame in connection.frames
    )
    db.close()


@pytest.mark.asyncio
async def test_provider_add_switch_and_delete(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    forgotten: list[str] = []

    async def configure() -> dict[str, Any]:
        return {"configured": True, "model": db.get_setting("provider.model")}

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        on_configure=configure,
        on_delete_api_key=forgotten.append,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        for provider_id, name, model, is_default in (
            ("api-openai", "openai", "gpt-test", True),
            ("api-deepseek", "deepseek", "deepseek-test", False),
        ):
            await _send(
                ws,
                type="upsert_provider",
                id=provider_id,
                provider_id=provider_id,
                name=name,
                auth_type="api-key",
                model=model,
                is_default=is_default,
            )
            await _recv_until(ws, "ok")

        await _send(
            ws,
            type="activate_provider",
            id="activate",
            provider_id="api-deepseek",
        )
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["configured"] is True
        assert db.get_setting("provider.name") == "deepseek"
        assert db.get_setting("provider.model") == "deepseek-test"

        await _send(
            ws,
            type="delete_provider",
            id="delete",
            provider_id="api-deepseek",
        )
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["default_provider"]["id"] == "api-openai"
        assert db.default_provider()["id"] == "api-openai"
        assert db.get_setting("provider.name") == "openai"
        assert forgotten == ["deepseek"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_api_provider_activation_auto_finalizes_candidate_transaction(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    db.upsert_provider(
        "api-openai",
        name="openai",
        auth_type="api-key",
        model="gpt-test",
        secret_name="openai",
    )
    finalized: list[str] = []

    async def configure_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        assert "api_key" not in candidate
        return {
            "provider": db.get_provider("api-openai"),
            "configured": True,
            "transaction_id": "activate-tx",
        }

    async def finalize(transaction_id: str) -> dict[str, Any]:
        finalized.append(transaction_id)
        return {"finalized": True}

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        on_configure_provider_candidate=configure_candidate,
        on_finalize_provider_candidate=finalize,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="activate_provider", id="activate", provider_id="api-openai")
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["configured"] is True
        assert "transaction_id" not in reply["data"]
        assert finalized == ["activate-tx"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_configure_provider_candidate_command_delegates_whole_candidate(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    captured: list[dict[str, Any]] = []

    async def configure_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        captured.append(candidate)
        return {"configured": False, "error": "test rejection", "rolled_back": True}

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        on_configure_provider_candidate=configure_candidate,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="configure_provider_candidate",
            id="candidate",
            provider_id="api-work",
            name="work",
            auth_type="api-key",
            model="model-one",
            runtime_name="custom",
            protocol="openai",
            api_base="https://models.example.test/v1",
            secret_name="work",
            api_key="candidate-secret",
        )
        reply = await _recv_until(ws, "ok")
        assert reply["data"] == {
            "configured": False,
            "error": "test rejection",
            "rolled_back": True,
        }
        assert captured == [{
            "provider_id": "api-work",
            "name": "work",
            "auth_type": "api-key",
            "model": "model-one",
            "runtime_name": "custom",
            "protocol": "openai",
            "api_base": "https://models.example.test/v1",
            "secret_name": "work",
            "api_key": "candidate-secret",
        }]
        assert "candidate-secret" not in json.dumps(db.all_settings())
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_custom_anthropic_endpoint_persists_runtime_fields(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")

    async def configure() -> dict[str, Any]:
        return {"configured": True}

    srv = CollieIPCServer(db, port=_free_port(), on_configure=configure)
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="upsert_provider",
            id="custom",
            provider_id="api-Work Gateway",
            name="Work Gateway",
            auth_type="api-key",
            protocol="anthropic",
            api_base="https://models.example.test",
            secret_name="Work Gateway",
            model="company-model",
            is_default=True,
        )
        await _recv_until(ws, "ok")
        assert db.get_setting("provider.name") == "anthropic"
        assert db.get_setting("provider.api_base") == "https://models.example.test"
        assert db.get_setting("provider.secret_name") == "Work Gateway"
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_chat_persists_project_and_generates_title(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    project = tmp_path / "Project Atlas"
    project.mkdir()

    async def title_generator(_: str) -> str:
        return "Design Project Navigation"

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        title_generator=title_generator,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="chat",
            id="chat",
            content="Could you improve how project navigation works?",
            project_path=str(project),
        )
        ack = await _recv_until(ws, "ok")
        conversation_id = ack["data"]["conversation_id"]

        updated = await _recv_until(ws, "conversation_updated")
        assert updated["conversation"]["title"] == "Design Project Navigation"
        conversation = db.get_conversation(conversation_id)
        assert conversation["project_path"] == str(project.resolve())
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_chat_full_flow(server: CollieIPCServer) -> None:
    ws = await _connect(server)
    await _send(ws, type="chat", id="1", content="fetch me good news")

    ack = await _recv_until(ws, "ok")
    conv_id = ack["data"]["conversation_id"]
    assert ack["data"]["message"]["role"] == "user"

    seen_states: list[str] = []
    deltas: list[str] = []
    final_msg = None
    while final_msg is None:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if frame["type"] == "thinking":
            seen_states.append(frame["state"])
        elif frame["type"] == "delta":
            deltas.append(frame["text"])
        elif frame["type"] == "message" and frame["message"]["role"] == "assistant":
            final_msg = frame["message"]

    assert "processing" in seen_states
    assert "searching" in seen_states       # web_search tool event
    assert "generating" in seen_states      # streaming started
    assert seen_states[-1] == "done"
    assert "".join(deltas) == "Hi! Here you go."
    assert final_msg["content"] == "Hi! Here you go."

    # Conversation auto-titled from first message + both messages persisted
    convs = server.db.list_conversations()
    assert convs[0]["title"] == "Fetch me good news"
    msgs = server.db.get_messages(conv_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    await ws.close()


async def steer_superseding_chat_runner(
    content: str,
    *,
    conversation_id: str,
    on_stream,
    on_progress,
    on_superseded_response,
    **kwargs,
):
    """Simulate a turn where a mid-turn steer supersedes the in-flight answer."""
    await on_stream("The in-flight answer that was superseded.")
    await on_superseded_response("The in-flight answer that was superseded.")
    await on_stream("The follow-up answer.")
    return FakeOutbound("The follow-up answer.")


@pytest.mark.asyncio
async def test_chat_steer_delivers_superseded_answer_as_own_message(tmp_path: Path) -> None:
    """A superseded in-flight answer lands in the transcript as its own message.

    Reproduces the reported bug: messaging Collie while it composes causes the
    composed answer to disappear (only the follow-up answer is persisted).
    """
    db = CollieDB(tmp_path / "c.db")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=steer_superseding_chat_runner,
        status_provider=lambda: {"configured": True, "model": "test-model"},
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="chat", id="1", content="first question")

        ack = await _recv_until(ws, "ok")
        conv_id = ack["data"]["conversation_id"]

        assistant_messages: list[dict] = []
        while len(assistant_messages) < 2:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if frame["type"] == "message" and frame["message"]["role"] == "assistant":
                assistant_messages.append(frame["message"])

        # Both answers were broadcast in chronological order.
        assert [m["content"] for m in assistant_messages] == [
            "The in-flight answer that was superseded.",
            "The follow-up answer.",
        ]
        # And both were persisted — the superseded answer is no longer lost.
        db_msgs = db.get_messages(conv_id)
        assert [m["role"] for m in db_msgs] == ["user", "assistant", "assistant"]
        assert [m["content"] for m in db_msgs if m["role"] == "assistant"] == [
            "The in-flight answer that was superseded.",
            "The follow-up answer.",
        ]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_chat_without_runner_reports_friendly_error(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "c.db")
    srv = CollieIPCServer(db, port=_free_port(), chat_runner=None)
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="chat", id="1", content="hello")
        err = await _recv_until(ws, "error")
        assert "provider" in err["message"].lower()
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_chat_runner_exception_is_friendly(tmp_path: Path) -> None:
    async def broken_runner(content, *, conversation_id, on_stream, on_progress):
        raise RuntimeError("boom")

    db = CollieDB(tmp_path / "c.db")
    srv = CollieIPCServer(db, port=_free_port(), chat_runner=broken_runner)
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="chat", id="1", content="hello")
        await _recv_until(ws, "ok")
        err = await _recv_until(ws, "error")
        assert "didn't go as planned" in err["message"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_service_commands_roundtrip(tmp_path: Path) -> None:
    from collie_core.services.credentials import CredentialStore
    from collie_core.services.manager import ServiceManager

    db = CollieDB(tmp_path / "c.db")
    manager = ServiceManager(
        db, credentials=CredentialStore(tmp_path / "creds"), platform="win32"
    )
    configured: list[bool] = []

    async def on_configure():
        configured.append(True)
        return {"configured": True}

    srv = CollieIPCServer(
        db, port=_free_port(), service_manager=manager, on_configure=on_configure
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="list_services", id="1")
        services = (await _recv_until(ws, "ok"))["data"]["services"]
        assert {s["id"] for s in services} >= {"gmail", "todoist", "outlook"}
        assert not any(service["available"] for service in services)

        await _send(ws, type="connect_service", id="2", service_id="todoist",
                    credentials={"todoist_token": "tok-1"})
        err = await _recv_until(ws, "error")
        assert err["id"] == "2"
        assert "coming soon" in err["message"].lower()
        assert configured == []

        await _send(ws, type="connect_service", id="3", service_id="gmail",
                    credentials={})
        err = await _recv_until(ws, "error")
        assert err["id"] == "3"

        await _send(ws, type="disconnect_service", id="4", service_id="todoist")
        reply = (await _recv_until(ws, "ok"))["data"]
        assert reply["status"] == "disconnected"

        await _send(ws, type="list_services", id="5")
        services = (await _recv_until(ws, "ok"))["data"]["services"]
        by_id = {s["id"]: s for s in services}
        assert by_id["todoist"]["status"] == "coming_soon"
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_cancel_subagent_ipc(tmp_path: Path) -> None:
    cancelled: list[str] = []

    async def fake_canceler(session_key: str) -> int:
        cancelled.append(session_key)
        return 3

    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        subagent_canceler=fake_canceler,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="cancel_subagent", id="1", conversation_id="conv-abc")
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["cancelled"] == 3
        assert cancelled == ["conv-abc"]

        await _send(ws, type="cancel_subagent", id="2", conversation_id="")
        err = await _recv_until(ws, "error")
        assert "conversation_id" in err["message"].lower()
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_stop_cancels_all_subagents_for_conversation(tmp_path: Path) -> None:
    cancelled: list[str] = []

    async def fake_canceler(session_key: str) -> int:
        cancelled.append(session_key)
        return 2

    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        subagent_canceler=fake_canceler,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="stop", id="1", conversation_id="conv-abc")
        reply = await _recv_until(ws, "ok")
        assert reply["data"] == {
            "stopped": True,
            "cancelled_subagents": 2,
            "cancelled_approvals": 0,
        }
        assert cancelled == ["conv-abc"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_steer_active_chat_persists_and_broadcasts_user_message(
    tmp_path: Path,
) -> None:
    steered: list[tuple[str, str]] = []

    async def fake_steerer(conversation_id: str, content: str) -> bool:
        steered.append((conversation_id, content))
        return True

    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation()
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=fake_chat_runner,
        chat_steerer=fake_steerer,
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="steer",
            id="1",
            conversation_id=conversation["id"],
            content="Focus on the mobile layout.",
        )
        message = await _recv_until(ws, "message")
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["accepted"] is True
        assert message["message"]["role"] == "user"
        assert message["message"]["content"] == "Focus on the mobile layout."
        assert steered == [(conversation["id"], "Focus on the mobile layout.")]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


def test_thinking_phrases_complete() -> None:
    for state in ("searching", "planning", "fetching", "generating", "processing",
                  "summarizing", "recovering", "done", "error", "idle", "startup"):
        payload = phrase_for_state(state)
        assert payload["phrase"]
        assert payload["pet_animation"]

    assert thinking_state_for_tool("web_search") == "searching"
    assert thinking_state_for_tool("web_fetch") == "fetching"
    assert thinking_state_for_tool("remember") == "remembering"
    assert thinking_state_for_tool("mcp_gmail_list") == "fetching"
    assert thinking_state_for_tool("whatever") == "processing"


# -- B1: IPC authentication ---------------------------------------------------


async def _expect_rejected(url: str, **connect_kwargs: Any) -> int:
    """Connect and assert the handshake is refused; return the status code."""
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
        await websockets.connect(url, **connect_kwargs)
    return exc_info.value.response.status_code


@pytest.mark.asyncio
async def test_token_mode_rejects_anonymous_and_wrong_tokens(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(db, port=_free_port(), token="boot-secret-123")
    await srv.start()
    try:
        url = f"ws://127.0.0.1:{srv.port}"
        assert await _expect_rejected(url) == 401
        assert (
            await _expect_rejected(url, subprotocols=["collie-wrong"]) == 401
        )
        ws = await websockets.connect(url, subprotocols=["collie-boot-secret-123"])
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready"
        await _send(ws, type="ping", id="1")
        assert (await _recv_until(ws, "ok"))["data"]["pong"] is True
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_origin_check_rejects_foreign_pages(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(db, port=_free_port())
    await srv.start()
    try:
        url = f"ws://127.0.0.1:{srv.port}"
        assert (
            await _expect_rejected(url, origin="https://evil.example") == 403
        )
        ws = await websockets.connect(url, origin="http://localhost:5173")
        assert json.loads(await ws.recv())["type"] == "ready"
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_set_setting_whitelist_blocks_core_managed_keys(
    server: CollieIPCServer,
) -> None:
    ws = await _connect(server)
    await _send(
        ws,
        type="set_setting",
        id="1",
        key="permissions.local_write_preset",
        value="allow",
    )
    err = await _recv_until(ws, "error")
    assert err["id"] == "1"
    assert "managed by Collie" in err["message"]
    assert server.db.get_setting("permissions.local_write_preset", "ask") == "ask"
    await ws.close()


@pytest.mark.asyncio
async def test_approval_preset_applies_to_next_evaluation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    evaluator = PermissionEvaluator(PermissionStore(db), local_write_preset="ask")
    srv = CollieIPCServer(
        db,
        port=_free_port(),
        on_set_approval_preset=evaluator.set_local_write_preset,
    )
    request = PermissionRequest(
        action="file.write",
        resource="C:/work/report.txt",
        risk=Risk.LOCAL_WRITE,
        summary="Write report",
        reversible=True,
        approve_for_me=True,
    )
    await srv.start()
    try:
        assert evaluator.evaluate(ExecutionContext(), request).effect == Effect.ASK
        ws = await _connect(srv)
        await _send(ws, type="set_approval_preset", id="preset", preset="allow")
        reply = await _recv_until(ws, "ok")
        assert reply["data"]["preset"] == "allow"
        assert db.get_setting("permissions.local_write_preset") == "allow"
        assert evaluator.evaluate(ExecutionContext(), request).effect == Effect.ALLOW
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_public_allow_run_ignores_renderer_scope_identity(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    evaluator = PermissionEvaluator(PermissionStore(db))
    srv = CollieIPCServer(db, port=_free_port())
    srv.approval_broker = ApprovalBroker(db, evaluator)
    request = db.create_approval_request(
        action="file.write",
        resource="C:/work/report.txt",
        risk="local_write",
        display={"summary": "Write report", "approve_for_me_eligible": True},
        run_id="trusted-run",
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="resolve_approval",
            id="resolve",
            approval_id=request["id"],
            resolution="allow_run",
            scope_type="global",
            scope_value="renderer-selected",
        )
        await _recv_until(ws, "ok")
        rules = db.list_approval_rules()
        assert len(rules) == 1
        assert rules[0]["scope_type"] == "run"
        assert rules[0]["scope_value"] == "trusted-run"
        assert rules[0]["resource_pattern"] == "C:/work/report.txt"
        await ws.close()
    finally:
        await srv.stop()
        db.close()


@pytest.mark.asyncio
async def test_upsert_provider_rejects_non_http_api_base(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    srv = CollieIPCServer(db, port=_free_port())
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(
            ws,
            type="upsert_provider",
            id="1",
            provider_id="api-evil",
            name="evil",
            auth_type="api-key",
            api_base="file:///C:/Windows/System32",
        )
        err = await _recv_until(ws, "error")
        assert "http(s)" in err["message"]
        assert db.get_provider("api-evil") is None
        await ws.close()
    finally:
        await srv.stop()
        db.close()


# -- C1: conversation deletion --------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation_cancels_turn_and_cleans_related_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a conversation cancels its in-flight turn and removes plans,
    runs, approval requests, and session files alongside the messages."""
    home = tmp_path / "home"
    monkeypatch.setenv("COLLIE_HOME", str(home))

    session_files: list[str] = []

    class FakeSessions:
        def delete_session(self, key: str) -> bool:
            session_files.append(key)
            return True

    class FakeLoop:
        sessions = FakeSessions()

    async def slow_runner(content, *, conversation_id, on_stream, on_progress):
        await asyncio.sleep(30)  # never finishes on its own

    db = CollieDB(home / "collie.db")
    conv = db.create_conversation("Doomed")
    conv_id = conv["id"]
    db.create_plan(
        title="P",
        goal="G",
        plan={"steps": [{"key": "s1", "title": "S1"}]},
        conversation_id=conv_id,
        plan_id="plan-x",
    )
    run = db.create_run(
        trigger_type="plan_approval",
        idempotency_key="plan:plan-x:v1",
        plan_id="plan-x",
        plan_version=1,
        conversation_id=conv_id,
    )
    db.create_approval_request(
        action="web_fetch", resource="http://x", risk="read",
        display={"url": "http://x"}, conversation_id=conv_id,
    )

    cancelled: list[str] = []

    async def fake_canceler(session_key: str) -> int:
        cancelled.append(session_key)
        return 0

    srv = CollieIPCServer(
        db,
        port=_free_port(),
        chat_runner=slow_runner,
        subagent_canceler=fake_canceler,
        conversation_deleter=(lambda cid: session_files.append(f"session:{cid}")),
    )
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="chat", id="1", content="start a long turn",
                    conversation_id=conv_id)
        await _recv_until(ws, "ok")

        await _send(ws, type="delete_conversation", id="2", conversation_id=conv_id)
        await _recv_until(ws, "ok")

        assert cancelled == [conv_id]
        assert db.get_conversation(conv_id) is None
        assert db.get_messages(conv_id) == []
        assert db.get_run(run["id"]) is None
        assert db.get_plan("plan-x", 1) is None
        assert db.list_pending_approvals() == []
        # The runtime deleter was invoked for the conversation session.
        assert any(f"session:{conv_id}" in item or item == f"collie:{conv_id}"
                   for item in session_files)
        # The in-flight task slot was released.
        assert conv_id not in srv._chat_tasks
        await ws.close()
    finally:
        await srv.stop()
        db.close()


# -- C4: plan approval idempotency ----------------------------------------------


@pytest.mark.asyncio
async def test_approve_plan_is_idempotent(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conv = db.create_conversation("Plan chat")
    db.create_plan(
        title="P", goal="G",
        plan={"steps": [{"key": "s1", "title": "S1"}]},
        conversation_id=conv["id"],
        plan_id="plan-y",
    )
    srv = CollieIPCServer(db, port=_free_port(), chat_runner=fake_chat_runner)
    await srv.start()
    try:
        ws = await _connect(srv)
        await _send(ws, type="approve_plan", id="1", plan_id="plan-y",
                    version=1, plan_hash=db.get_plan("plan-y", 1)["plan_hash"])
        first = await _recv_until(ws, "ok")

        await _send(ws, type="approve_plan", id="2", plan_id="plan-y",
                    version=1, plan_hash=db.get_plan("plan-y", 1)["plan_hash"])
        duplicate = await _recv_until(ws, "ok")
        assert duplicate["data"]["created"] is False
        assert duplicate["data"]["run"]["id"] == first["data"]["run"]["id"]
        runs = db.list_runs(limit=10)
        assert [r["trigger_type"] for r in runs] == ["plan_approval"]
        await ws.close()
    finally:
        await srv.stop()
        db.close()


# -- C5: message limits -----------------------------------------------------------


def test_get_messages_limit_semantics(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "limited.db")
    conv = db.create_conversation("Limited")
    for i in range(5):
        db.add_message(conv["id"], "user", f"m{i}")
    assert len(db.get_messages(conv["id"], limit=3)) == 3
    assert db.get_messages(conv["id"], limit=0) == []
    assert len(db.get_messages(conv["id"])) == 5
    db.close()


# -- B2: workspace escape ------------------------------------------------------


@pytest.mark.asyncio
async def test_read_write_file_rejects_escape_paths(
    server: CollieIPCServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from collie_core import ipc as ipc_pkg

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path))
    # Force the module-level collie_home() lookup to re-read the env.
    monkeypatch.setattr(ipc_pkg.server, "collie_home", lambda: tmp_path)

    ws = await _connect(server)
    good = workspace / "notes" / "idea.md"
    await _send(ws, type="write_file", id="1", path="notes/idea.md", content="hello")
    await _recv_until(ws, "ok")
    assert good.read_text(encoding="utf-8") == "hello"

    escapes = ["../outside.txt", "..\\outside.txt", "/Windows/evil.txt",
               "C:/Windows/evil.txt", "C:evil.txt", "//server/share/evil.txt"]
    for index, path in enumerate(escapes):
        await _send(ws, type="read_file", id=f"r{index}", path=path)
        err = await _recv_until(ws, "error")
        assert "Invalid path" in err["message"], path
    for index, path in enumerate(escapes):
        await _send(ws, type="write_file", id=f"w{index}", path=path, content="pwn")
        err = await _recv_until(ws, "error")
        assert "Invalid path" in err["message"], path

    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "Windows" / "evil.txt").exists()
    await ws.close()
