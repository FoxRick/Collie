"""Runtime behavior for pinned installed connector recipe snapshots."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from collie_core.connectors import manager as manager_module
from collie_core.connectors.catalog import connector_def
from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import ConnectorDefinition, ProbeResult
from collie_core.db import CollieDB
from collie_core.services.credentials import CredentialStore


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(
        tmp_path / "credentials",
        protect=lambda value: value[::-1],
        unprotect=lambda value: value[::-1],
    )


def _connected(db: CollieDB, connection_id: str = "con_legacy_notion") -> None:
    db.upsert_connector_connection(
        connection_id,
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="connected",
    )


class _RecordingDriver:
    def __init__(self) -> None:
        self.definitions: list[ConnectorDefinition] = []

    def probe(self, definition: ConnectorDefinition, connection_id: str) -> ProbeResult:
        del connection_id
        self.definitions.append(definition)
        return ProbeResult(tools=[{"name": "read_page", "schema_hash": "h", "risk": "read"}])


def test_manager_initialization_backfills_known_legacy_recipe_snapshot(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    credentials = _store(tmp_path)
    _connected(db)
    credentials.save("notion", {"tokens": {"access_token": "legacy-token"}})

    ConnectorManager(db, credentials=credentials)

    row = db.get_connector_definition("def_con_legacy_notion")
    assert row is not None
    assert row["provider_id"] == "notion"
    assert row["unresolved"] == 0
    assert json.loads(row["config_json"])["endpoint"] == connector_def("notion").endpoint
    db.close()


def test_runtime_uses_pinned_snapshot_after_catalog_recipe_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    credentials = _store(tmp_path)
    _connected(db, "con_snapshot")
    credentials.save("connector:con_snapshot", {"tokens": {"access_token": "token"}})
    driver = _RecordingDriver()
    manager = ConnectorManager(db, credentials=credentials, driver_factory=lambda _: driver)

    original = connector_def("notion")
    assert original is not None
    mutated = replace(
        original,
        available=True,
        endpoint="https://changed.example/mcp",
        scopes=("changed",),
        tool_overrides={"read_page": "change"},
    )
    monkeypatch.setattr(
        manager_module,
        "connector_def",
        lambda provider_id: mutated if provider_id == "notion" else None,
    )

    config = manager.mcp_servers_for_config()
    server = next(iter(config.values()))
    assert server["url"] == original.endpoint
    assert server["connectorToolOverrides"] == original.tool_overrides

    manager.test("con_snapshot")
    assert driver.definitions[-1].endpoint == original.endpoint
    assert driver.definitions[-1].scopes == original.scopes
    assert driver.definitions[-1].tool_overrides == original.tool_overrides
    db.close()


def test_catalog_unavailability_disables_binding_even_with_pinned_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    credentials = _store(tmp_path)
    _connected(db, "con_unavailable")
    credentials.save("connector:con_unavailable", {"tokens": {"access_token": "token"}})
    manager = ConnectorManager(db, credentials=credentials)

    original = connector_def("notion")
    assert original is not None
    unavailable = replace(original, available=False)
    monkeypatch.setattr(
        manager_module,
        "connector_def",
        lambda provider_id: unavailable if provider_id == "notion" else None,
    )

    assert manager.mcp_servers_for_config() == {}
    db.close()


def test_probe_keeps_cached_enabled_flags_in_sync(tmp_path: Path, monkeypatch) -> None:
    recipe = replace(connector_def("notion"), available=True)
    monkeypatch.setattr(manager_module, "connector_def", lambda _: recipe)
    with CollieDB(tmp_path / "collie.db") as db:
        credentials = _store(tmp_path)
        db.upsert_connector_connection(
            "account",
            provider_id="notion",
            driver="official_mcp",
            auth_type="oauth",
            status="connected",
            enabled_tools=["old_tool"],
        )
        credentials.save("connector:account", {"tokens": {"access_token": "test-token"}})
        manager = ConnectorManager(
            db,
            credentials=credentials,
            driver_factory=lambda _: _RecordingDriver(),
        )
        manager.test("account")
        assert db.list_connector_tools("account")[0]["enabled"] == 1
        assert next(iter(manager.mcp_servers_for_config().values()))["enabledTools"] == [
            "read_page"
        ]
        db.upsert_connector_connection(
            "account",
            provider_id="notion",
            driver="official_mcp",
            auth_type="oauth",
            status="connected",
            enabled_tools=[],
        )
        assert db.list_connector_tools("account")[0]["enabled"] == 0


def test_manager_backfill_pins_recipe_endpoint_across_catalog_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.connectors import catalog
    from collie_core.connectors import manager as manager_module

    recipe = catalog.connector_def("notion")
    assert recipe is not None
    initial = replace(recipe, available=True, endpoint="https://old.example.test/mcp")
    monkeypatch.setitem(catalog._BY_ID, "notion", initial)
    monkeypatch.setattr(manager_module, "connector_def", catalog.connector_def)
    db = CollieDB(tmp_path / "collie.db")
    db.upsert_connector_connection(
        "old-account",
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="connected",
    )
    store = CredentialStore(
        tmp_path / "credentials",
        protect=lambda value: value[::-1],
        unprotect=lambda value: value[::-1],
    )
    store.save("connector:old-account", {"tokens": {"access_token": "kept-out-of-db"}})
    manager = ConnectorManager(db, credentials=store)
    changed = replace(initial, endpoint="https://new.example.test/mcp")
    monkeypatch.setitem(catalog._BY_ID, "notion", changed)

    servers = manager.mcp_servers_for_config()
    assert next(iter(servers.values()))["url"] == "https://old.example.test/mcp"
    assert "kept-out-of-db" not in repr(db.export_all())
    db.close()
