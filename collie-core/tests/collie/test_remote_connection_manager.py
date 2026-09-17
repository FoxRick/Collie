"""Custom remote connections share the installed-account lifecycle."""

from dataclasses import replace

import pytest

from collie_core.connectors.manager import ConnectorManager
from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
    ProbeResult,
    RemoteRevocationStatus,
)
from collie_core.db import CollieDB
from collie_core.services.credentials import CredentialStore
from nanobot.config.schema import MCPServerConfig


def definition(**changes):
    return replace(
        InstalledConnectorDefinition(
            id="input_definition",
            driver=ConnectorDriverKind.CUSTOM_MCP,
            transport=ConnectorTransport.STREAMABLE_HTTP,
            auth_strategy=ConnectorAuthStrategy.NONE,
            provenance=ConnectorProvenance.CUSTOM,
            endpoint="https://mcp.example.com/mcp",
        ),
        **changes,
    )


class Driver:
    def __init__(self):
        self.definitions = []
        self.on_probe = None

    def connect_and_probe(self, config, connection_id):
        self.definitions.append(config)
        if self.on_probe:
            self.on_probe(connection_id)
        return ProbeResult(
            tools=[
                {
                    "name": "search",
                    "risk": "change",
                    "schema_hash": "schema",
                    "annotations": {"readOnlyHint": True},
                }
            ]
        )

    probe = connect_and_probe

    def revoke(self, config, connection_id):
        return RemoteRevocationStatus.NOT_APPLICABLE


@pytest.fixture
def setup(tmp_path):
    db = CollieDB(tmp_path / "collie.db")
    store = CredentialStore(
        tmp_path / "secrets", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]
    )
    driver = Driver()
    manager = ConnectorManager(db, credentials=store, driver_factory=lambda _: driver)
    yield manager, db, store, driver
    db.close()


@pytest.mark.parametrize("transport", [ConnectorTransport.STREAMABLE_HTTP, ConnectorTransport.SSE])
def test_unknown_server_connect_restart_query_and_remove(setup, transport):
    manager, db, store, driver = setup
    result = manager.connect_definition(definition(transport=transport), display_name="My server")
    connection_id = result["connection_id"]
    assert result["status"] == "connected"
    assert store.load(f"connector:{connection_id}") is None
    assert manager.get_connection(connection_id)["route"] == "Custom MCP"
    assert manager.get_connection(connection_id)["status"] == "connected"
    restarted = ConnectorManager(db, credentials=store, driver_factory=lambda _: driver)
    assert restarted.test(connection_id)["status"] == "connected"
    servers = restarted.mcp_servers_for_config()
    assert list(servers) == [connection_id]
    config = servers[connection_id]
    assert config["type"] == ("sse" if transport == ConnectorTransport.SSE else "streamableHttp")
    assert config["connectorTrusted"] is False
    assert config["oauthConnectionId"] == ""
    assert config["enabledTools"] == ["search"]
    assert MCPServerConfig.model_validate(config).url == definition().endpoint
    assert restarted.remove(connection_id)["status"] == "disconnected"
    assert restarted.mcp_servers_for_config() == {}
    assert db.list_connector_definitions() == []
    assert db.list_connector_tools(connection_id) == []


def test_same_label_and_endpoint_have_independent_accounts_and_grants(setup):
    manager, db, _, _ = setup
    first = manager.connect_definition(definition(), display_name="Same name")["connection_id"]
    second = manager.connect_definition(definition(), display_name="Same name")["connection_id"]
    assert first != second
    manager.update(first, approval_preference="every_time")
    configs = manager.mcp_servers_for_config()
    assert set(configs) == {first, second}
    assert configs[first]["connectorApprovalPreference"] == "every_time"
    assert configs[second]["connectorApprovalPreference"] == "important"
    manager.remove(first)
    assert list(manager.mcp_servers_for_config()) == [second]
    assert len(db.list_connector_definitions()) == 1


def test_private_network_choice_survives_restart(setup):
    manager, db, store, driver = setup
    result = manager.connect_definition(
        definition(
            endpoint="http://127.0.0.1:9876/mcp",
            allow_private_network=True,
        )
    )
    config = ConnectorManager(db, credentials=store).mcp_servers_for_config()[
        result["connection_id"]
    ]
    assert config["connectorAllowPrivateNetwork"] is True
    assert config["connectorEndpointPolicy"] is True
    parsed = MCPServerConfig.model_validate(config)
    assert parsed.connector_allow_private_network is True
    assert parsed.connector_endpoint_policy is True
    assert driver.definitions[0].allow_private_network is True


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_strategy": ConnectorAuthStrategy.TOKEN},
        {"transport": ConnectorTransport.STDIO},
        {"provenance": ConnectorProvenance.CURATED},
        {"tool_overrides": {"search": "read"}},
        {"trusted_hosts": ("example.com",)},
        {"unresolved": True},
    ],
)
def test_unsupported_or_trust_promoting_definitions_do_not_start(setup, changes):
    manager, db, _, driver = setup
    with pytest.raises(ValueError):
        manager.connect_definition(definition(**changes))
    assert not driver.definitions
    assert db.list_connector_connections() == []
    assert db.list_connector_definitions() == []


def test_removed_connection_cannot_return_from_late_probe(setup):
    manager, db, _, driver = setup
    driver.on_probe = lambda connection_id: manager.remove(connection_id)
    with pytest.raises(ValueError, match="cancelled"):
        manager.connect_definition(definition())
    assert db.list_connector_connections() == []
    assert db.list_connector_definitions() == []
    assert manager.mcp_servers_for_config() == {}


def test_private_choice_rejects_truthy_strings():
    value = definition().to_dict()
    value["allow_private_network"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        InstalledConnectorDefinition.from_dict(value)


def test_retest_preserves_approval_preference(setup):
    manager, _, _, _ = setup
    connection_id = manager.connect_definition(definition())["connection_id"]
    manager.update(connection_id, approval_preference="every_time")
    manager.test(connection_id)
    config = manager.mcp_servers_for_config()[connection_id]
    assert config["connectorApprovalPreference"] == "every_time"


def test_removed_connection_cannot_return_from_late_retest(setup):
    manager, db, _, driver = setup
    connection_id = manager.connect_definition(definition())["connection_id"]
    driver.on_probe = lambda account: manager.remove(account)
    with pytest.raises(ValueError, match="cancelled"):
        manager.test(connection_id)
    assert db.list_connector_connections() == []
    assert db.list_connector_definitions() == []
