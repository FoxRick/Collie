"""Collie core runtime — the process the Electron shell launches.

Boots the SQLite store, builds the agent from settings, starts the IPC
WebSocket server, and runs until the shell disconnects or the process is
terminated.

Usage: ``python -m collie_core.runtime [--port 3818]``
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
import urllib.parse
import uuid
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from loguru import logger

from collie_core import settings as collie_settings
from collie_core.automations.scheduler import AutomationScheduler
from collie_core.commands import CommandController
from collie_core.connectors.manager import ConnectorManager
from collie_core.db import CollieDB, collie_home
from collie_core.ipc.server import CollieIPCServer
from collie_core.memory.profile import ProfileStore
from collie_core.messengers import CollieBus, MessengerManager
from collie_core.permissions.broker import ApprovalBroker
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext
from collie_core.permissions.store import PermissionStore
from collie_core.services.manager import bind_service_manager
from collie_core.session_identity import desktop_session_key
from collie_core.subagents.loader import SubagentLoader, bind_subagent_loader
from collie_core.things.store import ThingStore
from collie_core.tools.artifacts import bind_things
from collie_core.tools.life_db import bind_life_db
from collie_core.tools.memory import bind_profile_store
from collie_core.tools.model_switch import SetModelTool, bind_model_switcher
from collie_core.tools.plans import bind_plans_db
from collie_core.tools.reminders import bind_reminders_db
from collie_core.tools.suggest_profile import bind_suggest_workspace
from collie_core.tools.task_checklists import bind_task_checklists_db

__all__ = ["CollieRuntime", "main"]


class CollieRuntime:
    """Owns the DB, profile store, agent loop, and IPC server."""

    def __init__(
        self, *, port: int = 3818, db: CollieDB | None = None, ipc_token: str | None = None
    ) -> None:
        self.db = db or CollieDB()
        # Anchor the engine's runtime data dir (media downloads, pairing
        # store, cron jobs) under ~/.collie instead of ~/.nanobot. No JSON
        # config file is ever written — the path only derives directories.
        from nanobot.config.loader import set_config_path

        set_config_path(collie_home() / "config.json")
        self.workspace = collie_settings.ensure_workspace()
        bind_suggest_workspace(self.workspace)
        from collie_core.versions import VersionStore

        self.versions = VersionStore(self.db)
        self.profile = ProfileStore(self.db, self.workspace, version_store=self.versions)
        self.profile.regenerate_memory_md()
        bind_profile_store(self.profile)
        bind_reminders_db(self.db)
        bind_life_db(self.db)
        bind_plans_db(self.db)
        bind_model_switcher(self._switch_model)
        bind_task_checklists_db(self.db)
        self.things = ThingStore()
        bind_things(store=self.things)
        # The connector manager keeps the old ServiceManager-shaped facade for
        # one transition release, so existing life-tool bridges stay bootable.
        self.services = ConnectorManager(self.db)
        bind_service_manager(self.services)
        self.subagents = SubagentLoader(self.workspace, self.db)
        self.subagents.seed_bundled_once()
        self.subagents.sync()
        bind_subagent_loader(self.subagents)
        self.messengers = MessengerManager(self.db)
        self.permission_store = PermissionStore(self.db)
        self.permission_evaluator = PermissionEvaluator(
            self.permission_store,
            local_write_preset=str(
                self.db.get_setting("permissions.local_write_preset", "ask") or "ask"
            ),
            review_gate_provider=self.db.get_conversation_review_gate,
        )

        self.loop: Any = None
        self._loop_task: asyncio.Task | None = None
        self._outbound_task: asyncio.Task | None = None
        self._reminder_task: asyncio.Task | None = None
        self._configure_lock = asyncio.Lock()
        self._model_switch_lock = asyncio.Lock()
        self._provider_config_generation = 0
        self._provider_rollbacks: dict[str, dict[str, Any]] = {}
        self._auto_reconfiguring = False
        # One session manager for every rebuilt loop: conversation history
        # survives provider switches and loop restarts.
        from nanobot.session.manager import SessionManager

        self._session_manager = SessionManager(self.workspace)
        self._scheduler: AutomationScheduler | None = None
        self.commands = CommandController(
            workspace=self.workspace,
            subagent_loader=self.subagents,
            loop_provider=lambda: self.loop,
            status_provider=self._status,
            model_switcher=self._switch_model,
            providers_provider=lambda: self.db.list_providers(),
            model_authorizer=self._authorize_model_switch,
        )
        self.ipc = CollieIPCServer(
            self.db,
            port=port,
            chat_runner=self._chat,
            on_set_api_key=collie_settings.set_api_key,
            on_delete_api_key=collie_settings.delete_api_key,
            on_configure=self._configure,
            on_configure_provider_candidate=self._configure_provider_candidate,
            on_finalize_provider_candidate=self._finalize_provider_candidate,
            on_rollback_provider_candidate=self._rollback_provider_candidate,
            status_provider=self._status,
            activity_provider=self.subagent_activity,
            service_manager=self.services,
            subagent_loader=self.subagents,
            prompt_writer=self._write_subagent_prompt,
            title_generator=self._generate_chat_title,
            subagents_running=self._subagents_running,
            subagent_canceler=self.cancel_subagents_for_conversation,
            conversation_canceler=self.cancel_conversation_work,
            chat_steerer=self._steer_chat,
            messenger_manager=self.messengers,
            skills_workspace=self.workspace,
            profile_store=self.profile,
            command_runner=self._run_command,
            command_catalog=self.commands.catalog,
            command_requires_approval=self.commands.requires_approval,
            session_target=self._session_target,
            conversation_deleter=self.delete_conversation_sessions,
            on_set_approval_preset=self.permission_evaluator.set_local_write_preset,
            token=ipc_token,
            dream_runner=self._run_dream_manual,
            gardener_runner=self._run_gardener_manual,
            thing_store=self.things,
        )
        self.approvals = ApprovalBroker(self.db, self.permission_evaluator, self.ipc.broadcast)
        self.ipc.approval_broker = self.approvals
        self.messengers.broadcaster = self.ipc.broadcast
        self._scheduler = AutomationScheduler(
            self.db, broadcaster=self.ipc.broadcast, runner=self._run_automation
        )

    # -- agent lifecycle ----------------------------------------------------

    def _build_loop(self) -> Any:
        import collie_core.tools as collie_tools
        from collie_core.telemetry.hook import create_telemetry_hook_factory
        from nanobot.agent.loop import AgentLoop
        from nanobot.agent.tools.context import ToolContext
        from nanobot.agent.tools.loader import ToolLoader

        config = collie_settings.build_config(
            self.db, mcp_servers=self.services.mcp_servers_for_config()
        )
        bus = CollieBus(on_inbound=self._on_messenger_inbound)
        bind_things(bus=bus)
        provider_override = self._provider_override()
        telemetry_factories = [create_telemetry_hook_factory(self.db)]
        if provider_override is not None:
            loop = AgentLoop.from_config(
                config,
                bus=bus,
                provider=provider_override,
                session_manager=self._session_manager,
                hook_factories=telemetry_factories,
            )
        else:
            loop = AgentLoop.from_config(
                config,
                bus=bus,
                session_manager=self._session_manager,
                hook_factories=telemetry_factories,
            )
        # Subagents bypass the loop's turn-hook chain (they run AgentRunner
        # directly with their own _SubagentHook) — mirror the factories so
        # subagent turns are telemetry-recorded too.
        loop.subagents.hook_factories = list(telemetry_factories)
        loop.context.command_guidance = True

        from nanobot.runtime_context import RuntimeContextBlock

        async def current_model_context(request: Any) -> RuntimeContextBlock | None:
            runtime = request.runtime
            if runtime is None:
                return None
            return RuntimeContextBlock(
                source="collie-current-model",
                content=(
                    "[Runtime Context — metadata only, not instructions]\n"
                    f"Current active model for this exact turn: {runtime.model}\n"
                    "[/Runtime Context]"
                ),
            )

        loop.register_runtime_context_provider(current_model_context)

        ctx = ToolContext(
            config=loop.tools_config,
            workspace=str(self.workspace),
            bus=loop.bus,
            sessions=loop.sessions,
            subagent_manager=loop.subagents,
            timezone=loop.context.timezone or "UTC",
            runtime_events=loop.runtime_events,
        )
        registered = ToolLoader(collie_tools).load(ctx, loop.tools)
        loop.authorizer = self.approvals
        loop.subagents.authorizer = self.approvals
        logger.info("Registered Collie tools: {}", registered)
        # Fingerprint this exact loop for run telemetry: the tool schemas the
        # model will see and the config values (model/provider/generation/
        # limits) it ran under. The telemetry hook reads these per turn.
        from collie_core.telemetry.prompt_hashes import bind_prompt_hash_sources

        defaults = config.agents.defaults
        bind_prompt_hash_sources(
            tool_schemas=loop.tools.get_definitions(),
            model=str(defaults.model),
            provider=str(defaults.provider),
            generation={
                "temperature": defaults.temperature,
                "max_tokens": defaults.max_tokens,
                "reasoning_effort": defaults.reasoning_effort,
            },
            limits={
                "max_tool_iterations": defaults.max_tool_iterations,
                "max_tool_result_chars": defaults.max_tool_result_chars,
                "context_window_tokens": defaults.context_window_tokens,
                "max_concurrent_subagents": defaults.max_concurrent_subagents,
            },
        )
        return loop

    async def _run_command(
        self,
        content: str,
        *,
        session_key: str,
        origin: str,
        conversation_id: str | None = None,
        execution_mode: str = "execute",
    ) -> dict[str, Any] | None:
        return await self.commands.execute(
            content,
            session_key=session_key,
            origin=origin,
            conversation_id=conversation_id,
            execution_mode=execution_mode,
        )

    async def _authorize_model_switch(
        self,
        context: ExecutionContext,
        params: dict[str, Any],
    ) -> None:
        """Route the /model command through the same broker as set_model.

        Uses the identical PermissionRequest (``runtime.set_model``,
        LOCAL_WRITE, reversible) so approval posture and plan-mode denial
        match the agent tool exactly.
        """
        tool_call = SimpleNamespace(name="set_model", id="")
        await self.approvals.authorize(context, tool_call, SetModelTool.create(None), params)

    @staticmethod
    def _desktop_session_key(conversation_id: str) -> str:
        """Return the one canonical engine key for a desktop conversation."""
        return desktop_session_key(conversation_id)

    def session_keys_for_conversation(self, conversation_id: str) -> set[str]:
        """Resolve every engine session associated with a desktop conversation."""
        return {
            self._desktop_session_key(conversation_id),
            *self.messengers.session_keys_for_conversation(conversation_id),
        }

    def _conversation_target(self, conversation_id: str) -> tuple[str, str, str]:
        """Resolve exact engine identity and routing fields for a conversation."""
        messenger = self.messengers.session_target_for_conversation(conversation_id)
        if messenger is not None:
            return messenger
        return self._desktop_session_key(conversation_id), "collie", conversation_id

    def _session_target(self, conversation_id: str) -> tuple[str, str]:
        """Compatibility adapter used by desktop command handling."""
        session_key, channel, _chat_id = self._conversation_target(conversation_id)
        return session_key, "desktop" if channel == "collie" else channel

    def active_subagents_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """List UI-safe running specialists across every mapped session."""
        if self.loop is None:
            return []
        active: list[dict[str, Any]] = []
        seen: set[str] = set()
        for session_key in sorted(self.session_keys_for_conversation(conversation_id)):
            try:
                statuses = self.loop.subagents.get_running_statuses_by_session(session_key)
            except Exception:
                logger.exception("Failed to list subagents for session {session_key}")
                continue
            for agent in statuses:
                agent_id = str(agent.get("id") or "")
                if agent_id and agent_id in seen:
                    continue
                if agent_id:
                    seen.add(agent_id)
                active.append(self._decorate_subagent(agent, conversation_id))
        return sorted(active, key=lambda item: float(item.get("started_at") or 0))

    @staticmethod
    def _decorate_subagent(agent: dict[str, Any], conversation_id: str) -> dict[str, Any]:
        """Attach wall-clock ms + conversation scoping to a UI-safe subagent row.

        The manager reports ``time.monotonic()`` values; the renderer needs
        epoch ms so elapsed time can be computed and frozen across polls.
        """
        now_mono = time.monotonic()
        offset_ms = (time.time() - now_mono) * 1000.0
        started_ms = agent.get("started_at")
        if isinstance(started_ms, (int, float)):
            agent["started_at_ms"] = int(started_ms * 1000.0 + offset_ms)
        ended_at = agent.get("ended_at")
        if isinstance(ended_at, (int, float)):
            agent["ended_at_ms"] = int(ended_at * 1000.0 + offset_ms)
        agent["conversation_id"] = conversation_id
        return agent

    @staticmethod
    def _conversation_id_for_session(session_key: str | None) -> str:
        """Map an engine session key back to a desktop conversation id.

        Desktop session keys embed the conversation id (``collie:<id>``).
        Messenger session keys cannot be reverse-mapped to a desktop
        conversation, so those rows surface with an empty conversation id —
        they appear in the Agents-tab roster but never match a desktop
        conversation in ChatScreen.
        """
        if session_key and session_key.startswith("collie:"):
            return session_key[len("collie:") :]
        return ""

    def subagent_activity(self) -> dict[str, list[dict[str, Any]]]:
        """Live + settled roster feed for poll-heavy surfaces.

        Reads the SubagentManager's active and settled collections directly
        instead of walking every conversation, so the cost is O(active
        sessions) rather than O(all conversations) — safe to poll every
        couple of seconds from the event loop.
        """
        if self.loop is None:
            return {"active_agents": [], "recent_agents": []}
        active: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        try:
            manager = self.loop.subagents
            for agent in manager.get_running_statuses():
                active.append(
                    self._decorate_subagent(
                        agent,
                        self._conversation_id_for_session(agent.get("session_key")),
                    )
                )
            for agent in manager.get_recent_statuses():
                recent.append(
                    self._decorate_subagent(
                        agent,
                        self._conversation_id_for_session(agent.get("session_key")),
                    )
                )
        except Exception:
            logger.exception("Failed to build the subagent activity feed")
        return {"active_agents": active, "recent_agents": recent}

    async def cancel_subagents_for_conversation(self, conversation_id: str) -> int:
        """Cancel specialists across every session mapped to a conversation."""
        if self.loop is None:
            return 0
        cancelled = 0
        for session_key in sorted(self.session_keys_for_conversation(conversation_id)):
            try:
                cancelled += await self.loop.subagents.cancel_by_session(session_key)
            except Exception:
                logger.exception("Failed to cancel subagents for session {}", session_key)
        return cancelled

    async def cancel_conversation_work(self, conversation_id: str) -> int:
        """Cancel active turns and specialists for every mapped session."""
        if self.loop is None:
            return 0
        cancelled = 0
        for session_key in sorted(self.session_keys_for_conversation(conversation_id)):
            try:
                cancelled += await self.loop.cancel_session(session_key)
            except Exception:
                logger.exception("Failed to cancel work for session {}", session_key)
        return cancelled

    def delete_conversation_sessions(self, conversation_id: str) -> None:
        """Delete all engine histories and persisted mirror identities."""
        for session_key in sorted(self.session_keys_for_conversation(conversation_id)):
            try:
                self._session_manager.delete_session(session_key)
            except Exception:
                logger.exception("Failed to delete session {}", session_key)
        self.messengers.forget_conversation(conversation_id)

    async def _on_messenger_inbound(self, msg: Any) -> bool:
        """Mirror messenger traffic, then intercept authorized command-only messages."""
        await self.messengers.on_inbound(msg)
        result = await self.commands.execute(
            str(msg.content or ""),
            session_key=msg.session_key,
            origin=str(msg.channel or "messenger"),
        )
        if result is None:
            return False
        if not result.get("handled"):
            msg.content = str(result.get("forward_prompt") or msg.content)
            return False
        if self.loop is not None:
            from nanobot.bus.events import OutboundMessage

            await self.loop.bus.publish_outbound(
                OutboundMessage(
                    channel=str(msg.channel),
                    chat_id=str(msg.chat_id),
                    content=str(result.get("content") or ""),
                    metadata=dict(msg.metadata or {}),
                )
            )
        return True

    def _provider_override(self) -> Any | None:
        """Build an OAuth-backed provider when the user signed in that way."""
        auth_type = str(self.db.get_setting("provider.auth", "") or "").lower()
        if auth_type == "claude-oauth":
            from collie_core.providers.claude_oauth import ClaudeOAuthProvider

            model = self.db.get_setting("provider.model") or "claude-sonnet-4-6"
            return ClaudeOAuthProvider(default_model=str(model))
        if auth_type == "chatgpt-oauth":
            from nanobot.providers.openai_codex_provider import OpenAICodexProvider

            model = self.db.get_setting("provider.model") or "openai-codex/gpt-5.4"
            return OpenAICodexProvider(default_model=str(model))
        return None

    @staticmethod
    def _validated_provider_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        """Normalize an API-key provider candidate and reject unsafe ambiguity."""
        provider_id = str(candidate.get("provider_id") or "").strip()
        name = str(candidate.get("name") or "").strip()
        auth_type = str(candidate.get("auth_type") or "api-key").strip().lower()
        if not provider_id or not name:
            raise ValueError("provider_id and name are required")
        if auth_type != "api-key":
            raise ValueError("Provider candidates currently support API keys only.")

        protocol = str(candidate.get("protocol") or "openai").strip().lower()
        if protocol not in {"openai", "anthropic"}:
            raise ValueError("protocol must be openai or anthropic")
        runtime_name = str(candidate.get("runtime_name") or name).strip().lower()
        secret_name = str(candidate.get("secret_name") or name).strip()
        if not runtime_name or not secret_name:
            raise ValueError("runtime_name and secret_name cannot be empty")

        model = str(candidate.get("model") or "").strip() or None
        if model is not None and (
            len(model) > 200
            or any(character.isspace() or ord(character) < 32 for character in model)
        ):
            raise ValueError("That model ID is not valid.")

        api_base = str(candidate.get("api_base") or "").strip() or None
        if api_base is not None:
            if len(api_base) > 2048:
                raise ValueError("api_base is too long")
            parsed = urllib.parse.urlparse(api_base)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("api_base must be an http(s) URL without credentials or fragments")
            runtime_name = "anthropic" if protocol == "anthropic" else "custom"
            if model is None:
                raise ValueError("A custom endpoint requires a model ID.")

        api_key = candidate.get("api_key")
        if api_key is not None:
            api_key = str(api_key).strip()
            if not api_key:
                raise ValueError("API key cannot be empty")
        return {
            "provider_id": provider_id,
            "name": name,
            "auth_type": auth_type,
            "model": model,
            "runtime_name": runtime_name,
            "protocol": protocol,
            "api_base": api_base,
            "secret_name": secret_name,
            "api_key": api_key,
        }

    async def _probe_provider_endpoint(self, api_base: str) -> None:
        """Verify a custom endpoint is reachable without a billable model request."""
        parsed = urllib.parse.urlparse(api_base)
        host = parsed.hostname
        if not host:
            raise ValueError("api_base is missing a host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        writer: asyncio.StreamWriter | None = None
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3.0
            )
        except (TimeoutError, OSError) as error:
            raise ValueError(
                "I couldn't reach that custom provider endpoint. Check the URL and try again."
            ) from error
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

    async def _configure_locked(self, *, probe_api_base: str | None = None) -> dict[str, Any]:
        """Rebuild the loop while the caller owns ``_configure_lock``."""
        await self._shutdown_loop()
        try:
            self.loop = self._build_loop()
            if probe_api_base:
                await self._probe_provider_endpoint(probe_api_base)
            self._loop_task = asyncio.create_task(self.loop.run())
            self._loop_task.add_done_callback(self._on_loop_done)
            self._outbound_task = asyncio.create_task(self._consume_outbound())
            await self.messengers.start(self.loop.bus)
            return {"configured": True, "model": self.loop.llm_runtime().model}
        except asyncio.CancelledError:
            await self._shutdown_loop()
            raise
        except Exception as error:
            message = str(error)
            await self._shutdown_loop()
            if "No API key configured" in message or "Not signed in" in message:
                logger.info("No provider configured yet — waiting for sign-in")
            else:
                logger.exception("Agent configuration failed")
            return {"configured": False, "error": message}

    async def _configure(self) -> dict[str, Any]:
        """(Re)build the agent loop from current settings."""
        async with self._configure_lock:
            return await self._configure_locked()

    async def _restore_provider_snapshot_locked(
        self,
        transaction: dict[str, Any],
    ) -> dict[str, Any]:
        """Restore DB, transient key, and previous runtime after a failed attempt."""
        errors: list[str] = []
        try:
            self.db.restore_provider_configuration(transaction["db_snapshot"])
        except Exception as error:
            logger.exception("Provider database rollback failed")
            errors.append(f"database restore failed: {error}")
        try:
            collie_settings.restore_api_key(
                str(transaction["secret_name"]), transaction.get("previous_key")
            )
        except Exception as error:
            logger.exception("Provider transient-key rollback failed")
            errors.append(f"credential restore failed: {error}")

        if transaction.get("had_runtime"):
            restored = await self._configure_locked()
            if not restored.get("configured"):
                errors.append(
                    "previous runtime rebuild failed: "
                    + str(restored.get("error") or "unknown error")
                )
        else:
            await self._shutdown_loop()
        return {
            "rolled_back": not errors,
            "rollback_error": "; ".join(errors) if errors else None,
        }

    async def _configure_provider_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Validate, activate, and verify one provider candidate transactionally."""
        from collie_core.catalog import CatalogueStore
        from collie_core.providers.validation import probe_api_key

        async with self._configure_lock:
            try:
                normalized = self._validated_provider_candidate(candidate)
            except Exception as error:
                return {"configured": False, "error": str(error), "rolled_back": True}

            secret_name = str(normalized["secret_name"])
            previous_key = collie_settings.get_api_key(secret_name)
            supplied_key = normalized.pop("api_key")
            if supplied_key is None and previous_key is None:
                return {
                    "configured": False,
                    "error": "No API key is available for that provider.",
                    "rolled_back": True,
                }
            transaction = {
                "db_snapshot": self.db.snapshot_provider_configuration(
                    str(normalized["provider_id"])
                ),
                "secret_name": secret_name,
                "previous_key": previous_key,
                "had_runtime": self.loop is not None,
            }

            try:
                if supplied_key is not None:
                    collie_settings.set_api_key(secret_name, supplied_key)
                # Catalogue providers arrive without an endpoint or model:
                # Collie fills protocol/base URL/default model (the strategy
                # doc's rule — never make a normie pick an endpoint).
                catalogue = CatalogueStore()
                runtime_name = str(normalized.get("runtime_name") or "")
                catalogue_entry = catalogue.get_provider(runtime_name)
                if catalogue_entry is not None:
                    if not normalized.get("api_base"):
                        normalized["api_base"] = catalogue_entry.get("api_base")
                    if not normalized.get("model"):
                        normalized["model"] = catalogue_entry.get("default_model")
                # Was the model explicitly chosen by the user (vs Collie's curated
                # default)? A typed model the provider doesn't advertise is a hard
                # error that must roll back — not something to paper over by
                # silently substituting the first entry in the provider's list.
                default_model = catalogue_entry.get("default_model") if catalogue_entry else None
                explicit_model = (
                    bool(normalized.get("model")) and normalized.get("model") != default_model
                )
                provider = self.db.configure_provider_candidate_record(**normalized)
                configured = await self._configure_locked(probe_api_base=normalized.get("api_base"))
                if not configured.get("configured"):
                    rollback = await self._restore_provider_snapshot_locked(transaction)
                    return {**configured, **rollback}
            except asyncio.CancelledError:
                await self._restore_provider_snapshot_locked(transaction)
                raise
            except Exception as error:
                rollback = await self._restore_provider_snapshot_locked(transaction)
                return {"configured": False, "error": str(error), **rollback}

            # Key validation: a tiny read-only request with the user's key.
            # The probe is the authority — prefix hints are only suggestions.
            # On a definitive failure the whole transaction rolls back so a
            # bad key never leaves the app half-configured.
            key_for_probe = supplied_key if supplied_key is not None else previous_key
            probe: dict[str, Any] = {}
            if key_for_probe and (catalogue_entry is not None or normalized.get("api_base")):
                probe = await probe_api_key(
                    provider_id=runtime_name or secret_name,
                    api_key=str(key_for_probe),
                    api_base=normalized.get("api_base"),
                    protocol=str(normalized.get("protocol") or "openai"),
                    model=normalized.get("model"),
                    explicit_model=explicit_model,
                    catalogue=catalogue,
                )
                if not probe.get("ok"):
                    error_kind = str(probe.get("error") or "invalid")
                    # Catalogue providers use known-good endpoints: any probe
                    # failure is meaningful. Custom endpoints are an advanced
                    # escape hatch — only a definitive auth rejection (401/403)
                    # is treated as a failure there.
                    hard_fail = error_kind == "auth" or (
                        catalogue_entry is not None and error_kind != "invalid"
                    )
                    if not hard_fail:
                        logger.info(
                            "Connect probe inconclusive for {} ({}) — continuing",
                            runtime_name,
                            error_kind,
                        )
                    else:
                        rollback = await self._restore_provider_snapshot_locked(transaction)
                        provider_display = (
                            catalogue_entry.get("name") if catalogue_entry else runtime_name
                        )
                        if error_kind == "auth":
                            message = (
                                "That key didn't work. Double-check it, or get help here → "
                                "https://heycollie.com/get-started"
                            )
                        elif error_kind == "network":
                            message = (
                                "I couldn't reach that provider to check the key. "
                                "Check your connection and try again."
                            )
                        elif error_kind == "model":
                            offered = probe.get("models") or []
                            sample = ", ".join(offered[:5])
                            message = (
                                f"That model name isn't one {provider_display} offers. "
                                f"Available: {sample}."
                                if sample
                                else f"That model name isn't one {provider_display} offers. "
                                f"Pick one of the models it lists instead."
                            )
                        else:
                            message = "That provider didn't accept the key. Double-check it and try again."
                        return {
                            "configured": False,
                            "validated": False,
                            "error": message,
                            "error_kind": error_kind,
                            **rollback,
                        }

            self._provider_config_generation += 1
            transaction_id = uuid.uuid4().hex
            transaction["generation"] = self._provider_config_generation
            self._provider_rollbacks[transaction_id] = transaction
            while len(self._provider_rollbacks) > 16:
                oldest = next(iter(self._provider_rollbacks))
                self._provider_rollbacks.pop(oldest, None)
            result: dict[str, Any] = {
                "provider": provider,
                **configured,
                "transaction_id": transaction_id,
            }
            if key_for_probe and probe.get("ok"):
                result["validated"] = True
                result["model_label"] = probe.get("model_label") or (
                    configured.get("model") or normalized.get("model")
                )
            return result

    async def _finalize_provider_candidate(self, transaction_id: str) -> dict[str, Any]:
        """Forget a successful candidate's compensating rollback snapshot."""
        async with self._configure_lock:
            transaction = self._provider_rollbacks.pop(transaction_id, None)
            return {"finalized": transaction is not None}

    async def _rollback_provider_candidate(self, transaction_id: str) -> dict[str, Any]:
        """Compensate a candidate when Electron could not persist its secret."""
        async with self._configure_lock:
            transaction = self._provider_rollbacks.get(transaction_id)
            if transaction is None:
                return {"rolled_back": False, "rollback_error": "rollback transaction not found"}
            if transaction.get("generation") != self._provider_config_generation:
                return {
                    "rolled_back": False,
                    "rollback_error": "provider configuration changed before rollback",
                }
            result = await self._restore_provider_snapshot_locked(transaction)
            if result.get("rolled_back"):
                self._provider_rollbacks.pop(transaction_id, None)
                self._provider_config_generation += 1
            return result

    def _on_loop_done(self, task: asyncio.Task) -> None:
        """Clear the loop reference when the agent task stops for any reason."""
        if task is not self._loop_task:
            return
        self._loop_task = None
        self.loop = None
        try:
            error = task.exception()
        except (asyncio.CancelledError, Exception):
            error = None
        if error is None:
            return
        logger.error("Agent loop crashed: {}", error)
        if self._auto_reconfiguring:
            return
        self._auto_reconfiguring = True
        asyncio.create_task(self._reconfigure_after_crash())

    async def _reconfigure_after_crash(self) -> None:
        try:
            await asyncio.sleep(2)
            if self.loop is not None:
                return  # a caller already rebuilt the agent
            result = await self._configure()
            logger.info("Agent loop rebuilt after crash: {}", result.get("configured"))
        except Exception:
            logger.exception("Agent loop auto-rebuild failed")
        finally:
            self._auto_reconfiguring = False

    async def _consume_outbound(self) -> None:
        """Deliver bus-published turns to their destination.

        ``process_direct`` returns its outbound straight to the IPC chat
        runner, so the bus carries background turns (subagent results,
        follow-ups) for the desktop plus every messenger-bound reply
        (Telegram/WhatsApp/Slack/Discord sessions answer over the bus).
        """
        loop = self.loop
        if loop is None:
            return
        from nanobot.bus.outbound_events import (
            ArtifactEvent,
            outbound_event_from_message,
        )

        while True:
            outbound = await loop.bus.consume_outbound()
            try:
                channel = str(getattr(outbound, "channel", "") or "")
                if channel != "collie":
                    # Messenger-bound replies go to the channel queue even when
                    # they carry a typed event (e.g. an ArtifactEvent falls
                    # back to its normie text in channel.send). Intercepting
                    # here would swallow the fallback for Telegram/Discord/…
                    await self.messengers.dispatch(outbound)
                    continue
                event = outbound_event_from_message(outbound)
                if isinstance(event, ArtifactEvent):
                    await self._deliver_artifact_event(event, outbound)
                    continue
                conv_id = str(getattr(outbound, "chat_id", "") or "")
                content = str(getattr(outbound, "content", "") or "")
                if not conv_id or not content:
                    continue
                if self.db.get_conversation(conv_id) is None:
                    continue
                assistant = self.db.add_message(conv_id, "assistant", content)
                await self.ipc.broadcast(
                    {
                        "type": "message",
                        "conversation_id": conv_id,
                        "message": assistant,
                    }
                )
                state = "buddy" if self._subagents_running(conv_id) else "done"
                await self.ipc.send_thinking(conv_id, state)
            except Exception:
                logger.exception("Failed to deliver background message")

    async def _deliver_artifact_event(self, event: Any, outbound: Any) -> None:
        """Broadcast a registered "thing" to the desktop panel.

        The ``save_thing`` tool already persisted the record and published the
        event; this only pushes the desktop payload to IPC clients for the
        conversation the tool ran in. Messenger channels never reach this
        branch — they fall back to the message's normie text (``📎 Made: …``)
        via ``messengers.dispatch``.
        """
        conv_id = str(getattr(outbound, "chat_id", "") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            return
        await self.ipc.broadcast(
            {
                "type": "artifact",
                "conversation_id": conv_id,
                "artifact": {
                    "id": event.artifact_id,
                    "title": event.title,
                    "kind": event.kind,
                    "path": event.file_path,
                    "size_bytes": event.size_bytes,
                    "created_at": event.created_at,
                    "status": event.status,
                    "version": event.version,
                },
            }
        )

    def _subagents_running(self, conversation_id: str) -> int:
        """How many subagents are still working for a conversation."""
        if self.loop is None:
            return 0
        count = 0
        for session_key in self.session_keys_for_conversation(conversation_id):
            try:
                count += self.loop.subagents.get_running_count_by_session(session_key)
            except Exception:
                logger.exception("Failed to count subagents for session {}", session_key)
        return count

    async def _steer_chat(self, conversation_id: str, content: str) -> bool:
        """Inject a follow-up into the active desktop turn."""
        if self.loop is None:
            return False
        session_key, channel, target_chat_id = self._conversation_target(conversation_id)
        return await self.loop.steer_session(
            session_key,
            content,
            channel=channel,
            chat_id=target_chat_id,
        )

    async def _run_automation(self, auto: dict[str, Any]) -> None:
        """Run a fired automation's prompt and deliver the result (F057-F065).

        The briefing lands in a per-automation desktop conversation and gets
        fanned out to messengers: any listed in ``delivery_channels`` plus
        every messenger with "deliver automations" switched on.
        """
        import json as _json

        if self.loop is None:
            result = await self._configure()
            if not result.get("configured"):
                logger.info("Automation skipped — no provider configured yet")
                return

        auto_id = str(auto.get("id") or "")
        name = str(auto.get("name") or "Automation")
        action_config = auto.get("action_config")
        if isinstance(action_config, str):
            try:
                action_config = _json.loads(action_config)
            except (TypeError, ValueError):
                action_config = {}
        action_type = str(auto.get("action_type") or "")

        # Gardener-family automations run their own bounded pipelines
        # instead of a free-form prompt turn.
        if action_type == "memory_maintenance":
            await self._run_memory_maintenance(auto)
            return
        if action_type == "gardener":
            await self._run_gardener(auto)
            return

        prompt = ""
        if isinstance(action_config, dict):
            prompt = str(action_config.get("prompt") or "")
        if not prompt:
            return

        conv_id = self._automation_conversation_id(auto)

        outbound = await self.loop.process_direct(
            prompt,
            session_key=self._desktop_session_key(conv_id),
            channel="collie",
            chat_id=conv_id,
            permission_context={
                "execution_mode": "execute",
                "routine_id": auto_id,
                "plan_id": auto.get("plan_id"),
                "plan_version": auto.get("plan_version"),
                "origin": "routine",
            },
        )
        content = str(getattr(outbound, "content", "") or "")
        if not content:
            return

        assistant = self.db.add_message(conv_id, "assistant", content)
        await self.ipc.broadcast(
            {
                "type": "message",
                "conversation_id": conv_id,
                "message": assistant,
            }
        )
        # Morning briefings get a seeable "Today at a glance" card (weather +
        # next 24h reminders) under the text. Best-effort: never blocks the
        # briefing itself.
        if isinstance(action_config, dict) and action_config.get("kind") == "morning":
            try:
                from collie_core.tools.today_glance import attach_today_glance

                await attach_today_glance(self.db, conv_id, self.ipc)
            except Exception:
                logger.exception("Today-at-a-glance card failed for {}", auto_id)

        await self.ipc.broadcast(
            {
                "type": "automation",
                "automation_id": auto_id,
                "name": name,
                "conversation_id": conv_id,
                "content": content,
            }
        )

        deliveries = auto.get("delivery_channels")
        if isinstance(deliveries, str):
            try:
                deliveries = _json.loads(deliveries)
            except (TypeError, ValueError):
                deliveries = []
        targets = {str(d) for d in (deliveries or []) if str(d) in self.messengers.channels}
        targets.update(self.messengers.automation_targets())
        for target in sorted(targets):
            await self.messengers.deliver(target, f"🔔 {name}\n\n{content}")

    # -- Gardener-family automation pipelines ------------------------------------

    def _automation_conversation_id(self, auto: dict[str, Any]) -> str:
        """Resolve (or create) the per-automation 🔔 conversation."""
        auto_id = str(auto.get("id") or "")
        name = str(auto.get("name") or "Automation")
        conv_key = f"automations.{auto_id}.conversation_id"
        conv_id = str(self.db.get_setting(conv_key, "") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            conv = self.db.create_conversation(title=f"🔔 {name}")
            conv_id = conv["id"]
            self.db.set_setting(conv_key, conv_id)
        return conv_id

    async def _announce_automation_result(
        self, auto: dict[str, Any], conv_id: str, content: str
    ) -> None:
        """Persist + broadcast + fan out an automation result message."""
        auto_id = str(auto.get("id") or "")
        name = str(auto.get("name") or "Automation")
        if not content:
            return
        assistant = self.db.add_message(conv_id, "assistant", content)
        await self.ipc.broadcast(
            {
                "type": "message",
                "conversation_id": conv_id,
                "message": assistant,
            }
        )
        await self.ipc.broadcast(
            {
                "type": "automation",
                "automation_id": auto_id,
                "name": name,
                "conversation_id": conv_id,
                "content": content,
            }
        )
        deliveries = auto.get("delivery_channels")
        if isinstance(deliveries, str):
            try:
                deliveries = json.loads(deliveries)
            except (TypeError, ValueError):
                deliveries = []
        targets = {str(d) for d in (deliveries or []) if str(d) in self.messengers.channels}
        targets.update(self.messengers.automation_targets())
        for target in sorted(targets):
            await self.messengers.deliver(target, f"🔔 {name}\n\n{content}")

    async def _run_dream_manual(self) -> dict[str, Any]:
        """Manual 'Review now' trigger (Settings -> Memory)."""
        if self.loop is None:
            result = await self._configure()
            if not result.get("configured"):
                return {
                    "changed": False,
                    "reason": "not_configured",
                    "message": "I need a model provider set up before I can review my memory.",
                }
        from collie_core.memory.dream import run_dream

        return await run_dream(
            workspace=self.workspace,
            db=self.db,
            loop=self.loop,
            version_store=self.versions,
        )

    async def _run_gardener_manual(self) -> dict[str, Any]:
        """Manual 'Suggest improvements' trigger (Settings -> Memory).

        Runs the same pipeline as the weekly automation and publishes the
        review cards into the 🔔 conversation, so a manual run has the same
        review surface as the scheduled one.
        """
        auto = {
            "id": "collie-gardener-suggestions",
            "name": "Improvement suggestions",
            "delivery_channels": ["in_app"],
        }
        return await self._run_gardener(auto) or {}

    async def _run_memory_maintenance(self, auto: dict[str, Any]) -> None:
        """Weekly Dream pass: propose a consolidation, pending for review."""
        if self.loop is None:
            result = await self._configure()
            if not result.get("configured"):
                logger.info("Memory maintenance skipped — no provider configured yet")
                return
        from collie_core.memory.dream import run_dream

        conv_id = self._automation_conversation_id(auto)
        outcome = await run_dream(
            workspace=self.workspace,
            db=self.db,
            loop=self.loop,
            version_store=self.versions,
        )
        content = str(outcome.get("message") or "Memory maintenance ran.")
        if outcome.get("changed"):
            content += (
                "\n\nOpen Settings → Memory to review and apply the changes, "
                "or dismiss them — nothing is written until you approve."
            )
        await self._announce_automation_result(auto, conv_id, content)

    async def _run_gardener(self, auto: dict[str, Any]) -> dict[str, Any] | None:
        """Weekly Gardener pass: evidence → suggestions → review cards."""
        if self.loop is None:
            result = await self._configure()
            if not result.get("configured"):
                logger.info("Gardener skipped — no provider configured yet")
                return None
        from collie_core.gardener.runner import run_gardener

        conv_id = self._automation_conversation_id(auto)
        outcome = await run_gardener(
            workspace=self.workspace,
            db=self.db,
            loop=self.loop,
            version_store=self.versions,
        )
        content = str(outcome.get("message") or "Gardener ran.")
        suggestions = outcome.get("suggestions") or []
        if suggestions:
            card = self.db.add_message(
                conv_id,
                "assistant",
                content,
                card_type="gardener_suggestion",
                card_data={"suggestions": suggestions},
            )
            await self.ipc.broadcast(
                {
                    "type": "message",
                    "conversation_id": conv_id,
                    "message": card,
                }
            )
            await self.ipc.broadcast(
                {
                    "type": "automation",
                    "automation_id": str(auto.get("id") or ""),
                    "name": str(auto.get("name") or "Automation"),
                    "conversation_id": conv_id,
                    "content": content,
                }
            )
            return outcome
        await self._announce_automation_result(auto, conv_id, content)
        return outcome

    async def _shutdown_loop(self) -> None:
        await self.messengers.stop()
        loop = self.loop
        loop_task = self._loop_task
        if loop is not None:
            # Freeze intake before taking the active-session snapshot. Without
            # this ordering, run() can consume one queued messenger message and
            # spawn a fresh _dispatch task after cancel_all_sessions() returns.
            with suppress(Exception):
                loop.stop()
        if loop_task is not None:
            loop_task.cancel()
        if loop is not None:
            try:
                await loop.cancel_all_sessions()
            except Exception:
                logger.exception("Failed to cancel active turns during loop shutdown")
            try:
                await loop.subagents.cancel_all()
            except Exception:
                logger.exception("Failed to cancel subagents during loop shutdown")
        if loop_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await loop_task
            if self._loop_task is loop_task:
                self._loop_task = None
        if loop is not None:
            # Close the provider's HTTP client so loop rebuilds don't leak
            # sockets (anthropic/openai/httpx clients all expose aclose/close).
            try:
                provider = loop.llm_runtime().provider
                closer = getattr(provider, "aclose", None) or getattr(provider, "close", None)
                if callable(closer):
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                logger.debug("Provider client close failed", exc_info=True)
        if self._outbound_task is not None:
            self._outbound_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._outbound_task
            self._outbound_task = None
        self.loop = None
        # Active turns are drained above; flush their telemetry so evidence
        # from cancelled/stopped turns is durable before the loop is gone.
        self._flush_telemetry()

    def _flush_telemetry(self) -> None:
        from collie_core.telemetry.recorder import RunRecorder

        recorder = RunRecorder.active_for(self.db)
        if recorder is not None:
            recorder.flush()

    async def _write_subagent_prompt(self, name: str, description: str) -> str:
        """Have the LLM write a subagent system prompt from a description."""
        if self.loop is None:
            raise RuntimeError("no model configured")
        runtime = self.loop.llm_runtime()
        response = await runtime.provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write concise system prompts for specialized "
                        "personal-assistant subagents. Output only the prompt "
                        "text — no preamble, no code fences. Second person "
                        "('You are...'), a short numbered list of how it "
                        "works, warm plain language, under 180 words."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Subagent name: {name}\nWhat it should be good at: {description or name}"
                    ),
                },
            ],
            model=runtime.model,
        )
        return (response.content or "").strip()

    async def _generate_chat_title(self, first_request: str) -> str:
        """Generate a short sidebar title without delaying the main answer."""
        if self.loop is None:
            return ""
        runtime = self.loop.llm_runtime()
        response = await runtime.provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Write a concise title for this chat. Use 3 to 7 words, "
                        "plain language, no quotation marks, no punctuation at the end. "
                        "Return only the title."
                    ),
                },
                {"role": "user", "content": first_request[:2000]},
            ],
            model=runtime.model,
        )
        return (response.content or "").strip()

    def _status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "configured": self.loop is not None,
            "workspace": str(self.workspace),
            "db_path": str(self.db.path),
            "active_agents": [],
            "recent_agents": [],
        }
        if self.loop is not None:
            try:
                runtime = self.loop.llm_runtime()
                status["model"] = runtime.model
            except Exception:
                pass
            try:
                activity = self.subagent_activity()
                status["active_agents"] = activity["active_agents"]
                status["recent_agents"] = activity["recent_agents"]
            except Exception:
                pass
        return status

    async def _switch_model(self, model: str) -> dict[str, Any]:
        """Persist and live-apply a new active model for future turns.

        The setting is saved first so a later loop rebuild (provider change,
        app restart) keeps the choice. When a loop is running, the change is
        also applied live via the runtime resolver so the very next turn uses
        the new model — no loop rebuild, no interrupted turn.
        """
        name = str(model).strip()
        if not name:
            return {"switched": False, "error": "A model name is required."}
        # Serialize the complete read/persist/live-apply so two concurrent
        # switches cannot split persisted and live state (threaded writes
        # finishing B->C while select_model applies C->B).
        async with self._model_switch_lock:
            # Sync SQLite calls stay off the event loop (project boundary:
            # ipc/server.py wraps every db call in asyncio.to_thread).
            previous = await asyncio.to_thread(self.db.get_setting, "provider.model")
            if name == previous:
                return {
                    "switched": True,
                    "model": name,
                    "previous": previous,
                    "unchanged": True,
                    "applied": True,
                }
            await asyncio.to_thread(self.db.set_active_model, name)
            applied = False
            loop = self.loop
            if loop is not None:
                resolver = getattr(loop, "runtime_resolver", None)
                select = getattr(resolver, "select_model", None)
                if callable(select):
                    try:
                        select(name)
                        applied = True
                    except Exception:
                        logger.exception(
                            "Live model switch failed; the new model will apply "
                            "on the next loop rebuild"
                        )
            return {
                "switched": True,
                "model": name,
                "previous": previous,
                "applied": applied,
            }

    async def _chat(
        self,
        content: str,
        *,
        conversation_id: str,
        on_stream,
        on_progress,
        on_superseded_response=None,
        media: list[str] | None = None,
        execution_mode: str = "plan",
        run_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        project_path: str | None = None,
        file_access_scope: dict[str, Any] | None = None,
        message_metadata: dict[str, Any] | None = None,
    ):
        if self.loop is None:
            result = await self._configure()
            if not result.get("configured"):
                raise RuntimeError(result.get("error") or "no provider configured")
        default_provider = self.db.default_provider()
        session_key, channel, target_chat_id = self._conversation_target(conversation_id)
        outbound = await self.loop.process_direct(
            content,
            session_key=session_key,
            channel=channel,
            chat_id=target_chat_id,
            on_stream=on_stream,
            on_superseded_response=on_superseded_response,
            on_progress=on_progress,
            media=media,
            message_metadata=message_metadata,
            permission_context={
                "execution_mode": execution_mode,
                "conversation_id": conversation_id,
                "run_id": run_id,
                "plan_id": plan_id,
                "plan_version": plan_version,
                "origin": "chat",
                # This value is read by the local-files permission request so
                # its approval makes the receiving model provider explicit.
                "model_provider": str(
                    (default_provider or {}).get("name")
                    or (default_provider or {}).get("id")
                    or "configured model provider"
                ),
            },
            workspace_scope=(
                {
                    **({"project_path": project_path} if project_path else {}),
                    "access_mode": "restricted",
                    **({"file_access_scope": file_access_scope} if file_access_scope else {}),
                }
                if project_path or file_access_scope
                else None
            ),
        )
        if default_provider:
            usage = getattr(self.loop, "_last_usage", None) or {}
            try:
                self.db.record_usage(
                    default_provider["id"],
                    messages=1,
                    tokens=int(usage.get("total_tokens") or 0),
                )
            except Exception:
                # The provider row may have been deleted mid-turn; never let
                # usage bookkeeping fail the turn.
                logger.exception("Failed to record usage for {}", default_provider.get("id"))
        return outbound

    # -- process lifecycle ----------------------------------------------------

    def _gc_media_uploads(self) -> None:
        """Delete uploads no message references (startup pass)."""
        import time
        from pathlib import Path

        uploads = collie_home() / "media" / "uploads"
        if not uploads.exists():
            return
        referenced: set[str] = set()
        for message in self.db.all_messages_with_attachments():
            for attachment in message.get("attachments") or []:
                stored = str(attachment.get("path") or "") if isinstance(attachment, dict) else ""
                if stored:
                    referenced.add(Path(stored).resolve().as_posix().lower())
        cutoff = time.time() - 24 * 3600
        for path in uploads.iterdir():
            if not path.is_file():
                continue
            try:
                stale = path.stat().st_mtime < cutoff
            except OSError:
                stale = True
            if stale and path.resolve().as_posix().lower() not in referenced:
                with suppress(OSError):
                    path.unlink()

    async def _reminder_checker(self) -> None:
        """Fire due reminders into a 🔔 conversation + OS notification."""
        from collie_core.db import utc_now

        conv_key = "reminders.conversation_id"
        while True:
            try:
                for reminder in self.db.due_reminders(utc_now()):
                    reminder_id = str(reminder["id"])
                    if not self.db.complete_reminder(reminder_id):
                        continue
                    conv_id = str(self.db.get_setting(conv_key, "") or "")
                    if not conv_id or self.db.get_conversation(conv_id) is None:
                        conv = self.db.create_conversation(title="🔔 Reminders")
                        conv_id = str(conv["id"])
                        self.db.set_setting(conv_key, conv_id)
                    content = str(reminder.get("text") or "Reminder!")
                    message = self.db.add_message(conv_id, "assistant", f"⏰ {content}")
                    await self.ipc.broadcast(
                        {
                            "type": "message",
                            "conversation_id": conv_id,
                            "message": message,
                        }
                    )
                    await self.ipc.broadcast(
                        {
                            "type": "automation",
                            "automation_id": f"reminder-{reminder_id[:8]}",
                            "name": "Reminder",
                            "conversation_id": conv_id,
                            "content": content,
                        }
                    )
            except Exception:
                logger.exception("Reminder checker failed")
            await asyncio.sleep(30)

    async def run(self) -> None:
        logs_dir = collie_home() / "logs"
        if os.environ.get("COLLIE_DEBUG"):
            logs_dir.mkdir(parents=True, exist_ok=True)
            logger.add(logs_dir / "core.log", rotation="5 MB", retention=3)

        self._gc_media_uploads()
        try:
            # A leftover core from a crashed app session can still own the
            # fixed IPC port; clear it before binding so the boot probe never
            # hangs on a phantom holder.
            from collie_core.lifecycle import reclaim_stale_core_port

            if not reclaim_stale_core_port(self.ipc.port):
                logger.warning(
                    "IPC port {} is still held after reclaim; boot may fail", self.ipc.port
                )
            await self.ipc.start()
            if self._scheduler:
                await self._scheduler.start()
            self._reminder_task = asyncio.create_task(self._reminder_checker())
            # Only attempt boot-time configuration when the user has already set
            # up a provider. OAuth tokens live on disk, so those can configure
            # immediately; API keys arrive from the shell over IPC first.
            auth = str(self.db.get_setting("provider.auth", "") or "")
            if auth in ("chatgpt-oauth", "claude-oauth"):
                await self._configure()
            else:
                logger.info("Waiting for the shell to deliver credentials")
        except Exception:
            logger.exception("Core boot failed")
            print("COLLIE_FATAL boot failed", flush=True)
            raise
        # Structured readiness handshake: the Electron shell switches to
        # "running" only on this line, never on a blind timeout.
        import json as _json

        print(
            f"COLLIE_READY {_json.dumps({'port': self.ipc.port})}",
            flush=True,
        )

        stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            # Order matters: in-flight chat turns must be cancelled and
            # drained before the loop/messengers stop and the DB closes, so no
            # task ever writes through a closed connection.
            await self.ipc.stop()
            if self._scheduler:
                await self._scheduler.stop()
            if self._reminder_task is not None:
                self._reminder_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._reminder_task
                self._reminder_task = None
            await self._shutdown_loop()
            self.approvals.cancel_all()
            # Stop telemetry after active turns are drained and before the
            # database closes, so no queued write is dropped or runs through
            # a closed connection.
            from collie_core.telemetry.recorder import RunRecorder

            recorder = RunRecorder.active_for(self.db)
            if recorder is not None:
                recorder.shutdown()
            self.db.close()


def _env_port(default: int = 3818) -> int:
    raw = os.environ.get("COLLIE_IPC_PORT", "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"COLLIE_IPC_PORT must be an integer, got {raw!r}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collie core runtime")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        port = args.port if args.port is not None else _env_port()
    except ValueError as error:
        parser.error(str(error))
    if port < 1 or port > 65535:
        parser.error(f"invalid port: {port}")
    ipc_token = os.environ.get("COLLIE_IPC_TOKEN") or None

    # If the Electron shell dies hard (OOM kill, crash), the core must not
    # outlive it holding the IPC port for the next launch to trip over.
    from collie_core.lifecycle import arm_parent_watchdog

    arm_parent_watchdog()

    runtime = CollieRuntime(port=port, ipc_token=ipc_token)
    with suppress(KeyboardInterrupt):
        asyncio.run(runtime.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
