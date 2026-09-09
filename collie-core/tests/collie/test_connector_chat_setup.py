from __future__ import annotations

import json
from typing import Any

import pytest

from collie_core.services.manager import bind_service_manager
from collie_core.tools.connectors import ConnectConnectorTool, PreviewConnectorTool


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def list_connections(self) -> list[Any]:
        return []

    def validate_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("validate", definition))
        return {
            "valid": True,
            "preview": {**definition, "has_secret": False, "requires_secret": False},
            "definition": {"id": "draft_1", **definition},
            "warnings": [],
            "errors": [],
        }

    def save_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("save", definition))
        return {"definition_id": "def_1", "requires_secret": False}

    def begin_auth(self, definition_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("begin", {"definition_id": definition_id, **kwargs}))
        return {"connection_id": "con_1", "status": "connected"}


@pytest.fixture()
def manager() -> FakeManager:
    value = FakeManager()
    bind_service_manager(value)  # type: ignore[arg-type]
    yield value
    bind_service_manager(None)


@pytest.mark.asyncio
async def test_chat_custom_connection_previews_then_uses_shared_manager(
    manager: FakeManager,
) -> None:
    definition = {
        "name": "Team notes",
        "endpoint": "https://notes.example/mcp",
        "transport": "streamable_http",
        "auth_strategy": "oauth",
        "oauth_registration": "automatic",
        "scopes": ["notes.read"],
        "allow_private_network": False,
    }
    preview = json.loads(await PreviewConnectorTool().execute(**definition))
    assert preview["valid"] is True
    assert "definition" not in preview

    approval = ConnectConnectorTool().permission_request(definition)
    assert approval.hard_approval is True
    assert approval.redacted_parameters == definition
    assert manager.calls == [("validate", definition)]

    result = json.loads(await ConnectConnectorTool().execute(**definition))
    assert result == {"connection_id": "con_1", "status": "connected"}
    assert [name for name, _ in manager.calls[1:]] == ["validate", "save", "begin"]
    assert manager.calls[-1][1]["origin"] == "chat"


@pytest.mark.asyncio
async def test_chat_connection_rejects_sensitive_fields_without_echo(manager: FakeManager) -> None:
    tool = ConnectConnectorTool()
    serialized_schema = json.dumps(tool.parameters).lower()
    assert all(word not in serialized_schema for word in ("token", "api_key", "client_secret"))
    params = {
        "endpoint": "https://notes.example/mcp",
        "auth_strategy": "none",
        "token": "must-not-appear",
    }
    with pytest.raises(ValueError, match="Add connection"):
        tool.permission_request(params)
    with pytest.raises(ValueError, match="Add connection") as caught:
        await tool.execute(**params)
    assert "must-not-appear" not in str(caught.value)
    assert manager.calls == []
