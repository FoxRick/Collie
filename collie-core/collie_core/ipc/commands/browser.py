"""Private browser IPC; calls the exact same authorizer/service as the chat tool."""

from __future__ import annotations

from collie_core.permissions.models import ExecutionContext


class BrowserCommands:
    async def _cmd_browser_action(self, connection, frame: dict) -> dict:
        conversation_id = str(frame.get("conversation_id") or "")
        conversation = self.db.get_conversation(conversation_id)
        if conversation is None or self.browser_service is None:
            raise ValueError("Open a local conversation before using the browser.")
        # Never accept authority, run IDs or shared identities from the frame.
        context = ExecutionContext(
            conversation_id=conversation_id,
            execution_mode=conversation.get("execution_mode") or "execute",
            origin="browser_ui",
        )
        command = frame.get("command")
        if not isinstance(command, dict):
            raise ValueError("Choose a browser action.")
        return await self.browser_service.execute(context, command)
