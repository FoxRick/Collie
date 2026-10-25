"""Read-only cross-session IPC and normal-path handoff adapter."""
import json


class CrossSessionCommands:
    async def _cmd_cross_session_read(self, connection, frame):
        return self.cross_session.read(str(frame.get("conversation_id") or ""),
                                       query=str(frame.get("query") or ""),
                                       target=str(frame.get("target_id") or ""))

    async def _launch_cross_session(self, conversation_id, brief):
        class Receipt:
            async def send(self, payload):
                frame = json.loads(payload)
                if frame.get("type") == "error":
                    raise ValueError(frame.get("message"))
        # No source folder, model override, attachments, metadata or file grants.
        # This uses the same persistence, streaming, cancellation and approval rail.
        await self._cmd_chat(Receipt(), {"conversation_id": conversation_id,
                                       "content": brief, "execution_mode": "execute",
                                       "file_access_scope": {"mode": "selected_folder"}})
