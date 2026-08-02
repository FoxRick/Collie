"""Bridge between Collie life tools and connected MCP services (Phase 3).

When a service is connected, its MCP tools are registered directly in the
agent's tool registry (named ``mcp_<service>_<tool>``). The life-tool stubs
use these helpers to point the model at them instead of nagging the user.
"""

from __future__ import annotations

from collie_core.services.manager import get_service_manager

__all__ = ["connected_service_id", "mcp_tools_hint"]


def connected_service_id(*service_ids: str) -> str | None:
    """Return the first connected service id among *service_ids*, if any."""
    manager = get_service_manager()
    if manager is None:
        return None
    for service_id in service_ids:
        try:
            if manager.is_connected(service_id):
                return service_id
        except Exception:
            return None
    return None


def mcp_tools_hint(service_id: str, task: str) -> str:
    """Tell the model to use the connected service's MCP tools for *task*."""
    prefix = "mcp_" + service_id.replace("-", "_")
    return (
        f"{service_id} is connected! To {task}, call its MCP tools directly — "
        f"they are registered with names starting with `{prefix}_`. "
        "Pick the matching tool and pass the user's request through."
    )
