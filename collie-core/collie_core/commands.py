"""Deterministic chat commands shared by desktop and messenger surfaces."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from collie_core.permissions.broker import PermissionDeniedError
from collie_core.permissions.models import ExecutionContext
from nanobot.agent.skills import SkillsLoader

_COMMAND = re.compile(r"^/([a-z][a-z0-9-]*)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_MODEL_IDENTITY_QUERY = re.compile(
    r"^(?:(?:what|which)\s+(?:ai\s+)?(?:model|llm)"
    r"(?:\s+(?:are\s+you|am\s+i)\s+(?:currently\s+)?(?:using|running|on)"
    r"|\s+are\s+you"
    r"|\s+is\s+(?:this|(?:currently\s+)?(?:active|in\s+use|selected|running))"
    r"|\s+powers\s+you)"
    r"|what\s+is\s+(?:the\s+)?(?:current|active|selected)\s+(?:ai\s+)?(?:model|llm)"
    r"|(?:tell|show)\s+me\s+(?:the\s+)?(?:current|active|selected)\s+(?:ai\s+)?model)"
    r"(?:\s+(?:right\s+now|currently))?[\s?.!]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    description: str
    usage: str
    category: str = "Commands"


CORE_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("start", "Show the Collie command menu", "/start", "Help"),
    CommandSpec("new", "Start a fresh conversation", "/new", "Session"),
    CommandSpec("compact", "Summarize older context and keep recent turns", "/compact", "Session"),
    CommandSpec("status", "Show model, context, and active helpers", "/status", "Session"),
    CommandSpec(
        "model",
        "Show or switch the active AI model",
        "/model [model-id]",
        "Settings",
    ),
    CommandSpec("stop", "Stop the current response and its helpers", "/stop", "Session"),
    CommandSpec(
        "goal",
        "Keep working toward an objective across continuation turns",
        "/goal <objective>",
        "Session",
    ),
    CommandSpec("agents", "Show your specialist agents", "/agents", "Capabilities"),
    CommandSpec("skills", "Show Collie's reusable skills", "/skills", "Capabilities"),
    CommandSpec("agent", "Ask a specialist to handle a task", "/agent <name> <task>", "Capabilities"),
    CommandSpec("skill", "Use a named skill for a request", "/skill <name> <request>", "Capabilities"),
    CommandSpec(
        "create-agent",
        "Create a reusable specialist with review and approval",
        "/create-agent <what it should do>",
        "Create",
    ),
    CommandSpec(
        "create-skill",
        "Teach Collie a reusable workflow with review and approval",
        "/create-skill <workflow>",
        "Create",
    ),
    CommandSpec("help", "Show available commands", "/help", "Help"),
)


def parse_command(content: str) -> tuple[str, str] | None:
    match = _COMMAND.fullmatch(content.strip())
    if not match:
        return None
    return match.group(1).lower(), (match.group(2) or "").strip()


class CommandController:
    """Execute command-only messages without asking the model to imitate control actions."""

    def __init__(
        self,
        *,
        workspace: Path,
        subagent_loader: Any,
        loop_provider: Callable[[], Any],
        status_provider: Callable[[], dict[str, Any]],
        model_switcher: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        providers_provider: Callable[[], list[dict[str, Any]]] | None = None,
        model_authorizer: Callable[
            [ExecutionContext, dict[str, Any]], Awaitable[None]
        ] | None = None,
    ) -> None:
        self.workspace = workspace
        self.subagents = subagent_loader
        self._loop_provider = loop_provider
        self._status_provider = status_provider
        self._model_switcher = model_switcher
        self._providers_provider = providers_provider
        self._model_authorizer = model_authorizer

    def catalog(self) -> dict[str, Any]:
        agents = self.subagents.sync() if self.subagents is not None else []
        loader = SkillsLoader(self.workspace)
        skills: list[dict[str, Any]] = []
        for entry in loader.list_skills(filter_unavailable=False):
            metadata = loader.get_skill_metadata(entry["name"]) or {}
            available, reason = loader.get_skill_availability(entry["name"])
            skills.append({
                "name": entry["name"],
                "description": str(metadata.get("description") or entry["name"]),
                "source": entry["source"],
                "available": available,
                "unavailable_reason": reason,
            })
        return {
            "commands": [asdict(command) for command in CORE_COMMANDS],
            "agents": [
                {
                    "name": row["name"],
                    "description": row.get("description") or "",
                    "execution_posture": row.get("execution_posture") or "read_only",
                }
                for row in agents
            ],
            "skills": skills,
        }

    async def execute(
        self,
        content: str,
        *,
        session_key: str,
        origin: str,
        conversation_id: str | None = None,
        execution_mode: str = "execute",
    ) -> dict[str, Any] | None:
        if _MODEL_IDENTITY_QUERY.fullmatch(content.strip()):
            status = self._status_provider()
            model = str(status.get("model") or "").strip()
            return {
                "handled": True,
                "content": (
                    f"I'm currently using **{model}**."
                    if model
                    else "No model is currently connected."
                ),
                "card_type": "status",
                "card_data": {"model": model or "Not connected"},
            }

        parsed = parse_command(content)
        if parsed is None:
            return None
        command, arguments = parsed
        known = {item.name for item in CORE_COMMANDS} | {"commands"}
        if command not in known:
            return {
                "handled": True,
                "content": f"I don't know /{command} yet. Try /help to see what I can do.",
            }

        if command in {"help", "commands", "start"}:
            lines = ["Here are my commands:"]
            for item in CORE_COMMANDS:
                if item.name == "help":
                    continue
                lines.append(f"- `{item.usage}` — {item.description}")
            return {"handled": True, "content": "\n".join(lines)}

        if command == "agents":
            items = self.catalog()["agents"]
            names = ", ".join(str(item["name"]) for item in items)
            return {
                "handled": True,
                "content": (
                    (
                        "Here are my specialist agents. Pick one, then tell it what to do."
                        if origin == "desktop"
                        else f"My specialist agents: {names}. Use /agent <name> <task>."
                    )
                    if items else
                    "No specialist agents yet. Try `/create-agent <what it should do>`."
                ),
                "card_type": "capability_list",
                "card_data": {"kind": "agent", "items": items},
            }

        if command == "skills":
            items = self.catalog()["skills"]
            names = ", ".join(str(item["name"]) for item in items)
            return {
                "handled": True,
                "content": (
                    (
                        "These are the reusable skills I can load for a task."
                        if origin == "desktop"
                        else f"My reusable skills: {names}. Use /skill <name> <request>."
                    )
                    if items else
                    "No skills yet. Try `/create-skill <workflow>`."
                ),
                "card_type": "capability_list",
                "card_data": {"kind": "skill", "items": items},
            }

        if command == "new":
            if origin != "desktop":
                loop = self._loop_provider()
                if loop is not None:
                    await loop.cancel_session(session_key)
                    loop.sessions.delete_session(session_key)
            return {
                "handled": True,
                "new_conversation": origin == "desktop",
                "content": "Fresh conversation. I kept your long-term memory, but cleared this chat's context.",
            }

        loop = self._loop_provider()
        if command == "compact":
            if loop is None:
                return {"handled": True, "content": "I need a model connection before I can compact."}
            summary = await loop.consolidator.compact_idle_session(
                session_key,
                runtime=loop.llm_runtime(),
                max_suffix=8,
            )
            if summary == "":
                content_out = "This conversation is already compact."
            elif summary is None:
                content_out = "I archived older context, but could not produce a clean summary."
            else:
                content_out = "Context compacted. I kept the recent turns and a summary of what came before."
            return {"handled": True, "content": content_out}

        if command == "status":
            status = self._status_provider()
            session = loop.sessions.get_or_create(session_key) if loop is not None else None
            active = status.get("active_agents") or []
            return {
                "handled": True,
                "content": (
                    f"**Model:** {status.get('model') or 'not connected'}\n"
                    f"**Session:** {len(session.messages) if session else 0} stored messages, "
                    f"{session.last_consolidated if session else 0} compacted\n"
                    f"**Active agents:** {len(active)}\n"
                    f"**Mode:** local desktop runtime"
                ),
                "card_type": "status",
                "card_data": {
                    "model": status.get("model") or "Not connected",
                    "messages": len(session.messages) if session else 0,
                    "compacted": session.last_consolidated if session else 0,
                    "active_agents": len(active),
                },
            }

        if command == "model":
            return await self._handle_model(
                arguments,
                conversation_id=conversation_id,
                execution_mode=execution_mode,
                origin=origin,
            )

        if command == "stop":
            stopped = await loop.cancel_session(session_key) if loop is not None else 0
            return {
                "handled": True,
                "content": "Stopped." if stopped else "Nothing is running in this conversation.",
            }

        if command == "goal":
            if not arguments:
                return {
                    "handled": True,
                    "content": "Tell me the objective after the command: `/goal <objective>`.",
                }
            return {
                "handled": False,
                "forward_prompt": arguments,
                "message_metadata": {
                    "goal_requested": True,
                    "original_command": "/goal",
                },
            }

        if command == "agent":
            if not arguments:
                return {
                    "handled": True,
                    "content": "Use `/agent <name> <task>`. Try `/agents` to see the choices.",
                }
            names = sorted(
                (str(row["name"]) for row in self.subagents.sync()),
                key=len,
                reverse=True,
            )
            chosen = next(
                (name for name in names if arguments.lower().startswith(name.lower())),
                None,
            )
            if chosen is None:
                return {
                    "handled": True,
                    "content": "I could not match that agent. Try `/agents` and pick one.",
                }
            task = arguments[len(chosen):].strip(" \t:-")
            if not task:
                return {
                    "handled": True,
                    "content": f"What should {chosen} do? Use `/agent {chosen} <task>`.",
                }
            return {
                "handled": False,
                "forward_prompt": (
                    f"The user explicitly invoked the specialist agent named “{chosen}”. "
                    f"Call the call_subagent tool for that exact agent with this task:\n\n{task}"
                ),
            }

        if command == "skill":
            if not arguments:
                return {
                    "handled": True,
                    "content": "Use `/skill <name> <request>`. Try `/skills` to see the choices.",
                }
            skill_names = sorted(
                (str(item["name"]) for item in self.catalog()["skills"]),
                key=len,
                reverse=True,
            )
            chosen = next(
                (name for name in skill_names if arguments.lower().startswith(name.lower())),
                None,
            )
            if chosen is None:
                return {
                    "handled": True,
                    "content": "I could not match that skill. Try `/skills` and pick one.",
                }
            request = arguments[len(chosen):].strip(" \t:-")
            return {
                "handled": False,
                "forward_prompt": (
                    f"The user explicitly invoked the skill “{chosen}”. First call load_skill "
                    f"with that exact name, then follow it for this request:\n\n"
                    f"{request or 'Apply this skill and ask for any information it requires.'}"
                ),
            }

        if command == "create-agent":
            if not arguments:
                return {
                    "handled": True,
                    "content": "Describe it after the command: `/create-agent <what it should do>`.",
                }
            return {
                "handled": False,
                "forward_prompt": (
                    "The user explicitly asked to create a reusable specialist agent. Draft a "
                    "clear name, description, complete instructions, and safest execution posture, "
                    "then call create_subagent so the user can review and approve it.\n\n"
                    f"Requested agent:\n{arguments}"
                ),
            }

        if command == "create-skill":
            if not arguments:
                return {
                    "handled": True,
                    "content": "Describe it after the command: `/create-skill <workflow>`.",
                }
            return {
                "handled": False,
                "forward_prompt": (
                    "The user explicitly asked to create a reusable skill. Draft a lowercase "
                    "hyphenated name, a specific description, and complete Markdown workflow "
                    "instructions, then call create_skill so the user can review and approve it.\n\n"
                    f"Requested skill:\n{arguments}"
                ),
            }

        return None

    async def _handle_model(
        self,
        arguments: str,
        *,
        conversation_id: str | None,
        execution_mode: str,
        origin: str,
    ) -> dict[str, Any]:
        """Implement ``/model [model-id]`` — show or switch the active model.

        Switching mutates provider settings, so it goes through the same
        permission broker as the ``set_model`` tool (approval-gated
        LOCAL_WRITE; denied in plan mode) before the runtime switcher runs.
        """
        if arguments:
            if self._model_switcher is None:
                return {
                    "handled": True,
                    "content": "Model switching isn't available in this session.",
                }
            if self._model_authorizer is not None:
                context = ExecutionContext(
                    execution_mode=execution_mode,
                    conversation_id=conversation_id,
                    origin=origin,
                )
                try:
                    await self._model_authorizer(context, {"model": arguments})
                except PermissionDeniedError as error:
                    return {
                        "handled": True,
                        "content": (
                            "I can't switch models right now: "
                            f"{error}"
                        ),
                    }
            result = await self._model_switcher(arguments)
            if not result.get("switched"):
                return {
                    "handled": True,
                    "content": (
                        "I couldn't switch models: "
                        f"{result.get('error') or 'unknown error'}"
                    ),
                }
            model = str(result.get("model") or arguments)
            if result.get("unchanged"):
                content = f"Collie is already on **{model}**."
            elif result.get("applied") is False:
                content = f"Saved — I'll use **{model}** once a provider is connected."
            else:
                previous = str(result.get("previous") or "").strip()
                if previous and previous != model:
                    content = (
                        f"Done — switched from **{previous}** to **{model}**. "
                        "The next messages will use it."
                    )
                else:
                    content = f"Done — the active model is now **{model}**."
            return {
                "handled": True,
                "content": content,
                "card_type": "status",
                "card_data": {"model": model},
            }

        status = self._status_provider()
        model = str(status.get("model") or "").strip()
        providers = (
            self._providers_provider()
            if self._providers_provider is not None
            else []
        )
        lines = [f"**Current model:** {model or 'not connected'}"]
        if providers:
            lines.append("")
            lines.append("Configured providers:")
            for provider in providers:
                marker = " (active)" if provider.get("is_default") else ""
                provider_model = provider.get("model") or "default"
                provider_name = (
                    provider.get("runtime_name") or provider.get("name") or "?"
                )
                lines.append(f"- **{provider_name}**: {provider_model}{marker}")
            lines.append("")
            lines.append(
                "Switch with `/model <model-id>` — e.g. `/model deepseek-v4-flash`."
            )
        else:
            lines.append("No providers configured yet. Add one in Settings first.")
        return {
            "handled": True,
            "content": "\n".join(lines),
            "card_type": "status",
            "card_data": {"model": model},
        }
