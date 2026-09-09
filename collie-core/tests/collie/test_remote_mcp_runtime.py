"""Runtime attachment and account isolation for custom remote MCP connections."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
    ProbeResult,
)
from collie_core.db import CollieDB
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig


def _installed(endpoint: str, transport: ConnectorTransport) -> InstalledConnectorDefinition:
    return InstalledConnectorDefinition(
        id="custom-definition",
        driver=ConnectorDriverKind.CUSTOM_MCP,
        transport=transport,
        auth_strategy=ConnectorAuthStrategy.NONE,
        provenance=ConnectorProvenance.CUSTOM,
        endpoint=endpoint,
    )


class _Driver:
    def connect_and_probe(self, definition, connection_id: str) -> ProbeResult:
        return ProbeResult(
            tools=[
                {
                    "name": "lookup",
                    "description": "Look up an item",
                    "inputSchema": {"type": "object", "properties": {"item": {"type": "string"}}},
                    "schema_hash": "lookup-schema-v1",
                    "input_schema": {"type": "object", "properties": {"item": {"type": "string"}}},
                    "risk": "read",
                }
            ]
        )


class _Session:
    def __init__(self, account: str) -> None:
        self.account = account
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(
            tools=[
                types.Tool(
                    name="lookup",
                    description="Look up an item",
                    inputSchema={"type": "object", "properties": {"item": {"type": "string"}}},
                )
            ],
            nextCursor=None,
        )

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[types.TextContent(type="text", text=f"{self.account}:{arguments['item']}")],
            isError=False,
        )

    async def list_resources(self):
        return SimpleNamespace(resources=[])

    async def list_prompts(self):
        return SimpleNamespace(prompts=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [ConnectorTransport.STREAMABLE_HTTP, ConnectorTransport.SSE])
async def test_custom_manager_connection_attaches_and_executes_selected_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: ConnectorTransport,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, driver_factory=lambda _definition: _Driver())
    connected = manager.connect_definition(
        _installed(f"https://{transport.value}.example/mcp", transport)
    )
    second = manager.connect_definition(
        _installed(f"https://other-{transport.value}.example/mcp", transport)
    )
    config = manager.mcp_servers_for_config()
    assert connected["connection_id"] in config
    assert second["connection_id"] in config
    assert connected["connection_id"] != second["connection_id"]
    server_name = connected["connection_id"]
    cfg = MCPServerConfig.model_validate(config[server_name])
    assert cfg.type == ("sse" if transport is ConnectorTransport.SSE else "streamableHttp")

    sessions: dict[str, _Session] = {}

    @asynccontextmanager
    async def fake_session(endpoint: str, **kwargs):
        session = _Session(endpoint)
        sessions[endpoint] = session
        yield session

    monkeypatch.setattr("nanobot.agent.tools.mcp._probe_http_url", lambda _url: _async_true())
    monkeypatch.setattr("collie_core.connectors.remote.remote_mcp_session", fake_session)
    registry = ToolRegistry()
    configs = {name: MCPServerConfig.model_validate(value) for name, value in config.items()}
    stacks = await connect_mcp_servers(configs, registry)
    try:
        assert set(stacks) == set(configs)
        tool = registry.get(f"mcp_{server_name}_lookup")
        assert tool is not None
        assert tool.read_only is False
        result = await tool.execute(item="chosen")
        assert f"{cfg.url}:chosen" in result
        assert sessions[cfg.url].calls == [("lookup", {"item": "chosen"})]
        other_name = second["connection_id"]
        other_tool = registry.get(f"mcp_{other_name}_lookup")
        assert other_tool is not None
        assert other_tool.permission_request({}).resource != tool.permission_request({}).resource
        other_cfg = configs[other_name]
        assert sessions[other_cfg.url].calls == []
        assert f"{other_cfg.url}:other" in await other_tool.execute(item="other")
        assert sessions[cfg.url].calls == [("lookup", {"item": "chosen"})]
    finally:
        for stack in stacks.values():
            await stack.aclose()
    db.close()


async def _async_true() -> bool:
    return True
