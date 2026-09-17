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
import json
import uuid
from typing import Any

from websockets.asyncio.server import ServerConnection


class ConnectorCommands:
    """Service and connector commands. Mixed into :class:`CollieIPCServer` by composition."""

    # -- connectors ---------------------------------------------------------------------

    @staticmethod
    def _connector_failure(error: Exception, *, stage: str) -> dict[str, Any]:
        """Return stable, credential-free failure details for renderer recovery UI."""
        failure = getattr(error, "failure", None)
        if isinstance(failure, dict):
            return {
                "code": str(failure.get("code") or "connection_failed"),
                "message": str(failure.get("message") or "That connection didn't work."),
                "recovery_action": str(
                    failure.get("recovery_action") or "Check the connection details and try again."
                ),
                "stage": str(failure.get("stage") or stage),
                "retryable": bool(failure.get("retryable", True)),
            }
        message = str(error) if isinstance(error, ValueError) else "That connection didn't work."
        return {
            "code": str(getattr(error, "code", None) or "connection_failed"),
            "message": message,
            "recovery_action": "Check the connection details and try again.",
            "stage": stage,
            "retryable": True,
        }

    async def _wait_for_connector_state(
        self, connection_id: str, statuses: set[str], *, attempts: int = 50
    ) -> dict[str, Any] | None:
        """Briefly yield until a worker reserves its row, keeping cancel races bounded."""
        for _ in range(attempts):
            item = self._service_manager.get_connection(connection_id)
            if item is not None and str(item.get("status")) in statuses:
                return item
            await asyncio.sleep(0.01)
        return self._service_manager.get_connection(connection_id)

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
        operation_id = f"op_{uuid.uuid4().hex}"
        replace_connection_id = str(frame.get("replace_connection_id") or "") or None
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._service_manager.connect,
                provider_id,
                None,
                origin=origin,
                replace_connection_id=replace_connection_id,
                connection_id=connection_id,
                operation_id=operation_id,
            )
        )
        reserved = await self._wait_for_connector_state(
            connection_id, {"authorizing", "testing", "failed"}
        )
        await self.broadcast(
            {
                "type": "connector_auth_started",
                "provider_id": provider_id,
                "connection_id": connection_id,
                "flow_id": flow_id,
                "operation_id": operation_id,
                "operation_revision": int((reserved or {}).get("operation_revision") or 0),
                "origin": origin,
                "status": "authorizing",
            }
        )
        try:
            result = await worker
        except Exception as error:
            current = self._service_manager.get_connection(connection_id)
            if current is not None and current.get("operation_id") == operation_id:
                await self.broadcast(
                    {
                        "type": "connector_failed",
                        "provider_id": provider_id,
                        "connection_id": connection_id,
                        "operation_id": operation_id,
                        "operation_revision": int(current.get("operation_revision") or 0),
                        "origin": origin,
                        "status": "failed",
                        "failure": self._connector_failure(error, stage="authenticate"),
                    }
                )
            raise
        result["reconfigured"] = await self._reconfigure_quietly()
        result["flow_id"] = flow_id
        current = self._service_manager.get_connection(str(result.get("connection_id") or ""))
        if current is not None and int(current.get("operation_revision") or 0) == int(
            result.get("operation_revision") or 0
        ):
            await self.broadcast({"type": "connector_connected", **result})
        return result

    async def _cmd_validate_connector_definition(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        definition = frame.get("definition")
        if not isinstance(definition, dict):
            raise ValueError("definition must be an object")
        result = self._service_manager.validate_definition(definition)
        errors = []
        for raw in result.get("errors", []):
            if isinstance(raw, dict):
                errors.append(
                    {
                        "code": str(raw.get("code") or "invalid_definition"),
                        "message": str(raw.get("message") or "Check the connection details."),
                        "recovery_action": str(
                            raw.get("recovery_action") or "Correct the highlighted details."
                        ),
                        "stage": str(raw.get("stage") or "validation"),
                        "retryable": bool(raw.get("retryable", True)),
                    }
                )
        result["errors"] = errors
        return result

    async def _cmd_preview_connector_import(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        source = frame.get("source")
        if not isinstance(source, (str, dict)):
            raise ValueError("source must be JSON text or an object")
        return self._service_manager.import_preview(source)

    async def _cmd_save_connector_definition(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        definition = frame.get("definition")
        secret = frame.get("secret")
        if not isinstance(definition, dict):
            raise ValueError("definition must be an object")
        if secret is not None:
            raise ValueError("credentials must be submitted when the connection begins")
        validated = self._service_manager.validate_definition(definition)
        if not validated.get("valid") or not isinstance(validated.get("definition"), dict):
            raise ValueError("Check the connection address and sign-in details.")
        return self._service_manager.save_definition(validated["definition"])

    async def _cmd_begin_definition_auth(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        definition_id = str(frame.get("definition_id") or "")
        if not definition_id:
            raise ValueError("definition_id is required")
        definition_row = self.db.get_connector_definition(definition_id)
        if definition_row is None:
            raise ValueError("I couldn't find those connection details.")
        secret = frame.get("secret")
        if str(definition_row.get("auth_strategy")) in {"token", "headers"} and not isinstance(
            secret, dict
        ):
            raise ValueError("Enter the credential for this connection.")
        connection_id = f"con_{uuid.uuid4().hex}"
        operation_id = f"op_{uuid.uuid4().hex}"
        origin = str(frame.get("origin") or "connectors_ui")
        kwargs = {
            "secret": secret,
            "display_name": frame.get("display_name"),
            "origin": origin,
            "connection_id": connection_id,
            "operation_id": operation_id,
        }

        worker = asyncio.create_task(
            asyncio.to_thread(self._service_manager.begin_auth, definition_id, **kwargs)
        )
        reserved = None
        for _ in range(50):
            reserved = self._service_manager.get_connection(connection_id)
            if reserved is not None:
                break
            if worker.done():
                # Validation/storage failures before row reservation belong to
                # this command; do not leave a phantom authorizing account.
                await worker
            await asyncio.sleep(0.01)
        if reserved is None and worker.done():
            await worker
        await self.broadcast(
            {
                "type": "connector_auth_started",
                "definition_id": definition_id,
                "connection_id": connection_id,
                "operation_id": operation_id,
                "operation_revision": int((reserved or {}).get("operation_revision") or 0),
                "origin": origin,
                "status": str((reserved or {}).get("status") or "authorizing"),
            }
        )

        async def finish() -> None:
            try:
                result = await worker
                result["reconfigured"] = await self._reconfigure_quietly()
                current = self._service_manager.get_connection(connection_id)
                if current is None or current.get("operation_id") != operation_id:
                    return
                if int(current.get("operation_revision") or 0) != int(
                    result.get("operation_revision") or 0
                ):
                    return
                await self.broadcast({"type": "connector_connected", **result})
            except Exception as error:
                current = self._service_manager.get_connection(connection_id)
                if current is None or current.get("operation_id") != operation_id:
                    return
                await self.broadcast(
                    {
                        "type": "connector_failed",
                        "connection_id": connection_id,
                        "operation_id": operation_id,
                        "operation_revision": int(current.get("operation_revision") or 0),
                        "origin": origin,
                        "status": "failed",
                        "failure": self._connector_failure(error, stage="authenticate"),
                    }
                )

        task = asyncio.create_task(finish())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return {
            "definition_id": definition_id,
            "connection_id": connection_id,
            "operation_id": operation_id,
            "operation_revision": int((reserved or {}).get("operation_revision") or 0),
            "status": str((reserved or {}).get("status") or "authorizing"),
        }

    async def _cmd_cancel_connector_auth(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        result = self._service_manager.cancel_auth(
            str(frame.get("connection_id") or ""),
            operation_id=frame.get("operation_id"),
            operation_revision=frame.get("operation_revision"),
        )
        if result["cancelled"]:
            await self.broadcast(
                {
                    "type": "connector_failed",
                    "connection_id": result["connection_id"],
                    "operation_id": frame.get("operation_id"),
                    "operation_revision": result.get("operation_revision", 0),
                    "status": "failed",
                    "failure": {
                        "code": "oauth_cancelled",
                        "message": "Sign-in was cancelled. Nothing was connected.",
                        "recovery_action": "Try signing in again when you're ready.",
                        "stage": "authorization",
                        "retryable": True,
                    },
                }
            )
        return result

    async def _cmd_reconnect_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        operation_id = str(frame.get("operation_id") or f"op_{uuid.uuid4().hex}")
        expected_revision = frame.get("operation_revision")
        origin = str(frame.get("origin") or "connectors_ui")

        result = await asyncio.to_thread(
            self._service_manager.reconnect,
            connection_id,
            secret=frame.get("secret"),
            operation_id=operation_id,
            operation_revision=expected_revision,
            origin=origin,
        )
        result["reconfigured"] = await self._reconfigure_quietly()
        current = self._service_manager.get_connection(connection_id)
        if current is not None and int(current.get("operation_revision") or 0) == int(
            result.get("operation_revision") or 0
        ):
            await self.broadcast({"type": "connector_connected", **result})
        return result

    async def _cmd_test_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        operation_id = f"op_{uuid.uuid4().hex}"
        expected_revision = frame.get("operation_revision")
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": "testing",
                "operation_id": operation_id,
            }
        )

        item = await asyncio.to_thread(
            self._service_manager.test,
            connection_id,
            operation_id=operation_id,
            operation_revision=expected_revision,
        )
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": item["status"],
                "operation_id": operation_id,
                "operation_revision": item.get("operation_revision", 0),
            }
        )
        return {"connection": item}

    async def _cmd_update_connector(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        capabilities = frame.get("enabled_capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            raise ValueError("enabled_capabilities must be a list")
        enabled_tools = frame.get("enabled_tools")
        if enabled_tools is not None and not isinstance(enabled_tools, list):
            raise ValueError("enabled_tools must be a list")
        item = self._service_manager.update(
            str(frame.get("connection_id") or ""),
            display_name=(str(frame["display_name"]) if "display_name" in frame else None),
            enabled_capabilities=capabilities,
            enabled_tools=enabled_tools,
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
        connection_id = str(frame.get("connection_id") or "")
        origin = str(frame.get("origin") or "connectors_ui")
        operation_id = f"op_{uuid.uuid4().hex}"
        expected_revision = frame.get("operation_revision")

        result = await asyncio.to_thread(
            self._service_manager.remove,
            connection_id,
            origin=origin,
            operation_id=operation_id,
            operation_revision=expected_revision,
        )
        result["reconfigured"] = await self._reconfigure_quietly()
        await self.broadcast({"type": "connector_removed", **result})
        return result

    async def _cmd_list_connector_tools(self, connection: ServerConnection, frame: dict) -> dict:
        connection_id = str(frame.get("connection_id") or "")
        inspect = getattr(self._service_manager, "inspect_tools", None)
        if callable(inspect):
            result = inspect(
                connection_id,
                query=str(frame.get("query") or ""),
                limit=min(max(int(frame.get("limit") or 50), 1), 100),
            )
            normalized_tools = []
            for tool in result.get("tools", []):
                schema = tool.get("input_schema")
                if schema is None:
                    try:
                        schema = json.loads(str(tool.get("input_schema_json") or "{}"))
                    except (TypeError, ValueError):
                        schema = {}
                normalized_tools.append(
                    {
                        **tool,
                        "name": str(tool.get("name") or tool.get("remote_tool_name") or ""),
                        "input_schema": schema if isinstance(schema, dict) else {},
                        "enabled": bool(tool.get("enabled")),
                    }
                )
            result["tools"] = normalized_tools
            return result
        tools = self.db.list_connector_tools(connection_id)[:50]
        return {"connection_id": connection_id, "total": len(tools), "tools": tools}

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
