"""Collie channels: the WebSocket IPC bridge to the Electron shell.

All platform channel bots (Telegram, Discord, Slack, etc.) were removed in
the Collie fork. The only channel is the local WebSocket used as IPC between
the Python core and the Electron UI.
"""

from nanobot.channels.base import BaseChannel

__all__ = ["BaseChannel"]
