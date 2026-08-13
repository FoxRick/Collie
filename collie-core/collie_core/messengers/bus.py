"""Message bus with an inbound observer hook.

The engine's ``MessageBus`` decouples channels from the agent loop. Collie
additionally wants to *see* messenger traffic so it can mirror those chats
into the desktop UI. ``CollieBus`` calls an optional observer for every
inbound message before handing it to the loop; failures in the observer
never block the turn.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus

__all__ = ["CollieBus"]

InboundObserver = Callable[[InboundMessage], Awaitable[Any]]


class CollieBus(MessageBus):
    """Engine bus + inbound observer for messenger chat mirroring."""

    def __init__(self, *, on_inbound: InboundObserver | None = None) -> None:
        super().__init__()
        self._on_inbound = on_inbound

    async def publish_inbound(self, msg: InboundMessage) -> None:
        handled = False
        if self._on_inbound is not None:
            try:
                handled = bool(await self._on_inbound(msg))
            except Exception:
                logger.exception("Inbound observer failed for {}", msg.channel)
        if not handled:
            await super().publish_inbound(msg)
