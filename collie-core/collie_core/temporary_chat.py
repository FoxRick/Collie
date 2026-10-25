"""Memory-only desktop conversations, composed through supported engine interfaces."""

from __future__ import annotations

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
