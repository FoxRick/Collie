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
            "Connect an official provider account. This opens provider sign-in only "
            "after Collie's connection approval is accepted."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider_id": {
                    "type": "string",
                    "description": "Provider ID returned by list_connectors.",
                }
            },
            "required": ["provider_id"],
        }

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        provider_id = str(params.get("provider_id") or "provider")
        return PermissionRequest(
            action="connector.connect",
            resource=provider_id,
            risk=Risk.SENSITIVE,
            summary=f"Connect Collie to {provider_id}",
            reversible=True,
            data_leaving_device=(provider_id,),
            suggested_scope=Scope.ONCE,
            redacted_parameters={"provider_id": provider_id},
            hard_approval=True,
        )

    async def execute(self, provider_id: str, **kwargs: Any) -> str:
        result = await asyncio.to_thread(_manager().connect, provider_id, None, origin="chat")
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
