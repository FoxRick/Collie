"""Secret-safe validation and preview of user supplied remote MCP definitions."""

from __future__ import annotations

import json
import uuid
from typing import Any

from collie_core.connectors.models import (
    ConnectorAuthStrategy,
    ConnectorDriverKind,
    ConnectorProvenance,
    ConnectorTransport,
    InstalledConnectorDefinition,
)

_SECRET_KEYS = frozenset({"token", "apiKey", "api_key", "secret", "authorization"})
_SUPPORTED_SERVER_KEYS = frozenset({"url", "type", "transport", "headers", "oauth", "name"})


def _auth_from_payload(value: dict[str, Any]) -> tuple[ConnectorAuthStrategy, str | None, bool]:
    auth = str(value.get("auth_strategy") or value.get("auth") or "none").lower()
    header_name = value.get("header_name")
    has_secret = bool(
        value.get("token") or value.get("secret") or value.get("apiKey") or value.get("api_key")
    )
    if has_secret and auth == "none":
        auth = "token"
    if isinstance(value.get("oauth"), dict):
        auth = "oauth"
    headers = value.get("headers")
    if isinstance(value.get("authorization"), str) and value["authorization"]:
        headers = {"Authorization": value["authorization"]}
    if isinstance(headers, dict) and headers:
        has_secret = True
        if len(headers) != 1:
            raise ValueError("Use exactly one authentication header.")
        header_name = str(next(iter(headers)))
        auth = "headers"
    return ConnectorAuthStrategy(auth), header_name, has_secret


def validate_definition_payload(
    payload: dict[str, Any], *, provenance: ConnectorProvenance = ConnectorProvenance.CUSTOM
) -> tuple[InstalledConnectorDefinition, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Connection details must be an object.")
    endpoint = payload.get("endpoint") or payload.get("url")
    strategy, header_name, has_secret = _auth_from_payload(payload)
    oauth = payload.get("oauth") if isinstance(payload.get("oauth"), dict) else {}
    scopes = oauth.get("scopes") or payload.get("scopes") or ()
    if not isinstance(scopes, (list, tuple)) or not all(isinstance(item, str) for item in scopes):
        raise ValueError("Scopes must be a list of strings.")
    transport_raw = str(payload.get("transport") or payload.get("type") or "streamable_http")
    transport_raw = {"http": "streamable_http", "streamable-http": "streamable_http"}.get(
        transport_raw, transport_raw
    )
    definition = InstalledConnectorDefinition(
        id=str(payload.get("id") or f"draft_{uuid.uuid4().hex}"),
        driver=ConnectorDriverKind.CUSTOM_MCP,
        transport=ConnectorTransport(transport_raw),
        auth_strategy=strategy,
        provenance=provenance,
        endpoint=str(endpoint or ""),
        oauth_registration=(
            oauth.get("registration")
            or payload.get("oauth_registration")
            or ("automatic" if strategy is ConnectorAuthStrategy.OAUTH else None)
        ),
        client_id=(oauth.get("client_id") or payload.get("client_id")),
        redirect_uri=(oauth.get("redirect_uri") or payload.get("redirect_uri")),
        issuer=(oauth.get("issuer") or payload.get("issuer")),
        resource=(oauth.get("resource") or payload.get("resource")),
        client_metadata_url=(
            oauth.get("client_metadata_url") or payload.get("client_metadata_url")
        ),
        header_name=header_name,
        scopes=tuple(scopes),
        allow_private_network=payload.get("allow_private_network", False),
    )
    preview = {
        "name": str(payload.get("name") or "Custom connection"),
        "endpoint": definition.endpoint,
        "transport": definition.transport.value,
        "auth_strategy": definition.auth_strategy.value,
        "oauth_registration": definition.oauth_registration,
        "client_id": definition.client_id,
        "redirect_uri": definition.redirect_uri,
        "issuer": definition.issuer,
        "resource": definition.resource,
        "scopes": list(definition.scopes),
        "allow_private_network": definition.allow_private_network,
        "has_secret": has_secret,
        "requires_secret": strategy in (ConnectorAuthStrategy.TOKEN, ConnectorAuthStrategy.HEADERS),
    }
    return definition, preview


def import_preview(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value) > 1_000_000:
            raise ValueError("That connection file is too large.")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("That file is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("The connection file must contain an object.")
    servers = value.get("mcpServers", value)
    if not isinstance(servers, dict):
        raise ValueError("The connection file must contain an mcpServers object.")
    definitions: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    for key, raw in servers.items():
        path = f"mcpServers.{key}"
        if not isinstance(raw, dict):
            unsupported.append({"path": path, "reason": "Expected an object."})
            continue
        if raw.get("command") or raw.get("args"):
            unsupported.append(
                {"path": path, "reason": "Local command connectors are not supported yet."}
            )
            continue
        unknown = set(raw) - _SUPPORTED_SERVER_KEYS - _SECRET_KEYS
        for field in sorted(unknown):
            unsupported.append({"path": f"{path}.{field}", "reason": "Unsupported field."})
        normalized = dict(raw)
        normalized["name"] = str(raw.get("name") or key)
        normalized["id"] = f"draft_{uuid.uuid4().hex}"
        try:
            definition, preview = validate_definition_payload(
                normalized, provenance=ConnectorProvenance.IMPORTED
            )
        except (TypeError, ValueError):
            unsupported.append({"path": path, "reason": "Invalid connection details."})
            continue
        sanitized = definition.to_dict()
        definitions.append(
            {
                "key": str(key),
                "name": normalized["name"],
                "definition": sanitized,
                "preview": preview,
            }
        )
    return {"definitions": definitions, "unsupported": unsupported, "warnings": []}
