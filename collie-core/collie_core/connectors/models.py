"""Shared connector catalog and lifecycle models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    AUTHORIZING = "authorizing"
    TESTING = "testing"
    CONNECTED = "connected"
    ATTENTION = "attention"
    FAILED = "failed"
    REVOKING = "revoking"


class ConnectorDriverKind(StrEnum):
    OFFICIAL_MCP = "official_mcp"
    OFFICIAL_API = "official_api"
    BUNDLED_MCP = "bundled_mcp"
    CUSTOM_MCP = "custom_mcp"


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    id: str
    name: str
    category: str
    description: str
    driver: ConnectorDriverKind
    auth_type: str
    endpoint: str = ""
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    featured: bool = False
    available: bool = False
    release_status: str = "coming_soon"
    note: str = ""
    trusted_hosts: tuple[str, ...] = ()
    tool_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProbeResult:
    tools: list[dict[str, Any]]
    account_label: str | None = None
    remote_account_id: str | None = None
    granted_scopes: list[str] = field(default_factory=list)

