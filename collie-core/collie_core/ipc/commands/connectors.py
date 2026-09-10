"""Service and connector commands.

Handlers for the ``connectors`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import asyncio
import uuid

from websockets.asyncio.server import ServerConnection


class ConnectorCommands:
    """Service and connector commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_list_connector_catalog(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            return {"connectors": []}
        return {"connectors": self._service_manager.catalog_view()}

    async def _cmd_list_connector_connections(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            return {"connections": []}
        return {"connections": self._service_manager.list_connections()}

    async def _cmd_get_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        item = self._service_manager.get_connection(connection_id)
        if item is None:
            raise ValueError("I couldn't find that connection.")
        return {"connection": item}

    async def _cmd_begin_connector_auth(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        provider_id = str(frame.get("provider_id") or "")
        origin = str(frame.get("origin") or "connectors_ui")
        connection_id = f"con_{uuid.uuid4().hex}"
        flow_id = f"caf_{uuid.uuid4().hex}"
        replace_connection_id = str(frame.get("replace_connection_id") or "") or None
        await self.broadcast(
            {
                "type": "connector_auth_started",
                "provider_id": provider_id,
                "connection_id": connection_id,
                "flow_id": flow_id,
                "origin": origin,
                "status": "authorizing",
            }
        )
        try:
            result = await asyncio.to_thread(
                self._service_manager.connect,
                provider_id,
                None,
                origin=origin,
                replace_connection_id=replace_connection_id,
                connection_id=connection_id,
            )
        except Exception as error:
            await self.broadcast(
                {
                    "type": "connector_failed",
                    "provider_id": provider_id,
                    "origin": origin,
                    "message": str(error),
                }
            )
            raise
        result["reconfigured"] = await self._reconfigure_quietly()
        await self.broadcast({"type": "connector_connected", **result})
        result["flow_id"] = flow_id
        return result

    async def _cmd_cancel_connector_auth(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        result = self._service_manager.cancel_auth(str(frame.get("connection_id") or ""))
        if result["cancelled"]:
            await self.broadcast(
                {
                    "type": "connector_failed",
                    "connection_id": result["connection_id"],
                    "status": "failed",
                    "message": "Sign-in was cancelled. Nothing was connected.",
                }
            )
        return result

    async def _cmd_test_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": "testing",
            }
        )
        item = await asyncio.to_thread(self._service_manager.test, connection_id)
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": item["status"],
            }
        )
        return {"connection": item}

    async def _cmd_update_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        capabilities = frame.get("enabled_capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            raise ValueError("enabled_capabilities must be a list")
        item = self._service_manager.update(
            str(frame.get("connection_id") or ""),
            display_name=(str(frame["display_name"]) if "display_name" in frame else None),
            enabled_capabilities=capabilities,
            approval_preference=(
                str(frame["approval_preference"]) if "approval_preference" in frame else None
            ),
        )
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": item["id"],
                "status": item["status"],
            }
        )
        return {"connection": item}

    async def _cmd_remove_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        result = await asyncio.to_thread(
            self._service_manager.remove,
            str(frame.get("connection_id") or ""),
            origin=str(frame.get("origin") or "connectors_ui"),
        )
        result["reconfigured"] = await self._reconfigure_quietly()
        await self.broadcast({"type": "connector_removed", **result})
        return result

    async def _cmd_list_connector_tools(self, connection: ServerConnection, frame: dict) -> dict:
        connection_id = str(frame.get("connection_id") or "")
        return {"tools": self.db.list_connector_tools(connection_id)}

    async def _cmd_list_services(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            return {"services": []}
        view = getattr(self._service_manager, "legacy_catalog_view", None)
        return {"services": (view() if callable(view) else self._service_manager.catalog_view())}

    async def _cmd_connect_service(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("services aren't available yet")
        service_id = str(frame.get("service_id") or "")
        credentials = frame.get("credentials")
        if credentials is not None and not isinstance(credentials, dict):
            raise ValueError("credentials must be an object")
        result = await asyncio.to_thread(self._service_manager.connect, service_id, credentials)
        result["reconfigured"] = await self._reconfigure_quietly()
        return result

    async def _cmd_disconnect_service(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("services aren't available yet")
        service_id = str(frame.get("service_id") or "")
        result = await asyncio.to_thread(self._service_manager.disconnect, service_id)
        result["reconfigured"] = await self._reconfigure_quietly()
        return result
