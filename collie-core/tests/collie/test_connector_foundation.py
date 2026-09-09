"""Persistent connector-definition and V16 migration contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import collie_core.db as db_mod
from collie_core.connectors.models import InstalledConnectorDefinition
from collie_core.db import CollieDB


def _v15(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA_V1)
    for migration in db_mod._MIGRATIONS[1:15]:
        conn.executescript(migration)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (15)")
    return conn


def test_v15_upgrade_preserves_accounts_permissions_and_inventory(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.db"
    conn = _v15(path)
    conn.execute(
        "INSERT INTO connector_connections "
        "(id, provider_id, driver, auth_type, status, enabled_tools_json, tool_policy_json, "
        "updated_at) VALUES ('account-a', 'unknown-provider', 'legacy_service', 'oauth', "
        "'connected', '[]', '{\"read\":\"every_time\"}', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO connector_tool_cache VALUES "
        "('account-a', 'same_tool', 'hash', '{\"readOnlyHint\":true}', 'read', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    db = CollieDB(path)
    row = db.get_connector_connection("account-a")
    definition = db.get_connector_definition("def_account-a")
    tools = db.list_connector_tools("account-a")
    assert row is not None and row["credential_ref"] == "connector:account-a"
    assert row["operation_revision"] == 0
    assert row["enabled_tools_json"] == "[]"
    assert json.loads(row["tool_policy_json"]) == {"read": "every_time"}
    assert definition is not None and definition["unresolved"] == 1
    assert definition["provider_id"] == "unknown-provider"
    assert tools[0]["tool_identity"] == "9:account-a:9:same_tool"
    assert tools[0]["enabled"] == 0
    db.close()


def test_definitions_are_validated_and_exported_without_credentials(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    with pytest.raises(ValueError, match="query or fragment"):
        InstalledConnectorDefinition.from_dict(
            {
                "id": "unsafe",
                "driver": "custom_mcp",
                "transport": "streamable_http",
                "auth_strategy": "token",
                "provenance": "custom",
                "endpoint": "https://example.test/mcp?key=secret",
            }
        )
    with pytest.raises(ValueError, match="list of strings"):
        InstalledConnectorDefinition.from_dict(
            {
                "id": "bad-list",
                "driver": "custom_mcp",
                "transport": "streamable_http",
                "auth_strategy": "none",
                "provenance": "custom",
                "endpoint": "https://example.test/mcp",
                "scopes": "read",
            }
        )
    definition = InstalledConnectorDefinition.from_dict(
        {
            "id": "custom-1",
            "driver": "custom_mcp",
            "transport": "streamable_http",
            "auth_strategy": "token",
            "provenance": "custom",
            "endpoint": "https://example.test/mcp",
            "trusted_hosts": ["example.test"],
        }
    )
    db.save_connector_definition(definition)
    exported = db.export_all()
    assert exported["connector_definitions"][0]["id"] == "custom-1"
    assert "secret" not in json.dumps(exported["connector_definitions"]).lower()
    db.clear_all()
    assert db.list_connector_definitions() == []
    db.close()


def test_account_scoped_tool_identity_and_operation_revision_cas(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    for account in ("first", "second"):
        db.upsert_connector_connection(
            account,
            provider_id="notion",
            driver="official_mcp",
            auth_type="oauth",
            status="connected",
            enabled_tools=[] if account == "first" else ["*"],
        )
        db.replace_connector_tools(
            account,
            [{"name": "search", "schema_hash": "h", "risk": "read"}],
        )
    identities = {
        db.list_connector_tools("first")[0]["tool_identity"],
        db.list_connector_tools("second")[0]["tool_identity"],
    }
    assert len(identities) == 2
    assert db.list_connector_tools("first")[0]["enabled"] == 0
    assert db.list_connector_tools("second")[0]["enabled"] == 1
    assert db.advance_connector_operation("first", 0) == 1
    with pytest.raises(ValueError, match="changed"):
        db.advance_connector_operation("first", 0)
    with pytest.raises(ValueError, match="no longer exists"):
        db.advance_connector_operation("missing", 0)
    db.delete_connector_connection("first")
    assert db.get_connector_definition("def_first") is None
    db.close()


def test_explicit_definition_association_is_retained_and_checked(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    definition = InstalledConnectorDefinition.from_dict(
        {
            "id": "shared-definition",
            "driver": "custom_mcp",
            "transport": "streamable_http",
            "auth_strategy": "none",
            "provenance": "custom",
            "provider_id": "custom",
            "endpoint": "https://example.test/mcp",
        }
    )
    db.save_connector_definition(definition)
    db.upsert_connector_connection(
        "account",
        provider_id="custom",
        driver="custom_mcp",
        auth_type="none",
        status="disconnected",
        definition_id="shared-definition",
    )
    db.upsert_connector_connection(
        "account",
        provider_id="custom",
        driver="custom_mcp",
        auth_type="none",
        status="failed",
    )
    assert db.get_connector_connection("account")["definition_id"] == "shared-definition"
    with pytest.raises(ValueError, match="does not match"):
        db.upsert_connector_connection(
            "other",
            provider_id="different",
            driver="custom_mcp",
            auth_type="none",
            status="disconnected",
            definition_id="shared-definition",
        )
    second = InstalledConnectorDefinition.from_dict(
        {
            "id": "second-definition",
            "driver": "custom_mcp",
            "transport": "streamable_http",
            "auth_strategy": "none",
            "provenance": "custom",
            "provider_id": "custom",
            "endpoint": "https://second.example.test/mcp",
        }
    )
    db.save_connector_definition(second)
    with pytest.raises(ValueError, match="cannot be replaced"):
        db.upsert_connector_connection(
            "account",
            provider_id="custom",
            driver="custom_mcp",
            auth_type="none",
            status="failed",
            definition_id="second-definition",
        )
    db.close()
