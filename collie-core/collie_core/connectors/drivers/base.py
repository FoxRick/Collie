"""Connector driver contract."""

from __future__ import annotations

from typing import Protocol

from collie_core.connectors.models import ConnectorDefinition, ProbeResult


class ConnectorDriver(Protocol):
    def connect_and_probe(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> ProbeResult: ...

    def probe(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> ProbeResult: ...

    def revoke(self, definition: ConnectorDefinition, connection_id: str) -> None: ...
