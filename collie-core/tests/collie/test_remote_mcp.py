from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from collie_core.connectors.drivers.remote_mcp import RemoteMcpDriver
from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
)
from collie_core.connectors.remote import (
    _request_validator,
    discover_all_tools,
    validate_remote_endpoint,
)


def _definition(**changes):
    values = {
        "id": "custom",
        "driver": ConnectorDriverKind.CUSTOM_MCP,
        "transport": ConnectorTransport.STREAMABLE_HTTP,
        "auth_strategy": ConnectorAuthStrategy.NONE,
        "provenance": ConnectorProvenance.CUSTOM,
        "endpoint": "https://mcp.example/mcp",
    }
    values.update(changes)
    return InstalledConnectorDefinition(**values)


@pytest.mark.asyncio
async def test_discovery_follows_all_pages() -> None:
    calls = []

    class Session:
        async def list_tools(self, cursor=None):
            calls.append(cursor)
            if cursor is None:
                return SimpleNamespace(
                    tools=[SimpleNamespace(name="one", inputSchema={})],
                    nextCursor="second",
                )
            return SimpleNamespace(
                tools=[SimpleNamespace(name="two", inputSchema={})],
                nextCursor=None,
            )

    assert [tool.name for tool in await discover_all_tools(Session())] == ["one", "two"]
    assert calls == [None, "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "cursor", "malformed"])
async def test_discovery_rejects_invalid_or_nonterminating_results(failure: str) -> None:
    class Session:
        async def list_tools(self, cursor=None):
            if failure == "malformed":
                return SimpleNamespace(tools=None, nextCursor=None)
            if failure == "cursor":
                return SimpleNamespace(tools=[], nextCursor="repeat")
            return SimpleNamespace(
                tools=[SimpleNamespace(name="same", inputSchema={})],
                nextCursor="again" if cursor is None else None,
            )

    with pytest.raises(ValueError):
        await discover_all_tools(Session(), max_pages=3)


@pytest.mark.asyncio
async def test_discovery_timeout_cancels_request() -> None:
    cancelled = asyncio.Event()

    class Session:
        async def list_tools(self, cursor=None):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    with pytest.raises(TimeoutError):
        await discover_all_tools(Session(), timeout=0.01)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_discovery_rejects_normalized_name_collisions() -> None:
    class Session:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name="search", inputSchema={}),
                    SimpleNamespace(name="_search", inputSchema={}),
                ],
                nextCursor=None,
            )

    with pytest.raises(ValueError, match="collide"):
        await discover_all_tools(Session())


def test_private_opt_in_is_limited_to_selected_origin(monkeypatch) -> None:
    def resolve(host, *_args, **_kwargs):
        address = "192.168.1.20" if host.endswith("example") else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        validate_remote_endpoint("http://device.example/mcp")
    assert validate_remote_endpoint("http://device.example/mcp", allow_private_network=True) == (
        "192.168.1.20",
    )
    with pytest.raises(ValueError):
        validate_remote_endpoint(
            "http://device.example/mcp",
            "http://redirect.example/mcp",
            allow_private_network=True,
        )


@pytest.mark.asyncio
async def test_static_credentials_cannot_follow_cross_origin_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("1.1.1.1",)
    )
    validate = _request_validator(
        "https://mcp.example/mcp",
        allow_private_network=False,
        has_static_credentials=True,
    )
    with pytest.raises(httpx.RequestError):
        await validate(httpx.Request("GET", "https://other.example/mcp"))


def test_custom_driver_rejects_auth_and_trust_overrides() -> None:
    driver = RemoteMcpDriver()
    with pytest.raises(ValueError, match="auth strategy"):
        driver.probe(_definition(auth_strategy=ConnectorAuthStrategy.TOKEN), "connection")
    with pytest.raises(ValueError, match="trusted tool overrides"):
        driver.probe(_definition(tool_overrides={"send": "read"}), "connection")


def test_custom_driver_supports_explicit_sse(monkeypatch) -> None:
    observed = {}

    @asynccontextmanager
    async def session(endpoint, **kwargs):
        observed.update(endpoint=endpoint, **kwargs)
        yield SimpleNamespace()

    async def discover(_session):
        return []

    monkeypatch.setattr("collie_core.connectors.drivers.remote_mcp.remote_mcp_session", session)
    monkeypatch.setattr("collie_core.connectors.drivers.remote_mcp.discover_all_tools", discover)
    result = RemoteMcpDriver().probe(
        _definition(transport=ConnectorTransport.SSE, allow_private_network=True), "connection"
    )
    assert result.tools == []
    assert observed["transport"] == "sse"
    assert observed["allow_private_network"] is True
