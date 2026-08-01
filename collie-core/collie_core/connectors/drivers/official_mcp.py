"""Official provider-hosted MCP connector driver."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from collie_core.connectors.auth import build_oauth_provider
from collie_core.connectors.models import ConnectorDefinition, ProbeResult
from collie_core.connectors.policy import cached_tool
from collie_core.services.credentials import CredentialStore


class OfficialMcpDriver:
    def __init__(self, credentials: CredentialStore) -> None:
        self.credentials = credentials

    def connect_and_probe(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id, interactive=True))

    def probe(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id, interactive=False))

    async def _probe(
        self,
        definition: ConnectorDefinition,
        connection_id: str,
        *,
        interactive: bool,
    ) -> ProbeResult:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        auth = build_oauth_provider(
            connection_id,
            definition.endpoint,
            self.credentials,
            scopes=definition.scopes,
            interactive=interactive,
        )
        async with httpx.AsyncClient(
            auth=auth,
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=10),
            headers={"Accept": "application/json, text/event-stream"},
        ) as client:
            async with streamable_http_client(
                definition.endpoint, http_client=client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tools: list[dict[str, Any]] = [
                        cached_tool(
                            tool,
                            trusted=True,
                            overrides=definition.tool_overrides,
                        )
                        for tool in result.tools
                    ]
        return ProbeResult(tools=tools, granted_scopes=list(definition.scopes))

    def revoke(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> None:
        # MCP OAuth does not expose a universal revocation endpoint. Local token
        # deletion is immediate; provider-side revocation remains provider-specific.
        return
