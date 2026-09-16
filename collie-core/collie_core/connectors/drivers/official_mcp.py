"""Official provider-hosted MCP connector driver."""

from __future__ import annotations

import asyncio
from typing import Any

from collie_core.connectors.auth import build_oauth_provider
from collie_core.connectors.models import (
    ConnectorDefinition,
    ProbeResult,
    RemoteRevocationStatus,
)
from collie_core.connectors.policy import cached_tool
from collie_core.connectors.remote import discover_all_tools, remote_mcp_session
from collie_core.services.credentials import CredentialStore


class OfficialMcpDriver:
    def __init__(self, credentials: CredentialStore) -> None:
        self.credentials = credentials

    def connect_and_probe(self, definition: ConnectorDefinition, connection_id: str) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id, interactive=True))

    def probe(self, definition: ConnectorDefinition, connection_id: str) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id, interactive=False))

    async def _probe(
        self,
        definition: ConnectorDefinition,
        connection_id: str,
        *,
        interactive: bool,
    ) -> ProbeResult:
        auth = build_oauth_provider(
            connection_id,
            definition.endpoint,
            self.credentials,
            scopes=definition.scopes,
            interactive=interactive,
            allow_private_network=definition.allow_private_network,
        )
        async with remote_mcp_session(
            definition.endpoint,
            transport=definition.transport.value,
            auth=auth,
            allow_private_network=definition.allow_private_network,
            server_name=definition.id,
            timeout=300.0 if interactive else 30.0,
        ) as session:
            discovered = await discover_all_tools(session)
            tools: list[dict[str, Any]] = [
                cached_tool(
                    tool,
                    trusted=True,
                    overrides=definition.tool_overrides,
                )
                for tool in discovered
            ]
        granted = list(definition.scopes)
        # Record the scopes the authorization server actually granted from
        # the stored token (fall back to the requested set when absent).
        stored = self.credentials.load(f"connector:{connection_id}")
        if stored:
            tokens = stored.get("tokens") or {}
            actual = tokens.get("scope")
            if actual:
                granted = str(actual).split()
        return ProbeResult(tools=tools, granted_scopes=granted)

    def revoke(self, definition: ConnectorDefinition, connection_id: str) -> RemoteRevocationStatus:
        # MCP OAuth does not expose a universal revocation endpoint. Local token
        # deletion is immediate; provider-side revocation remains provider-specific.
        return RemoteRevocationStatus.UNSUPPORTED
