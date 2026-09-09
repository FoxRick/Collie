from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest
import websockets

from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
)
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _open(server: CollieIPCServer):
    ws = await websockets.connect(f"ws://127.0.0.1:{server.port}")
    assert json.loads(await ws.recv())["type"] == "ready"
    return ws


async def _receive_id(ws, request_id: str) -> dict[str, Any]:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 3))
        if frame.get("id") == request_id:
            return frame


@pytest.mark.asyncio
async def test_same_socket_can_cancel_a_blocked_connector_begin(tmp_path: Path) -> None:
    started = threading.Event()
    released = threading.Event()

    class Manager:
        view: dict[str, Any] | None = None

        def connect(self, provider_id, credentials, **kwargs):
            connection_id = kwargs["connection_id"]
            operation_id = kwargs["operation_id"]
            self.view = {
                "id": connection_id,
                "status": "authorizing",
                "operation_id": operation_id,
                "operation_revision": 0,
            }
            started.set()
            released.wait(2)
            raise ValueError("Sign-in was cancelled.")

        def get_connection(self, connection_id):
            return self.view if self.view and self.view["id"] == connection_id else None

        def cancel_auth(self, connection_id, **kwargs):
            assert kwargs["operation_id"] == self.view["operation_id"]
            self.view = {**self.view, "status": "failed", "operation_revision": 1}
            released.set()
            return {"connection_id": connection_id, "cancelled": True, "operation_revision": 1}

    db = CollieDB(tmp_path / "collie.db")
    server = CollieIPCServer(db, port=_port(), service_manager=Manager())
    await server.start()
    try:
        ws = await _open(server)
        await ws.send(
            json.dumps({"type": "begin_connector_auth", "id": "begin", "provider_id": "fake"})
        )
        while True:
            event = json.loads(await ws.recv())
            if event.get("type") == "connector_auth_started":
                break
        assert started.is_set()
        await ws.send(
            json.dumps(
                {
                    "type": "cancel_connector_auth",
                    "id": "cancel",
                    "connection_id": event["connection_id"],
                    "operation_id": event["operation_id"],
                    "operation_revision": event["operation_revision"],
                }
            )
        )
        reply = await _receive_id(ws, "cancel")
        assert reply["type"] == "ok" and reply["data"]["cancelled"] is True
        await ws.close()
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_import_preview_never_echoes_inline_secret_and_default_inspect_is_bounded(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db)
    server = CollieIPCServer(db, port=_port(), service_manager=manager)
    await server.start()
    try:
        ws = await _open(server)
        secret = "never-echo-this-token"
        source = {"mcpServers": {"demo": {"url": "https://example.com/mcp", "token": secret}}}
        await ws.send(
            json.dumps({"type": "preview_connector_import", "id": "preview", "source": source})
        )
        preview = await _receive_id(ws, "preview")
        assert preview["type"] == "ok"
        assert secret not in json.dumps(preview)

        db.upsert_connector_connection(
            "con_tools",
            provider_id="custom",
            driver="custom_mcp",
            auth_type="none",
            status="connected",
        )
        db.replace_connector_tools(
            "con_tools",
            [
                {
                    "name": "search_notes",
                    "schema_hash": "hash-1",
                    "risk": "read",
                    "description": "Search notes",
                    "input_schema": {"type": "object"},
                }
            ],
        )
        await ws.send(
            json.dumps(
                {"type": "list_connector_tools", "id": "tools", "connection_id": "con_tools"}
            )
        )
        tools = (await _receive_id(ws, "tools"))["data"]
        assert tools["connection_id"] == "con_tools" and tools["total"] == 1
        assert tools["tools"][0]["name"] == "search_notes"
        assert tools["tools"][0]["input_schema"] == {"type": "object"}
        await ws.close()
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_remove_suppresses_late_custom_begin_completion(tmp_path: Path) -> None:
    released = threading.Event()

    class Manager:
        view: dict[str, Any] | None = None

        def begin_auth(self, definition_id, **kwargs):
            self.view = {
                "id": kwargs["connection_id"],
                "status": "authorizing",
                "operation_id": kwargs["operation_id"],
                "operation_revision": 0,
            }
            released.wait(2)
            return {
                "connection_id": kwargs["connection_id"],
                "status": "connected",
                "operation_id": kwargs["operation_id"],
                "operation_revision": 0,
            }

        def get_connection(self, connection_id):
            return self.view if self.view and self.view["id"] == connection_id else None

        def remove(self, connection_id, **kwargs):
            self.view = None
            released.set()
            return {
                "connection_id": connection_id,
                "status": "disconnected",
                "operation_id": kwargs["operation_id"],
                "operation_revision": 1,
                "remote_revocation": "not_applicable",
            }

    db = CollieDB(tmp_path / "collie.db")
    db.save_connector_definition(
        InstalledConnectorDefinition(
            id="def_test",
            driver=ConnectorDriverKind.CUSTOM_MCP,
            transport=ConnectorTransport.STREAMABLE_HTTP,
            auth_strategy=ConnectorAuthStrategy.NONE,
            provenance=ConnectorProvenance.CUSTOM,
            endpoint="https://example.com/mcp",
        )
    )
    server = CollieIPCServer(db, port=_port(), service_manager=Manager())
    await server.start()
    try:
        ws = await _open(server)
        await ws.send(
            json.dumps(
                {"type": "begin_definition_auth", "id": "begin", "definition_id": "def_test"}
            )
        )
        started = None
        while started is None:
            frame = json.loads(await ws.recv())
            if frame.get("type") == "connector_auth_started":
                started = frame
        await ws.send(
            json.dumps(
                {
                    "type": "remove_connector",
                    "id": "remove",
                    "connection_id": started["connection_id"],
                    "operation_revision": started["operation_revision"],
                }
            )
        )
        removed = await _receive_id(ws, "remove")
        assert removed["type"] == "ok"
        terminal_types: list[str] = []
        try:
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 0.15))
                if frame.get("type") in {"connector_connected", "connector_failed"}:
                    terminal_types.append(frame["type"])
        except TimeoutError:
            pass
        assert terminal_types == []
        await ws.close()
    finally:
        await server.stop()
        db.close()
