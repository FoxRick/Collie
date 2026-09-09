"""Adversarial protocol and endpoint boundaries for remote MCP connectors."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDefinition,
    ConnectorDriverKind,
    ConnectorTransport,
)


def _definition(**overrides: object) -> ConnectorDefinition:
    values: dict[str, object] = {
        "id": "custom",
        "name": "Custom MCP",
        "category": "custom",
        "description": "test server",
        "driver": ConnectorDriverKind.CUSTOM_MCP,
        "auth_type": ConnectorAuthStrategy.NONE.value,
        "endpoint": "https://mcp.example.test/mcp",
        "available": True,
        "transport": ConnectorTransport.STREAMABLE_HTTP,
    }
    values.update(overrides)
    return ConnectorDefinition(**values)


class _PagedSession:
    def __init__(self, pages: list[object]) -> None:
        self.pages = iter(pages)
        self.cursors: list[str | None] = []

    async def list_tools(self, *, cursor: str | None = None) -> object:
        self.cursors.append(cursor)
        return next(self.pages)


@pytest.mark.asyncio
async def test_tool_discovery_follows_cursors_deduplicates_and_bounds_pages() -> None:
    """A hostile server cannot create an unbounded discovery loop or duplicate tools."""
    from collie_core.connectors.remote import discover_all_tools

    session = _PagedSession(
        [
            SimpleNamespace(
                tools=[SimpleNamespace(name="search", description="one", inputSchema={})],
                nextCursor="page-2",
            ),
            SimpleNamespace(
                tools=[
                    SimpleNamespace(name="write", description="two", inputSchema={}),
                ],
                nextCursor=None,
            ),
        ]
    )

    tools = await discover_all_tools(session, max_pages=4)

    assert [tool.name for tool in tools] == ["search", "write"]
    assert session.cursors == [None, "page-2"]

    looping = _PagedSession([SimpleNamespace(tools=[], nextCursor="same")] * 3)
    with pytest.raises(ValueError, match="pagination|cursor|loop|bound"):
        await discover_all_tools(looping, max_pages=2)


@pytest.mark.asyncio
async def test_tool_discovery_rejects_malformed_and_duplicate_names() -> None:
    from collie_core.connectors.remote import discover_all_tools

    session = _PagedSession(
        [
            SimpleNamespace(
                tools=[
                    SimpleNamespace(name="", description="empty"),
                    SimpleNamespace(name="search", description="ok"),
                    SimpleNamespace(name="search", description="duplicate"),
                    SimpleNamespace(name=None, description="wrong type"),
                    object(),
                ],
                nextCursor=None,
            )
        ]
    )

    with pytest.raises(ValueError, match="tool|name|malformed|duplicate"):
        await discover_all_tools(session)


@pytest.mark.asyncio
async def test_tool_discovery_timeout_and_tool_count_are_bounded() -> None:
    from collie_core.connectors.remote import discover_all_tools

    class SlowSession:
        async def list_tools(self, *, cursor: str | None = None) -> object:
            del cursor
            import asyncio

            await asyncio.sleep(0.05)
            return SimpleNamespace(tools=[], nextCursor=None)

    with pytest.raises(TimeoutError):
        await discover_all_tools(SlowSession(), timeout=0.001)

    too_many = _PagedSession(
        [SimpleNamespace(tools=[SimpleNamespace(name="one")], nextCursor=None)]
    )
    with pytest.raises(ValueError, match="bounds|limit"):
        await discover_all_tools(too_many, max_tools=0)


@pytest.mark.parametrize(
    ("endpoint", "allow_private", "allowed"),
    [
        ("https://mcp.example.test/mcp", False, True),
        ("http://127.0.0.1:8765/mcp", False, False),
        ("http://127.0.0.1:8765/mcp", True, True),
        ("http://10.0.0.4/mcp", False, False),
        ("http://10.0.0.4/mcp", True, True),
    ],
)
def test_endpoint_policy_requires_explicit_private_network_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    allow_private: bool,
    allowed: bool,
) -> None:
    import collie_core.connectors.remote as remote

    monkeypatch.setattr(
        remote.socket,
        "getaddrinfo",
        lambda host, *args: [
            (0, 0, 0, "", ("127.0.0.1" if host.startswith(("127.", "10.")) else "8.8.8.8", 0))
        ],
    )
    try:
        remote.validate_remote_endpoint(endpoint, allow_private_network=allow_private)
    except ValueError:
        assert not allowed
    else:
        assert allowed


def test_endpoint_policy_rechecks_redirect_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    import collie_core.connectors.remote as remote

    monkeypatch.setattr(
        remote.socket,
        "getaddrinfo",
        lambda host, *args: [(0, 0, 0, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ValueError, match="Blocked"):
        remote.validate_remote_endpoint(
            "https://mcp.example.test/mcp",
            "http://127.0.0.1:8765/mcp",
        )
    # A private opt-in applies only to the exact selected origin.
    with pytest.raises(ValueError, match="Blocked"):
        remote.validate_remote_endpoint(
            "http://127.0.0.1:8765/mcp",
            "http://127.0.0.2:8765/mcp",
            allow_private_network=True,
        )


def test_remote_driver_rejects_unsupported_auth_before_network() -> None:
    from collie_core.connectors.drivers.remote_mcp import RemoteMcpDriver

    definition = _definition(auth_type="oauth")
    with pytest.raises(ValueError, match="auth strategy|no-auth"):
        RemoteMcpDriver().connect_and_probe(definition, "connection-1")


@pytest.mark.asyncio
async def test_request_transport_revalidates_each_redirect_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-request hook must remain active even when HTTPX follows redirects."""
    import collie_core.connectors.remote as remote

    seen: list[str] = []

    def validate(initial: str, candidate: str | None = None, **kwargs: object) -> tuple[str, ...]:
        del initial, kwargs
        seen.append(candidate or "")
        if candidate and "127.0.0.1" in candidate:
            raise ValueError("Blocked remote MCP endpoint: private address")
        return ("203.0.113.10",)

    class Inner:
        async def handle_async_request(self, request: object) -> object:
            return SimpleNamespace(status_code=200)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(remote, "validate_remote_endpoint", validate)
    monkeypatch.setattr(remote.httpx, "AsyncHTTPTransport", Inner)
    transport = remote.RemoteEndpointTransport("https://mcp.example.test/mcp")
    request = httpx.Request("GET", "https://mcp.example.test/mcp")
    await transport.handle_async_request(request)
    assert seen == ["https://mcp.example.test/mcp"]
    request = httpx.Request("GET", "http://127.0.0.1:8765/mcp")
    with pytest.raises(ValueError, match="Blocked"):
        await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_static_credentials_are_blocked_after_cross_origin_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import collie_core.connectors.remote as remote

    monkeypatch.setattr(
        remote, "validate_remote_endpoint", lambda *args, **kwargs: ("203.0.113.10",)
    )
    validate = remote._request_validator(
        "https://mcp.example.test/mcp",
        allow_private_network=False,
        has_static_credentials=True,
    )
    request = httpx.Request("GET", "https://evil.example/mcp")
    with pytest.raises(httpx.RequestError, match="different origin"):
        await validate(request)
