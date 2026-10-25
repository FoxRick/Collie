"""Memory-only desktop conversations, composed through supported engine interfaces."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from loguru import logger

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.session.manager import Session, SessionManager


def redact_temporary_logs(record):
    """Remove engine/provider diagnostics for the current temporary task and children."""
    if record["extra"].get("temporary_chat"):
        record["message"] = "Temporary chat diagnostic omitted"
        record["exception"] = None
        record["extra"] = {}


class NoTemporaryTools(ToolRegistry):
    # The engine uses `tools or self.tools`; an empty registry must remain truthy.
    def __bool__(self):
        return True


class DesktopSessionManager:
    """Route registered ghost keys to RAM; durable APIs see only the real manager."""

    def __init__(self, workspace):
        self.durable = SessionManager(workspace)
        self.temporary: dict[str, Session] = {}

    def __getattr__(self, name):
        return getattr(self.durable, name)

    def open(self):
        key = f"ghost:{uuid4().hex}"
        self.temporary[key] = Session(key=key)
        return key

    def get_or_create(self, key):
        if key.startswith("ghost:"):
            if key not in self.temporary:
                raise ValueError("This temporary chat is closed.")
            return self.temporary[key]
        return self.durable.get_or_create(key)

    def save(self, session, *, fsync=False):
        if not session.key.startswith("ghost:"):
            self.durable.save(session, fsync=fsync)

    def close(self, key):
        self.temporary.pop(key, None)

    def fork_session_before_user_index(self, source_key, target_key, *args, **kwargs):
        if source_key.startswith("ghost:") or target_key.startswith("ghost:"):
            raise ValueError("Temporary chats cannot be copied into saved history.")
        return self.durable.fork_session_before_user_index(source_key, target_key, *args, **kwargs)

    def delete_session(self, key):
        if key.startswith("ghost:"):
            existed = key in self.temporary
            self.close(key)
            return existed
        return self.durable.delete_session(key)


class TemporaryChatService:
    def __init__(self, sessions, loop_provider, configure):
        self.sessions = sessions
        self.loop_provider = loop_provider
        self.configure = configure

    async def turn(self, key, content, on_stream):
        if key not in self.sessions.temporary:
            raise ValueError("This temporary chat is closed.")
        self.sessions.get_or_create(key)  # closed keys fail before provider work
        with logger.contextualize(temporary_chat=True):
            if self.loop_provider() is None:
                result = await self.configure()
                if not result.get("configured"):
                    raise ValueError("Connect a model provider before starting a temporary chat.")
            outbound = await self.loop_provider().process_direct(
                content,
                session_key=key,
                channel="ghost",
                chat_id=key,
                ephemeral=True,
                tools=NoTemporaryTools(),
                hooks=[],
                hook_factories=[],
                on_stream=on_stream,
            )
            return outbound.content if outbound else ""


class TemporaryChatCommands:
    """Owner-scoped IPC. Never broadcast temporary events or accept caller session IDs."""

    async def _cmd_temporary_open(self, connection, frame):
        service = self.temporary_chat
        key = service.sessions.open()
        self._temporary_owners[key] = connection
        return {"session_id": key}

    def _owned_temporary(self, connection, frame):
        key = str(frame.get("session_id") or "")
        if self._temporary_owners.get(key) is not connection:
            raise ValueError("This temporary chat is closed.")
        return key

    async def _cmd_temporary_turn(self, connection, frame):
        key = self._owned_temporary(connection, frame)
        content = str(frame.get("content") or "").strip()
        if not content or len(content) > 100_000:
            raise ValueError("Please send a shorter text message.")
        if key in self._temporary_tasks:
            raise ValueError("Please wait for this reply or stop it first.")

        async def stream(text):
            if self._temporary_owners.get(key) is connection:
                await self._send(
                    connection, {"type": "temporary_delta", "session_id": key, "text": text}
                )

        async def run():
            try:
                answer = await self.temporary_chat.turn(key, content, stream)
                if self._temporary_owners.get(key) is connection:
                    await self._send(
                        connection, {"type": "temporary_done", "session_id": key, "content": answer}
                    )
            except asyncio.CancelledError:
                pass
            except Exception:
                await self._send(
                    connection,
                    {
                        "type": "temporary_done",
                        "session_id": key,
                        "content": "I couldn't finish that reply. Try again?",
                    },
                )
            finally:
                self._temporary_tasks.pop(key, None)

        self._temporary_tasks[key] = asyncio.create_task(run())
        return {"started": True}

    async def _cmd_temporary_stop(self, connection, frame):
        key = self._owned_temporary(connection, frame)
        task = self._temporary_tasks.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return {"stopped": True}

    async def _cmd_temporary_close(self, connection, frame):
        key = self._owned_temporary(connection, frame)
        self._temporary_owners.pop(key, None)  # suppress events before cancellation
        task = self._temporary_tasks.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.temporary_chat.sessions.close(key)
        return {"closed": True}
