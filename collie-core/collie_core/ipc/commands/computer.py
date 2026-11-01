"""Computer UI calls the same controller and approval broker as agent tools."""

from collie_core.permissions.models import ExecutionContext


class ComputerCommands:
    async def _cmd_computer_action(self, connection, frame):
        conversation_id = str(frame.get("conversation_id") or "")
        conversation = self.db.get_conversation(conversation_id)
        if conversation is None or self.computer_service is None:
            raise ValueError("Open a local chat before using computer control.")
        context = ExecutionContext(
            conversation_id=conversation_id,
            execution_mode=conversation.get("execution_mode") or "execute",
            origin="computer_ui",
        )
        command = frame.get("command")
        if not isinstance(command, dict):
            raise ValueError("Choose a computer action.")
        return await self.computer_service.execute(context, command, images_allowed=True)
