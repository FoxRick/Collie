from __future__ import annotations

import threading
from pathlib import Path

import pytest

from collie_core.connectors.import_config import import_preview
from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import ProbeResult
from collie_core.db import CollieDB
from collie_core.services.credentials import CredentialStore


def _store(path: Path) -> CredentialStore:
    return CredentialStore(
        path, protect=lambda value: value[::-1], unprotect=lambda value: value[::-1]
    )


def test_import_preview_extracts_secret_without_returning_it() -> None:
    preview = import_preview(
        {
            "mcpServers": {
                "work": {
                    "url": "https://mcp.example.test/api",
                    "headers": {"X-Api-Key": "top-secret"},
                }
            }
        }
    )

    assert preview["unsupported"] == []
    item = preview["definitions"][0]
    assert item["preview"]["has_secret"] is True
    assert item["preview"]["auth_strategy"] == "headers"
    assert item["definition"]["header_name"] == "X-Api-Key"
    assert "top-secret" not in repr(preview)


def test_import_preview_normalizes_token_alias_and_raw_authorization() -> None:
    preview = import_preview(
        {
            "mcpServers": {
                "token": {"url": "https://one.example/mcp", "apiKey": "key-value"},
                "basic": {"url": "https://two.example/mcp", "authorization": "Basic abc"},
            }
        }
    )
    by_name = {item["key"]: item for item in preview["definitions"]}
    assert by_name["token"]["preview"]["auth_strategy"] == "token"
    assert by_name["basic"]["preview"]["auth_strategy"] == "headers"
    assert by_name["basic"]["definition"]["header_name"] == "Authorization"
    assert "key-value" not in repr(preview)
    assert "Basic abc" not in repr(preview)


def test_static_secret_is_protected_and_lifecycle_results_are_revisioned(tmp_path: Path) -> None:
    class Driver:
        def connect_and_probe(self, definition, connection_id):
            assert store.load(f"connector:{connection_id}")["static"]["value"] == "secret-value"
            return ProbeResult(
                tools=[{"name": "search", "risk": "read", "schema_hash": "h", "annotations": {}}]
            )

        probe = connect_and_probe

        def revoke(self, definition, connection_id):
            return "unsupported"

    store = _store(tmp_path / "credentials")
    manager = ConnectorManager(
        CollieDB(tmp_path / "collie.db"),
        credentials=store,
        driver_factory=lambda definition: Driver(),
    )
    validated = manager.validate_definition(
        {"endpoint": "https://mcp.example.test/api", "auth_strategy": "token"}
    )
    saved = manager.save_definition(validated["definition"])
    result = manager.begin_auth(
        saved["definition_id"],
        secret={"token": "secret-value"},
        connection_id="con_static",
        operation_id="op_static",
    )

    assert result["operation_id"] == "op_static"
    assert result["operation_revision"] == 0
    assert "secret-value" not in repr(result)
    removed = manager.remove("con_static", operation_revision=0)
    assert removed["operation_revision"] == 1
    assert store.load("connector:con_static") is None


def test_reconnect_preserves_identity_preferences_and_requires_material_review(
    tmp_path: Path,
) -> None:
    inventories = [
        [("read", "h1"), ("write", "h2")],
        [("read", "changed"), ("write", "h2"), ("new", "h3")],
    ]

    class Driver:
        def connect_and_probe(self, definition, connection_id):
            current = inventories.pop(0)
            return ProbeResult(
                tools=[
                    {"name": name, "risk": "read", "schema_hash": hash_, "annotations": {}}
                    for name, hash_ in current
                ]
            )

        probe = connect_and_probe

        def revoke(self, definition, connection_id):
            return "unsupported"

    manager = ConnectorManager(
        CollieDB(tmp_path / "collie.db"),
        credentials=_store(tmp_path / "credentials"),
        driver_factory=lambda definition: Driver(),
    )
    normalized = manager.validate_definition({"endpoint": "https://mcp.example.test/api"})
    saved = manager.save_definition(normalized["definition"])
    manager.begin_auth(saved["definition_id"], connection_id="con_same")
    manager.update("con_same", enabled_tools=["read"], approval_preference="always")
    before_definition = manager.db.get_connector_connection("con_same")["definition_id"]

    reconnected = manager.reconnect("con_same", operation_revision=0)

    assert reconnected["id"] == "con_same"
    assert manager.db.get_connector_connection("con_same")["definition_id"] == before_definition
    assert reconnected["enabled_tools"] == []
    assert reconnected["tool_policy"]["_approval_preference"] == "always"
    statuses = {
        item["remote_tool_name"]: item["review_status"]
        for item in manager.inspect_tools("con_same")["tools"]
    }
    assert statuses == {"new": "new", "read": "changed", "write": "reviewed"}


def test_stale_test_failure_cannot_overwrite_newer_revision(tmp_path: Path) -> None:
    started = threading.Event()
    finish = threading.Event()

    class Driver:
        def connect_and_probe(self, definition, connection_id):
            return ProbeResult(
                tools=[{"name": "read", "risk": "read", "schema_hash": "h", "annotations": {}}]
            )

        def probe(self, definition, connection_id):
            started.set()
            finish.wait(2)
            raise RuntimeError("network failed")

        def revoke(self, definition, connection_id):
            return "unsupported"

    manager = ConnectorManager(
        CollieDB(tmp_path / "collie.db"),
        credentials=_store(tmp_path / "credentials"),
        driver_factory=lambda definition: Driver(),
    )
    saved = manager.save_definition(
        manager.validate_definition({"endpoint": "https://mcp.example.test/api"})["definition"]
    )
    manager.begin_auth(saved["definition_id"], connection_id="con_race")
    errors: list[Exception] = []
    thread = threading.Thread(target=lambda: _capture_error(errors, manager.test, "con_race"))
    thread.start()
    assert started.wait(1)
    manager.db.advance_connector_operation("con_race", 1)
    manager.db.upsert_connector_connection(
        "con_race",
        provider_id=manager.db.get_connector_connection("con_race")["provider_id"],
        driver="custom_mcp",
        auth_type="none",
        status="connected",
    )
    finish.set()
    thread.join(2)
    row = manager.db.get_connector_connection("con_race")
    assert errors and row["status"] == "connected" and row["last_error_code"] is None


def test_cancelled_reconnect_must_settle_before_new_credentials_are_accepted(
    tmp_path: Path,
) -> None:
    blocking_started = threading.Event()
    release = threading.Event()
    calls = 0

    class Driver:
        def connect_and_probe(self, definition, connection_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                blocking_started.set()
                release.wait(2)
            return ProbeResult(
                tools=[{"name": "read", "risk": "read", "schema_hash": "h", "annotations": {}}]
            )

        probe = connect_and_probe

        def revoke(self, definition, connection_id):
            return "unsupported"

    store = _store(tmp_path / "credentials")
    manager = ConnectorManager(
        CollieDB(tmp_path / "collie.db"),
        credentials=store,
        driver_factory=lambda definition: Driver(),
    )
    saved = manager.save_definition(
        manager.validate_definition(
            {"endpoint": "https://mcp.example.test/api", "auth_strategy": "token"}
        )["definition"]
    )
    manager.begin_auth(saved["definition_id"], secret={"token": "old"}, connection_id="con_overlap")
    errors: list[Exception] = []
    worker = threading.Thread(
        target=lambda: _capture_error(
            errors, manager.reconnect, "con_overlap", operation_id="op_a", operation_revision=0
        )
    )
    worker.start()
    assert blocking_started.wait(1)
    with pytest.raises(ValueError, match="still stopping"):
        manager.reconnect("con_overlap")
    active_row = manager.db.get_connector_connection("con_overlap")
    with pytest.raises(ValueError, match="still stopping"):
        manager._connect(
            manager._definition_for_row(active_row),
            origin="race_regression",
            replace_connection_id="con_overlap",
            connection_id="con_overlap",
        )
    manager.cancel_auth("con_overlap", operation_id="op_a", operation_revision=1)
    with pytest.raises(ValueError, match="still stopping"):
        manager.reconnect("con_overlap", secret={"token": "new"}, operation_revision=2)
    release.set()
    worker.join(2)

    manager.reconnect("con_overlap", secret={"token": "new"}, operation_revision=2)
    assert store.load("connector:con_overlap")["static"]["value"] == "new"


def _capture_error(errors, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except Exception as exc:
        errors.append(exc)
