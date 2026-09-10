"""Storage domains for the Collie SQLite layer.

Each module owns one group of tables and exposes it as a mixin. Those mixins
are composed into :class:`collie_core.db.CollieDB`, which remains the single
public entry point for storage, so call sites keep using ``db.<method>``.
"""

from __future__ import annotations

from collie_core.db_domains.approvals import ApprovalsDomain
from collie_core.db_domains.artifacts import ArtifactsDomain
from collie_core.db_domains.automations import AutomationsDomain
from collie_core.db_domains.checklists import ChecklistsDomain
from collie_core.db_domains.connectors import ConnectorsDomain
from collie_core.db_domains.conversations import ConversationsDomain
from collie_core.db_domains.life import LifeDomain
from collie_core.db_domains.providers import ProvidersDomain
from collie_core.db_domains.settings import SettingsDomain

__all__ = [
    "ApprovalsDomain",
    "ArtifactsDomain",
    "AutomationsDomain",
    "ChecklistsDomain",
    "ConnectorsDomain",
    "ConversationsDomain",
    "LifeDomain",
    "ProvidersDomain",
    "SettingsDomain",
]
