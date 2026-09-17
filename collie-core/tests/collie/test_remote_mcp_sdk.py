"""Exercise the installed MCP SDK against deterministic HTTP protocol responses."""

import asyncio
import json

import httpx
import pytest

from collie_core.connectors import remote
from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import InstalledConnectorDefinition
from collie_core.db import CollieDB
from collie_core.services.credentials import CredentialStore
from nanobot.agent.tools import mcp as runtime
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig


class EventStream(httpx.AsyncByteStream):
    def __init__(self, session_id):
        self.session_id = session_id
        self.messages = asyncio.Queue()

    async def __aiter__(self):
        yield f"event: endpoint\ndata: /messages?session_id={self.session_id}\n\n".encode()
        while True:
            message = await self.messages.get()
            yield f"event: message\ndata: {json.dumps(message)}\n\n".encode()


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
def test_unknown_server_real_sdk_discovers_all_pages_and_calls_tool(
    tmp_path, monkeypatch, transport
):
    requests = []
    streams = {}

    def respond(request):
        if request.method == "GET":
            if transport == "sse":
                session_id = str(len(streams))
                stream = EventStream(session_id)
                streams[session_id] = stream
                return httpx.Response(
                    200, headers={"Content-Type": "text/event-stream"}, stream=stream
                )
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        requests.append(message)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Unknown server", "version": "1.0"},
            }
        elif method == "tools/list":
            second = message.get("params", {}).get("cursor") == "next-page"
            result = {
                "tools": [
                    {
                        "name": "read_second" if second else "read_first",
                        "description": "Read a value",
                        "inputSchema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
            if not second:
                result["nextCursor"] = "next-page"
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": message["params"]["name"]}]}
        else:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Method not supported"},
                },
            )
        response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        if transport == "sse":
            streams[request.url.params["session_id"]].messages.put_nowait(response)
            return httpx.Response(202)
        return httpx.Response(200, json=response)

    monkeypatch.setattr(remote, "validate_remote_endpoint", lambda *a, **kw: ("8.8.8.8",))
    monkeypatch.setattr(
        remote,
        "_http_client_kwargs",
        lambda *a, **kw: {
            "transport": httpx.MockTransport(respond),
            "trust_env": False,
        },
    )
    store = CredentialStore(tmp_path / "secrets", protect=lambda b: b, unprotect=lambda b: b)
    with CollieDB(tmp_path / "collie.db") as db:
        manager = ConnectorManager(db, credentials=store)
        definition = InstalledConnectorDefinition.from_dict(
            {
                "id": "input",
                "driver": "custom_mcp",
                "transport": transport,
                "auth_strategy": "none",
                "provenance": "custom",
                "endpoint": "https://example.test/mcp",
            }
        )
        connection_id = manager.connect_definition(definition)["connection_id"]
        assert {t["remote_tool_name"] for t in db.list_connector_tools(connection_id)} == {
            "read_first",
            "read_second",
        }
        assert set(manager.get_connection(connection_id)["tool_policy"].values()) == {"change"}

        async def use():
            registry = ToolRegistry()
            configs = {
                name: MCPServerConfig.model_validate(config)
                for name, config in manager.mcp_servers_for_config().items()
            }
            connections = await runtime.connect_mcp_servers(configs, registry)
            try:
                assert set(connections) == {connection_id}
                tool = registry.get(f"mcp_{connection_id}_read_second")
                assert tool is not None
                assert not tool.read_only
                assert tool.permission_request({}) is not None
                assert "read_second" in await tool.execute()
            finally:
                for connection in connections.values():
                    await connection.aclose()

        asyncio.run(use())
        manager.remove(connection_id)
        assert manager.mcp_servers_for_config() == {}
    assert any(request["method"] == "tools/call" for request in requests)
    assert sum(request["method"] == "initialize" for request in requests) == 2
