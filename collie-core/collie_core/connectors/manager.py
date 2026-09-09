"""Connection lifecycle, probing, policy caching, and runtime binding."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from loguru import logger

from collie_core.connectors.catalog import CONNECTOR_CATALOG, connector_def
from collie_core.connectors.drivers.official_mcp import OfficialMcpDriver
from collie_core.connectors.import_config import import_preview as parse_import_preview
from collie_core.connectors.import_config import validate_definition_payload
from collie_core.connectors.models import (
    ConnectionStatus,
    ConnectorAuthStrategy,
    ConnectorDefinition,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
    ProbeResult,
    RemoteRevocationStatus,
)
from collie_core.db import CollieDB, utc_now
from collie_core.services.credentials import CredentialStore

__all__ = ["ConnectorManager"]

_ACTIVE = {
    ConnectionStatus.AUTHORIZING.value,
    ConnectionStatus.TESTING.value,
    ConnectionStatus.CONNECTED.value,
}

# Failures that invalidate the stored credentials. Everything else
# (network, tool discovery, timeouts) keeps them so a retry needs no
# fresh sign-in.
_CREDENTIAL_DELETING_CODES = frozenset({"oauth_cancelled", "scope_denied", "token_refresh_failed"})


def _json_value(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


class ConnectorManager:
    def __init__(
        self,
        db: CollieDB,
        *,
        credentials: CredentialStore | None = None,
        driver_factory: Callable[[ConnectorDefinition], Any] | None = None,
        on_runtime_change: Callable[[], None] | None = None,
    ) -> None:
        self.db = db
        self.credentials = credentials or CredentialStore()
        self._driver_factory = driver_factory or self._default_driver
        self._lock = threading.RLock()
        self._on_runtime_change = on_runtime_change
        self._operations: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._connecting: set[str] = set()
        self._migrate_legacy_credentials()
        self._resolve_migrated_definitions()

    def _default_driver(self, definition: ConnectorDefinition) -> Any:
        if definition.driver == ConnectorDriverKind.OFFICIAL_MCP:
            return OfficialMcpDriver(self.credentials)
        if definition.driver == ConnectorDriverKind.CUSTOM_MCP:
            from collie_core.connectors.drivers.remote_mcp import RemoteMcpDriver

            return RemoteMcpDriver(self.credentials)
        raise ValueError(f"{definition.name}'s official connection is not ready yet.")

    def _migrate_legacy_credentials(self) -> None:
        for row in self.db.list_connector_connections():
            if not str(row["id"]).startswith("con_legacy_"):
                continue
            target = f"connector:{row['id']}"
            if self.credentials.load(target) is None:
                old = self.credentials.load(str(row["provider_id"]))
                if old is not None:
                    self.credentials.save(target, old)

    def _definition_for_row(self, row: dict[str, Any]) -> ConnectorDefinition | None:
        """Resolve a connection against its installed snapshot when one is complete."""
        recipe = connector_def(str(row["provider_id"]))
        stored = self.db.get_connector_definition(str(row.get("definition_id") or ""))
        if stored is None or stored.get("unresolved"):
            return recipe
        config = _json_value(stored.get("config_json"), {})
        if stored.get("provenance") != ConnectorProvenance.CURATED.value:
            if stored["driver"] != ConnectorDriverKind.CUSTOM_MCP.value:
                return None
            return ConnectorDefinition(
                id=str(row["provider_id"]),
                name=str(row.get("display_name") or "Custom connection"),
                category="custom",
                description="A remote connection added by you.",
                driver=ConnectorDriverKind.CUSTOM_MCP,
                auth_type=str(stored["auth_strategy"]),
                endpoint=str(config.get("endpoint") or ""),
                transport=ConnectorTransport(str(stored["transport"])),
                allow_private_network=config.get("allow_private_network", False),
                scopes=tuple(config.get("scopes") or ()),
                oauth_registration=config.get("oauth_registration"),
                client_id=config.get("client_id"),
                redirect_uri=config.get("redirect_uri"),
                issuer=config.get("issuer"),
                resource=config.get("resource"),
                client_metadata_url=config.get("client_metadata_url"),
                header_name=config.get("header_name"),
                available=True,
                release_status="alpha",
            )
        if recipe is None:
            return None
        return replace(
            recipe,
            driver=ConnectorDriverKind(str(stored["driver"])),
            auth_type=str(stored["auth_strategy"]),
            endpoint=str(config.get("endpoint") or ""),
            scopes=tuple(config.get("scopes") or ()),
            trusted_hosts=tuple(config.get("trusted_hosts") or ()),
            tool_overrides=dict(config.get("tool_overrides") or {}),
            transport=ConnectorTransport(str(stored["transport"])),
            allow_private_network=config.get("allow_private_network", False),
            oauth_registration=config.get("oauth_registration"),
            client_id=config.get("client_id"),
            redirect_uri=config.get("redirect_uri"),
            issuer=config.get("issuer"),
            resource=config.get("resource"),
            client_metadata_url=config.get("client_metadata_url"),
            header_name=config.get("header_name"),
        )

    def _resolve_migrated_definitions(self) -> None:
        """Pin the packaged recipe once for V15 accounts; unknown rows remain removable."""
        for row in self.db.list_connector_connections():
            stored = self.db.get_connector_definition(str(row.get("definition_id") or ""))
            if not stored or not stored.get("unresolved"):
                continue
            recipe = connector_def(str(row["provider_id"]))
            if recipe is not None and row["driver"] == recipe.driver.value:
                self._save_definition_snapshot(recipe, str(row["id"]))

    def _save_definition_snapshot(
        self, definition: ConnectorDefinition, connection_id: str
    ) -> None:
        self.db.save_connector_definition(
            InstalledConnectorDefinition(
                id=f"def_{connection_id}",
                provider_id=definition.id,
                recipe_id=definition.id,
                recipe_version="1",
                driver=definition.driver,
                transport=(
                    ConnectorTransport.API
                    if definition.driver == ConnectorDriverKind.OFFICIAL_API
                    else ConnectorTransport.STREAMABLE_HTTP
                ),
                auth_strategy=ConnectorAuthStrategy(definition.auth_type),
                provenance=ConnectorProvenance.CURATED,
                endpoint=definition.endpoint or None,
                oauth_registration="automatic" if definition.auth_type == "oauth" else None,
                scopes=definition.scopes,
                trusted_hosts=definition.trusted_hosts,
                tool_overrides=definition.tool_overrides,
            )
        )

    # -- catalog and connection views ---------------------------------------

    def catalog_view(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        statuses: dict[str, str] = {}
        for row in self.db.list_connector_connections():
            provider_id = str(row["provider_id"])
            definition = connector_def(provider_id)
            compatible = bool(
                definition and definition.available and row["driver"] == definition.driver.value
            )
            if not compatible:
                continue
            if row["status"] == ConnectionStatus.CONNECTED.value:
                # A connected row only counts (and stays green) when its
                # credentials actually exist.
                if self._has_credentials(row):
                    counts[provider_id] = counts.get(provider_id, 0) + 1
                else:
                    statuses.setdefault(provider_id, ConnectionStatus.AUTH_REQUIRED.value)
            else:
                statuses.setdefault(provider_id, str(row["status"]))
        return [
            {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "description": item.description,
                "auth": item.auth_type,
                "driver": item.driver.value,
                "capabilities": list(item.capabilities),
                "permissions": list(item.permissions),
                "featured": item.featured,
                "available": item.available,
                "release_status": item.release_status,
                "note": item.note,
                "status": (
                    "connected"
                    if counts.get(item.id)
                    else statuses.get(
                        item.id,
                        "disconnected" if item.available else "coming_soon",
                    )
                ),
                "connection_count": counts.get(item.id, 0),
                # Legacy Services-tab fields:
                "fields": [],
                "account_info": None,
                "connected_at": None,
                "last_error": None,
            }
            for item in CONNECTOR_CATALOG
        ]

    def legacy_catalog_view(self) -> list[dict[str, Any]]:
        """Keep one-release read compatibility for Settings -> Services."""
        from collie_core.services.manager import ServiceManager

        return ServiceManager(self.db, credentials=self.credentials).catalog_view()

    def _has_credentials(self, row: dict[str, Any]) -> bool:
        """A connection is only genuinely connected when it holds a usable
        access token. Empty records, client-info-only entries, and token
        blobs without an ``access_token`` do not count."""
        definition = self._definition_for_row(row)
        if definition is not None and definition.auth_type == "none":
            return True
        data = self.credentials.load(f"connector:{row['id']}") or {}
        if definition is not None and definition.auth_type in {"token", "headers"}:
            return bool((data.get("static") or {}).get("value"))
        tokens = data.get("tokens") or {}
        return bool(tokens.get("access_token"))

    def _connection_view(self, row: dict[str, Any]) -> dict[str, Any]:
        definition = self._definition_for_row(row)
        compatible = bool(
            definition and definition.available and row["driver"] == definition.driver.value
        )
        status = str(row["status"])
        last_error_code = row.get("last_error_code")
        last_error_message = row.get("last_error_message")
        # Historical rows stay removable, but a static catalog entry cannot
        # make an unavailable route appear healthy in the UI.
        if not compatible and status != ConnectionStatus.FAILED.value:
            status = ConnectionStatus.ATTENTION.value
            last_error_code = "provider_unavailable"
            last_error_message = (
                "This connection is not available in this build and cannot be used."
            )
        # A connected row without stored credentials (token deleted, DB
        # restored without the credential files, legacy migration gap) must
        # not look healthy or bind at runtime — it needs a fresh sign-in.
        elif status == ConnectionStatus.CONNECTED.value and not self._has_credentials(row):
            status = ConnectionStatus.AUTH_REQUIRED.value
            last_error_code = "credentials_missing"
            last_error_message = (
                "The saved credentials for this connection are missing — sign in again."
            )
        return {
            "id": row["id"],
            "provider_id": row["provider_id"],
            "provider_name": definition.name if definition else row["provider_id"],
            "display_name": row.get("display_name"),
            "account_label": row.get("account_label"),
            "driver": row["driver"],
            "auth_type": row["auth_type"],
            "status": status,
            "granted_scopes": _json_value(row.get("granted_scopes_json"), []),
            "enabled_capabilities": _json_value(row.get("enabled_capabilities_json"), []),
            "enabled_tools": _json_value(row.get("enabled_tools_json"), []),
            "tool_policy": _json_value(row.get("tool_policy_json"), {}),
            "remote_account_id": row.get("remote_account_id"),
            "connected_at": row.get("connected_at"),
            "updated_at": row.get("updated_at"),
            "last_verified_at": row.get("last_verified_at"),
            "last_error_code": last_error_code,
            "last_error_message": last_error_message,
            "operation_revision": int(row.get("operation_revision") or 0),
            "operation_id": self._operations.get(str(row["id"])),
            "failure": self._failure(last_error_code, last_error_message),
            "permissions": list(definition.permissions) if definition else [],
            "capabilities": list(definition.capabilities) if definition else [],
            "route": "Official MCP"
            if definition and definition.driver == "official_mcp"
            else "Custom MCP"
            if definition and definition.driver == "custom_mcp"
            else "Official API",
        }

    def list_connections(self) -> list[dict[str, Any]]:
        return [
            self._connection_view(row)
            for row in self.db.list_connector_connections()
            if row["status"] != ConnectionStatus.DISCONNECTED.value
        ]

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        row = self.db.get_connector_connection(connection_id)
        return self._connection_view(row) if row else None

    @staticmethod
    def _failure(code: Any, message: Any = None) -> dict[str, Any] | None:
        if not code:
            return None
        recovery = {
            "oauth_cancelled": "Try signing in again when you're ready.",
            "scope_denied": "Sign in again and allow the requested access.",
            "callback_timeout": "Try signing in again and finish in the browser.",
            "token_refresh_failed": "Reconnect this account.",
            "credentials_missing": "Reconnect this account.",
            "account_admin_blocked": "Ask your workspace administrator to allow the app.",
            "tool_discovery_failed": "Test the connection again.",
            "server_unreachable": "Check the address and your network, then try again.",
            "provider_unavailable": "Use an available connection route or update Collie.",
            "interrupted": "Try the connection again.",
        }
        return {
            "code": str(code),
            "message": str(message or "I couldn't finish that connection step."),
            "recovery_action": recovery.get(str(code), "Check the details and try again."),
            "stage": "authorization"
            if str(code).startswith(("oauth", "scope", "token", "callback", "account"))
            else "discovery",
            "retryable": str(code) not in {"provider_unavailable", "account_admin_blocked"},
        }

    def validate_definition(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            definition, preview = validate_definition_payload(payload)
        except (TypeError, ValueError):
            return {
                "valid": False,
                "preview": None,
                "warnings": [],
                "errors": [
                    {
                        "code": "invalid_definition",
                        "message": "Check the connection address and sign-in details.",
                    }
                ],
            }
        return {
            "valid": True,
            "preview": preview,
            "definition": definition.to_dict(),
            "warnings": [],
            "errors": [],
        }

    def import_preview(self, value: str | dict[str, Any]) -> dict[str, Any]:
        return parse_import_preview(value)

    def save_definition(self, payload: dict[str, Any]) -> dict[str, Any]:
        definition = InstalledConnectorDefinition.from_dict(payload)
        if definition.id.startswith("draft_"):
            definition = replace(definition, id=f"def_{uuid.uuid4().hex}")
        self.db.save_connector_definition(definition)
        return {
            "definition_id": definition.id,
            "auth_strategy": definition.auth_strategy.value,
            "requires_secret": definition.auth_strategy
            in {ConnectorAuthStrategy.TOKEN, ConnectorAuthStrategy.HEADERS},
        }

    def begin_auth(
        self,
        definition_id: str,
        *,
        secret: dict[str, Any] | None = None,
        display_name: str | None = None,
        origin: str = "connectors_ui",
        connection_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.db.get_connector_definition(definition_id)
        if row is None:
            raise ValueError("I couldn't find those connection details.")
        config = _json_value(row.get("config_json"), {})
        installed = InstalledConnectorDefinition.from_dict(
            {
                "id": str(row["id"]),
                "provider_id": row.get("provider_id"),
                "recipe_id": row.get("recipe_id"),
                "recipe_version": row.get("recipe_version"),
                "driver": row["driver"],
                "transport": row["transport"],
                "auth_strategy": row["auth_strategy"],
                "provenance": row["provenance"],
                "unresolved": bool(row.get("unresolved")),
                **config,
            }
        )
        return self.connect_definition(
            installed,
            display_name=display_name,
            origin=origin,
            secret=secret,
            connection_id=connection_id,
            operation_id=operation_id,
        )

    def inspect_tools(
        self, connection_id: str, *, query: str = "", limit: int = 50
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        if hasattr(self.db, "search_connector_tools"):
            tools = self.db.search_connector_tools(
                connection_id, query, limit=limit, enabled_only=False
            )
        else:
            needle = query.casefold().strip()
            tools = [
                tool
                for tool in self.db.list_connector_tools(connection_id)
                if not needle or needle in str(tool.get("remote_tool_name", "")).casefold()
            ][:limit]
        row = self.db.get_connector_connection(connection_id)
        policy = _json_value((row or {}).get("tool_policy_json"), {})
        review_status = policy.get("_tool_review_status", {})
        enabled = set(_json_value((row or {}).get("enabled_tools_json"), []))
        tools = [
            {
                **tool,
                "review_status": review_status.get(str(tool.get("remote_tool_name")), "reviewed"),
                "previously_enabled": str(tool.get("remote_tool_name")) in enabled,
            }
            for tool in tools
        ]
        return {
            "connection_id": connection_id,
            "total": len(self.db.list_connector_tools(connection_id)),
            "tools": tools,
        }

    def _notify_runtime_change(self) -> None:
        if self._on_runtime_change is not None:
            try:
                self._on_runtime_change()
            except Exception:
                logger.exception("Connector runtime reconciliation callback failed")

    # -- lifecycle -----------------------------------------------------------

    def connect(
        self,
        provider_id: str,
        credentials: dict[str, Any] | None = None,
        *,
        origin: str = "connectors_ui",
        replace_connection_id: str | None = None,
        connection_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        del credentials  # Ordinary connector flows never accept renderer credentials.
        definition = connector_def(provider_id)
        if definition is None:
            raise ValueError(f"I don't know a connector called '{provider_id}'.")
        if not definition.available:
            raise ValueError(f"{definition.name} is coming soon in this build.")
        return self._connect(
            definition,
            origin=origin,
            replace_connection_id=replace_connection_id,
            connection_id=connection_id,
            operation_id=operation_id,
        )

    def connect_definition(
        self,
        installed: InstalledConnectorDefinition,
        *,
        display_name: str | None = None,
        origin: str = "custom_connection",
        secret: dict[str, Any] | None = None,
        connection_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Connect a validated custom remote definition through the shared lifecycle.

        This backend entry point precedes the desktop add/import flow. Each call
        creates a distinct account, even when the same definition is reused.
        """
        installed = InstalledConnectorDefinition.from_dict(installed.to_dict())
        if (
            installed.driver is not ConnectorDriverKind.CUSTOM_MCP
            or installed.provenance
            not in (ConnectorProvenance.CUSTOM, ConnectorProvenance.IMPORTED)
            or installed.transport
            not in (ConnectorTransport.STREAMABLE_HTTP, ConnectorTransport.SSE)
            or installed.unresolved
        ):
            raise ValueError("Choose a complete custom remote connection definition.")
        if installed.tool_overrides or installed.trusted_hosts:
            raise ValueError("Custom connections cannot grant trusted tool or host overrides.")
        connection_id = connection_id or f"con_{uuid.uuid4().hex}"
        # Caller-provided labels/recipe IDs cannot impersonate a curated route.
        provider_id = f"custom_{uuid.uuid4().hex}"
        installed = replace(
            installed,
            id=f"def_{connection_id}",
            provider_id=provider_id,
            recipe_id=None,
            recipe_version=None,
        )
        definition = ConnectorDefinition(
            id=provider_id,
            name=(display_name or "Custom connection").strip() or "Custom connection",
            category="custom",
            description="A remote connection added by you.",
            driver=ConnectorDriverKind.CUSTOM_MCP,
            auth_type="none",
            endpoint=installed.endpoint or "",
            transport=installed.transport,
            allow_private_network=installed.allow_private_network,
            scopes=installed.scopes,
            oauth_registration=installed.oauth_registration,
            client_id=installed.client_id,
            redirect_uri=installed.redirect_uri,
            issuer=installed.issuer,
            resource=installed.resource,
            client_metadata_url=installed.client_metadata_url,
            header_name=installed.header_name,
            available=True,
            release_status="alpha",
        )
        definition = replace(definition, auth_type=installed.auth_strategy.value, available=True)
        if installed.auth_strategy in (ConnectorAuthStrategy.TOKEN, ConnectorAuthStrategy.HEADERS):
            value = (secret or {}).get("token") or (secret or {}).get("value")
            submitted_headers = (secret or {}).get("headers")
            if isinstance(submitted_headers, dict):
                if list(submitted_headers) != [installed.header_name]:
                    raise ValueError(
                        "The submitted authentication header does not match the definition."
                    )
                value = submitted_headers[installed.header_name]
            existing_secret = self.credentials.load(f"connector:{connection_id}")
            if (not isinstance(value, str) or not value) and not existing_secret:
                raise ValueError("Enter the credential for this connection.")
            pending_static = (
                {
                    "kind": installed.auth_strategy.value,
                    "value": value,
                    "header_name": installed.header_name,
                }
                if isinstance(value, str) and value
                else None
            )
        else:
            pending_static = None
        self._operations[connection_id] = operation_id or f"op_{uuid.uuid4().hex}"
        return self._connect(
            definition,
            origin=origin,
            connection_id=connection_id,
            installed=installed,
            pending_static=pending_static,
            operation_id=operation_id,
        )

    def _connect(
        self,
        definition: ConnectorDefinition,
        *,
        origin: str,
        replace_connection_id: str | None = None,
        connection_id: str | None = None,
        installed: InstalledConnectorDefinition | None = None,
        pending_static: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if connection_id and connection_id in self._connecting:
                raise ValueError("That connection is still stopping. Try again in a moment.")
            existing = next(
                (
                    row
                    for row in self.db.list_connector_connections(definition.id)
                    if row["status"] in _ACTIVE and row["id"] != replace_connection_id
                ),
                None,
            )
            if existing and replace_connection_id is None:
                return {
                    "provider_id": definition.id,
                    "connection_id": existing["id"],
                    "status": existing["status"],
                    "origin": origin,
                }
            # Reap any connection stuck in AUTHORIZING/TESTING from a previous
            # crash — a stale auth flow must never block a fresh one.
            for stale in self.db.list_connector_connections(definition.id):
                if stale["status"] in {
                    ConnectionStatus.AUTHORIZING.value,
                    ConnectionStatus.TESTING.value,
                }:
                    self.db.upsert_connector_connection(
                        stale["id"],
                        provider_id=definition.id,
                        driver=str(stale["driver"]),
                        auth_type=str(stale["auth_type"]),
                        status=ConnectionStatus.FAILED.value,
                        last_error_code="interrupted",
                        last_error_message="The previous sign-in was interrupted.",
                    )
            connection_id = connection_id or f"con_{uuid.uuid4().hex}"
            operation_id = operation_id or f"op_{uuid.uuid4().hex}"
            self._operations[connection_id] = operation_id
            previous_row = self.db.get_connector_connection(connection_id)
            previous_tool_names = {
                str(tool["remote_tool_name"])
                for tool in self.db.list_connector_tools(connection_id)
            }
            if installed is None:
                self._save_definition_snapshot(definition, connection_id)
            else:
                self.db.save_connector_definition(installed)
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=definition.id,
                display_name=definition.name,
                driver=definition.driver.value,
                auth_type=definition.auth_type,
                status=ConnectionStatus.AUTHORIZING.value,
                enabled_capabilities=list(definition.capabilities),
            )
            if pending_static is not None:
                self.credentials.save(f"connector:{connection_id}", {"static": pending_static})
            self._connecting.add(connection_id)

        try:
            driver = self._driver_factory(definition)
            result: ProbeResult = driver.connect_and_probe(definition, connection_id)
            with self._lock:
                if connection_id in self._cancelled:
                    raise RuntimeError("oauth cancelled")
                if self._operations.get(connection_id) != operation_id:
                    raise RuntimeError("oauth cancelled")
                # A concurrent remove() may have deleted the row while we probed.
                if self.db.get_connector_connection(connection_id) is None:
                    raise RuntimeError("oauth cancelled")
                self.db.upsert_connector_connection(
                    connection_id,
                    provider_id=definition.id,
                    driver=definition.driver.value,
                    auth_type=definition.auth_type,
                    status=ConnectionStatus.TESTING.value,
                )
                if not result.tools:
                    raise RuntimeError("The provider connected but returned no usable tools.")
                policy = {tool["name"]: tool["risk"] for tool in result.tools}
                enabled_tools = [tool["name"] for tool in result.tools]
                report = self.db.replace_connector_tools(connection_id, result.tools)
                if previous_row is not None:
                    previous_policy = _json_value(previous_row.get("tool_policy_json"), {})
                    if "_approval_preference" in previous_policy:
                        policy["_approval_preference"] = previous_policy["_approval_preference"]
                    previous_enabled = _json_value(previous_row.get("enabled_tools_json"), [])
                    enabled_tools = (
                        sorted(previous_tool_names & {str(tool["name"]) for tool in result.tools})
                        if "*" in previous_enabled
                        else sorted(
                            set(previous_enabled) & {str(tool["name"]) for tool in result.tools}
                        )
                    )
                    enabled_tools = sorted(
                        set(enabled_tools)
                        - set(report.get("changed_tools", ()))
                        - set(report.get("new_tools", ()))
                    )
                    prior_reviews = previous_policy.get("_tool_review_status", {})
                    policy["_tool_review_status"] = {
                        name: (
                            "changed"
                            if name in set(report.get("changed_tools", ()))
                            else "new"
                            if name in set(report.get("new_tools", ()))
                            else prior_reviews.get(name, "reviewed")
                        )
                        for name in {str(tool["name"]) for tool in result.tools}
                    }
                # Second cancellation check: the flag may have been set while the
                # probe ran — the CONNECTED upsert must not win the race.
                if connection_id in self._cancelled:
                    raise RuntimeError("oauth cancelled")
                row = self.db.upsert_connector_connection(
                    connection_id,
                    provider_id=definition.id,
                    display_name=definition.name,
                    account_label=result.account_label,
                    driver=definition.driver.value,
                    auth_type=definition.auth_type,
                    status=ConnectionStatus.CONNECTED.value,
                    granted_scopes=result.granted_scopes,
                    enabled_capabilities=list(definition.capabilities),
                    enabled_tools=enabled_tools,
                    tool_policy=policy,
                    remote_account_id=result.remote_account_id,
                    last_verified_at=utc_now(),
                )
            if replace_connection_id and replace_connection_id != connection_id:
                self.remove(replace_connection_id, origin=origin)
        except Exception as error:
            code = self._error_code(error)
            # Credentials survive retryable failures (network, tool discovery)
            # so the user can retry without a fresh sign-in — but never for
            # cancellations or auth-level refusals.
            with self._lock:
                owns_operation = self._operations.get(connection_id) == operation_id
                if code in _CREDENTIAL_DELETING_CODES and owns_operation:
                    self.credentials.delete(f"connector:{connection_id}")
                if owns_operation and self.db.get_connector_connection(connection_id) is not None:
                    self.db.upsert_connector_connection(
                        connection_id,
                        provider_id=definition.id,
                        driver=definition.driver.value,
                        auth_type=definition.auth_type,
                        status=ConnectionStatus.FAILED.value,
                        last_error_code=code,
                        last_error_message=self._friendly_error(code),
                    )
            raise ValueError(self._friendly_error(code)) from error
        finally:
            with self._lock:
                cancelled = connection_id in self._cancelled
                if cancelled:
                    # Clean up credentials before releasing the in-flight marker,
                    # so a reconnect cannot race this deletion.
                    self.credentials.delete(f"connector:{connection_id}")
                self._cancelled.discard(connection_id)
                self._connecting.discard(connection_id)
        logger.info("Connector connected: {} ({})", definition.id, connection_id)
        self._notify_runtime_change()
        return {
            **self._connection_view(row),
            "provider_id": definition.id,
            "connection_id": connection_id,
            "status": row["status"],
            "origin": origin,
            "operation_id": operation_id,
            "operation_revision": int(row.get("operation_revision") or 0),
        }

    def cancel_auth(
        self,
        connection_id: str,
        *,
        operation_id: str | None = None,
        operation_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            row = self.db.get_connector_connection(connection_id)
            if row is None or row["status"] not in {
                ConnectionStatus.AUTHORIZING.value,
                ConnectionStatus.TESTING.value,
            }:
                return {"connection_id": connection_id, "cancelled": False}
            if operation_id is not None and self._operations.get(connection_id) != operation_id:
                return {"connection_id": connection_id, "cancelled": False}
            expected = int(row.get("operation_revision") or 0)
            if operation_revision is not None and operation_revision != expected:
                return {"connection_id": connection_id, "cancelled": False}
            revision = self.db.advance_connector_operation(connection_id, expected)
            self._operations[connection_id] = f"cancelled_{uuid.uuid4().hex}"
            from collie_core.connectors.auth import cancel_oauth_connection

            cancel_oauth_connection(connection_id)
            self._cancelled.add(connection_id)
            # Credentials are deleted by the connect thread after it finishes
            # (deleting mid-write resurrects the token file) — unless no
            # connect is in flight at all.
            if connection_id not in self._connecting:
                self.credentials.delete(f"connector:{connection_id}")
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=str(row["provider_id"]),
                driver=str(row["driver"]),
                auth_type=str(row["auth_type"]),
                status=ConnectionStatus.FAILED.value,
                last_error_code="oauth_cancelled",
                last_error_message=self._friendly_error("oauth_cancelled"),
            )
            self._notify_runtime_change()
            return {
                "connection_id": connection_id,
                "cancelled": True,
                "operation_revision": revision,
            }

    def test(
        self,
        connection_id: str,
        *,
        operation_id: str | None = None,
        operation_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            row = self.db.get_connector_connection(connection_id)
            if row is None:
                raise ValueError("I couldn't find that connection.")
            expected = int(row.get("operation_revision") or 0)
            if operation_revision is not None and operation_revision != expected:
                raise ValueError("The connection changed. Refresh it and try again.")
            revision = self.db.advance_connector_operation(connection_id, expected)
            operation_id = operation_id or f"op_{uuid.uuid4().hex}"
            self._operations[connection_id] = operation_id
            definition = self._definition_for_row(row)
            if definition is None:
                raise ValueError("That provider is no longer in this build.")
            if not definition.available:
                raise ValueError(f"{definition.name} is coming soon in this build.")
            if row["driver"] != definition.driver.value:
                raise ValueError(
                    "This saved connection no longer matches its provider route. Reconnect?"
                )
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=definition.id,
                driver=definition.driver.value,
                auth_type=definition.auth_type,
                status=ConnectionStatus.TESTING.value,
            )
        try:
            result = self._driver_factory(definition).probe(definition, connection_id)
            if not result.tools:
                raise RuntimeError("No tools returned")
            with self._lock:
                current = self.db.get_connector_connection(connection_id)
                if (
                    current is None
                    or current["status"] == ConnectionStatus.REVOKING.value
                    or int(current.get("operation_revision") or 0) != revision
                    or self._operations.get(connection_id) != operation_id
                ):
                    raise RuntimeError("Connection test cancelled")
                policy = {tool["name"]: tool["risk"] for tool in result.tools}
                previous = _json_value(current.get("tool_policy_json"), {})
                if "_approval_preference" in previous:
                    policy["_approval_preference"] = previous["_approval_preference"]
                previous_enabled = _json_value(current.get("enabled_tools_json"), [])
                previous_names = {
                    str(tool["remote_tool_name"])
                    for tool in self.db.list_connector_tools(connection_id)
                }
                discovered_names = {str(tool["name"]) for tool in result.tools}
                if "*" in previous_enabled:
                    enabled_tools = sorted(discovered_names & previous_names)
                else:
                    enabled_tools = sorted(discovered_names & set(previous_enabled))
                report = self.db.replace_connector_tools(connection_id, result.tools)
                enabled_tools = sorted(
                    set(enabled_tools)
                    - set(report.get("changed_tools", ()))
                    - set(report.get("new_tools", ()))
                )
                prior_reviews = previous.get("_tool_review_status", {})
                policy["_tool_review_status"] = {
                    name: (
                        "changed"
                        if name in set(report.get("changed_tools", ()))
                        else "new"
                        if name in set(report.get("new_tools", ()))
                        else prior_reviews.get(name, "reviewed")
                    )
                    for name in discovered_names
                }
                updated = self.db.upsert_connector_connection(
                    connection_id,
                    provider_id=definition.id,
                    driver=definition.driver.value,
                    auth_type=definition.auth_type,
                    status=ConnectionStatus.CONNECTED.value,
                    account_label=result.account_label,
                    granted_scopes=result.granted_scopes,
                    enabled_tools=enabled_tools,
                    tool_policy=policy,
                    remote_account_id=result.remote_account_id,
                    last_verified_at=utc_now(),
                )
        except Exception as error:
            code = self._error_code(error)
            with self._lock:
                current = self.db.get_connector_connection(connection_id)
                if (
                    current is not None
                    and current["status"] != ConnectionStatus.REVOKING.value
                    and int(current.get("operation_revision") or 0) == revision
                    and self._operations.get(connection_id) == operation_id
                ):
                    self.db.upsert_connector_connection(
                        connection_id,
                        provider_id=definition.id,
                        driver=definition.driver.value,
                        auth_type=definition.auth_type,
                        status=ConnectionStatus.ATTENTION.value,
                        last_error_code=code,
                        last_error_message=self._friendly_error(code),
                    )
            raise ValueError(self._friendly_error(code)) from error
        self._notify_runtime_change()
        view = self._connection_view(updated)
        view.update(
            {
                "operation_id": operation_id,
                "operation_revision": revision,
                "inventory_change": report,
            }
        )
        return view

    def reconnect(
        self,
        connection_id: str,
        *,
        secret: dict[str, Any] | None = None,
        operation_id: str | None = None,
        operation_revision: int | None = None,
        origin: str = "connectors_ui",
    ) -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            raise ValueError("I couldn't find that connection.")
        with self._lock:
            if connection_id in self._connecting:
                raise ValueError("That connection is still stopping. Try again in a moment.")
        current_revision = int(row.get("operation_revision") or 0)
        if operation_revision is not None and operation_revision != current_revision:
            raise ValueError("The connection changed. Refresh it and try again.")
        self.db.advance_connector_operation(connection_id, current_revision)
        stored = self.db.get_connector_definition(str(row.get("definition_id") or ""))
        definition = self._definition_for_row(row)
        if stored is None or definition is None:
            raise ValueError("Those saved connection details are no longer available.")
        config = _json_value(stored.get("config_json"), {})
        installed = InstalledConnectorDefinition.from_dict(
            {
                "id": str(stored["id"]),
                "provider_id": stored.get("provider_id"),
                "recipe_id": stored.get("recipe_id"),
                "recipe_version": stored.get("recipe_version"),
                "driver": stored["driver"],
                "transport": stored["transport"],
                "auth_strategy": stored["auth_strategy"],
                "provenance": stored["provenance"],
                "unresolved": bool(stored.get("unresolved")),
                **config,
            }
        )
        pending_static = None
        if definition.auth_type in {"token", "headers"} and secret:
            value = secret.get("token") or secret.get("value")
            headers = secret.get("headers")
            if isinstance(headers, dict):
                if list(headers) != [definition.header_name]:
                    raise ValueError(
                        "The submitted authentication header does not match the definition."
                    )
                value = headers[definition.header_name]
            if not isinstance(value, str) or not value:
                raise ValueError("Enter the credential for this connection.")
            pending_static = {
                "kind": definition.auth_type,
                "value": value,
                "header_name": definition.header_name,
            }
        return self._connect(
            definition,
            origin=origin,
            replace_connection_id=connection_id,
            connection_id=connection_id,
            installed=installed,
            pending_static=pending_static,
            operation_id=operation_id,
        )

    def update(
        self,
        connection_id: str,
        *,
        display_name: str | None = None,
        enabled_capabilities: list[str] | None = None,
        enabled_tools: list[str] | None = None,
        approval_preference: str | None = None,
    ) -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            raise ValueError("I couldn't find that connection.")
        policy = _json_value(row.get("tool_policy_json"), {})
        if approval_preference:
            policy["_approval_preference"] = approval_preference
        if enabled_tools is not None:
            known = {
                str(tool["remote_tool_name"])
                for tool in self.db.list_connector_tools(connection_id)
            }
            if "*" in enabled_tools or not set(enabled_tools) <= known:
                raise ValueError("Choose tools discovered for this connection.")
            reviews = policy.get("_tool_review_status", {})
            policy["_tool_review_status"] = {
                name: "reviewed" if name in enabled_tools else reviews.get(name, "reviewed")
                for name in known
            }
        updated = self.db.upsert_connector_connection(
            connection_id,
            provider_id=str(row["provider_id"]),
            display_name=(display_name or "").strip() or None,
            driver=str(row["driver"]),
            auth_type=str(row["auth_type"]),
            status=str(row["status"]),
            enabled_capabilities=enabled_capabilities,
            enabled_tools=enabled_tools,
            tool_policy=policy,
        )
        self._notify_runtime_change()
        return self._connection_view(updated)

    def remove(
        self,
        connection_id: str,
        *,
        origin: str = "connectors_ui",
        operation_id: str | None = None,
        operation_revision: int | None = None,
    ) -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            return {
                "connection_id": connection_id,
                "status": "disconnected",
                "remote_revocation": RemoteRevocationStatus.NOT_APPLICABLE.value,
            }
        definition = self._definition_for_row(row)
        with self._lock:
            expected = int(row.get("operation_revision") or 0)
            if operation_revision is not None and operation_revision != expected:
                raise ValueError("The connection changed. Refresh it and try again.")
            revision = self.db.advance_connector_operation(connection_id, expected)
            operation_id = operation_id or f"op_{uuid.uuid4().hex}"
            self._operations[connection_id] = operation_id
            from collie_core.connectors.auth import cancel_oauth_connection

            cancel_oauth_connection(connection_id)
            # A concurrent connect() must not resurrect this connection.
            self._cancelled.add(connection_id)
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=str(row["provider_id"]),
                driver=str(row["driver"]),
                auth_type=str(row["auth_type"]),
                status=ConnectionStatus.REVOKING.value,
            )
        remote_revocation = RemoteRevocationStatus.UNSUPPORTED
        if definition is not None:
            try:
                result_box: list[Any] = []
                error_box: list[Exception] = []
                done = threading.Event()

                def revoke_remote() -> None:
                    try:
                        result_box.append(
                            self._driver_factory(definition).revoke(definition, connection_id)
                        )
                    except Exception as exc:
                        error_box.append(exc)
                    finally:
                        done.set()

                threading.Thread(target=revoke_remote, daemon=True).start()
                if not done.wait(10):
                    raise TimeoutError("Remote revocation timed out")
                if error_box:
                    raise RuntimeError("Remote revocation failed") from error_box[0]
                outcome = result_box[0] if result_box else None
                remote_revocation = RemoteRevocationStatus(
                    outcome or RemoteRevocationStatus.REVOKED
                )
            except Exception:
                remote_revocation = RemoteRevocationStatus.FAILED
                logger.warning("Remote connector revocation unavailable: {}", definition.id)
        with self._lock:
            if connection_id not in self._connecting:
                self._cancelled.discard(connection_id)
            self.credentials.delete(f"connector:{connection_id}")
            self.db.delete_connector_connection(connection_id)
        logger.info("Connector removed: {}", connection_id)
        self._notify_runtime_change()
        return {
            "connection_id": connection_id,
            "provider_id": row["provider_id"],
            "status": "disconnected",
            "origin": origin,
            "remote_revocation": remote_revocation.value,
            "operation_id": operation_id,
            "operation_revision": revision,
        }

    # -- runtime and legacy facade ------------------------------------------

    def mcp_servers_for_config(self) -> dict[str, dict[str, Any]]:
        servers: dict[str, dict[str, Any]] = {}
        for row in self.db.list_connector_connections():
            if row["status"] != ConnectionStatus.CONNECTED.value:
                continue
            definition = self._definition_for_row(row)
            if (
                not definition
                or not definition.available
                or definition.driver
                not in (ConnectorDriverKind.OFFICIAL_MCP, ConnectorDriverKind.CUSTOM_MCP)
                or row["driver"] != definition.driver.value
                or not self._has_credentials(row)
            ):
                continue
            custom = definition.driver == ConnectorDriverKind.CUSTOM_MCP
            name = str(row["id"]) if custom else f"{definition.id}_{str(row['id'])[-8:]}"
            policy = _json_value(row.get("tool_policy_json"), {})
            inventory = {
                str(tool["remote_tool_name"]): {
                    "schema_hash": str(tool["schema_hash"]),
                    "risk": str(tool["risk"]),
                    "enabled": bool(tool.get("enabled")),
                }
                for tool in self.db.list_connector_tools(str(row["id"]))
            }
            inventory_fingerprint = hashlib.sha256(
                json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            servers[name] = {
                "type": "sse"
                if definition.transport == ConnectorTransport.SSE
                else "streamableHttp",
                "url": definition.endpoint,
                "toolTimeout": 60,
                "enabledTools": _json_value(row.get("enabled_tools_json"), ["*"]),
                "oauthConnectionId": row["id"] if definition.auth_type == "oauth" else "",
                "connectorProviderId": definition.id,
                "connectorTrusted": not custom,
                "connectorEndpointPolicy": True,
                "connectorAllowPrivateNetwork": definition.allow_private_network,
                "connectorToolOverrides": definition.tool_overrides,
                "connectorApprovalPreference": policy.get("_approval_preference", "important"),
                "connectorConnectionId": row["id"],
                "connectorAccountLabel": row.get("account_label")
                or row.get("display_name")
                or definition.name,
                "connectorAuthType": definition.auth_type,
                "connectorHeaderName": definition.header_name or "",
                "connectorOAuthConfig": {
                    "scopes": list(definition.scopes),
                    "oauth_registration": definition.oauth_registration,
                    "client_id": definition.client_id,
                    "redirect_uri": definition.redirect_uri,
                    "issuer": definition.issuer,
                    "resource": definition.resource,
                    "client_metadata_url": definition.client_metadata_url,
                }
                if definition.auth_type == "oauth"
                else {},
                "connectorToolInventory": inventory,
                "connectorInventoryFingerprint": inventory_fingerprint,
            }
        return servers

    def is_connected(self, provider_id: str) -> bool:
        definition = connector_def(provider_id)
        if definition is None or not definition.available:
            return False
        return any(
            row["status"] == ConnectionStatus.CONNECTED.value and self._has_credentials(row)
            for row in self.db.list_connector_connections(provider_id)
        )

    def disconnect(self, provider_id: str) -> dict[str, Any]:
        row = next(iter(self.db.list_connector_connections(provider_id)), None)
        if row is None:
            return {
                "service_id": provider_id,
                "status": "disconnected",
                "remote_revocation": RemoteRevocationStatus.NOT_APPLICABLE.value,
            }
        result = self.remove(str(row["id"]))
        return {
            "service_id": provider_id,
            "connection_id": result["connection_id"],
            "status": result["status"],
            "remote_revocation": result["remote_revocation"],
        }

    @staticmethod
    def _error_code(error: Exception) -> str:
        message = str(error).lower()
        if "declin" in message or "denied" in message or "cancel" in message:
            return "oauth_cancelled"
        if "scope" in message:
            return "scope_denied"
        if "time" in message:
            return "callback_timeout"
        if "401" in message or "refresh" in message:
            return "token_refresh_failed"
        if "tool" in message:
            return "tool_discovery_failed"
        if "admin" in message or "organization" in message:
            return "account_admin_blocked"
        return "server_unreachable"

    @staticmethod
    def _friendly_error(code: str) -> str:
        return {
            "oauth_cancelled": "Sign-in was cancelled. Nothing was connected.",
            "scope_denied": "The provider did not grant the access Collie needs.",
            "callback_timeout": "Sign-in took too long. Give it another go?",
            "token_refresh_failed": "Your sign-in needs a quick refresh. Reconnect?",
            "tool_discovery_failed": "The provider connected, but I couldn't check its tools.",
            "account_admin_blocked": "Your organization needs an admin to allow this app.",
            "server_unreachable": "I couldn't reach the provider. Check your connection and try again.",
        }[code]
