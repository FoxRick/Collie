"""Driver for arbitrary validated remote MCP definitions."""

from __future__ import annotations

import asyncio
from typing import Any

from collie_core.connectors.auth import build_oauth_provider, close_oauth_provider
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
        return asyncio.run(self._probe(definition, connection_id, interactive=True))

    def probe(self, definition: Any, connection_id: str) -> ProbeResult:
        return asyncio.run(self._probe(definition, connection_id, interactive=False))

    async def _probe(
        self, definition: Any, connection_id: str, *, interactive: bool
    ) -> ProbeResult:
        auth_strategy = getattr(definition, "auth_strategy", None)
        if auth_strategy is None:
            auth_strategy = getattr(definition, "auth_type", "none")
        auth_value = getattr(auth_strategy, "value", auth_strategy)
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
        headers: dict[str, str] = {}
        auth = None
        if auth_value != ConnectorAuthStrategy.NONE.value and self.credentials is None:
            raise ValueError("This auth strategy requires protected credential storage.")
        if auth_value in (ConnectorAuthStrategy.TOKEN.value, ConnectorAuthStrategy.HEADERS.value):
            stored = (
                self.credentials.load(f"connector:{connection_id}") if self.credentials else None
            )
            static = (stored or {}).get("static") or {}
            secret = static.get("value")
            if not isinstance(secret, str) or not secret:
                raise ValueError("The saved connector credential is missing.")
            if auth_value == ConnectorAuthStrategy.TOKEN.value:
                headers["Authorization"] = f"Bearer {secret}"
            else:
                headers[
                    str(getattr(definition, "header_name", "") or static.get("header_name"))
                ] = secret
        elif auth_value == ConnectorAuthStrategy.OAUTH.value:
            credential_key = f"connector:{connection_id}"
            credential_revision = self.credentials.revision(credential_key)
            auth = build_oauth_provider(
                connection_id,
                endpoint,
                self.credentials,
                scopes=tuple(getattr(definition, "scopes", ())),
                interactive=interactive,
                allow_private_network=bool(getattr(definition, "allow_private_network", False)),
                registration=getattr(definition, "oauth_registration", "automatic") or "automatic",
                client_id=getattr(definition, "client_id", None),
                redirect_uri=getattr(definition, "redirect_uri", None),
                issuer=getattr(definition, "issuer", None),
                resource=getattr(definition, "resource", None),
                client_metadata_url=getattr(definition, "client_metadata_url", None),
                write_allowed=lambda: (
                    self.credentials.revision(credential_key) == credential_revision
                ),
            )
        try:
            async with remote_mcp_session(
                endpoint,
                transport=transport_value,
                headers=headers,
                auth=auth,
                allow_private_network=bool(getattr(definition, "allow_private_network", False)),
                server_name=getattr(definition, "id", "remote"),
            ) as session:
                discovered = await discover_all_tools(session)
        finally:
            if auth is not None:
                close_oauth_provider(auth)
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
