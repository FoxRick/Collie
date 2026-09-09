"""Validation and persistence boundaries for installed connector definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collie_core.connectors.models import InstalledConnectorDefinition
from collie_core.db import CollieDB


def _base(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "definition-1",
        "driver": "custom_mcp",
        "transport": "streamable_http",
        "auth_strategy": "none",
        "provenance": "custom",
        "endpoint": "https://example.test/mcp",
    }
    value.update(overrides)
    return value


def test_valid_no_auth_remote_definition_roundtrips_through_save(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    definition = InstalledConnectorDefinition.from_dict(
        _base(trusted_hosts=["example.test"], scopes=["read"])
    )

    saved = db.save_connector_definition(definition)

    assert saved["id"] == definition.id
    assert json.loads(saved["config_json"]) == {
        "endpoint": "https://example.test/mcp",
        "scopes": ["read"],
        "trusted_hosts": ["example.test"],
        "tool_overrides": {},
    }
    db.close()


def test_valid_oauth_definition_roundtrips_without_secret_fields(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    definition = InstalledConnectorDefinition.from_dict(
        _base(
            id="oauth-definition",
            auth_strategy="oauth",
            oauth_registration="preregistered",
            client_id="public-client-id",
            scopes=["read", "write"],
            recipe_id="recipe-1",
            recipe_version="1",
        )
    )

    saved = db.save_connector_definition(definition)
    config = json.loads(saved["config_json"])

    assert config["oauth_registration"] == "preregistered"
    assert config["client_id"] == "public-client-id"
    assert config["scopes"] == ["read", "write"]
    assert all(
        fragment not in json.dumps(saved).lower()
        for fragment in ("access_token", "refresh_token", "client_secret", "api_key")
    )
    db.close()


@pytest.mark.parametrize(
    "field", ["secret", "api_key", "access_token", "refresh_token", "client_secret", "credentials"]
)
def test_from_dict_rejects_unknown_or_credential_fields(field: str) -> None:
    with pytest.raises(ValueError, match="Unknown connector definition fields"):
        InstalledConnectorDefinition.from_dict(_base(**{field: "must-not-cross-boundary"}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scopes", "read"),
        ("scopes", ["read", 1]),
        ("trusted_hosts", "example.test"),
        ("trusted_hosts", [""]),
        ("tool_overrides", []),
        ("tool_overrides", {"read": 1}),
        ("unresolved", "false"),
        ("id", 42),
        ("driver", 42),
        ("transport", None),
        ("auth_strategy", []),
        ("provenance", {}),
    ],
)
def test_from_dict_rejects_malformed_collections_and_wrong_types(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        InstalledConnectorDefinition.from_dict(_base(**{field: value}))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@example.test/mcp",
        "https://example.test/mcp?token=secret",
        "https://example.test/mcp#fragment",
    ],
)
def test_from_dict_rejects_endpoint_userinfo_query_and_fragment(endpoint: str) -> None:
    with pytest.raises(ValueError, match="credentials|query or fragment"):
        InstalledConnectorDefinition.from_dict(_base(endpoint=endpoint))
