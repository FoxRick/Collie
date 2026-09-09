"""Chat tools for discovering and managing connected apps."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.services.manager import get_service_manager
from nanobot.agent.tools.base import Tool


def _manager() -> Any:
    manager = get_service_manager()
    if manager is None or not hasattr(manager, "list_connections"):
        raise RuntimeError("Connectors are not available in this runtime.")
    return manager


_SENSITIVE_DEFINITION_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credentials",
        "headers",
        "import",
        "mcpservers",
        "raw_json",
        "secret",
        "token",
    }
)
_SAFE_DEFINITION_FIELDS = (
    "name",
    "endpoint",
    "transport",
    "auth_strategy",
    "oauth_registration",
    "client_id",
    "scopes",
    "redirect_uri",
    "issuer",
    "resource",
    "allow_private_network",
)


def _safe_definition(params: dict[str, Any]) -> dict[str, Any]:
    def contains_sensitive(value: Any) -> bool:
        if isinstance(value, dict):
            if {str(key).lower() for key in value} & _SENSITIVE_DEFINITION_FIELDS:
                return True
            return any(contains_sensitive(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_sensitive(item) for item in value)
        return False

    if contains_sensitive(params):
        raise ValueError(
            "Credentials and imported configuration must be entered in Add connection, "
            "where Collie can keep them out of chat."
        )
    return {key: params[key] for key in _SAFE_DEFINITION_FIELDS if key in params}


def _definition_properties() -> dict[str, Any]:
    return {
        "name": {"type": "string", "description": "A friendly name for this connection."},
        "endpoint": {"type": "string", "description": "The exact remote MCP HTTPS endpoint."},
        "transport": {"type": "string", "enum": ["streamable_http", "sse"]},
        "auth_strategy": {"type": "string", "enum": ["none", "oauth"]},
        "oauth_registration": {"type": "string", "enum": ["automatic", "preregistered"]},
        "client_id": {"type": "string", "description": "Public OAuth client ID, if required."},
        "scopes": {"type": "array", "items": {"type": "string"}},
        "redirect_uri": {"type": "string"},
        "issuer": {"type": "string"},
        "resource": {"type": "string"},
        "allow_private_network": {"type": "boolean"},
    }


class PreviewConnectorTool(Tool):
    @property
    def name(self) -> str:
        return "preview_connector"

    @property
    def description(self) -> str:
        return (
            "Validate and preview a secret-free remote MCP connection without contacting it. "
            "Token, header, and imported-secret connections must use Add connection."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": _definition_properties(), "required": ["endpoint"]}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        result = _manager().validate_definition(_safe_definition(kwargs))
        return json.dumps(
            {key: result.get(key) for key in ("valid", "preview", "warnings", "errors")},
            ensure_ascii=False,
        )


class ListConnectorsTool(Tool):
    @property
    def name(self) -> str:
        return "list_connectors"

    @property
    def description(self) -> str:
        return (
            "List Collie's supported connected apps and existing account connections. "
            "Use this before asking the user which provider or account they mean."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        manager = _manager()
        return json.dumps(
            {
                "catalog": manager.catalog_view(),
                "connections": manager.list_connections(),
            },
            ensure_ascii=False,
        )


class ConnectConnectorTool(Tool):
    @property
    def name(self) -> str:
        return "connect_connector"

    @property
    def description(self) -> str:
        return (
            "Connect an official provider account, or a secret-free no-auth/OAuth remote MCP "
            "definition after approval. Token/header connections must use Add connection."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider_id": {
                    "type": "string",
                    "description": "Provider ID returned by list_connectors.",
                },
                **_definition_properties(),
            },
            "anyOf": [{"required": ["provider_id"]}, {"required": ["endpoint"]}],
        }

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        provider_id = str(params.get("provider_id") or "")
        if provider_id:
            extras = _safe_definition(
                {key: value for key, value in params.items() if key != "provider_id"}
            )
            if extras:
                raise ValueError("Choose either an official provider or a custom endpoint.")
            details = {"provider_id": provider_id}
            resource = provider_id
            summary = f"Connect Collie to {provider_id}"
        else:
            details = _safe_definition(params)
            details.setdefault("transport", "streamable_http")
            details.setdefault("auth_strategy", "none")
            details.setdefault("scopes", [])
            details.setdefault("allow_private_network", False)
            if details["auth_strategy"] not in {"none", "oauth"}:
                raise ValueError(
                    "Open Add connection to enter token or header credentials securely."
                )
            endpoint = str(details.get("endpoint") or "remote MCP server")
            resource = endpoint
            summary = f"Connect Collie to {endpoint}"
        return PermissionRequest(
            action="connector.connect",
            resource=resource,
            risk=Risk.SENSITIVE,
            summary=summary,
            reversible=True,
            data_leaving_device=(resource,),
            suggested_scope=Scope.ONCE,
            redacted_parameters=details,
            hard_approval=True,
        )

    async def execute(self, provider_id: str = "", **kwargs: Any) -> str:
        manager = _manager()
        if provider_id:
            if _safe_definition(kwargs):
                raise ValueError("Choose either an official provider or a custom endpoint.")
            result = await asyncio.to_thread(manager.connect, provider_id, None, origin="chat")
        else:
            definition = _safe_definition(kwargs)
            strategy = str(definition.get("auth_strategy") or "none")
            if strategy not in {"none", "oauth"}:
                raise ValueError(
                    "Open Add connection to enter token or header credentials securely."
                )
            validated = manager.validate_definition(definition)
            if not validated.get("valid") or not isinstance(validated.get("definition"), dict):
                return json.dumps(validated, ensure_ascii=False)
            saved = manager.save_definition(validated["definition"])
            result = await asyncio.to_thread(
                manager.begin_auth,
                saved["definition_id"],
                display_name=definition.get("name"),
                origin="chat",
            )
        return json.dumps(result, ensure_ascii=False)


class DisconnectConnectorTool(Tool):
    @property
    def name(self) -> str:
        return "disconnect_connector"

    @property
    def description(self) -> str:
        return "Remove a connected app account and its local authorization."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "connection_id": {
                    "type": "string",
                    "description": "Connection ID returned by list_connectors.",
                }
            },
            "required": ["connection_id"],
        }

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        connection_id = str(params.get("connection_id") or "connection")
        return PermissionRequest(
            action="delete.destructive",
            resource=connection_id,
            risk=Risk.DESTRUCTIVE,
            summary="Remove this connected account from Collie",
            reversible=False,
            suggested_scope=Scope.ONCE,
            redacted_parameters={"connection_id": connection_id},
            hard_approval=True,
        )

    async def execute(self, connection_id: str, **kwargs: Any) -> str:
        result = await asyncio.to_thread(_manager().remove, connection_id, origin="chat")
        return json.dumps(result, ensure_ascii=False)
