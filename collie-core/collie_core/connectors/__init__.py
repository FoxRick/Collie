"""Consumer-facing connected app framework."""

from collie_core.connectors.catalog import CONNECTOR_CATALOG, connector_def
from collie_core.connectors.manager import ConnectorManager

__all__ = ["CONNECTOR_CATALOG", "ConnectorManager", "connector_def"]
