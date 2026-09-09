"""Shared remote MCP transport, endpoint policy, and discovery helpers."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

from nanobot.security.network import (
    env_proxy_applies_to_url,
    httpx_env_proxy_mounts,
)

DEFAULT_PROTOCOL_TIMEOUT = 30.0
MAX_DISCOVERY_PAGES = 100
MAX_DISCOVERED_TOOLS = 10_000


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Remote MCP endpoint must be an HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise ValueError("Remote MCP endpoint must not contain credentials.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.rstrip(".").lower(), port


def validate_remote_endpoint(
    initial_url: str,
    candidate_url: str | None = None,
    *,
    allow_private_network: bool = False,
) -> tuple[str, ...]:
    """Validate an initial endpoint or a redirect/metadata request target.

    Private access is deliberately tied to the exact origin the user selected.
    A private opt-in therefore cannot be inherited by redirects or OAuth
    metadata hosted elsewhere.
    """
    target = candidate_url or initial_url
    initial_origin = _origin(initial_url)
    target_origin = _origin(target)
    try:
        infos = socket.getaddrinfo(target_origin[1], None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Remote MCP host cannot be resolved: {target_origin[1]}") from exc
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        parsed_addresses.append(getattr(address, "ipv4_mapped", None) or address)
    if not parsed_addresses:
        raise ValueError(f"Remote MCP host cannot be resolved: {target_origin[1]}")
    permits_private = allow_private_network and target_origin == initial_origin
    forbidden_even_with_opt_in = next(
        (
            address
            for address in parsed_addresses
            if address.is_multicast or address.is_unspecified or address.is_reserved
        ),
        None,
    )
    if forbidden_even_with_opt_in is not None:
        raise ValueError(f"Blocked remote MCP endpoint address: {forbidden_even_with_opt_in}")
    if not permits_private:
        blocked = next(
            (address for address in parsed_addresses if not address.is_global),
            None,
        )
        if blocked is not None:
            raise ValueError(
                f"Blocked remote MCP endpoint: {target_origin[1]} resolves to "
                f"private/internal address {blocked}"
            )
    return tuple(dict.fromkeys(str(address) for address in parsed_addresses))


class RemoteEndpointTransport(httpx.AsyncBaseTransport):
    """Apply endpoint policy and DNS pinning to every MCP HTTP request."""

    def __init__(self, initial_url: str, *, allow_private_network: bool = False) -> None:
        self.initial_url = initial_url
        self.allow_private_network = allow_private_network
        self._transports: dict[tuple[str, str, int], httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        addresses = validate_remote_endpoint(
            self.initial_url,
            url,
            allow_private_network=self.allow_private_network,
        )
        origin = _origin(url)
        inner = self._transports.get(origin)
        if inner is None:
            inner = httpx.AsyncHTTPTransport()
            self._transports[origin] = inner
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        last_error: httpx.ConnectError | httpx.ConnectTimeout | None = None
        for address in addresses:
            pinned_request = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": request.url.host},
            )
            try:
                response = await inner.handle_async_request(pinned_request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                continue
            response.request = request
            return response
        assert last_error is not None
        raise last_error

    async def aclose(self) -> None:
        await asyncio.gather(*(transport.aclose() for transport in self._transports.values()))


def _http_client_kwargs(initial_url: str, allow_private_network: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "transport": RemoteEndpointTransport(
            initial_url,
            allow_private_network=allow_private_network,
        )
    }
    # A configured proxy is itself the network boundary. Preserve existing MCP
    # proxy behavior for public endpoints; private opt-in always stays direct.
    if not allow_private_network and env_proxy_applies_to_url(initial_url):
        mounts = httpx_env_proxy_mounts()
        if mounts:
            kwargs["mounts"] = mounts
    return kwargs


def _request_validator(
    initial_url: str,
    *,
    allow_private_network: bool,
    has_static_credentials: bool,
):
    async def validate(request: httpx.Request) -> None:
        validate_remote_endpoint(
            initial_url,
            str(request.url),
            allow_private_network=allow_private_network,
        )
        if has_static_credentials and _origin(str(request.url)) != _origin(initial_url):
            raise httpx.RequestError(
                "Remote MCP credentials cannot be sent to a different origin.",
                request=request,
            )

    return validate


@asynccontextmanager
async def remote_mcp_session(
    endpoint: str,
    *,
    transport: str,
    headers: Mapping[str, str] | None = None,
    auth: httpx.Auth | None = None,
    allow_private_network: bool = False,
    timeout: float = DEFAULT_PROTOCOL_TIMEOUT,
    server_name: str = "remote",
) -> AsyncIterator[Any]:
    """Open an initialized MCP session for probing or runtime use."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    from nanobot.agent.tools.mcp import _filter_malformed_mcp_progress_notifications

    validate_remote_endpoint(endpoint, allow_private_network=allow_private_network)
    normalized = transport.replace("-", "_").lower()
    client_headers = {"Accept": "application/json, text/event-stream", **dict(headers or {})}
    validate_request = _request_validator(
        endpoint,
        allow_private_network=allow_private_network,
        has_static_credentials=bool(headers),
    )

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={**client_headers, **(headers or {})},
            follow_redirects=True,
            event_hooks={"request": [validate_request]},
            timeout=timeout or httpx.Timeout(DEFAULT_PROTOCOL_TIMEOUT, connect=10),
            auth=auth,
            **_http_client_kwargs(endpoint, allow_private_network),
        )

    if normalized == "sse":
        context = sse_client(
            endpoint,
            timeout=min(timeout, 30),
            sse_read_timeout=timeout,
            httpx_client_factory=client_factory,
            auth=auth,
        )
        async with context as (read, write):
            filtered = _filter_malformed_mcp_progress_notifications(read, server_name)
            async with ClientSession(filtered, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                yield session
        return

    if normalized not in {"streamable_http", "streamablehttp"}:
        raise ValueError(f"Unsupported remote MCP transport: {transport}")
    async with (
        httpx.AsyncClient(
            headers=client_headers,
            follow_redirects=True,
            event_hooks={"request": [validate_request]},
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            auth=auth,
            **_http_client_kwargs(endpoint, allow_private_network),
        ) as client,
        streamable_http_client(endpoint, http_client=client) as (read, write, _),
    ):
        filtered = _filter_malformed_mcp_progress_notifications(read, server_name)
        async with ClientSession(filtered, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            yield session


async def discover_all_tools(
    session: Any,
    *,
    timeout: float = DEFAULT_PROTOCOL_TIMEOUT,
    max_pages: int = MAX_DISCOVERY_PAGES,
    max_tools: int = MAX_DISCOVERED_TOOLS,
) -> list[Any]:
    """Return a complete, bounded, de-duplicated MCP tool inventory."""
    if max_pages < 1 or max_tools < 1:
        raise ValueError("Remote MCP discovery bounds must be positive.")
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_names: set[str] = set()
    seen_normalized_names: set[str] = set()
    tools: list[Any] = []
    for _page in range(max_pages):
        request = session.list_tools() if cursor is None else session.list_tools(cursor=cursor)
        result = await asyncio.wait_for(request, timeout=timeout)
        page_tools = getattr(result, "tools", None)
        if not isinstance(page_tools, list):
            raise ValueError("Remote MCP server returned a malformed tool list.")
        for tool in page_tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Remote MCP server returned a tool without a valid name.")
            if name in seen_names:
                raise ValueError(f"Remote MCP server returned duplicate tool name: {name}")
            input_schema = getattr(tool, "inputSchema", None)
            if input_schema is None:
                input_schema = getattr(tool, "input_schema", None)
            if not isinstance(input_schema, Mapping):
                raise ValueError(f"Remote MCP tool {name!r} has a malformed input schema.")
            from nanobot.agent.tools.mcp import _sanitize_mcp_tool_name

            normalized_name = _sanitize_mcp_tool_name(f"mcp_remote_{name}")
            if normalized_name in seen_normalized_names:
                raise ValueError(f"Remote MCP tool names collide after normalization: {name}")
            seen_names.add(name)
            seen_normalized_names.add(normalized_name)
            tools.append(tool)
            if len(tools) > max_tools:
                raise ValueError("Remote MCP tool discovery exceeded its safe limit.")
        next_cursor = getattr(result, "nextCursor", None)
        if next_cursor is None:
            next_cursor = getattr(result, "next_cursor", None)
        if next_cursor in (None, ""):
            return tools
        if not isinstance(next_cursor, str):
            raise ValueError("Remote MCP server returned a malformed pagination cursor.")
        if next_cursor in seen_cursors:
            raise ValueError("Remote MCP server repeated a pagination cursor.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("Remote MCP tool discovery exceeded its page limit.")
