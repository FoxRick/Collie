"""Service, connector, and subagent storage.

Split out of :mod:`collie_core.db` so that one storage domain lives in one
module. The methods are mixed into :class:`collie_core.db.CollieDB`, which stays
the single public interface for storage, so call sites keep using
``db.<method>``. They depend only on the ``CollieDB`` plumbing (``_write``,
``_write_immediate``, ``_row``, ``_rows``, ``_local_today``) and never on another
domain.

Tables owned here: ``services``, ``connector_connections``, ``connector_definitions``,
``connector_tool_cache`` and ``subagents``.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.db_primitives import new_id, utc_now


class ConnectorsDomain:
    """Service, connector, and subagent storage. Mixed into :class:`CollieDB` by composition,
    never a domain to domain call."""

    def upsert_service(
        self,
        service_id: str,
        *,
        name: str,
        provider: str,
        auth_type: str = "oauth",
        status: str = "disconnected",
        account_info: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO services (id, name, provider, auth_type, status, "
                "account_info, connected_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
                "account_info = excluded.account_info, last_error = excluded.last_error, "
                "connected_at = CASE WHEN excluded.status = 'connected' "
                "THEN excluded.connected_at ELSE services.connected_at END",
                (
                    service_id,
                    name,
                    provider,
                    auth_type,
                    status,
                    account_info,
                    utc_now() if status == "connected" else None,
                    last_error,
                ),
            )

    def get_service(self, service_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM services WHERE id = ?", (service_id,))

    def list_services(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM services ORDER BY name COLLATE NOCASE")

    def upsert_connector_connection(
        self,
        connection_id: str,
        *,
        provider_id: str,
        driver: str,
        auth_type: str,
        status: str,
        display_name: str | None = None,
        account_label: str | None = None,
        granted_scopes: list[str] | None = None,
        enabled_capabilities: list[str] | None = None,
        enabled_tools: list[str] | None = None,
        tool_policy: dict[str, Any] | None = None,
        remote_account_id: str | None = None,
        last_verified_at: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
        definition_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._write() as conn:
            existing = conn.execute(
                "SELECT definition_id, provider_id, driver FROM connector_connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
            if (
                existing
                and definition_id is not None
                and existing["definition_id"] is not None
                and definition_id != existing["definition_id"]
            ):
                raise ValueError("An installed connector definition cannot be replaced in place.")
            definition_id = (
                definition_id
                or (
                    str(existing["definition_id"])
                    if existing and existing["definition_id"]
                    else None
                )
                or f"def_{connection_id}"
            )
            associated = conn.execute(
                "SELECT provider_id, driver FROM connector_definitions WHERE id = ?",
                (definition_id,),
            ).fetchone()
            if associated and (
                associated["provider_id"] not in (None, provider_id)
                or associated["driver"] != driver
            ):
                raise ValueError("The connector definition does not match this account.")
            conn.execute(
                "INSERT OR IGNORE INTO connector_definitions "
                "(id, provider_id, recipe_id, driver, transport, auth_strategy, provenance, "
                "config_json, unresolved, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'curated', '{}', 1, ?, ?)",
                (
                    definition_id,
                    provider_id,
                    provider_id,
                    driver,
                    "api" if driver.endswith("api") else "streamable_http",
                    "oauth"
                    if auth_type == "oauth"
                    else ("none" if auth_type == "none" else "token"),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO connector_connections (
                    id, provider_id, display_name, account_label, driver, auth_type,
                    status, granted_scopes_json, enabled_capabilities_json,
                    enabled_tools_json, tool_policy_json, remote_account_id,
                    connected_at, updated_at, last_verified_at, last_error_code,
                    last_error_message, definition_id, credential_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, display_name),
                    account_label = COALESCE(excluded.account_label, account_label),
                    status = excluded.status,
                    granted_scopes_json = COALESCE(
                        excluded.granted_scopes_json, granted_scopes_json
                    ),
                    enabled_capabilities_json = COALESCE(
                        excluded.enabled_capabilities_json, enabled_capabilities_json
                    ),
                    enabled_tools_json = COALESCE(
                        excluded.enabled_tools_json, enabled_tools_json
                    ),
                    tool_policy_json = COALESCE(
                        excluded.tool_policy_json, tool_policy_json
                    ),
                    remote_account_id = COALESCE(
                        excluded.remote_account_id, remote_account_id
                    ),
                    connected_at = CASE
                        WHEN excluded.status = 'connected'
                        THEN COALESCE(connected_at, excluded.connected_at)
                        ELSE connected_at
                    END,
                    updated_at = excluded.updated_at,
                    last_verified_at = COALESCE(
                        excluded.last_verified_at, last_verified_at
                    ),
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message
                """,
                (
                    connection_id,
                    provider_id,
                    display_name,
                    account_label,
                    driver,
                    auth_type,
                    status,
                    json.dumps(granted_scopes) if granted_scopes is not None else None,
                    (
                        json.dumps(enabled_capabilities)
                        if enabled_capabilities is not None
                        else None
                    ),
                    json.dumps(enabled_tools) if enabled_tools is not None else None,
                    json.dumps(tool_policy) if tool_policy is not None else None,
                    remote_account_id,
                    now if status == "connected" else None,
                    now,
                    last_verified_at,
                    last_error_code,
                    last_error_message,
                    definition_id,
                    f"connector:{connection_id}",
                ),
            )
            if enabled_tools is not None:
                conn.execute(
                    "UPDATE connector_tool_cache SET enabled = CASE WHEN EXISTS "
                    "(SELECT 1 FROM json_each(?) WHERE value = '*' "
                    "OR value = remote_tool_name) THEN 1 ELSE 0 END WHERE connection_id = ?",
                    (json.dumps(enabled_tools), connection_id),
                )
        return self.get_connector_connection(connection_id)  # type: ignore[return-value]

    def get_connector_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM connector_connections WHERE id = ?", (connection_id,))

    def list_connector_connections(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        if provider_id:
            return self._rows(
                "SELECT * FROM connector_connections WHERE provider_id = ? "
                "ORDER BY updated_at DESC",
                (provider_id,),
            )
        return self._rows("SELECT * FROM connector_connections ORDER BY updated_at DESC")

    def delete_connector_connection(self, connection_id: str) -> None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT definition_id FROM connector_connections WHERE id = ?", (connection_id,)
            ).fetchone()
            conn.execute("DELETE FROM connector_connections WHERE id = ?", (connection_id,))
            if row and row["definition_id"]:
                conn.execute(
                    "DELETE FROM connector_definitions WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM connector_connections WHERE definition_id = ?)",
                    (row["definition_id"], row["definition_id"]),
                )

    def save_connector_definition(self, definition: Any) -> dict[str, Any]:
        """Persist a validated, secret-free installed definition snapshot."""
        from collie_core.connectors.models import InstalledConnectorDefinition

        if not isinstance(definition, InstalledConnectorDefinition):
            definition = InstalledConnectorDefinition.from_dict(definition)
        value = definition.to_dict()
        config = {
            key: value[key]
            for key in (
                "endpoint",
                "oauth_registration",
                "client_id",
                "scopes",
                "trusted_hosts",
                "tool_overrides",
            )
            if value[key] not in (None, [], "")
        }
        now = utc_now()
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM connector_definitions WHERE id = ?", (definition.id,)
            ).fetchone()
            if current is not None and not current["unresolved"]:
                current_config = json.loads(str(current["config_json"]))
                immutable = (
                    current["provider_id"],
                    current["recipe_id"],
                    current["recipe_version"],
                    current["driver"],
                    current["transport"],
                    current["auth_strategy"],
                    current["provenance"],
                    current_config,
                )
                proposed = (
                    definition.provider_id,
                    definition.recipe_id,
                    definition.recipe_version,
                    definition.driver.value,
                    definition.transport.value,
                    definition.auth_strategy.value,
                    definition.provenance.value,
                    config,
                )
                if immutable != proposed:
                    raise ValueError("Installed connector definitions are immutable.")
                return dict(current)
            conn.execute(
                "INSERT INTO connector_definitions (id, provider_id, recipe_id, recipe_version, "
                "driver, transport, auth_strategy, provenance, config_json, unresolved, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET provider_id=excluded.provider_id, "
                "recipe_id=excluded.recipe_id, recipe_version=excluded.recipe_version, "
                "driver=excluded.driver, transport=excluded.transport, "
                "auth_strategy=excluded.auth_strategy, provenance=excluded.provenance, "
                "config_json=excluded.config_json, unresolved=excluded.unresolved, "
                "updated_at=excluded.updated_at",
                (
                    definition.id,
                    definition.provider_id,
                    definition.recipe_id,
                    definition.recipe_version,
                    definition.driver.value,
                    definition.transport.value,
                    definition.auth_strategy.value,
                    definition.provenance.value,
                    json.dumps(config, sort_keys=True),
                    int(definition.unresolved),
                    now,
                    now,
                ),
            )
        return self.get_connector_definition(definition.id)  # type: ignore[return-value]

    def get_connector_definition(self, definition_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM connector_definitions WHERE id = ?", (definition_id,))

    def list_connector_definitions(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM connector_definitions ORDER BY created_at, id")

    def advance_connector_operation(self, connection_id: str, expected_revision: int) -> int:
        """Claim the next lifecycle operation; reject absent or stale accounts."""
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE connector_connections SET operation_revision = operation_revision + 1, "
                "updated_at = ? WHERE id = ? AND operation_revision = ?",
                (utc_now(), connection_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("The connection changed or no longer exists.")
        return expected_revision + 1

    def replace_connector_tools(self, connection_id: str, tools: list[dict[str, Any]]) -> None:
        with self._write() as conn:
            account = conn.execute(
                "SELECT enabled_tools_json FROM connector_connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
            configured = (
                json.loads(account["enabled_tools_json"])
                if (account and account["enabled_tools_json"] is not None)
                else None
            )
            conn.execute(
                "DELETE FROM connector_tool_cache WHERE connection_id = ?",
                (connection_id,),
            )
            conn.executemany(
                "INSERT INTO connector_tool_cache "
                "(connection_id, remote_tool_name, schema_hash, annotations_json, "
                "risk, discovered_at, tool_identity, description, input_schema_json, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        connection_id,
                        str(tool["name"]),
                        str(tool["schema_hash"]),
                        json.dumps(tool.get("annotations") or {}),
                        str(tool["risk"]),
                        utc_now(),
                        f"{len(connection_id)}:{connection_id}:"
                        f"{len(str(tool['name']))}:{tool['name']}",
                        tool.get("description"),
                        json.dumps(tool.get("input_schema"))
                        if tool.get("input_schema") is not None
                        else None,
                        int(
                            bool(
                                tool.get(
                                    "enabled",
                                    (
                                        configured is None
                                        or "*" in configured
                                        or str(tool["name"]) in configured
                                    ),
                                )
                            )
                        ),
                    )
                    for tool in tools
                ],
            )

    def list_connector_tools(self, connection_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM connector_tool_cache WHERE connection_id = ? ORDER BY remote_tool_name",
            (connection_id,),
        )

    def upsert_subagent(
        self,
        name: str,
        *,
        description: str = "",
        system_prompt: str = "",
        filename: str = "",
        execution_posture: str = "read_only",
        subagent_id: str | None = None,
    ) -> dict[str, Any]:
        sid = subagent_id or new_id()
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO subagents (id, name, description, system_prompt, filename, "
                "execution_posture, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "description = excluded.description, system_prompt = excluded.system_prompt, "
                "filename = excluded.filename, execution_posture = excluded.execution_posture, "
                "updated_at = excluded.updated_at",
                (
                    sid,
                    name,
                    description,
                    system_prompt,
                    filename,
                    execution_posture,
                    now,
                    now,
                ),
            )
        return self._row("SELECT * FROM subagents WHERE id = ?", (sid,))  # type: ignore[return-value]

    def list_subagents(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM subagents ORDER BY name COLLATE NOCASE")

    def delete_subagent(self, subagent_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM subagents WHERE id = ?", (subagent_id,))
