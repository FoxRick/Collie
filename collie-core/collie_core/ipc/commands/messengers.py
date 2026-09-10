"""Messenger commands.

Handlers for the ``messengers`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

from websockets.asyncio.server import ServerConnection


class MessengerCommands:
    """Messenger commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_get_messengers(self, connection: ServerConnection, frame: dict) -> dict:
        return {"messengers": self._messengers().status()}

    async def _cmd_set_messenger(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.messengers import MESSENGERS

        manager = self._messengers()
        name = str(frame.get("messenger") or "").lower()
        has_updates = "enabled" in frame or "deliver_automations" in frame
        if has_updates and name not in MESSENGERS:
            raise ValueError(f"unknown messenger: {name or '(none)'}")
        if "enabled" in frame:
            enabled = bool(frame.get("enabled"))
            manager.set_enabled(name, enabled)
            if not enabled:
                manager.clear_local_connection(name)
        if "deliver_automations" in frame:
            manager.set_deliver_automations(name, bool(frame.get("deliver_automations")))
        await manager.restart()
        return {"messengers": manager.status()}

    async def _cmd_set_messenger_secret(self, connection: ServerConnection, frame: dict) -> dict:
        manager = self._messengers()
        name = str(frame.get("messenger") or "").lower()
        key = str(frame.get("key") or "")
        value = str(frame.get("value") or "")
        if not name or not key:
            raise ValueError("set_messenger_secret requires 'messenger' and 'key'")
        if name == "telegram" and key == "token":
            if not value or ":" not in value:
                raise ValueError("That Telegram token doesn't look right. Copy it from @BotFather.")
            try:
                from telegram import Bot

                async with Bot(value) as bot:
                    await bot.get_me()
            except Exception as error:
                raise ValueError(
                    "Telegram didn't accept that token. Copy the latest token from @BotFather."
                ) from error
        manager.set_secret(name, key, value)
        return {"saved": True}

    async def _cmd_revoke_messenger_sender(self, connection: ServerConnection, frame: dict) -> dict:
        from nanobot.pairing import revoke

        name = str(frame.get("messenger") or "").lower()
        sender_id = str(frame.get("sender_id") or "")
        return {"revoked": revoke(name, sender_id)}
