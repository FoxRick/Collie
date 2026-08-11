"""Tests for tool plugin architecture: ToolLoader, ToolContext, metadata."""

from __future__ import annotations

from dataclasses import fields
from typing import Any
from unittest.mock import MagicMock

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.loader import _SKIP_MODULES, ToolLoader


class _MinimalTool(Tool):
    @property
    def name(self) -> str:
        return "test_minimal"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return "ok"


def test_tool_default_config_cls_is_none():
    assert _MinimalTool.config_cls() is None


def test_tool_default_config_key_is_empty():
    assert _MinimalTool.config_key == ""


def test_tool_default_enabled_is_true():
    assert _MinimalTool.enabled(None) is True


def test_tool_default_create_returns_instance():
    tool = _MinimalTool.create(None)
    assert isinstance(tool, _MinimalTool)
    assert tool.name == "test_minimal"


def test_tool_plugin_discoverable_default_is_true():
    assert _MinimalTool._plugin_discoverable is True


# --- ToolContext tests ---


def test_tool_context_has_required_fields():
    field_names = {f.name for f in fields(ToolContext)}
    required = {
        "config",
        "workspace",
        "bus",
        "subagent_manager",
        "cron_service",
        "file_state_store",
        "provider_snapshot_loader",
        "image_generation_provider_configs",
        "timezone",
    }
    assert required <= field_names


def test_tool_context_defaults():
    ctx = ToolContext(config=None, workspace="/tmp")
    assert ctx.bus is None
    assert ctx.subagent_manager is None
    assert ctx.cron_service is None
    assert ctx.provider_snapshot_loader is None
    assert ctx.image_generation_provider_configs is None
    assert ctx.timezone == "UTC"


# --- ToolLoader tests ---


def test_skip_modules_excludes_infrastructure():
    infra = {
        "base",
        "schema",
        "registry",
        "context",
        "loader",
        "config",
        "file_state",
        "sandbox",
        "mcp",
        "__init__",
    }
    assert infra <= _SKIP_MODULES


def test_discover_finds_concrete_tools():
    loader = ToolLoader()
    discovered = loader.discover()
    class_names = {cls.__name__ for cls in discovered}
    assert "MessageTool" in class_names
    assert "WebSearchTool" in class_names
    assert "WebFetchTool" in class_names
    assert "CronTool" in class_names


def test_discover_excludes_abstract_and_mcp():
    loader = ToolLoader()
    discovered = loader.discover()
    class_names = {cls.__name__ for cls in discovered}
    assert "_FsTool" not in class_names
    assert "_SearchTool" not in class_names
    assert "MCPToolWrapper" not in class_names
    assert "MCPResourceWrapper" not in class_names
    assert "MCPPromptWrapper" not in class_names


def test_discover_skips_private_classes():
    loader = ToolLoader()
    discovered = loader.discover()
    for cls in discovered:
        assert not cls.__name__.startswith("_")


# --- Task 5: MessageTool, CronTool ---


async def test_message_tool_create():
    from nanobot.agent.tools.message import MessageTool

    mock_bus = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", bus=mock_bus)
    tool = MessageTool.create(ctx)
    assert isinstance(tool, MessageTool)


def test_cron_tool_enabled_without_service():
    from nanobot.agent.tools.cron import CronTool

    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", cron_service=None)
    assert CronTool.enabled(ctx) is False


def test_cron_tool_enabled_with_service():
    from nanobot.agent.tools.cron import CronTool

    mock_service = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", cron_service=mock_service)
    assert CronTool.enabled(ctx) is True


def test_cron_tool_create():
    from nanobot.agent.tools.cron import CronTool

    mock_service = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(
        config=mock_config,
        workspace="/tmp",
        cron_service=mock_service,
        timezone="Asia/Shanghai",
    )
    tool = CronTool.create(ctx)
    assert isinstance(tool, CronTool)


def test_web_tools_config_cls():
    from nanobot.agent.tools.web import WebFetchTool, WebSearchTool, WebToolsConfig

    assert WebSearchTool.config_key == "web"
    assert WebSearchTool.config_cls() is WebToolsConfig
    assert WebFetchTool.config_key == "web"
    assert WebFetchTool.config_cls() is WebToolsConfig


def test_web_tools_enabled():
    from nanobot.agent.tools.web import WebSearchTool

    mock_config = MagicMock()
    mock_config.web.enable = True
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    assert WebSearchTool.enabled(ctx) is True
    mock_config.web.enable = False
    assert WebSearchTool.enabled(ctx) is False


def test_web_search_tool_create():
    from nanobot.agent.tools.web import WebSearchTool

    mock_config = MagicMock()
    mock_config.web.enable = True
    mock_config.web.search = MagicMock()
    mock_config.web.proxy = None
    mock_config.web.user_agent = None
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    tool = WebSearchTool.create(ctx)
    assert isinstance(tool, WebSearchTool)


def test_web_fetch_tool_create():
    from nanobot.agent.tools.web import WebFetchTool

    mock_config = MagicMock()
    mock_config.web.enable = True
    mock_config.web.fetch = MagicMock()
    mock_config.web.proxy = None
    mock_config.web.user_agent = None
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    tool = WebFetchTool.create(ctx)
    assert isinstance(tool, WebFetchTool)


def test_image_gen_tool_config_cls():
    from nanobot.agent.tools.image_generation import ImageGenerationTool, ImageGenerationToolConfig

    assert ImageGenerationTool.config_key == "image_generation"
    assert ImageGenerationTool.config_cls() is ImageGenerationToolConfig


def test_image_gen_tool_enabled():
    from nanobot.agent.tools.image_generation import ImageGenerationTool

    mock_config = MagicMock()
    mock_config.image_generation.enabled = True
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    assert ImageGenerationTool.enabled(ctx) is True
    mock_config.image_generation.enabled = False
    assert ImageGenerationTool.enabled(ctx) is False


def test_image_gen_tool_create():
    from nanobot.agent.tools.image_generation import ImageGenerationTool

    mock_config = MagicMock()
    mock_config.image_generation = MagicMock()
    ctx = ToolContext(
        config=mock_config,
        workspace="/tmp",
        image_generation_provider_configs={"openrouter": MagicMock()},
    )
    tool = ImageGenerationTool.create(ctx)
    assert isinstance(tool, ImageGenerationTool)


def test_mcp_wrappers_not_discoverable():
    from nanobot.agent.tools.mcp import MCPPromptWrapper, MCPResourceWrapper, MCPToolWrapper

    assert MCPToolWrapper._plugin_discoverable is False
    assert MCPResourceWrapper._plugin_discoverable is False
    assert MCPPromptWrapper._plugin_discoverable is False


# --- Task 10: Integration test ---


def test_loader_registers_same_tools_as_old_hardcoded():
    """Verify the loader produces the same tool set as the old _register_default_tools."""
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.registry import ToolRegistry

    mock_config = MagicMock()
    mock_config.restrict_to_workspace = False
    mock_config.web.enable = True
    mock_config.web.search = MagicMock()
    mock_config.web.fetch = MagicMock()
    mock_config.web.proxy = None
    mock_config.web.user_agent = None
    mock_config.image_generation.enabled = False

    ctx = ToolContext(
        config=mock_config,
        workspace="/tmp",
        bus=MagicMock(),
        subagent_manager=MagicMock(),
        cron_service=MagicMock(),
        timezone="UTC",
    )
    registry = ToolRegistry()
    loader = ToolLoader()
    registered = loader.load(ctx, registry)

    expected = {
        "web_search",
        "web_fetch",
        "message",
        "cron",
    }
    actual = set(registered)
    assert expected <= actual, f"Missing tools: {expected - actual}"
