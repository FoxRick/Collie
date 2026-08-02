"""Collie service connections (spec §3.4, F037-F042).

The GUI service catalog: users click Connect, approve in the browser (OAuth)
or paste one token, and Collie wires up the matching MCP server. No JSON.
"""

from collie_core.services.catalog import SERVICE_CATALOG, ServiceDef, service_def
from collie_core.services.manager import (
    ServiceManager,
    bind_service_manager,
    get_service_manager,
)

__all__ = [
    "SERVICE_CATALOG",
    "ServiceDef",
    "ServiceManager",
    "bind_service_manager",
    "get_service_manager",
    "service_def",
]
