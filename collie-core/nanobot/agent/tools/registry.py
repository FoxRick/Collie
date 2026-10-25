"""Tool registry for dynamic tool management."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import ContextAware, current_request_context

_DIRECT_MCP_SCHEMA_LIMIT = 32
_SELECTED_MCP_SCHEMA_LIMIT = 24
_selected_connected_tools: ContextVar[tuple[str, list[str]] | None] = ContextVar(
    "selected_connected_tools", default=None
)


class _ConnectedToolSearch(Tool):
    """Select a bounded set of already-authorized connector tool schemas."""

    _plugin_discoverable = False

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "search_connected_tools"

    @property
    def description(self) -> str:
        return (
            "Search enabled tools from connected accounts. Matching tool schemas become "
            "available on the next step; this search does not run them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Capability or service to find"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 8},
            },
            "required": ["query"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query") or "").strip().lower()
        limit = max(1, min(int(kwargs.get("limit") or 8), 10))
        if not query:
            return ToolResult.error("Please say what connected capability you want to find.")
        matches: list[tuple[str, Tool]] = []
        for name, tool in self._registry._tools.items():
            if not name.startswith("mcp_"):
                continue
            authority_error = getattr(tool, "_connector_authority_error", None)
            if callable(authority_error) and authority_error():
                continue
            account = str(getattr(tool, "_connector_account_label", "") or "")
            provider = str(getattr(tool, "_connector_provider_id", "") or "")
            haystack = f"{name} {tool.description} {provider} {account}".lower()
            if all(part in haystack for part in query.split()):
                matches.append((name, tool))
        matches.sort(key=lambda item: item[0])
        selected = [name for name, _ in matches[:limit]]
        _, turn_selection = self._registry._turn_selection()
        # AgentRunner executes read tools in child tasks. Mutating the inherited
        # per-turn list makes the selection visible to the parent task's next
        # model iteration without leaking it to another concurrent turn.
        turn_selection[:] = selected[:_SELECTED_MCP_SCHEMA_LIMIT]
        if not selected:
            return "No enabled connected tools matched that search."
        return "Selected connected tools: " + ", ".join(selected)


if TYPE_CHECKING:
    from nanobot.runtime_context import RuntimeContextProvider


def is_tool_error_result(name: str, result: Any) -> bool:
    return isinstance(result, ToolResult) and result.is_error


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        self._connected_tool_search = _ConnectedToolSearch(self)

    def _large_mcp_inventory(self) -> bool:
        return sum(name.startswith("mcp_") for name in self._tools) > _DIRECT_MCP_SCHEMA_LIMIT

    def _turn_selection(self) -> tuple[str, list[str]]:
        context = current_request_context()
        key = str((getattr(context, "turn_id", None) or id(context)) if context else "direct")
        current = _selected_connected_tools.get()
        if current is None or current[0] != key:
            current = (key, [])
            _selected_connected_tools.set(current)
        return current

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        if name == self._connected_tool_search.name and self._large_mcp_inventory():
            return self._connected_tool_search
        return self._tools.get(name)

    def get_runtime_context_providers(self) -> list[RuntimeContextProvider]:
        """Return tool-owned providers in stable tool-name order."""
        providers: list[RuntimeContextProvider] = []
        for name in sorted(self._tools):
            provider = self._tools[name].runtime_context_provider()
            if provider is not None:
                providers.append(provider)
        return providers

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [registered for registered in self._tools if self._lookup_key(registered) == key]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return self.get(name) is not None

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.  The result is cached until the next
        register/unregister call.
        """
        large_inventory = self._large_mcp_inventory()
        if self._cached_definitions is not None and not large_inventory:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        if large_inventory:
            _, turn_selection = self._turn_selection()
            selected = set(turn_selection)
            mcp_tools = [schema for schema in mcp_tools if self._schema_name(schema) in selected][
                :_SELECTED_MCP_SCHEMA_LIMIT
            ]
            builtins.append(self._connected_tool_search.to_schema())
            builtins.sort(key=self._schema_name)
        definitions = builtins + mcp_tools
        if not large_inventory:
            self._cached_definitions = definitions
        return definitions

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one tool call."""
        tool = self.get(name)
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = (
                f" Did you mean '{suggestion}'? Tool names must match exactly."
                if suggestion
                else ""
            )
            return (
                None,
                params,
                (
                    ToolResult.error(
                        f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
                    )
                ),
            )

        # Compatibility for external tools that still implement the legacy
        # setter protocol. Built-ins read the authoritative ContextVar
        # directly and never copy routing state.
        if isinstance(tool, ContextAware) and (ctx := current_request_context()) is not None:
            tool.set_context(ctx)

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return (
                tool,
                params,
                (
                    ToolResult.error(
                        f"Error: Tool '{name}' parameters must be a JSON object, got "
                        f"{type(params).__name__}. Use named parameters like "
                        'tool_name(param1="value1", param2="value2") matching the tool schema.'
                    )
                ),
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return (
                tool,
                cast_params,
                (
                    ToolResult.error(
                        f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
                    )
                ),
            )
        return tool, cast_params, None

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return value

        return parsed

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params.get("arguments"))

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name with given parameters."""
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return ToolResult.error(str(error) + hint)

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if is_tool_error_result(name, result):
                return ToolResult.error(str(result) + hint)
            return result
        except Exception as e:
            return ToolResult.error(f"Error executing {name}: {str(e)}" + hint)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        names = list(self._tools.keys())
        if self._large_mcp_inventory():
            names.append(self._connected_tool_search.name)
        return names

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
