"""Connection lifecycle, probing, policy caching, and runtime binding."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from typing import Any

from loguru import logger

from collie_core.connectors.catalog import CONNECTOR_CATALOG, connector_def
from collie_core.connectors.drivers.official_mcp import OfficialMcpDriver
from collie_core.connectors.models import (
    ConnectionStatus,
    ConnectorDefinition,
    ConnectorDriverKind,
    ProbeResult,
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
    ) -> None:
        self.db = db
        self.credentials = credentials or CredentialStore()
        self._driver_factory = driver_factory or self._default_driver
        self._lock = threading.RLock()
        self._cancelled: set[str] = set()
        self._connecting: set[str] = set()
        self._migrate_legacy_credentials()

    def _default_driver(self, definition: ConnectorDefinition) -> Any:
        if definition.driver == ConnectorDriverKind.OFFICIAL_MCP:
            return OfficialMcpDriver(self.credentials)
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
        data = self.credentials.load(f"connector:{row['id']}") or {}
        tokens = data.get("tokens") or {}
        return bool(tokens.get("access_token"))

    def _connection_view(self, row: dict[str, Any]) -> dict[str, Any]:
        definition = connector_def(str(row["provider_id"]))
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
            "permissions": list(definition.permissions) if definition else [],
            "capabilities": list(definition.capabilities) if definition else [],
            "route": "Official MCP"
            if definition and definition.driver == "official_mcp"
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

    # -- lifecycle -----------------------------------------------------------

    def connect(
        self,
        provider_id: str,
        credentials: dict[str, Any] | None = None,
        *,
        origin: str = "connectors_ui",
        replace_connection_id: str | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        del credentials  # Ordinary connector flows never accept renderer credentials.
        definition = connector_def(provider_id)
        if definition is None:
            raise ValueError(f"I don't know a connector called '{provider_id}'.")
        if not definition.available:
            raise ValueError(f"{definition.name} is coming soon in this build.")
        with self._lock:
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
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=definition.id,
                display_name=definition.name,
                driver=definition.driver.value,
                auth_type=definition.auth_type,
                status=ConnectionStatus.AUTHORIZING.value,
                enabled_capabilities=list(definition.capabilities),
            )
            self._connecting.add(connection_id)

        driver = self._driver_factory(definition)
        try:
            result: ProbeResult = driver.connect_and_probe(definition, connection_id)
            if connection_id in self._cancelled:
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
            self.db.replace_connector_tools(connection_id, result.tools)
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
            if code in _CREDENTIAL_DELETING_CODES:
                self.credentials.delete(f"connector:{connection_id}")
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
            cancelled = connection_id in self._cancelled
            self._cancelled.discard(connection_id)
            self._connecting.discard(connection_id)
            if cancelled:
                # Clean up credentials only after the probe thread is done
                # writing them (deleting mid-write resurrects the file).
                self.credentials.delete(f"connector:{connection_id}")
        logger.info("Connector connected: {} ({})", definition.id, connection_id)
        return {
            "provider_id": definition.id,
            "connection_id": connection_id,
            "status": row["status"],
            "origin": origin,
        }

    def cancel_auth(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.db.get_connector_connection(connection_id)
            if row is None or row["status"] not in {
                ConnectionStatus.AUTHORIZING.value,
                ConnectionStatus.TESTING.value,
            }:
                return {"connection_id": connection_id, "cancelled": False}
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
            return {"connection_id": connection_id, "cancelled": True}

    def test(self, connection_id: str) -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            raise ValueError("I couldn't find that connection.")
        definition = connector_def(str(row["provider_id"]))
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
            self.db.replace_connector_tools(connection_id, result.tools)
            updated = self.db.upsert_connector_connection(
                connection_id,
                provider_id=definition.id,
                driver=definition.driver.value,
                auth_type=definition.auth_type,
                status=ConnectionStatus.CONNECTED.value,
                account_label=result.account_label,
                granted_scopes=result.granted_scopes,
                enabled_tools=[tool["name"] for tool in result.tools],
                tool_policy={tool["name"]: tool["risk"] for tool in result.tools},
                remote_account_id=result.remote_account_id,
                last_verified_at=utc_now(),
            )
        except Exception as error:
            code = self._error_code(error)
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
        return self._connection_view(updated)

    def update(
        self,
        connection_id: str,
        *,
        display_name: str | None = None,
        enabled_capabilities: list[str] | None = None,
        approval_preference: str | None = None,
    ) -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            raise ValueError("I couldn't find that connection.")
        policy = _json_value(row.get("tool_policy_json"), {})
        if approval_preference:
            policy["_approval_preference"] = approval_preference
        updated = self.db.upsert_connector_connection(
            connection_id,
            provider_id=str(row["provider_id"]),
            display_name=(display_name or "").strip() or None,
            driver=str(row["driver"]),
            auth_type=str(row["auth_type"]),
            status=str(row["status"]),
            enabled_capabilities=enabled_capabilities,
            tool_policy=policy,
        )
        return self._connection_view(updated)

    def remove(self, connection_id: str, *, origin: str = "connectors_ui") -> dict[str, Any]:
        row = self.db.get_connector_connection(connection_id)
        if row is None:
            return {"connection_id": connection_id, "status": "disconnected"}
        definition = connector_def(str(row["provider_id"]))
        with self._lock:
            # A concurrent connect() must not resurrect this connection.
            self._cancelled.add(connection_id)
            self.db.upsert_connector_connection(
                connection_id,
                provider_id=str(row["provider_id"]),
                driver=str(row["driver"]),
                auth_type=str(row["auth_type"]),
                status=ConnectionStatus.REVOKING.value,
            )
        if definition is not None:
            try:
                import asyncio

                asyncio.run(
                    asyncio.wait_for(
                        asyncio.to_thread(
                            self._driver_factory(definition).revoke,
                            definition,
                            connection_id,
                        ),
                        timeout=10,
                    )
                )
            except Exception:
                logger.warning("Remote connector revocation unavailable: {}", definition.id)
        with self._lock:
            self._cancelled.discard(connection_id)
            self.credentials.delete(f"connector:{connection_id}")
            self.db.delete_connector_connection(connection_id)
        logger.info("Connector removed: {}", connection_id)
        return {
            "connection_id": connection_id,
            "provider_id": row["provider_id"],
            "status": "disconnected",
            "origin": origin,
        }

    # -- runtime and legacy facade ------------------------------------------

    def mcp_servers_for_config(self) -> dict[str, dict[str, Any]]:
        servers: dict[str, dict[str, Any]] = {}
        for row in self.db.list_connector_connections():
            if row["status"] != ConnectionStatus.CONNECTED.value:
                continue
            definition = connector_def(str(row["provider_id"]))
            if (
                not definition
                or not definition.available
                or definition.driver != ConnectorDriverKind.OFFICIAL_MCP
                or row["driver"] != definition.driver.value
                or not self._has_credentials(row)
            ):
                continue
            name = f"{definition.id}_{str(row['id'])[-8:]}"
            policy = _json_value(row.get("tool_policy_json"), {})
            servers[name] = {
                "type": "streamableHttp",
                "url": definition.endpoint,
                "toolTimeout": 60,
                "enabledTools": _json_value(row.get("enabled_tools_json"), ["*"]),
                "oauthConnectionId": row["id"],
                "connectorProviderId": definition.id,
                "connectorTrusted": True,
                "connectorToolOverrides": definition.tool_overrides,
                "connectorApprovalPreference": policy.get("_approval_preference", "important"),
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
            return {"service_id": provider_id, "status": "disconnected"}
        result = self.remove(str(row["id"]))
        return {
            "service_id": provider_id,
            "connection_id": result["connection_id"],
            "status": result["status"],
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
