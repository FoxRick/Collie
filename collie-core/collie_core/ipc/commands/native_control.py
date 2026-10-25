"""Private desktop native-control command family; no persisted native state."""

from __future__ import annotations

import asyncio
from typing import Any

from collie_core.permissions.models import ExecutionContext


class NativeControlCommands:
    async def _cmd_native_control(self, connection: Any, frame: dict) -> dict:
        controller = getattr(self, "native_control", None)
        if controller is None:
            raise ValueError("Native control is unavailable in this runtime.")
        operation = str(frame.get("operation") or "")
        if operation == "discover":
            return await controller.discover()
        conversation = str(frame.get("conversation_id") or "")
        row = self.db.get_conversation(conversation) if conversation else None
        if not row:
            raise ValueError("Choose an existing private desktop chat first.")
        if self._session_target is not None and self._session_target(conversation)[1] != "desktop":
            raise ValueError("Native control needs a private desktop chat.")
        if operation == "select":
            target = frame.get("target")
            if not isinstance(target, dict):
                raise ValueError("Choose a supported window first.")
            await controller.select(conversation, target, str(frame.get("scope") or ""))
            return {"selected": True}
        if operation == "stop":
            controller.stop(conversation)
            await self._cmd_stop(connection, {"conversation_id": conversation})
            return {"stopped": True}
        try:
            return await controller.execute(
                ExecutionContext(
                    conversation_id=conversation,
                    execution_mode=str(row.get("execution_mode") or "execute"),
                ),
                operation,
                str(frame.get("text") or ""),
                str(frame.get("direction") or "down"),
            )
        except asyncio.CancelledError:
            return {"stopped": True}
