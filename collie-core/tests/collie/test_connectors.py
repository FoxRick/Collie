"""Connector vertical-slice tests with an in-process provider driver."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from collie_core.connectors.catalog import CONNECTOR_CATALOG, connector_def
from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import ConnectorDriverKind, ProbeResult
from collie_core.connectors.policy import classify_connector_tool
from collie_core.db import CollieDB
from collie_core.services.credentials import CredentialStore
from nanobot.agent.tools.mcp import MCPToolWrapper


class _FakeDriver:
    def __init__(self, store: CredentialStore, *, fail: bool = False) -> None:
        self.store = store
        self.fail = fail
        self.revoked: list[str] = []

    def connect_and_probe(self, definition, connection_id: str) -> ProbeResult:
        self.store.save(
            f"connector:{connection_id}",
            {"tokens": {"access_token": "secret-access-token"}},
        )
        if self.fail:
            raise RuntimeError("tool discovery failed")
        return ProbeResult(
            account_label="Test Workspace",
            remote_account_id="workspace-1",
            tools=[
                {
                    "name": "search_pages",
                    "schema_hash": "read-hash",
                    "annotations": {"readOnlyHint": True},
                    "risk": "read",
                },
                {
                    "name": "create_page",
                    "schema_hash": "write-hash",
                    "annotations": {},
                    "risk": "change",
                },
            ],
        )

    def probe(self, definition, connection_id: str) -> ProbeResult:
        return self.connect_and_probe(definition, connection_id)

    def revoke(self, definition, connection_id: str) -> None:
        self.revoked.append(connection_id)


@pytest.fixture()
def connector_store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(
        tmp_path / "credentials",
        protect=lambda value: value[::-1],
        unprotect=lambda value: value[::-1],
    )


def test_launch_catalog_enables_direct_mcp_routes_ready_for_live_oauth() -> None:
    by_id = {item.id: item for item in CONNECTOR_CATALOG}
    oauth_routes = {
        "notion",
        "linear",
        "todoist",
        "atlassian",
        "airtable",
        # Wave 2 (2026-08-22): official hosted MCP with dynamic client
        # registration — verified live against each provider's metadata.
        "asana",
        "clickup",
        "monday",
        "cal",
        "figma",
        "canva",
        "gitlab",
        # circleci parked 2026-08-22: mcp.circleci.com answers 404 to a proper
        # MCP initialize (working routes answer 401/405) — not enabled until
        # the official endpoint is verified.
        "netlify",
        "supabase",
        "neon",
        "sentry",
        "cloudflare",
        "paypal",
        "square",
        "ramp",
        "klaviyo",
        "vimeo",
        "webflow",
    }
    assert oauth_routes <= set(by_id)
    enabled = {item.id for item in CONNECTOR_CATALOG if item.available}
    # Only routes that can complete OAuth today (official hosted MCP with
    # dynamic client registration) are enabled; the rest stay coming_soon.
    # OAuth token persistence is Windows-DPAPI-only for now, so on other
    # platforms none of the OAuth routes are enabled.
    assert enabled == (oauth_routes if sys.platform == "win32" else set())
    for provider_id in oauth_routes:
        definition = by_id[provider_id]
        assert definition.driver == ConnectorDriverKind.OFFICIAL_MCP
        # release_status derives from available: alpha where the route is
        # enabled, coming_soon where the platform can't persist OAuth yet.
        expected_status = "alpha" if sys.platform == "win32" else "coming_soon"
        assert definition.release_status == expected_status
        assert definition.endpoint
        # A live route must pin its network boundary to its endpoint host.
        endpoint_host = urlparse(definition.endpoint).hostname or ""
        assert definition.trusted_hosts == (endpoint_host,)
    assert connector_def("NoTiOn") is by_id["notion"]
    assert by_id["notion"].endpoint == "https://mcp.notion.com/mcp"
    # Parked 2026-08-22: mcp.circleci.com answers 404 to a proper MCP
    # initialize (working routes answer 401/405) — it stays coming_soon
    # until the official endpoint is verified.
    circleci = by_id["circleci"]
    assert circleci.available is False
    assert circleci.release_status == "coming_soon"


def test_enabled_catalog_routes_declare_explicit_least_privilege_scopes() -> None:
    by_id = {item.id: item for item in CONNECTOR_CATALOG}
    oauth_routes = {item.id for item in CONNECTOR_CATALOG if item.available}
    # Every live route must either declare explicit scopes or carry an empty
    # tuple ONLY because the provider's authorization server advertises no
    # scopes_supported and applies its own server-side MCP defaults.
    no_advertised_scopes = {
        "asana",  # AS lists no scopes_supported; server-side MCP default set.
        "cal",
        "canva",
        "cloudflare",
        "paypal",
        "square",
        "klaviyo",
        "webflow",
        "notion" if by_id["notion"].scopes == () else "",
    } - {""}
    for provider_id in oauth_routes:
        definition = by_id[provider_id]
        if definition.scopes:
            continue
        assert provider_id in no_advertised_scopes, f"{provider_id} must request explicit scopes"
    assert by_id["linear"].scopes == ("read", "write")
    assert by_id["todoist"].scopes == ("data:read_write",)
    assert by_id["airtable"].scopes == (
        "data.records:read",
        "data.records:write",
        "schema.bases:read",
    )
    assert by_id["figma"].scopes == ("mcp:connect",)
    assert by_id["gitlab"].scopes == ("read_api", "read_user", "profile", "mcp")
    # Wave-2 review (2026-08-22): scope vocabularies verified live against
    # each provider's RFC 8414 metadata; read-only cards must request
    # read-only scopes.
    assert by_id["netlify"].scopes == ("offline_access", "read")
    assert by_id["neon"].scopes == ("read",)
    assert by_id["vimeo"].scopes == ("public", "private", "stats")
    # Sentry's server has no read-only variant — the card honestly declares
    # Read + Update instead of pretending write scopes don't exist.
    assert by_id["sentry"].scopes == (
        "org:read",
        "project:write",
        "team:write",
        "event:write",
    )
    sentry = by_id["sentry"]
    assert sentry.capabilities == ("Read", "Update")
    assert any("update issues" in p for p in sentry.permissions)


def _enable_connector_for_unit_test(monkeypatch: pytest.MonkeyPatch, provider_id: str) -> None:
    from collie_core.connectors import catalog
    from collie_core.connectors import manager as connector_manager

    definition = connector_def(provider_id)
    assert definition is not None
    enabled = replace(definition, available=True, release_status="alpha", note="")
    monkeypatch.setitem(catalog._BY_ID, provider_id, enabled)
    monkeypatch.setattr(connector_manager, "connector_def", catalog.connector_def)


def test_connect_probe_runtime_bind_and_remove(
    tmp_path: Path,
    connector_store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")
    driver = _FakeDriver(connector_store)
    manager = ConnectorManager(
        db,
        credentials=connector_store,
        driver_factory=lambda definition: driver,
    )
    result = manager.connect("notion")
    connection_id = result["connection_id"]
    row = manager.get_connection(connection_id)
    assert row is not None
    assert row["status"] == "connected"
    assert row["account_label"] == "Test Workspace"
    assert row["tool_policy"] == {
        "search_pages": "read",
        "create_page": "change",
    }
    assert len(db.list_connector_tools(connection_id)) == 2

    servers = manager.mcp_servers_for_config()
    server = next(iter(servers.values()))
    assert server["oauthConnectionId"] == connection_id
    assert "secret-access-token" not in repr(servers)
    assert connector_store.load(f"connector:{connection_id}") is not None

    removed = manager.remove(connection_id)
    assert removed["status"] == "disconnected"
    assert driver.revoked == [connection_id]
    assert connector_store.load(f"connector:{connection_id}") is None
    assert manager.get_connection(connection_id) is None
    assert manager.mcp_servers_for_config() == {}
    db.close()


def test_failed_probe_never_marks_connected(
    tmp_path: Path,
    connector_store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(
        db,
        credentials=connector_store,
        driver_factory=lambda definition: _FakeDriver(connector_store, fail=True),
    )
    with pytest.raises(ValueError, match="tools"):
        manager.connect("notion")
    rows = db.list_connector_connections("notion")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["last_error_code"] == "tool_discovery_failed"
    # Retryable probe failures keep the stored credentials so the user can
    # retry without a fresh sign-in.
    assert connector_store.load(f"connector:{rows[0]['id']}") == {
        "tokens": {"access_token": "secret-access-token"}
    }
    db.close()


def test_disabled_connector_cannot_connect_or_rebind_existing_runtime(
    tmp_path: Path, connector_store: CredentialStore
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, credentials=connector_store)
    with pytest.raises(ValueError, match="coming soon"):
        manager.connect("github")
    db.upsert_connector_connection(
        "con_old_github",
        provider_id="github",
        driver="bundled_mcp",
        auth_type="oauth",
        status="connected",
        enabled_tools=["search_repositories"],
    )
    assert manager.is_connected("github") is False
    assert manager.mcp_servers_for_config() == {}
    catalog = {item["id"]: item for item in manager.catalog_view()}
    assert catalog["github"]["status"] == "coming_soon"
    assert catalog["github"]["connection_count"] == 0
    # Historical rows stay removable, but must not be displayed as a healthy
    # connection when the provider route is unavailable in this build.
    connection = manager.get_connection("con_old_github")
    assert connection is not None
    assert connection["status"] == "attention"
    assert connection["last_error_code"] == "provider_unavailable"
    assert connection["last_error_message"] == (
        "This connection is not available in this build and cannot be used."
    )
    db.close()


def test_enabled_provider_historical_connected_row_without_credentials_requires_auth(
    tmp_path: Path, connector_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The enabled-provider path is platform-independent — pin the definition.
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, credentials=connector_store)
    db.upsert_connector_connection(
        "con_old_notion",
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="connected",
        enabled_tools=["search_pages"],
    )
    # A connected row without stored credentials (token deleted, DB restored
    # without the credential files) must not report healthy or bind at
    # runtime — it needs a fresh sign-in.
    assert manager.is_connected("notion") is False
    assert manager.mcp_servers_for_config() == {}
    connection = manager.get_connection("con_old_notion")
    assert connection is not None
    assert connection["status"] == "auth_required"
    assert connection["last_error_code"] == "credentials_missing"
    catalog = {item["id"]: item for item in manager.catalog_view()}
    assert catalog["notion"]["status"] == "auth_required"
    assert catalog["notion"]["connection_count"] == 0
    db.close()


def test_enabled_provider_connected_row_with_credentials_stays_healthy_and_rebinds(
    tmp_path: Path, connector_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, credentials=connector_store)
    connector_store.save(
        "connector:con_old_notion",
        {"tokens": {"access_token": "tok", "token_type": "Bearer"}},
    )
    db.upsert_connector_connection(
        "con_old_notion",
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="connected",
        enabled_tools=["search_pages"],
    )
    assert manager.is_connected("notion") is True
    assert list(manager.mcp_servers_for_config()) != []
    connection = manager.get_connection("con_old_notion")
    assert connection is not None
    assert connection["status"] == "connected"
    assert connection["last_error_code"] is None
    catalog = {item["id"]: item for item in manager.catalog_view()}
    assert catalog["notion"]["status"] == "connected"
    assert catalog["notion"]["connection_count"] == 1
    db.close()


def test_connected_row_requires_usable_access_token(
    tmp_path: Path, connector_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, credentials=connector_store)
    try:
        # Empty records, client-info-only entries, and token blobs without
        # an access_token must NOT count as connected.
        invalid_payloads: list[dict] = [
            {},
            {"client_info": {"client_id": "x"}},
            {"tokens": {}},
            {"tokens": {"refresh_token": "r"}},
        ]
        for index, payload in enumerate(invalid_payloads):
            connection_id = f"con_bad_{index}"
            connector_store.save(f"connector:{connection_id}", payload)
            db.upsert_connector_connection(
                connection_id,
                provider_id="notion",
                driver="official_mcp",
                auth_type="oauth",
                status="connected",
            )
            view = manager.get_connection(connection_id)
            assert view is not None and view["status"] == "auth_required", payload
            assert manager.is_connected("notion") is False

        connector_store.save(
            "connector:con_ok",
            {"tokens": {"access_token": "tok", "token_type": "Bearer"}},
        )
        db.upsert_connector_connection(
            "con_ok",
            provider_id="notion",
            driver="official_mcp",
            auth_type="oauth",
            status="connected",
        )
        assert manager.is_connected("notion") is True
    finally:
        db.close()


def test_recheck_only_exposes_identity_returned_by_the_provider(
    tmp_path: Path,
    connector_store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_connector_for_unit_test(monkeypatch, "notion")
    db = CollieDB(tmp_path / "collie.db")

    class _NoIdentityDriver(_FakeDriver):
        def probe(self, definition, connection_id: str) -> ProbeResult:
            # A re-test runs against a connection that already has tokens.
            self.store.save(
                f"connector:{connection_id}",
                {"tokens": {"access_token": "secret-access-token"}},
            )
            return ProbeResult(tools=[{"name": "search_pages", "risk": "read", "schema_hash": "h"}])

    manager = ConnectorManager(
        db,
        credentials=connector_store,
        driver_factory=lambda definition: _NoIdentityDriver(connector_store),
    )
    db.upsert_connector_connection(
        "con_no_identity",
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="attention",
    )

    connection = manager.test("con_no_identity")
    assert connection["status"] == "connected"
    assert connection["account_label"] is None
    assert connection["remote_account_id"] is None
    assert connection["granted_scopes"] == []
    assert connection["last_verified_at"]
    db.close()


def test_multiple_accounts_are_supported_by_schema(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    for connection_id, label in (("con_personal", "Personal"), ("con_work", "Work")):
        db.upsert_connector_connection(
            connection_id,
            provider_id="notion",
            display_name=label,
            driver="official_mcp",
            auth_type="oauth",
            status="connected",
        )
    rows = db.list_connector_connections("notion")
    assert {row["id"] for row in rows} == {"con_personal", "con_work"}
    db.close()


def test_cancel_auth_clears_local_credentials(
    tmp_path: Path, connector_store: CredentialStore
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    manager = ConnectorManager(db, credentials=connector_store)
    db.upsert_connector_connection(
        "con_pending",
        provider_id="notion",
        driver="official_mcp",
        auth_type="oauth",
        status="authorizing",
    )
    connector_store.save("connector:con_pending", {"temporary": "secret"})
    result = manager.cancel_auth("con_pending")
    assert result["cancelled"] is True
    row = db.get_connector_connection("con_pending")
    assert row is not None and row["last_error_code"] == "oauth_cancelled"
    assert connector_store.load("connector:con_pending") is None
    db.close()


def test_tool_policy_combines_hints_names_and_conservative_defaults() -> None:
    assert classify_connector_tool("search_pages", {"readOnlyHint": True}, trusted=True) == "read"
    assert classify_connector_tool("send_message", {}, trusted=True) == "important"
    assert classify_connector_tool("delete_page", {}, trusted=True) == "destructive"
    assert (
        classify_connector_tool("mysterious_operation", {"readOnlyHint": True}, trusted=False)
        == "change"
    )


def test_connector_approval_preference_controls_hard_approval() -> None:
    read_definition = SimpleNamespace(
        name="search_pages",
        description="Search pages",
        inputSchema={"type": "object", "properties": {}},
        annotations={"readOnlyHint": True},
    )
    always = MCPToolWrapper(
        object(),
        "notion_test",
        read_definition,
        connector_provider_id="notion",
        connector_trusted=True,
        connector_approval_preference="every_time",
    )
    recommended = MCPToolWrapper(
        object(),
        "notion_test",
        read_definition,
        connector_provider_id="notion",
        connector_trusted=True,
        connector_approval_preference="important",
    )
    assert always.permission_request({}).hard_approval is True
    assert recommended.permission_request({}).hard_approval is False
