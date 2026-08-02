"""Messenger lifecycle + routing for Collie (Phase 4, F093-F100 revised).

Instead of a companion phone app, Collie talks through the messengers the
user already carries: Telegram, WhatsApp, Slack, and Discord. The channel
implementations are vendored nanobot code; this manager owns:

- which messengers are enabled (SQLite settings) and their secrets
  (runtime-injected from the OS keychain, never persisted here),
- starting/stopping channel tasks against the current agent bus,
- dispatching outbound bus traffic to the right channel,
- mirroring messenger chats into a desktop conversation per messenger,
- pairing status (unknown senders get a code; approval happens in
  Settings -> Phone),
- delivering automations ("push notifications") to the phone.

Settings keys (collie.db):
- ``messengers.<name>.enabled``              "1"/"0"
- ``messengers.<name>.deliver_automations``  "1"/"0"
- ``messengers.<name>.last_chat_id``         delivery target for automations
- ``messengers.<name>.conversation_id``      desktop mirror conversation
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger

from collie_core.db import CollieDB, collie_home
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.outbound_events import (
    ProgressEvent,
    RetryWaitEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    outbound_event_from_message,
)
from nanobot.bus.queue import MessageBus
from nanobot.pairing import PAIRING_CODE_META_KEY

__all__ = ["MESSENGERS", "MessengerManager"]

Broadcaster = Callable[[dict[str, Any]], Awaitable[None]]

# The connectable messengers: label for the UI plus the secret fields each
# one needs before it can start. WhatsApp needs no token — it pairs by QR.
MESSENGERS: dict[str, dict[str, Any]] = {
    "telegram": {"label": "Telegram", "secrets": ("token",), "emoji": "✈️"},
    "whatsapp": {"label": "WhatsApp", "secrets": (), "emoji": "💬"},
    "slack": {"label": "Slack", "secrets": ("bot_token", "app_token"), "emoji": "🏢"},
    "discord": {"label": "Discord", "secrets": ("token",), "emoji": "🎮"},
}


def _is_group_chat_id(chat_id: str) -> bool:
    """Telegram group/supergroup chats use negative numeric ids."""
    digits = chat_id.lstrip("-")
    if not digits.isdigit():
        return False
    return chat_id.startswith("-")


# The Windows alpha activates Telegram only. The vendored channel modules stay
# in the source tree for attribution/history, but cannot be enabled at runtime.
MESSENGERS = {"telegram": MESSENGERS["telegram"]}


class MessengerManager:
    """Owns messenger channels, their lifecycle, and Collie-side routing."""

    def __init__(self, db: CollieDB, *, broadcaster: Broadcaster | None = None) -> None:
        self.db = db
        self.broadcaster = broadcaster
        self.channels: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._drain_tasks: dict[str, asyncio.Task] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._secrets: dict[str, dict[str, str]] = {}
        self._errors: dict[str, str] = {}
        self._whatsapp_qr: str | None = None
        self._bus: MessageBus | None = None

    # -- settings & secrets -------------------------------------------------

    def set_secret(self, messenger: str, key: str, value: str) -> None:
        """Inject a messenger secret at runtime (keychain -> IPC -> here)."""
        name = messenger.lower()
        if name not in MESSENGERS:
            raise ValueError(f"unknown messenger: {messenger}")
        self._secrets.setdefault(name, {})[key] = value

    def secrets_ok(self, name: str) -> bool:
        if name not in MESSENGERS:
            return False
        needed = MESSENGERS.get(name, {}).get("secrets", ())
        have = self._secrets.get(name, {})
        return all(have.get(k) for k in needed)

    def _flag(self, key: str) -> bool:
        return str(self.db.get_setting(key, "") or "") in ("1", "true", "True")

    def enabled_names(self) -> list[str]:
        return [n for n in MESSENGERS if self._flag(f"messengers.{n}.enabled")]

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.db.set_setting(f"messengers.{name}.enabled", "1" if enabled else "0")

    def clear_local_connection(self, name: str) -> None:
        """Forget in-memory credentials and paired senders on disconnect."""
        from nanobot.pairing import get_approved, revoke

        self._secrets.pop(name, None)
        self._errors.pop(name, None)
        self.db.set_setting(f"messengers.{name}.last_chat_id", None)
        for sender_id in get_approved(name):
            revoke(name, sender_id)

    def set_deliver_automations(self, name: str, deliver: bool) -> None:
        self.db.set_setting(
            f"messengers.{name}.deliver_automations", "1" if deliver else "0"
        )

    def automation_targets(self) -> list[str]:
        """Messengers that get every automation delivered."""
        return [
            n
            for n in self.channels
            if self._flag(f"messengers.{n}.deliver_automations")
        ]

    def _config_for(self, name: str) -> dict[str, Any]:
        secrets = self._secrets.get(name, {})
        if name == "telegram":
            return {"enabled": True, "token": secrets.get("token", "")}
        if name == "whatsapp":
            wa_dir = collie_home() / "whatsapp"
            wa_dir.mkdir(parents=True, exist_ok=True)
            return {"enabled": True, "databasePath": str(wa_dir / "whatsapp.db")}
        if name == "slack":
            return {
                "enabled": True,
                "botToken": secrets.get("bot_token", ""),
                "appToken": secrets.get("app_token", ""),
            }
        if name == "discord":
            return {"enabled": True, "token": secrets.get("token", "")}
        raise ValueError(f"unknown messenger: {name}")

    # -- lifecycle -----------------------------------------------------------

    async def start(self, bus: MessageBus) -> None:
        """Start every enabled, fully-configured messenger against *bus*."""
        await self.stop()
        self._bus = bus
        for name in self.enabled_names():
            if not self.secrets_ok(name):
                self._errors[name] = "missing credentials"
                continue
            await self._start_one(name, bus)

    async def _start_one(self, name: str, bus: MessageBus) -> None:
        from nanobot.channels.registry import load_channel_class

        try:
            cls = load_channel_class(name)
            channel = cls(self._config_for(name), bus)
        except Exception as e:
            logger.error("Failed to build messenger {} ({})", name, type(e).__name__)
            self._errors[name] = str(e)
            return

        # Messengers stay quiet about tool churn — final answers only.
        channel.send_progress = False
        channel.send_tool_hints = False
        channel.show_reasoning = False

        if name == "whatsapp":
            channel.on_qr = self._on_whatsapp_qr
            channel.on_status = self._on_whatsapp_status

        self.channels[name] = channel
        self._errors.pop(name, None)
        self._queues[name] = asyncio.Queue(maxsize=50)
        self._tasks[name] = asyncio.create_task(self._run_channel(name, channel))
        self._drain_tasks[name] = asyncio.create_task(self._drain_channel(name, channel))
        logger.info("Messenger started: {}", name)

    async def _drain_channel(self, name: str, channel: Any) -> None:
        """Deliver queued outbound messages one at a time, in order."""
        queue = self._queues.get(name)
        if queue is None:
            return
        while True:
            msg = await queue.get()
            try:
                await self._deliver_one(name, channel, msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Messenger send failed on {} ({})", name, type(e).__name__)
                self._errors[name] = str(e)
                await self._broadcast({
                    "type": "messenger_status", "messenger": name, "status": "error",
                    "error": str(e),
                })

    async def _run_channel(self, name: str, channel: Any) -> None:
        try:
            await channel.start()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Messenger {} crashed ({})", name, type(e).__name__)
            self._errors[name] = str(e)
            await self._broadcast(
                {"type": "messenger_status", "messenger": name, "status": "error",
                 "error": str(e)}
            )
            return
        # channel.start() returned: surface a latched fatal error (e.g. an
        # invalid Telegram token) instead of silently "not running".
        fatal = getattr(channel, "fatal_error", None)
        if fatal:
            logger.error("Messenger {} failed fatally: {}", name, fatal)
            self._errors[name] = str(fatal)
            await self._broadcast(
                {"type": "messenger_status", "messenger": name, "status": "error",
                 "error": str(fatal)}
            )

    async def stop(self) -> None:
        for name, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._tasks.pop(name, None)
        for name, task in list(self._drain_tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_tasks.pop(name, None)
        self._queues.clear()
        for name, channel in list(self.channels.items()):
            try:
                await channel.stop()
            except Exception:
                logger.debug("Messenger {} stop failed", name, exc_info=True)
            self.channels.pop(name, None)
        self._whatsapp_qr = None

    async def restart(self) -> None:
        """Re-read settings and restart against the current bus."""
        if self._bus is not None:
            await self.start(self._bus)

    def is_running(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    # -- whatsapp hooks -------------------------------------------------------

    async def _on_whatsapp_qr(self, payload: str) -> None:
        self._whatsapp_qr = payload
        await self._broadcast(
            {"type": "messenger_qr", "messenger": "whatsapp", "qr": payload}
        )

    async def _on_whatsapp_status(self, status: str) -> None:
        if status == "connected":
            self._whatsapp_qr = None
        await self._broadcast(
            {"type": "messenger_status", "messenger": "whatsapp", "status": status}
        )

    # -- routing --------------------------------------------------------------

    async def on_inbound(self, msg: InboundMessage) -> None:
        """Mirror inbound messenger messages into the desktop conversation."""
        name = str(msg.channel or "")
        if name not in MESSENGERS or name not in self.channels:
            return
        if not msg.content:
            return
        label = MESSENGERS[name]["label"]
        conv_id = self._mirror_conversation(
            name,
            label,
            session_key=msg.session_key,
            chat_id=str(msg.chat_id or ""),
        )
        message = self.db.add_message(conv_id, "user", msg.content)
        await self._broadcast(
            {"type": "message", "conversation_id": conv_id, "message": message}
        )

    async def dispatch(self, msg: OutboundMessage) -> bool:
        """Queue an outbound bus message for its messenger. Never blocks.

        Each channel drains its own bounded queue in a dedicated task, so a
        slow messenger can no longer stall every other outbound (desktop
        included). Returns False only when no channel exists for the message.
        """
        name = str(msg.channel or "")
        queue = self._queues.get(name)
        if queue is None:
            return False
        try:
            queue.put_nowait(msg)
        except asyncio.QueueFull:
            # Drop the oldest queued message to keep the bound; the newest
            # state wins for streaming content.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(msg)
        return True

    async def _deliver_one(self, name: str, channel: Any, msg: OutboundMessage) -> None:
        """Deliver one queued outbound message to its channel."""
        event = outbound_event_from_message(msg)
        if isinstance(event, (ProgressEvent, RetryWaitEvent)):
            return  # keep phones quiet: no tool hints / retries
        if isinstance(event, StreamDeltaEvent):
            await channel.send_delta(
                msg.chat_id, event.content, msg.metadata, stream_id=event.stream_id
            )
            return
        if isinstance(event, StreamEndEvent):
            await channel.send_delta(
                msg.chat_id,
                event.content,
                msg.metadata,
                stream_id=event.stream_id,
                stream_end=True,
            )
            await self._after_final(name, msg)
            return
        if isinstance(event, StreamedResponseEvent):
            # Content already went out via deltas; mirror + remember only.
            await self._after_final(name, msg)
            return
        await channel.send(msg)
        if PAIRING_CODE_META_KEY in (msg.metadata or {}):
            await self._broadcast(
                {"type": "messenger_pairing", "messenger": name}
            )
            return
        await self._after_final(name, msg)

    async def _after_final(self, name: str, msg: OutboundMessage) -> None:
        """Remember the delivery target + mirror Collie's reply to desktop."""
        if msg.chat_id:
            chat_id = str(msg.chat_id)
            # Telegram group chats have negative ids. Never remember a group
            # as the automation delivery target — briefings must only reach
            # the user's private chat.
            if not _is_group_chat_id(chat_id):
                self.db.set_setting(f"messengers.{name}.last_chat_id", chat_id)
        if not msg.content:
            return
        label = MESSENGERS[name]["label"]
        conv_id = self._mirror_conversation(name, label)
        message = self.db.add_message(conv_id, "assistant", msg.content)
        await self._broadcast(
            {"type": "message", "conversation_id": conv_id, "message": message}
        )

    def _mirror_conversation(
        self,
        name: str,
        label: str,
        *,
        session_key: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        key = f"messengers.{name}.conversation_id"
        conv_id = str(self.db.get_setting(key, "") or "")
        if conv_id and self.db.get_conversation(conv_id) is not None:
            self._remember_session_identity(
                name,
                conv_id,
                session_key=session_key,
                chat_id=chat_id,
            )
            return conv_id
        conv = self.db.create_conversation(title=f"📱 {label}")
        self.db.set_setting(key, conv["id"])
        # A deleted mirror may leave its channel settings behind until the
        # next inbound message creates the replacement. Do not associate the
        # replacement with stale thread/topic sessions from the old chat.
        self.db.set_setting(f"messengers.{name}.session_keys", [])
        self.db.set_setting(f"messengers.{name}.session_key", None)
        self.db.set_setting(f"messengers.{name}.session_chat_id", None)
        self._remember_session_identity(
            name,
            str(conv["id"]),
            session_key=session_key,
            chat_id=chat_id,
        )
        return conv["id"]

    def _remember_session_identity(
        self,
        name: str,
        conversation_id: str,
        *,
        session_key: str | None,
        chat_id: str | None,
    ) -> None:
        """Persist an inbound messenger's exact engine session identity."""
        exact_key = str(session_key or "").strip()
        if not exact_key:
            return
        setting = f"messengers.{name}.session_keys"
        stored = self.db.get_setting(setting, [])
        keys = [
            value
            for value in (stored if isinstance(stored, list) else [])
            if isinstance(value, str) and value
        ]
        if exact_key not in keys:
            keys.append(exact_key)
        self.db.set_setting(setting, keys)
        self.db.set_setting(f"messengers.{name}.session_key", exact_key)
        self.db.set_setting(f"messengers.{name}.session_chat_id", str(chat_id or ""))
        # Keep the association explicit even when tests or migrations call
        # this helper directly rather than creating the mirror first.
        self.db.set_setting(f"messengers.{name}.conversation_id", conversation_id)

    def _names_for_conversation(self, conversation_id: str) -> list[str]:
        """Return persisted messenger names mapped to a desktop conversation."""
        names: list[str] = []
        suffix = ".conversation_id"
        for key, value in self.db.all_settings().items():
            if not key.startswith("messengers.") or not key.endswith(suffix):
                continue
            if str(value or "") != conversation_id:
                continue
            name = key[len("messengers.") : -len(suffix)]
            if name:
                names.append(name)
        return sorted(set(names))

    def session_keys_for_conversation(self, conversation_id: str) -> set[str]:
        """Resolve every exact messenger session associated with a mirror."""
        keys: set[str] = set()
        for name in self._names_for_conversation(conversation_id):
            name_keys: set[str] = set()
            stored = self.db.get_setting(f"messengers.{name}.session_keys", [])
            if isinstance(stored, list):
                name_keys.update(
                    value for value in stored if isinstance(value, str) and value
                )
            latest = self.db.get_setting(f"messengers.{name}.session_key", "")
            if isinstance(latest, str) and latest:
                name_keys.add(latest)
            # Compatibility for mirrors created before authoritative session
            # keys were persisted. New inbound traffic replaces this fallback.
            if not name_keys:
                chat_id = str(
                    self.db.get_setting(f"messengers.{name}.last_chat_id", "") or ""
                )
                if chat_id:
                    name_keys.add(f"{name}:{chat_id}")
            keys.update(name_keys)
        return keys

    def session_target_for_conversation(
        self, conversation_id: str
    ) -> tuple[str, str, str] | None:
        """Return latest exact (session key, channel, chat id) routing target."""
        for name in self._names_for_conversation(conversation_id):
            session_key = str(
                self.db.get_setting(f"messengers.{name}.session_key", "") or ""
            )
            chat_id = str(
                self.db.get_setting(f"messengers.{name}.session_chat_id", "") or ""
            )
            if session_key and chat_id:
                return session_key, name, chat_id

            # Legacy mirrors had only the automation target. Preserve their
            # behavior until a new inbound message records the exact identity.
            legacy_chat_id = str(
                self.db.get_setting(f"messengers.{name}.last_chat_id", "") or ""
            )
            if legacy_chat_id:
                return f"{name}:{legacy_chat_id}", name, legacy_chat_id
        return None

    def forget_conversation(self, conversation_id: str) -> None:
        """Remove mirror/session associations after conversation deletion."""
        for name in self._names_for_conversation(conversation_id):
            for suffix in (
                "conversation_id",
                "session_keys",
                "session_key",
                "session_chat_id",
            ):
                self.db.delete_setting(f"messengers.{name}.{suffix}")

    # -- automations ("push notifications") ----------------------------------

    async def deliver(self, name: str, content: str) -> bool:
        """Send *content* to the user's chat on *name* (automation fanout)."""
        queue = self._queues.get(name)
        if queue is None or not content:
            return False
        chat_id = str(self.db.get_setting(f"messengers.{name}.last_chat_id", "") or "")
        if not chat_id or _is_group_chat_id(chat_id):
            self._errors[name] = "no private chat yet — message Collie once from your phone"
            return False
        try:
            queue.put_nowait(
                OutboundMessage(channel=name, chat_id=chat_id, content=content)
            )
            return True
        except asyncio.QueueFull:
            self._errors[name] = "the messenger queue is full — try again shortly"
            return False

    async def confirm_pairing(self, name: str, sender_id: str) -> bool:
        """Send the required post-approval test reply to a paired Telegram user."""
        channel = self.channels.get(name)
        if channel is None or not sender_id:
            return False
        try:
            await channel.send(
                OutboundMessage(
                    channel=name,
                    chat_id=sender_id,
                    content="Paired! I'm ready to chat here. 🐕",
                )
            )
            self.db.set_setting(f"messengers.{name}.last_chat_id", sender_id)
            return True
        except Exception as e:
            logger.error("Pairing confirmation failed on {} ({})", name, type(e).__name__)
            self._errors[name] = "I paired you, but the test reply did not get through."
            return False

    # -- status ----------------------------------------------------------------

    def status(self) -> list[dict[str, Any]]:
        from nanobot.pairing import get_approved, list_pending

        out: list[dict[str, Any]] = []
        pending_all = list_pending()
        for name, meta in MESSENGERS.items():
            connected = self.is_running(name)
            if name == "whatsapp" and connected:
                channel = self.channels.get(name)
                connected = bool(getattr(channel, "_connected", False))
            channel = self.channels.get(name)
            fatal = str(getattr(channel, "fatal_error", None) or "") or None
            out.append({
                "id": name,
                "label": meta["label"],
                "emoji": meta["emoji"],
                "secrets": list(meta["secrets"]),
                "enabled": self._flag(f"messengers.{name}.enabled"),
                "configured": self.secrets_ok(name),
                "running": self.is_running(name),
                "connected": connected,
                "deliver_automations": self._flag(
                    f"messengers.{name}.deliver_automations"
                ),
                "error": self._errors.get(name) or fatal,
                "approved": get_approved(name),
                "pending": [
                    {"code": p["code"], "sender_id": p["sender_id"]}
                    for p in pending_all
                    if p.get("channel") == name
                ],
                "qr": self._whatsapp_qr if name == "whatsapp" else None,
                "last_chat_id": str(
                    self.db.get_setting(f"messengers.{name}.last_chat_id", "") or ""
                ),
            })
        return out

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if self.broadcaster is None:
            return
        try:
            await self.broadcaster(payload)
        except Exception:
            logger.debug("Messenger broadcast failed", exc_info=True)
