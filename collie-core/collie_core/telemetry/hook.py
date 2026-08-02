"""AgentHook that records every turn + tool call via RunRecorder.

Registered as a per-turn hook factory on the AgentLoop (the same pattern
as ``create_file_edit_activity_hook``) so every turn kind — chat, plan,
routine, cron, automation, subagent — is recorded with zero edits to the
nanobot orchestration core.

All recorder calls are fire-and-forget: they enqueue onto a dedicated
writer thread and return immediately, so SQLite writes (and their
redaction work) never run on — or stall — the event loop.
"""

from __future__ import annotations

import time
from typing import Any

from collie_core.db import CollieDB, new_id
from collie_core.permissions.classifier import classify_tool
from collie_core.telemetry.recorder import RunRecorder
from nanobot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
)
from nanobot.agent.turn_hooks import AgentTurnHookFactory
from nanobot.providers.base import ToolCallRequest

# Mirrors MemoryStore._INTERNAL_HISTORY_SESSION_PREFIXES (cron:, dream:)
# plus Collie's routine key prefix.
_INTERNAL_TURN_KINDS = (
    ("cron:", "cron"),
    ("dream:", "automation"),
    ("routine:", "routine"),
)

_KNOWN_TURN_KINDS = {"chat", "plan", "routine", "cron", "subagent", "automation"}


def turn_kind_for_session_key(session_key: str | None) -> str:
    """Map an engine session key to a ``turn_events.turn_kind`` value."""
    if session_key:
        for prefix, kind in _INTERNAL_TURN_KINDS:
            if session_key.startswith(prefix):
                return kind
    return "chat"


def conversation_id_for_session_key(session_key: str | None) -> str | None:
    """Desktop conversations use ``collie:<conversation_id>`` engine keys."""
    if session_key and session_key.startswith("collie:"):
        return session_key[len("collie:"):]
    return None


def resolve_turn_kind(
    session_key: str | None, metadata: dict[str, Any] | None = None
) -> str:
    """Resolve the turn kind from metadata first, then the session key.

    Subagents pass an explicit ``turn_kind`` hint; routines dispatch via
    ``permission_context.origin == "routine"`` (their session key is a
    plain ``collie:`` desktop key). Everything else falls back to the
    session-key prefixes.
    """
    hint = (metadata or {}).get("turn_kind")
    if hint in _KNOWN_TURN_KINDS:
        return hint
    permission_context = (metadata or {}).get("permission_context") or {}
    if permission_context.get("origin") == "routine" or permission_context.get(
        "routine_id"
    ):
        return "routine"
    if permission_context.get("execution_mode") == "plan":
        return "plan"
    return turn_kind_for_session_key(session_key)


class TelemetryHook(AgentHook):
    """Record one agent turn and its tool calls (fire-and-forget)."""

    def __init__(
        self,
        recorder: RunRecorder,
        *,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._recorder = recorder
        self._session_key = session_key
        self._metadata = dict(metadata or {})
        self._turn_id: str | None = None
        self._turn_started: float = 0.0
        self._finished = False
        self._tool_count = 0
        self._tool_ids: dict[str, str] = {}
        self._tool_starts: dict[str, float] = {}

    # -- run lifecycle ---------------------------------------------------------

    async def before_run(self, context: AgentRunHookContext) -> None:
        self._turn_id = new_id()
        self._turn_started = time.monotonic()
        self._recorder.start_turn(
            turn_id=self._turn_id,
            session_key=self._session_key,
            conversation_id=conversation_id_for_session_key(self._session_key),
            turn_kind=resolve_turn_kind(self._session_key, self._metadata),
        )

    async def after_run(self, context: AgentRunHookContext) -> None:
        await self._finish_turn(context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        await self._finish_turn(context, forced_status="error")

    async def on_finally(self, context: AgentRunHookContext) -> None:
        if self._turn_id is not None and not self._finished:
            status = "cancelled" if context.stop_reason == "cancelled" else "stopped"
            await self._finish_turn(context, forced_status=status)
        # Turns that died mid-tool leave running rows behind — mark them.
        for tool_id in list(self._tool_ids.values()):
            self._recorder.finish_tool(
                tool_id=tool_id,
                turn_id=self._turn_id or "",
                tool_name="unknown",
                status="error",
                error_message="Interrupted before completion.",
            )
        self._tool_ids.clear()

    # -- tool lifecycle ----------------------------------------------------------

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        if self._turn_id is None:
            return
        tool_id = new_id()
        self._tool_ids[tool_call.id] = tool_id
        self._tool_starts[tool_call.id] = time.monotonic()
        self._tool_count += 1
        action, resource = self._classify(tool, tool_call, params)
        self._recorder.start_tool(
            tool_id=tool_id,
            turn_id=self._turn_id,
            tool_name=getattr(tool_call, "name", "") or "",
            params=params,
            action=action,
            resource=resource,
        )

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        await self._finish_tool(tool_call, status="ok", result=result)

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        await self._finish_tool(tool_call, status="error", error_message=str(error))

    async def on_tool_blocked(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        status: str,
        reason: str,
    ) -> None:
        """Record a tool that never executed (denied / prep / lookup block)."""
        if self._turn_id is None:
            return
        self._tool_count += 1
        action, resource = self._classify(tool, tool_call, params)
        self._recorder.blocked_tool(
            tool_id=new_id(),
            turn_id=self._turn_id,
            tool_name=getattr(tool_call, "name", "") or "",
            status=status,
            reason=reason,
            params=params,
            action=action,
            resource=resource,
        )

    # -- internals -----------------------------------------------------------------

    @staticmethod
    def _classify(
        tool: Any, tool_call: ToolCallRequest, params: Any
    ) -> tuple[str | None, str | None]:
        """Best-effort permission classification for action/resource columns."""
        try:
            request = classify_tool(tool, tool_call.name, params or {})
            return request.action, request.resource
        except Exception:
            return None, None

    async def _finish_turn(
        self, context: AgentRunHookContext, *, forced_status: str | None = None
    ) -> None:
        if self._turn_id is None or self._finished:
            return
        self._finished = True
        if forced_status is not None:
            status = forced_status
        elif context.error is not None:
            status = "error"
        elif context.stop_reason == "cancelled":
            status = "cancelled"
        elif context.stop_reason in (None, "completed"):
            status = "ok"
        else:
            # Incomplete terminal reasons: max_iterations, tool_error,
            # empty_final_response, ...
            status = "stopped"
        usage = context.usage or {}
        self._recorder.finish_turn(
            turn_id=self._turn_id,
            status=status,
            error_message=context.error,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.monotonic() - self._turn_started) * 1000),
            tool_count=self._tool_count,
        )

    async def _finish_tool(
        self,
        tool_call: ToolCallRequest,
        *,
        status: str,
        result: Any = None,
        error_message: str | None = None,
    ) -> None:
        tool_id = self._tool_ids.pop(tool_call.id, None)
        if tool_id is None:
            return
        started = self._tool_starts.pop(tool_call.id, time.monotonic())
        self._recorder.finish_tool(
            tool_id=tool_id,
            turn_id=self._turn_id or "",
            tool_name=getattr(tool_call, "name", "") or "",
            status=status,
            result=result,
            error_message=error_message,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def create_telemetry_hook_factory(db: CollieDB) -> AgentTurnHookFactory:
    """Build a per-turn TelemetryHook factory bound to one shared recorder."""

    recorder = RunRecorder.for_db(db)

    def factory(context: AgentTurnHookContext) -> AgentHook | None:
        return TelemetryHook(
            recorder,
            session_key=context.session_key,
            metadata=context.metadata,
        )

    return factory
