"""Service manager: connect/disconnect services and materialize MCP configs.

Connect flow (F038):
1. UI sends ``connect_service`` over IPC.
2. OAuth services run the browser flow; api_key services validate the pasted
   fields; local (auth="none") services just flip on.
3. Credentials land in ``~/.collie/credentials/``; the ``services`` table
   tracks status for the Settings → Services tab.
4. The runtime rebuilds the agent, and ``mcp_servers_for_config`` injects the
   matching MCP server so its tools appear on the next turn.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import Any, Callable

from loguru import logger

from collie_core.db import CollieDB
from collie_core.services import oauth as service_oauth
from collie_core.services.catalog import (
    SERVICE_CATALOG,
    McpTemplate,
    ServiceDef,
    platform_supported,
    service_def,
)
from collie_core.services.credentials import CredentialStore

__all__ = ["ServiceManager", "bind_service_manager", "get_service_manager"]


class _Placeholders(dict):
    """format_map helper: unknown placeholders become empty strings."""

    def __missing__(self, key: str) -> str:
        return ""


class ServiceManager:
    """Owns the service catalog state for one Collie install."""

    def __init__(
        self,
        db: CollieDB,
        *,
        credentials: CredentialStore | None = None,
        oauth_runner: Callable[..., dict[str, Any]] | None = None,
        platform: str | None = None,
    ) -> None:
        self.db = db
        self.credentials = credentials or CredentialStore()
        self._oauth_runner = oauth_runner or service_oauth.run_oauth_flow
        self._platform = platform or sys.platform

    # -- catalog -----------------------------------------------------------

    def catalog_view(self) -> list[dict[str, Any]]:
        """Catalog merged with connection status for the Services tab."""
        rows = {row["id"]: row for row in self.db.list_services()}
        view: list[dict[str, Any]] = []
        for service in SERVICE_CATALOG:
            row = rows.get(service.id) or {}
            supported = platform_supported(service, self._platform)
            view.append({
                "id": service.id,
                "name": service.name,
                "category": service.category,
                "description": service.description,
                "auth": service.auth,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "secret": f.secret,
                        "placeholder": f.placeholder,
                    }
                    for f in service.fields
                ],
                "permissions": list(service.permissions),
                "available": service.available and supported,
                "release_status": service.release_status,
                "note": service.note or (
                    "" if supported else "Only available on macOS."
                ),
                "status": row.get("status") or (
                    "disconnected" if service.available and supported else "coming_soon"
                ),
                "account_info": row.get("account_info"),
                "connected_at": row.get("connected_at"),
                "last_error": row.get("last_error"),
            })
        return view

    def is_connected(self, service_id: str) -> bool:
        row = self.db.get_service(service_id)
        return bool(row and row.get("status") == "connected")

    def connected_services(self) -> list[ServiceDef]:
        out: list[ServiceDef] = []
        for row in self.db.list_services():
            if row.get("status") != "connected":
                continue
            service = service_def(str(row["id"]))
            if service is not None:
                out.append(service)
        return out

    # -- connect / disconnect ----------------------------------------------

    def connect(
        self,
        service_id: str,
        credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Connect a service (blocking; run in a worker thread)."""
        service = service_def(service_id)
        if service is None:
            raise ValueError(f"I don't know a service called '{service_id}'.")
        if not service.available:
            raise ValueError(
                f"{service.name} is coming soon — its Connect button is disabled."
            )
        if not platform_supported(service, self._platform):
            raise ValueError(f"{service.name} only works on macOS.")

        account_info: str | None = None
        self.db.upsert_service(
            service.id,
            name=service.name,
            provider=service.category.lower(),
            auth_type=service.auth,
            status="connecting",
            last_error=None,
        )
        try:
            if service.auth == "oauth":
                if service.oauth is None:
                    raise ValueError(f"{service.name} is missing its sign-in setup.")
                tokens = self._oauth_runner(
                    service.oauth, service_name=service.name
                )
                self.credentials.save(service.id, tokens)
            elif service.auth == "api_key":
                creds = {
                    f.key: str((credentials or {}).get(f.key) or "").strip()
                    for f in service.fields
                }
                missing = [f.label for f in service.fields if not creds[f.key]]
                if missing:
                    raise ValueError(
                        f"I still need: {', '.join(missing)}. Paste it in and "
                        "I'll take it from there!"
                    )
                self.credentials.save(service.id, creds)
                first_public = next(
                    (f.key for f in service.fields if not f.secret), None
                )
                if first_public:
                    account_info = creds.get(first_public) or None
            # auth == "none": nothing to store
        except Exception as e:
            self.db.upsert_service(
                service.id,
                name=service.name,
                provider=service.category.lower(),
                auth_type=service.auth,
                status="failed",
                last_error=self._friendly_error(e),
            )
            raise

        self.db.upsert_service(
            service.id,
            name=service.name,
            provider=service.category.lower(),
            auth_type=service.auth,
            status="connected",
            account_info=account_info,
            last_error=None,
        )
        logger.info("Service connected: {}", service.id)
        return {"service_id": service.id, "status": "connected"}

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        if isinstance(error, ValueError):
            return str(error)
        return "I couldn't finish that connection. Check your internet and try again."

    def disconnect(self, service_id: str) -> dict[str, Any]:
        service = service_def(service_id)
        name = service.name if service else service_id
        self.credentials.delete(service_id)
        row = self.db.get_service(service_id)
        if row is not None:
            self.db.upsert_service(
                service_id,
                name=str(row.get("name") or name),
                provider=str(row.get("provider") or ""),
                auth_type=str(row.get("auth_type") or "oauth"),
                status="disconnected",
                account_info=None,
                last_error=None,
            )
        logger.info("Service disconnected: {}", service_id)
        return {"service_id": service_id, "status": "disconnected"}

    # -- MCP config materialization ------------------------------------------

    def mcp_servers_for_config(self) -> dict[str, dict[str, Any]]:
        """Build ``tools.mcpServers`` entries for all connected services."""
        servers: dict[str, dict[str, Any]] = {}
        for service in self.connected_services():
            if not service.mcp.command and not service.mcp.url:
                continue
            values = self._placeholder_values(service)
            servers[service.id] = _materialize(service.mcp, values)
        return servers

    def _placeholder_values(self, service: ServiceDef) -> _Placeholders:
        values = _Placeholders()
        creds = self.credentials.load(service.id) or {}
        if service.auth == "oauth" and service.oauth is not None:
            with suppress(Exception):
                fresh, refreshed = service_oauth.ensure_fresh_tokens(
                    service.oauth, creds
                )
                if refreshed:
                    self.credentials.save(service.id, fresh)
                creds = fresh
            client_id, client_secret = service_oauth.resolve_client(service.oauth)
            values["client_id"] = client_id
            values["client_secret"] = client_secret
            values["access_token"] = str(creds.get("access_token") or "")
            values["refresh_token"] = str(creds.get("refresh_token") or "")
        else:
            for key, value in creds.items():
                values[key] = str(value)
        return values


def _materialize(template: McpTemplate, values: _Placeholders) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if template.transport:
        cfg["type"] = template.transport
    if template.command:
        cfg["command"] = template.command
        cfg["args"] = [a.format_map(values) for a in template.args]
    if template.url:
        cfg["url"] = template.url.format_map(values)
    if template.env:
        cfg["env"] = {k: v.format_map(values) for k, v in template.env}
    if template.headers:
        cfg["headers"] = {k: v.format_map(values) for k, v in template.headers}
    cfg["toolTimeout"] = template.tool_timeout
    return cfg


_manager: ServiceManager | None = None


def bind_service_manager(manager: ServiceManager | None) -> None:
    global _manager
    _manager = manager


def get_service_manager() -> ServiceManager | None:
    return _manager
