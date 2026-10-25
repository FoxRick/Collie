"""Connection-owned temporary chat commands; transcript storage stays in RAM."""

from __future__ import annotations

import asyncio
from contextlib import suppress


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
