"""Command modules for the Collie IPC server.

One module per command group. Each exposes a mixin that
:class:`collie_core.ipc.server.CollieIPCServer` composes, so a handler stays
reachable as ``server._cmd_<kind>`` and the renderer's wire contract does not
change. The groups never call each other; anything shared lives on the server
class or in :mod:`collie_core.ipc.command_support`.
"""

from __future__ import annotations

from collie_core.ipc.commands.agents import AgentCommands
from collie_core.ipc.commands.collaboration import CollaborationCommands
from collie_core.ipc.commands.connectors import ConnectorCommands
from collie_core.ipc.commands.files import FileCommands
from collie_core.ipc.commands.memory import MemoryCommands
from collie_core.ipc.commands.messengers import MessengerCommands
from collie_core.ipc.commands.providers import ProviderCommands
from collie_core.ipc.commands.routines import RoutineCommands

__all__ = [
    "AgentCommands",
    "CollaborationCommands",
    "ConnectorCommands",
    "FileCommands",
    "MemoryCommands",
    "MessengerCommands",
    "ProviderCommands",
    "RoutineCommands",
]
