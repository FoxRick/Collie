"""Driver for arbitrary validated remote MCP definitions."""

from __future__ import annotations

import asyncio
from typing import Any

from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorTransport,
    ProbeResult,
    RemoteRevocationStatus,
)
from collie_core.connectors.policy import cached_tool
from collie_core.connectors.remote import discover_all_tools, remote_mcp_session


class RemoteMcpDriver:
    """Probe no-auth Streamable HTTP and explicit SSE definitions."""

    def __init__(self, credentials: Any | None = None) -> None:
        self.credentials = credentials

    def connect_and_probe(self, definition: Any, connection_id: str) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id))

    def probe(self, definition: Any, connection_id: str) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id))

    async def _probe(self, definition: Any, connection_id: str) -> ProbeResult:
        del connection_id
        auth_strategy = getattr(definition, "auth_strategy", None)
        if auth_strategy is None:
            auth_strategy = getattr(definition, "auth_type", "none")
        auth_value = getattr(auth_strategy, "value", auth_strategy)
        if auth_value != ConnectorAuthStrategy.NONE.value:
            raise ValueError(
                "This remote connector auth strategy is not supported yet; use a no-auth endpoint."
            )
        if getattr(definition, "tool_overrides", {}):
            raise ValueError("Custom remote connectors cannot declare trusted tool overrides.")
        endpoint = getattr(definition, "endpoint", "")
        transport = getattr(definition, "transport", ConnectorTransport.STREAMABLE_HTTP)
        transport_value = getattr(transport, "value", transport)
        if transport_value not in {
            ConnectorTransport.STREAMABLE_HTTP.value,
            ConnectorTransport.SSE.value,
        }:
            raise ValueError("Remote MCP supports Streamable HTTP or explicit SSE.")
        async with remote_mcp_session(
            endpoint,
            transport=transport_value,
            allow_private_network=bool(getattr(definition, "allow_private_network", False)),
            server_name=getattr(definition, "id", "remote"),
        ) as session:
            discovered = await discover_all_tools(session)
        return ProbeResult(
            tools=[
                cached_tool(
                    tool,
                    trusted=False,
                    overrides=getattr(definition, "tool_overrides", {}),
                )
                for tool in discovered
            ]
        )

    def revoke(self, definition: Any, connection_id: str) -> RemoteRevocationStatus:
        del definition, connection_id
        return RemoteRevocationStatus.NOT_APPLICABLE
