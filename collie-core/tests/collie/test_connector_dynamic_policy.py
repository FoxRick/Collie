"""Account-scoped policy and bounded inventory behavior for dynamic connectors."""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent.runner_helpers import make_run_spec
from loguru import logger

from collie_core.connectors.policy import (
    ConnectorToolAuthority,
    bind_connector_tool_authority,
    classify_connector_tool,
)
from collie_core.db import CollieDB
from nanobot.agent.tools.mcp import MCPToolWrapper, connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import AgentDefaults, MCPServerConfig
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


@pytest.fixture(autouse=True)
def _clear_authority_resolver():
    bind_connector_tool_authority(None)
    yield
    bind_connector_tool_authority(None)


def _tool(name: str = "search_pages", schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description="Search the account",
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations={"readOnlyHint": True},
    )


def test_untrusted_annotations_never_grant_read_authority() -> None:
    assert (
        classify_connector_tool("query_everything", {"readOnlyHint": True}, trusted=False)
        == "change"
    )


def test_permission_identity_is_scoped_to_the_connection() -> None:
    tool = _tool()
    first = MCPToolWrapper(
        object(),
        "notion_one",
        tool,
        connector_provider_id="notion",
        connector_connection_id="account-one",
        connector_schema_hash="v1",
        connector_trusted=True,
    )
    second = MCPToolWrapper(
        object(),
        "notion_two",
        tool,
        connector_provider_id="notion",
        connector_connection_id="account-two",
        connector_schema_hash="v1",
        connector_trusted=True,
    )
    bind_connector_tool_authority(
        lambda connection_id, _: ConnectorToolAuthority(
            connected=True,
            enabled=True,
            schema_hash="v1",
            risk="read",
            approval_preference="important",
        )
    )

    first_request = first.permission_request({})
    second_request = second.permission_request({})
    assert first_request.resource == "connector:account-one"
    assert second_request.resource == "connector:account-two"
    assert first_request.action != second_request.action


@pytest.mark.asyncio
async def test_disconnect_or_schema_change_blocks_an_old_live_wrapper() -> None:
    calls: list[str] = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append(name)
            return SimpleNamespace(content=[], isError=False)

    state = {"connected": True, "schema_hash": "v1"}
    bind_connector_tool_authority(
        lambda _connection_id, _tool_name: ConnectorToolAuthority(
            connected=state["connected"],
            enabled=True,
            schema_hash=state["schema_hash"],
            risk="read",
            approval_preference="important",
        )
    )
    wrapper = MCPToolWrapper(
        Session(),
        "server",
        _tool(),
        connector_provider_id="notion",
        connector_connection_id="account",
        connector_schema_hash="v1",
        connector_trusted=True,
    )

    state["schema_hash"] = "v2"
    assert "changed" in await wrapper.execute()
    state["schema_hash"] = "v1"
    state["connected"] = False
    assert "disconnected" in await wrapper.execute()
    assert calls == []


def test_inventory_changes_are_reported_and_search_is_bounded(tmp_path) -> None:
    with CollieDB(tmp_path / "collie.db") as db:
        db.upsert_connector_connection(
            "account",
            provider_id="custom",
            driver="custom_mcp",
            auth_type="none",
            status="connected",
        )
        initial = db.replace_connector_tools(
            "account",
            [
                {
                    "name": f"tool_{index:03d}",
                    "schema_hash": "v1",
                    "risk": "change",
                    "description": "calendar lookup",
                }
                for index in range(150)
            ],
        )
        assert initial["material_change"] is False
        assert len(db.search_connector_tools(connection_id="account", limit=500)) == 100

        changed = db.replace_connector_tools(
            "account",
            [
                {"name": "tool_000", "schema_hash": "v2", "risk": "change"},
                {"name": "brand_new", "schema_hash": "v1", "risk": "change"},
            ],
        )
        assert changed["material_change"] is True
        assert changed["changed_tools"] == ["tool_000"]
        assert changed["new_tools"] == ["brand_new"]
        assert "tool_001" in changed["removed_tools"]
        assert db.get_connector_tool("account", "tool_000")["schema_hash"] == "v2"


@pytest.mark.asyncio
async def test_large_runtime_inventory_exposes_only_selected_schemas() -> None:
    registry = ToolRegistry()
    for index in range(40):
        registry.register(
            MCPToolWrapper(
                object(),
                "account",
                _tool(f"calendar_action_{index:02d}"),
            )
        )

    initial_names = {definition["function"]["name"] for definition in registry.get_definitions()}
    assert initial_names == {"search_connected_tools"}

    result = await registry.execute(
        "search_connected_tools", {"query": "calendar action 03", "limit": 5}
    )
    assert "mcp_account_calendar_action_03" in result
    selected_names = {definition["function"]["name"] for definition in registry.get_definitions()}
    assert selected_names == {
        "search_connected_tools",
        "mcp_account_calendar_action_03",
    }


@pytest.mark.asyncio
async def test_agent_runner_search_selection_reaches_the_next_model_iteration() -> None:
    from nanobot.agent.runner import AgentRunner

    registry = ToolRegistry()
    for index in range(40):
        registry.register(
            MCPToolWrapper(object(), "account", _tool(f"calendar_action_{index:02d}"))
        )
    provider = MagicMock(spec=LLMProvider)
    seen: list[set[str]] = []

    async def chat_with_retry(*, tools=None, **_kwargs):
        names = {item["function"]["name"] for item in (tools or [])}
        seen.append(names)
        if len(seen) == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="lookup",
                        name="search_connected_tools",
                        arguments={"query": "calendar action 03"},
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    authorizer = SimpleNamespace(authorize=AsyncMock(return_value=None))
    result = await AgentRunner().run(
        make_run_spec(
            provider,
            initial_messages=[{"role": "user", "content": "find calendar tools"}],
            tools=registry,
            authorizer=authorizer,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        )
    )

    assert result.final_content == "done"
    assert seen[0] == {"search_connected_tools"}
    assert "mcp_account_calendar_action_03" in seen[1]
    authorizer.authorize.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_type", "header_name", "expected_header"),
    [("token", "", "Authorization"), ("headers", "X-Api-Key", "X-Api-Key")],
)
async def test_runtime_attaches_protected_static_auth_for_both_remote_transports(
    monkeypatch, auth_type, header_name, expected_header
) -> None:
    class Store:
        def load(self, key):
            assert key == "connector:account"
            return {"static": {"value": "protected-secret", "header_name": header_name}}

    captured: list[dict] = []

    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[_tool()], nextCursor=None)

    @asynccontextmanager
    async def remote_session(_endpoint, **kwargs):
        captured.append(kwargs)
        yield Session()

    monkeypatch.setattr("collie_core.services.credentials.CredentialStore", Store)
    monkeypatch.setattr("collie_core.connectors.remote.remote_mcp_session", remote_session)

    for transport in ("streamableHttp", "sse"):
        registry = ToolRegistry()
        cfg = MCPServerConfig(
            type=transport,
            url="https://example.test/mcp",
            enabled_tools=["search_pages"],
            connector_connection_id="account",
            connector_auth_type=auth_type,
            connector_header_name=header_name,
            connector_endpoint_policy=True,
        )
        stacks = await connect_mcp_servers({"account": cfg}, registry)
        await stacks["account"].aclose()

    assert len(captured) == 2
    for call in captured:
        assert call["headers"][expected_header].endswith("protected-secret")


@pytest.mark.asyncio
async def test_runtime_attaches_and_closes_oauth_for_both_remote_transports(monkeypatch) -> None:
    auth = object()
    captured: list[dict] = []
    closed: list[object] = []

    class Store:
        pass

    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[_tool()], nextCursor=None)

    @asynccontextmanager
    async def remote_session(_endpoint, **kwargs):
        captured.append(kwargs)
        yield Session()

    monkeypatch.setattr("collie_core.services.credentials.CredentialStore", Store)
    monkeypatch.setattr("collie_core.connectors.auth.build_oauth_provider", lambda *a, **k: auth)
    monkeypatch.setattr("collie_core.connectors.auth.close_oauth_provider", closed.append)
    monkeypatch.setattr("collie_core.connectors.remote.remote_mcp_session", remote_session)

    for transport in ("streamableHttp", "sse"):
        cfg = MCPServerConfig(
            type=transport,
            url="https://example.test/mcp",
            enabled_tools=["search_pages"],
            connector_connection_id="account",
            connector_auth_type="oauth",
            connector_oauth_config={"scopes": ["read"]},
            connector_endpoint_policy=True,
        )
        stacks = await connect_mcp_servers({"account": cfg}, ToolRegistry())
        await stacks["account"].aclose()

    assert [call["auth"] for call in captured] == [auth, auth]
    assert closed == [auth, auth]


@pytest.mark.asyncio
async def test_authenticated_attach_failure_never_logs_decrypted_secret(monkeypatch) -> None:
    secret = "sentinel-decrypted-credential"

    class Store:
        def load(self, _key):
            return {"static": {"value": secret}}

    @asynccontextmanager
    async def failing_session(_endpoint, **_kwargs):
        # Put the secret in both the exception and this diagnostic stack frame.
        leaked_local = secret
        raise RuntimeError(f"transport rejected {leaked_local}")
        yield  # pragma: no cover

    monkeypatch.setattr("collie_core.services.credentials.CredentialStore", Store)
    monkeypatch.setattr("collie_core.connectors.remote.remote_mcp_session", failing_session)
    output = StringIO()
    sink = logger.add(
        output,
        format="{message}\n{exception}",
        backtrace=True,
        diagnose=True,
    )
    try:
        config = MCPServerConfig(
            type="streamableHttp",
            url="https://example.test/mcp",
            connector_connection_id="account",
            connector_auth_type="token",
            connector_endpoint_policy=True,
        )
        assert await connect_mcp_servers({"private": config}, ToolRegistry()) == {}
    finally:
        logger.remove(sink)

    rendered = output.getvalue()
    assert "RuntimeError" in rendered
    assert secret not in rendered
