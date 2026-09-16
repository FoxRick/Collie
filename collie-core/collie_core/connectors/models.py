"""Shared connector catalog and lifecycle models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    AUTHORIZING = "authorizing"
    TESTING = "testing"
    CONNECTED = "connected"
    AUTH_REQUIRED = "auth_required"
    ATTENTION = "attention"
    FAILED = "failed"
    REVOKING = "revoking"


class ConnectorDriverKind(StrEnum):
    OFFICIAL_MCP = "official_mcp"
    OFFICIAL_API = "official_api"
    BUNDLED_MCP = "bundled_mcp"
    CUSTOM_MCP = "custom_mcp"


class ConnectorTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"
    STDIO = "stdio"
    API = "api"


class ConnectorProvenance(StrEnum):
    CURATED = "curated"
    IMPORTED = "imported"
    CUSTOM = "custom"


class ConnectorAuthStrategy(StrEnum):
    NONE = "none"
    TOKEN = "token"
    HEADERS = "headers"
    OAUTH = "oauth"


_SECRET_FIELD_FRAGMENTS = ("token", "secret", "password", "credential", "api_key", "apikey")


@dataclass(frozen=True, slots=True)
class InstalledConnectorDefinition:
    """Validated, secret-free snapshot of an installed connector configuration."""

    id: str
    driver: ConnectorDriverKind
    transport: ConnectorTransport
    auth_strategy: ConnectorAuthStrategy
    provenance: ConnectorProvenance
    provider_id: str | None = None
    recipe_id: str | None = None
    recipe_version: str | None = None
    endpoint: str | None = None
    oauth_registration: str | None = None
    client_id: str | None = None
    redirect_uri: str | None = None
    issuer: str | None = None
    resource: str | None = None
    client_metadata_url: str | None = None
    header_name: str | None = None
    scopes: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    tool_overrides: dict[str, str] = field(default_factory=dict)
    unresolved: bool = False
    allow_private_network: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("A definition id is required.")
        for name in (
            "provider_id",
            "recipe_id",
            "recipe_version",
            "endpoint",
            "oauth_registration",
            "client_id",
            "redirect_uri",
            "issuer",
            "resource",
            "client_metadata_url",
            "header_name",
        ):
            item = getattr(self, name)
            if item is not None and not isinstance(item, str):
                raise ValueError(f"{name} must be a string or null.")
        if self.oauth_registration not in (None, "automatic", "preregistered"):
            raise ValueError("Unsupported OAuth registration mode.")
        if self.auth_strategy is ConnectorAuthStrategy.OAUTH:
            if self.oauth_registration is None:
                raise ValueError("OAuth definitions require a registration mode.")
            if self.oauth_registration == "preregistered" and not self.client_id:
                raise ValueError("Pre-registered OAuth definitions require a public client id.")
        elif any(
            (
                self.oauth_registration,
                self.client_id,
                self.redirect_uri,
                self.issuer,
                self.resource,
                self.client_metadata_url,
            )
        ):
            raise ValueError("OAuth client fields require the OAuth strategy.")
        if self.auth_strategy is ConnectorAuthStrategy.HEADERS and not self.header_name:
            raise ValueError("Custom-header authentication requires a header name.")
        if self.header_name:
            if any(ch in self.header_name for ch in "\r\n:") or not self.header_name.strip():
                raise ValueError("Custom header name is invalid.")
            if self.header_name.lower() in {"host", "content-length", "connection", "cookie"}:
                raise ValueError("That header cannot be used for connector authentication.")
        for name in ("redirect_uri", "issuer", "resource", "client_metadata_url"):
            url = getattr(self, name)
            if url:
                parsed_url = urlsplit(url)
                if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
                    raise ValueError(f"{name} must be an HTTP(S) URL.")
                if parsed_url.username or parsed_url.password or parsed_url.fragment:
                    raise ValueError(f"{name} must not contain credentials or a fragment.")
        if (
            self.transport in (ConnectorTransport.STREAMABLE_HTTP, ConnectorTransport.SSE)
            and not self.endpoint
            and not self.unresolved
        ):
            raise ValueError("Remote connector definitions require an endpoint.")
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("Connector endpoint must be an HTTP(S) URL.")
            if parsed.username or parsed.password:
                raise ValueError("Connector endpoint must not contain credentials.")
            if parsed.query or parsed.fragment:
                raise ValueError("Connector endpoint must not contain a query or fragment.")
        if not isinstance(self.scopes, tuple) or not isinstance(self.trusted_hosts, tuple):
            raise ValueError("Connector definition collections must be tuples.")
        for value in (*self.scopes, *self.trusted_hosts):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Connector definition lists must contain non-empty strings.")
        if not isinstance(self.unresolved, bool):
            raise ValueError("unresolved must be a boolean.")
        if not isinstance(self.allow_private_network, bool):
            raise ValueError("allow_private_network must be a boolean.")
        if not isinstance(self.tool_overrides, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.tool_overrides.items()
        ):
            raise ValueError("tool_overrides must map strings to strings.")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InstalledConnectorDefinition:
        allowed = {
            "id",
            "driver",
            "transport",
            "auth_strategy",
            "provenance",
            "provider_id",
            "recipe_id",
            "recipe_version",
            "endpoint",
            "oauth_registration",
            "client_id",
            "redirect_uri",
            "issuer",
            "resource",
            "client_metadata_url",
            "header_name",
            "scopes",
            "trusted_hosts",
            "tool_overrides",
            "unresolved",
            "allow_private_network",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown connector definition fields: {', '.join(sorted(unknown))}")
        if not isinstance(value.get("id"), str):
            raise ValueError("id must be a string.")
        for key in ("scopes", "trusted_hosts"):
            items = value.get(key, ())
            if not isinstance(items, (list, tuple)) or not all(isinstance(x, str) for x in items):
                raise ValueError(f"{key} must be a list of strings.")
        if "unresolved" in value and not isinstance(value["unresolved"], bool):
            raise ValueError("unresolved must be a boolean.")
        overrides = value.get("tool_overrides", {})
        if not isinstance(overrides, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in overrides.items()
        ):
            raise ValueError("tool_overrides must map strings to strings.")
        return cls(
            id=value["id"],
            driver=ConnectorDriverKind(value["driver"]),
            transport=ConnectorTransport(value["transport"]),
            auth_strategy=ConnectorAuthStrategy(value["auth_strategy"]),
            provenance=ConnectorProvenance(value["provenance"]),
            provider_id=value.get("provider_id"),
            recipe_id=value.get("recipe_id"),
            recipe_version=value.get("recipe_version"),
            endpoint=value.get("endpoint"),
            oauth_registration=value.get("oauth_registration"),
            client_id=value.get("client_id"),
            redirect_uri=value.get("redirect_uri"),
            issuer=value.get("issuer"),
            resource=value.get("resource"),
            client_metadata_url=value.get("client_metadata_url"),
            header_name=value.get("header_name"),
            scopes=tuple(value.get("scopes") or ()),
            trusted_hosts=tuple(value.get("trusted_hosts") or ()),
            tool_overrides=dict(overrides),
            unresolved=value.get("unresolved", False),
            allow_private_network=value.get("allow_private_network", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "driver": self.driver.value,
            "transport": self.transport.value,
            "auth_strategy": self.auth_strategy.value,
            "provenance": self.provenance.value,
            "provider_id": self.provider_id,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "endpoint": self.endpoint,
            "oauth_registration": self.oauth_registration,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "issuer": self.issuer,
            "resource": self.resource,
            "client_metadata_url": self.client_metadata_url,
            "header_name": self.header_name,
            "scopes": list(self.scopes),
            "trusted_hosts": list(self.trusted_hosts),
            "tool_overrides": dict(self.tool_overrides),
            "unresolved": self.unresolved,
            "allow_private_network": self.allow_private_network,
        }


class RemoteRevocationStatus(StrEnum):
    """Safe, user-facing result of a best-effort provider revocation."""

    REVOKED = "revoked"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


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
    transport: ConnectorTransport = ConnectorTransport.STREAMABLE_HTTP
    allow_private_network: bool = False
    oauth_registration: str | None = None
    client_id: str | None = None
    redirect_uri: str | None = None
    issuer: str | None = None
    resource: str | None = None
    client_metadata_url: str | None = None
    header_name: str | None = None


@dataclass(slots=True)
class ProbeResult:
    tools: list[dict[str, Any]]
    account_label: str | None = None
    remote_account_id: str | None = None
    granted_scopes: list[str] = field(default_factory=list)
