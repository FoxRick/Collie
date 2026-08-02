"""AgentHook that records every turn + tool call via RunRecorder.

Registered as a per-turn hook factory on the AgentLoop (the same pattern
as ``create_file_edit_activity_hook``) so every turn kind — chat, plan,
routine, cron, automation, subagent — is recorded with zero edits to the
nanobot orchestration core.
"""

from __future__ import annotations

import time
from typing import Any

from collie_core.db import CollieDB, new_id
from collie_core.telemetry.recorder import (
    TOOL_OUTPUT_LIMIT,
    TURN_INPUT_LIMIT,
    RunRecorder,
    summarize,
)
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
            turn_kind=turn_kind_for_session_key(self._session_key),
        )

    async def after_run(self, context: AgentRunHookContext) -> None:
        self._finish_turn(context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        self._finish_turn(context, forced_status="error")

    async def on_finally(self, context: AgentRunHookContext) -> None:
        if self._turn_id is not None and not self._finished:
            status = "cancelled" if context.stop_reason == "cancelled" else "stopped"
            self._finish_turn(context, forced_status=status)
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
        self._recorder.start_tool(
            tool_id=tool_id,
            turn_id=self._turn_id,
            tool_name=getattr(tool_call, "name", "") or "",
            input_summary=summarize(params, TURN_INPUT_LIMIT),
        )

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        self._finish_tool(
            tool_call,
            status="ok",
            output_summary=summarize(result, TOOL_OUTPUT_LIMIT),
        )

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        self._finish_tool(tool_call, status="error", error_message=str(error))

    # -- internals -----------------------------------------------------------------

    def _finish_turn(
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
        else:
            status = "ok"
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

    def _finish_tool(
        self,
        tool_call: ToolCallRequest,
        *,
        status: str,
        output_summary: str | None = None,
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
            output_summary=output_summary,
            error_message=error_message,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def create_telemetry_hook_factory(db: CollieDB) -> AgentTurnHookFactory:
    """Build a per-turn TelemetryHook factory bound to one shared recorder."""

    recorder = RunRecorder(db)

    def factory(context: AgentTurnHookContext) -> AgentHook | None:
        return TelemetryHook(
            recorder,
            session_key=context.session_key,
            metadata=context.metadata,
        )

    return factory
