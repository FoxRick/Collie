"""Subagent and skill commands.

Handlers for the ``agents`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

from loguru import logger
from websockets.asyncio.server import ServerConnection

from nanobot.webui.skills_api import webui_skill_detail_payload, webui_skills_payload


class AgentCommands:
    """Subagent and skill commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_get_subagent_activity(self, connection: ServerConnection, frame: dict) -> dict:
        """Cheap subagent roster for poll-heavy surfaces (Agents tab).

        Prefers the dedicated activity provider (runtime.subagent_activity,
        which reads the manager's active + settled collections directly);
        falls back to the status provider's roster keys when no dedicated
        provider was wired in. Either way the payload is just the two
        roster arrays — never the full status payload.
        """
        provider = self._activity_provider or self._status_provider
        if provider is None:
            return {"active_agents": [], "recent_agents": []}
        status = provider()
        return {
            "active_agents": status.get("active_agents") or [],
            "recent_agents": status.get("recent_agents") or [],
        }

    async def _cmd_list_skills(self, connection: ServerConnection, frame: dict) -> dict:
        if self._skills_workspace is None:
            return {"skills": []}
        disabled = set(self.db.get_setting("agent.disabled_skills", []) or [])
        return webui_skills_payload(self._skills_workspace, disabled_skills=disabled)

    async def _cmd_get_skill(self, connection: ServerConnection, frame: dict) -> dict:
        if self._skills_workspace is None:
            raise ValueError("Skills are not available yet.")
        name = str(frame.get("name") or "").strip()
        if not name:
            raise ValueError("Pick a skill to inspect.")
        disabled = set(self.db.get_setting("agent.disabled_skills", []) or [])
        skill = webui_skill_detail_payload(
            self._skills_workspace,
            name,
            disabled_skills=disabled,
        )
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        # Collie's UI needs a useful overview, not the full local instruction file.
        skill.pop("raw_markdown", None)
        return {"skill": skill}

    async def _cmd_list_subagents(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.subagents.loader import STARTERS

        if self._subagent_loader is not None:
            subagents = self._subagent_loader.sync()
        else:
            subagents = self.db.list_subagents()
        return {"subagents": subagents, "starters": list(STARTERS)}

    async def _cmd_create_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.subagents.loader import draft_system_prompt

        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        name = str(frame.get("name") or "").strip()
        description = str(frame.get("description") or "").strip()
        system_prompt = str(frame.get("system_prompt") or "").strip()
        execution_posture = str(frame.get("execution_posture") or "read_only").strip()
        if execution_posture not in {"read_only", "inherit"}:
            execution_posture = "read_only"
        if not name:
            raise ValueError("Every helper needs a name!")
        generated = False
        if not system_prompt:
            if self._prompt_writer is not None:
                try:
                    system_prompt = (await self._prompt_writer(name, description)).strip()
                    generated = bool(system_prompt)
                except Exception:
                    logger.exception("LLM prompt writing failed; using template")
            if not system_prompt:
                system_prompt = draft_system_prompt(name, description)
        row = self._subagent_loader.create(
            name,
            description=description,
            system_prompt=system_prompt,
            execution_posture=execution_posture,
        )
        return {"subagent": row, "prompt_written_by_collie": generated}

    async def _cmd_update_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        row = self._subagent_loader.update(
            str(frame.get("subagent_id") or ""),
            name=frame.get("name"),
            description=frame.get("description"),
            system_prompt=frame.get("system_prompt"),
            execution_posture=frame.get("execution_posture"),
        )
        return {"subagent": row}

    async def _cmd_delete_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        self._subagent_loader.delete(str(frame.get("subagent_id") or ""))
        return {"deleted": True}

    async def _cmd_cancel_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        conversation_id = str(frame.get("conversation_id") or "")
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if self._subagent_canceler is None:
            raise ValueError("subagent cancellation is not available")
        count = await self._subagent_canceler(conversation_id)
        return {"cancelled": count}
