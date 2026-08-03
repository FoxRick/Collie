"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import (
    AbstractContextManager,
    ExitStack,
    asynccontextmanager,
    nullcontext,
    suppress,
)
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent import context as agent_context
from nanobot.agent import model_presets as preset_helpers
from nanobot.agent.automation_turns import publish_next_deferred_turn
from nanobot.agent.context import ContextBuilder
from nanobot.agent.cron_turns import CronTurnCoordinator
from nanobot.agent.goal_permission import goal_mutation_permission
from nanobot.agent.hook import AgentHook, AgentTurnHookFactory
from nanobot.agent.memory import Consolidator
from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.outbound_events import (
    RetryWaitEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    outbound_message_for_event,
)
from nanobot.bus.progress import build_bus_progress_callback
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventPublisher,
    ensure_runtime_event_publisher,
)
from nanobot.config.schema import AgentDefaults, ModelPresetConfig
from nanobot.providers.base import LLMProvider
from nanobot.providers.factory import ProviderSnapshot
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    RuntimeContextProvider,
    append_runtime_context,
    resolve_runtime_context,
)
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScopeResolver,
    bind_workspace_scope,
    clear_live_local_file_scope,
    reset_workspace_scope,
)
from nanobot.session import turn_continuation
from nanobot.session.automation_turns import automation_history_overrides
from nanobot.session.goal_state import (
    goal_state_runtime_lines,
    runner_wall_llm_timeout_s,
    sustained_goal_active,
    trusted_goal_start_requested,
)
from nanobot.session.history_visibility import HIDDEN_HISTORY_META
from nanobot.session.keys import UNIFIED_SESSION_KEY
from nanobot.session.manager import (
    Session,
    SessionManager,
    replay_max_messages_for_context,
)
from nanobot.triggers.local_turns import LocalTriggerTurnCoordinator
from nanobot.utils.document import extract_documents, reference_non_image_attachments
from nanobot.utils.helpers import image_placeholder_text
from nanobot.utils.helpers import truncate_text as truncate_text_fn
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
)

_MAX_SESSION_LOCKS = 512

if TYPE_CHECKING:
    from nanobot.agent.tools.mcp import MCPConnection
    from nanobot.config.schema import (
        ChannelsConfig,
        ProviderConfig,
        ToolsConfig,
    )
    from nanobot.cron.service import CronService

class TurnState(Enum):
    RESTORE = auto()
    COMPACT = auto()
    BUILD = auto()
    RUN = auto()
    SAVE = auto()
    RESPOND = auto()
    DONE = auto()


@dataclass
class StateTraceEntry:
    state: TurnState
    started_at: float
    duration_ms: float
    event: str
    error: str | None = None


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    state: TurnState
    turn_id: str
    runtime: LLMRuntime
    original_user_text: str | None = None
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)
    request_context: RequestContext | None = None
    runtime_context_blocks: list[RuntimeContextBlock] = field(default_factory=list)

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    had_injections: bool = False

    user_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    suppress_response: bool = False

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_superseded_response: Callable[[str], Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue | None = None
    pending_summary: str | None = None

    ephemeral: bool = False
    run_extra_hooks_for_ephemeral: bool = False
    hooks: list[AgentHook] = field(default_factory=list)
    hook_factories: list[AgentTurnHookFactory] = field(default_factory=list)
    turn_scopes: list[AbstractContextManager[Any]] = field(default_factory=list)
    tools: ToolRegistry | None = None

    turn_wall_started_at: float = field(default_factory=time.time)
    visible_run_started_at: float | None = None
    turn_latency_ms: int | None = None

    trace: list[StateTraceEntry] = field(default_factory=list)


@dataclass
class _SessionLockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    last_used: int = field(default_factory=time.monotonic_ns)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    @property
    def current_iteration(self) -> int:
        return self._current_iteration

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names

    @property
    def provider(self) -> LLMProvider:
        """Provider selected for future turn admissions."""
        return self.runtime_resolver.runtime.provider

    @property
    def model(self) -> str:
        """Model selected for future turn admissions."""
        return self.runtime_resolver.runtime.model

    @property
    def context_window_tokens(self) -> int:
        """Context limit selected for future turn admissions."""
        return self.runtime_resolver.runtime.context_window_tokens

    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        """Configured model presets exposed for selection and display."""
        return self.runtime_resolver.model_presets

    @property
    def model_preset(self) -> str | None:
        return self.runtime_resolver.model_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        self.set_model_preset(name)

    def llm_runtime(self) -> LLMRuntime:
        """Resolve the immutable default used to admit the next turn."""
        previous = self.runtime_resolver.runtime
        try:
            runtime = self.runtime_resolver.current(refresh=True)
        except Exception:
            logger.exception("Failed to refresh model runtime")
            return previous
        if (
            runtime.model != previous.model
            or runtime.model_preset != previous.model_preset
            or runtime.snapshot_signature != previous.snapshot_signature
        ):
            self._publish_runtime_selection(runtime)
        return runtime

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"

    # Event-driven state transition table.
    # Handlers return an event string; the driver looks up the next state here.
    _TRANSITIONS: dict[tuple[TurnState, str], TurnState] = {
        (TurnState.RESTORE, "ok"): TurnState.COMPACT,
        (TurnState.COMPACT, "ok"): TurnState.BUILD,
        (TurnState.BUILD, "ok"): TurnState.RUN,
        (TurnState.RUN, "ok"): TurnState.SAVE,
        (TurnState.SAVE, "ok"): TurnState.RESPOND,
        (TurnState.RESPOND, "ok"): TurnState.DONE,
    }

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        fail_on_tool_error: bool | None = None,
        provider_retry_mode: str = "standard",
        tool_hint_max_length: int | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        consolidation_ratio: float = 0.5,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        image_generation_provider_config: ProviderConfig | None = None,
        image_generation_provider_configs: dict[str, ProviderConfig] | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        runtime_events: RuntimeEventBus | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
        restart_mode: str = "auto",
        local_trigger_store: Any | None = None,
    ):
        from nanobot.config.schema import ToolsConfig

        _tc = tools_config or ToolsConfig()
        defaults = AgentDefaults()
        self.bus = bus
        self.runtime_events = runtime_events or RuntimeEventBus()
        self.runtime_event_publisher = RuntimeEventPublisher(self.runtime_events)
        self.channels_config = channels_config
        self.restart_mode = restart_mode
        self._runtime_model_publisher = runtime_model_publisher
        self.workspace = workspace
        initial_model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        initial_context_window = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        configured_presets = model_presets or {}
        self.runtime_resolver = ModelRuntimeResolver(
            LLMRuntime.capture(
                provider,
                initial_model,
                context_window_tokens=initial_context_window,
                snapshot_signature=provider_signature,
            ),
            model_presets=configured_presets,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.tool_hint_max_length = (
            tool_hint_max_length if tool_hint_max_length is not None
            else defaults.tool_hint_max_length
        )
        self.tools_config = _tc
        self.web_config = _tc.web
        self._image_generation_provider_configs = dict(image_generation_provider_configs or {})
        if (
            image_generation_provider_config is not None
            and "openrouter" not in self._image_generation_provider_configs
        ):
            self._image_generation_provider_configs["openrouter"] = image_generation_provider_config
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace_scopes = WorkspaceScopeResolver(
            default_workspace=workspace,
            default_restrict_to_workspace=restrict_to_workspace,
        )
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []
        self._hook_factories: list[AgentTurnHookFactory] = hook_factories or []

        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner()
        self.subagents = SubagentManager(
            workspace=workspace,
            bus=bus,
            tools_config=_tc,
            max_tool_result_chars=self.max_tool_result_chars,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
            max_iterations=self.max_iterations,
            max_concurrent_subagents=max_concurrent_subagents,
            fail_on_tool_error=fail_on_tool_error,
            llm_wall_timeout_for_session=lambda sk: runner_wall_llm_timeout_s(self.sessions, sk),
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, MCPConnection] = {}
        self._mcp_connecting = False
        self._runtime_context_providers: list[RuntimeContextProvider] = []
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, _SessionLockEntry] = {}
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        self._deferred_automation_turns: dict[str, list[InboundMessage]] = {}
        self._cron_turns = CronTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._local_trigger_turns = LocalTriggerTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._automation_turn_coordinators = (
            ("cron", self._cron_turns),
            ("local trigger", self._local_trigger_turns),
        )
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            sessions=self.sessions,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            consolidation_ratio=consolidation_ratio,
            unified_session=unified_session,
        )
        if model_preset:
            self.set_model_preset(model_preset, publish_update=False)
        self._register_default_tools(provider_snapshot_loader=provider_snapshot_loader)
        self._runtime_vars: dict[str, Any] = {}
        self._current_iteration: int = 0

    @classmethod
    def from_config(
        cls,
        config: Any,
        bus: MessageBus | None = None,
        **extra: Any,
    ) -> AgentLoop:
        """Create an AgentLoop from config with the common parameter set.

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend the standard config-derived
        parameters (e.g. ``cron_service``, ``session_manager``).
        """
        from nanobot.providers.factory import make_provider

        if bus is None:
            bus = MessageBus()
        defaults = config.agents.defaults
        provider = extra.pop("provider", None) or make_provider(config)
        resolved = config.resolve_preset()
        model = extra.pop("model", None) or resolved.model
        context_window_tokens = extra.pop("context_window_tokens", None) or resolved.context_window_tokens
        provider_snapshot_loader = extra.pop("provider_snapshot_loader", None)
        preset_snapshot_loader = extra.pop("preset_snapshot_loader", None) or preset_helpers.make_preset_snapshot_loader(
            config,
            provider_snapshot_loader,
        )
        return cls(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            max_iterations=defaults.max_tool_iterations,
            max_concurrent_subagents=defaults.max_concurrent_subagents,
            context_window_tokens=context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            fail_on_tool_error=defaults.fail_on_tool_error,
            provider_retry_mode=defaults.provider_retry_mode,
            tool_hint_max_length=defaults.tool_hint_max_length,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            timezone=defaults.timezone,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            consolidation_ratio=defaults.consolidation_ratio,
            tools_config=config.tools,
            model_presets=preset_helpers.configured_model_presets(config),
            model_preset=defaults.model_preset,
            restart_mode=config.gateway.restart_mode,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
            **extra,
        )

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings."""
        self.subagents.max_iterations = self.max_iterations

    def _publish_runtime_selection(
        self,
        runtime: LLMRuntime,
        *,
        publish_update: bool = True,
    ) -> None:
        if not publish_update:
            return
        if self._runtime_model_publisher is not None:
            self._runtime_model_publisher(runtime.model, runtime.model_preset)
        self._runtime_events().runtime_model_changed(
            runtime.model,
            runtime.model_preset,
        )

    def set_model_preset(
        self,
        name: str | None,
        *,
        publish_update: bool = True,
    ) -> LLMRuntime:
        """Select a named default runtime for future turns."""
        old_model = self.model
        runtime = self.runtime_resolver.select_preset(name)
        self._publish_runtime_selection(runtime, publish_update=publish_update)
        logger.info(
            "Runtime model switched for next turn: {} -> {}",
            old_model,
            runtime.model,
        )
        return runtime

    def set_runtime_model(self, model: str) -> LLMRuntime:
        """Select a model on the current provider for future turns."""
        return self.runtime_resolver.select_model(model)

    def set_runtime_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Select a context limit for future turns."""
        return self.runtime_resolver.select_context_window(context_window_tokens)

    def _register_default_tools(
        self,
        *,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None,
    ) -> None:
        """Register the default set of tools via plugin loader."""
        from nanobot.agent.tools.context import ToolContext
        from nanobot.agent.tools.loader import ToolLoader

        ctx = ToolContext(
            config=self.tools_config,
            workspace=str(self.workspace),
            bus=self.bus,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
            sessions=self.sessions,
            provider_snapshot_loader=provider_snapshot_loader,
            image_generation_provider_configs=self._image_generation_provider_configs,
            timezone=self.context.timezone or "UTC",
            workspace_sandbox=self.workspace_scopes.sandbox_status,
            runtime_events=self.runtime_events,
        )
        loader = ToolLoader()
        registered = loader.load(ctx, self.tools)

        logger.info("Registered {} tools: {}", len(registered), registered)

    async def _connect_mcp(self) -> None:
        """Connect configured MCP servers."""
        await agent_context.connect_mcp(self, self.tools)

    def register_runtime_context_provider(
        self,
        provider: RuntimeContextProvider,
    ) -> None:
        """Register a provider resolved once before each inbound model turn."""
        if provider not in self._runtime_context_providers:
            self._runtime_context_providers.append(provider)

    @staticmethod
    def _runtime_chat_id(msg: InboundMessage) -> str:
        """Return the chat id shown in runtime metadata for the model."""
        return str(msg.metadata.get("context_chat_id") or msg.chat_id)

    async def _build_bus_progress_callback(
        self, msg: InboundMessage
    ) -> Callable[..., Awaitable[None]]:
        """Build a progress callback that publishes to the message bus."""
        return build_bus_progress_callback(self.bus, msg)

    async def _build_retry_wait_callback(
        self, msg: InboundMessage
    ) -> Callable[[str], Awaitable[None]]:
        """Build a retry-wait callback that publishes to the message bus."""

        async def _on_retry_wait(content: str) -> None:
            await self.bus.publish_outbound(
                outbound_message_for_event(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    event=RetryWaitEvent(content=content),
                    metadata=msg.metadata,
                )
            )

        return _on_retry_wait

    def _runtime_events(self) -> RuntimeEventPublisher:
        return ensure_runtime_event_publisher(self)

    def _session_lock_entry(self, session_key: str) -> _SessionLockEntry:
        entry = self._session_locks.get(session_key)
        if entry is None:
            entry = _SessionLockEntry()
            self._session_locks[session_key] = entry
        return entry

    def _evict_idle_session_locks(self, *, current_session_key: str) -> None:
        excess = len(self._session_locks) - _MAX_SESSION_LOCKS
        if excess <= 0:
            return
        idle = sorted(
            (
                (key, entry)
                for key, entry in self._session_locks.items()
                if key != current_session_key and entry.users == 0
            ),
            key=lambda item: item[1].last_used,
        )
        for key, entry in idle[:excess]:
            if self._session_locks.get(key) is entry and entry.users == 0:
                self._session_locks.pop(key, None)

    @asynccontextmanager
    async def _session_lock(self, session_key: str) -> AsyncIterator[None]:
        entry = self._session_lock_entry(session_key)
        # Count owners and waiters before awaiting the lock so pruning cannot
        # replace a lock while any turn still references it.
        entry.users += 1
        entry.last_used = time.monotonic_ns()
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.users -= 1
            entry.last_used = time.monotonic_ns()
            self._evict_idle_session_locks(current_session_key=session_key)

    @asynccontextmanager
    async def _pending_turn_queue(
        self,
        session_key: str,
    ) -> AsyncIterator[asyncio.Queue[InboundMessage]]:
        pending: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=20)
        self._pending_queues[session_key] = pending
        try:
            yield pending
        finally:
            if self._pending_queues.get(session_key) is pending:
                self._pending_queues.pop(session_key, None)

    @staticmethod
    def _drain_pending_queue(
        pending: asyncio.Queue[InboundMessage],
    ) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        while True:
            try:
                messages.append(pending.get_nowait())
            except asyncio.QueueEmpty:
                return messages

    async def _process_internal_continuation_chain(
        self,
        msg: InboundMessage,
        *,
        pending: asyncio.Queue[InboundMessage],
        process: Callable[[InboundMessage], Awaitable[OutboundMessage | None]],
        deferred_messages: list[InboundMessage],
    ) -> tuple[OutboundMessage | None, InboundMessage]:
        continuations: deque[InboundMessage] = deque([msg])
        response: OutboundMessage | None = None
        completed_msg = msg
        while continuations:
            completed_msg = continuations.popleft()
            response = await process(completed_msg)
            for queued in self._drain_pending_queue(pending):
                if turn_continuation.internal_continuation_inbound(queued.metadata):
                    continuations.append(queued)
                else:
                    deferred_messages.append(queued)
        return response, completed_msg

    async def _republish_pending_messages(
        self,
        messages: list[InboundMessage],
        *,
        session_key: str,
    ) -> None:
        for item in messages:
            await self.bus.publish_inbound(item)
        if messages:
            logger.info(
                "Re-published {} leftover message(s) to bus for session {}",
                len(messages),
                session_key,
            )

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._cron_turns.submit(msg)

    async def submit_local_trigger_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._local_trigger_turns.submit(msg)

    def pending_cron_job_ids_for_session(self, session_key: str) -> set[str]:
        return self._cron_turns.pending_job_ids_for_session(session_key)

    def pending_local_trigger_ids_for_session(self, session_key: str) -> set[str]:
        return self._local_trigger_turns.pending_trigger_ids_for_session(session_key)

    async def _publish_next_deferred_automation_turn(self, session_key: str) -> None:
        await publish_next_deferred_turn(
            deferred_queues=self._deferred_automation_turns,
            publish_inbound=self.bus.publish_inbound,
            session_key=session_key,
        )

    def _persist_user_message_early(
        self,
        msg: InboundMessage,
        session: Session,
        runtime_context_blocks: list[RuntimeContextBlock] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Persist the triggering user message before the turn starts.

        Returns True if the message was persisted.
        """
        if not turn_continuation.should_persist_user_message(msg.metadata):
            return False
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if has_text or media_paths or runtime_context_blocks:
            extra: dict[str, Any] = ({"media": list(media_paths)} if media_paths else {}) | agent_context.session_extra(msg.metadata)
            extra.update(kwargs)
            text = msg.content if isinstance(msg.content, str) else ""
            text_override, automation_extra = automation_history_overrides(msg.metadata)
            if text_override is not None:
                text = text_override
            extra.update(automation_extra)
            text, runtime_context_meta = append_runtime_context(
                text,
                runtime_context_blocks or (),
            )
            if runtime_context_meta is not None:
                extra[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            session.add_message("user", text, **extra)
            self._mark_pending_user_turn(session)
            self.sessions.save(session)
            return True
        return False

    def _build_initial_messages(
        self,
        msg: InboundMessage,
        session: Session,
        history: list[dict[str, Any]],
        pending_summary: str | None,
        include_memory_recent_history: bool = True,
        runtime_context_blocks: list[RuntimeContextBlock] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for the LLM turn."""
        scope = self.workspace_scopes.for_message(msg, session.metadata)
        return self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=self._runtime_chat_id(msg),
            sender_id=msg.sender_id,
            session_summary=pending_summary,
            session_metadata=session.metadata,
            workspace=scope.project_path,
            runtime_context_blocks=runtime_context_blocks,
            include_memory_recent_history=include_memory_recent_history,
            session_key=session.key,
            unified_session=self._unified_session,
        )

    def _request_context_for_turn(self, ctx: TurnContext) -> RequestContext:
        scope = self.workspace_scopes.for_message(ctx.msg, ctx.session.metadata)
        return RequestContext(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            message_id=ctx.msg.metadata.get("message_id"),
            session_key=ctx.session_key,
            original_user_text=ctx.original_user_text,
            runtime=ctx.runtime,
            metadata=dict(ctx.msg.metadata or {}),
            sender_id=ctx.msg.sender_id,
            turn_id=ctx.turn_id,
            workspace=scope.project_path,
        )

    async def _resolve_runtime_context_for_turn(
        self,
        ctx: TurnContext,
    ) -> list[RuntimeContextBlock]:
        tools = ctx.tools or self.tools
        providers = [
            *tools.get_runtime_context_providers(),
            *self._runtime_context_providers,
        ]
        assert ctx.request_context is not None
        return await resolve_runtime_context(providers, ctx.request_context)

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await t
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    async def cancel_session(self, key: str) -> int:
        """Public runtime control for deterministic /stop and /new commands."""
        return await self._cancel_active_tasks(key)

    async def cancel_all_sessions(self) -> int:
        """Cancel and drain active turns and subagents across every session."""
        keys = set(self._active_tasks)
        keys.update(self.subagents.get_running_sessions())
        cancelled = 0
        for key in sorted(keys):
            cancelled += await self._cancel_active_tasks(key)
        return cancelled

    async def steer_session(
        self,
        key: str,
        content: str,
        *,
        channel: str = "collie",
        chat_id: str = "direct",
        sender_id: str = "user",
    ) -> bool:
        """Inject a user follow-up into an active turn for *key*."""
        pending = self._pending_queues.get(key)
        if pending is None:
            return False
        message = InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            session_key_override=key,
        )
        try:
            pending.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Steering queue full for session {}", key)
            return False
        logger.info("Steered active turn for session {}", key)
        return True

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    @staticmethod
    def _replay_token_budget(runtime: LLMRuntime) -> int:
        """Derive a token budget for session history replay from the context window."""
        if runtime.context_window_tokens <= 0:
            return 0
        max_output = runtime.generation.max_tokens
        try:
            reserved_output = int(max_output)
        except (TypeError, ValueError):
            reserved_output = 4096
        budget = runtime.context_window_tokens - max(1, reserved_output) - 1024
        return budget if budget > 0 else max(128, runtime.context_window_tokens // 2)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_superseded_response: Callable[[str], Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        runtime: LLMRuntime,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        original_user_text: str | None = None,
        pending_queue: asyncio.Queue | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        turn_scopes: list[AbstractContextManager[Any]] | None = None,
        tools: ToolRegistry | None = None,
        request_context: RequestContext | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        self._sync_subagent_runtime_limits()

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            When no messages are immediately available but sub-agents
            spawned in this dispatch are still running, blocks until at
            least one result arrives (or timeout).  This keeps the runner
            loop alive so subsequent sub-agent completions are consumed
            in-order rather than dispatched separately.
            """
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = self._prepare_message_media(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                row: dict[str, Any] = {"role": "user", "content": user_content}
                metadata = pending_msg.metadata if isinstance(pending_msg.metadata, dict) else {}
                if (
                    pending_msg.sender_id == "subagent"
                    and metadata.get("injected_event") == "subagent_result"
                ):
                    marker: dict[str, Any] = {"kind": "subagent_result"}
                    task_id = metadata.get("subagent_task_id")
                    if isinstance(task_id, str) and task_id:
                        marker["subagent_task_id"] = task_id
                        row["subagent_task_id"] = task_id
                    row[HIDDEN_HISTORY_META] = marker
                    row["injected_event"] = "subagent_result"
                return row

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            # Block if nothing drained but sub-agents spawned in this dispatch
            # are still running.  Keeps the runner loop alive so subsequent
            # completions are injected in-order rather than dispatched separately.
            if (not items
                    and session is not None
                    and self.subagents.get_running_count_by_session(session.key) > 0):
                try:
                    msg = await asyncio.wait_for(pending_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout waiting for sub-agent completion in session {}",
                        session.key,
                    )
                    return items
                items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        items.append(_to_user_message(pending_queue.get_nowait()))
                    except asyncio.QueueEmpty:
                        break

            return items

        active_session_key = session.key if session else session_key
        effective_scope = self.workspace_scopes.for_turn(
            channel=channel,
            message_metadata=metadata,
            session_metadata=session.metadata if session is not None else None,
        )
        effective_tools = tools or self.tools
        request_ctx = request_context or RequestContext(
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            session_key=active_session_key,
            original_user_text=original_user_text,
            runtime=runtime,
            metadata=dict(metadata or {}),
            workspace=effective_scope.project_path,
        )
        request_token = bind_request_context(request_ctx)
        workspace_token = bind_workspace_scope(effective_scope)
        turn_scope_stack = ExitStack()
        # Compute lazily because create_goal may create goal metadata during this run.
        def _goal_continue() -> str | None:
            _goal_lines = goal_state_runtime_lines(session.metadata if session is not None else None)
            if not _goal_lines:
                return None
            return (
                "You have an active sustained goal:\n\n"
                + "\n".join(_goal_lines)
                + "\n\nPlease continue working toward the objective using your tools, "
                "or call update_goal with action='complete' if the work is truly finished."
            )

        session_metadata = session.metadata if session is not None else None
        permission_context = (
            metadata.get("permission_context", {})
            if isinstance(metadata, dict)
            else {}
        )
        if not isinstance(permission_context, dict):
            permission_context = {}
        try:
            turn_scope_stack.enter_context(goal_mutation_permission(
                trusted_goal_start_requested(metadata)
                or sustained_goal_active(session_metadata)
            ))
            for scope in turn_scopes or ():
                turn_scope_stack.enter_context(scope)
            hook = build_agent_turn_hook(AgentTurnHookSpec(
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                on_superseded_response=on_superseded_response,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
                metadata=metadata,
                session_key=active_session_key,
                workspace=effective_scope.project_path,
                tool_hint_max_length=self.tool_hint_max_length,
                on_iteration=lambda iteration: setattr(self, "_current_iteration", iteration),
                registered_hook_factories=self._hook_factories,
                turn_hook_factories=list(hook_factories or []),
                registered_hooks=self._extra_hooks,
                turn_hooks=list(hooks or []),
                ephemeral=ephemeral,
                run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            ))
            result = await self.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=effective_tools,
                runtime=runtime,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=hook,
                error_message="Sorry, I encountered an error calling the AI model.",
                concurrent_tools=True,
                workspace=effective_scope.project_path,
                session_key=session.key if session else None,
                context_block_limit=self.context_block_limit,
                provider_retry_mode=self.provider_retry_mode,
                progress_callback=on_progress,
                stream_progress_deltas=on_stream is not None,
                retry_wait_callback=on_retry_wait,
                checkpoint_callback=_checkpoint,
                injection_callback=_drain_pending,
                # Sustained goals may legitimately exceed NANOBOT_LLM_TIMEOUT_S; idle stall
                # is still capped by NANOBOT_STREAM_IDLE_TIMEOUT_S in streaming providers.
                llm_timeout_s=runner_wall_llm_timeout_s(
                    self.sessions,
                    session.key if session is not None else session_key,
                    metadata=session_metadata,
                    message_metadata=metadata,
                ),
                goal_active_predicate=lambda: sustained_goal_active(session.metadata) if session is not None else False,
                goal_continue_message=_goal_continue,
                # Goal state can transition to terminal on the last allowed
                # tool iteration, so decide at the budget boundary rather than
                # snapshotting the active state before the runner starts.
                finalize_on_max_iterations=lambda: (
                    turn_continuation.should_finalize_on_max_iterations(
                        pending_queue_available=pending_queue is not None and session is not None,
                        session_metadata=session_metadata,
                        message_metadata=metadata,
                    )
                ),
                authorizer=getattr(self, "authorizer", None),
                execution_mode=str(permission_context.get("execution_mode") or "execute"),
                run_id=permission_context.get("run_id"),
                plan_id=permission_context.get("plan_id"),
                plan_version=permission_context.get("plan_version"),
                conversation_id=permission_context.get("conversation_id") or chat_id,
                routine_id=permission_context.get("routine_id"),
                origin=str(permission_context.get("origin") or channel),
                approve_all_for_run=bool(
                    permission_context.get("approve_all_for_run", False)
                ),
            ))
        finally:
            turn_scope_stack.close()
            reset_workspace_scope(workspace_token)
            reset_request_context(request_token)
            # A file-access override granted mid-turn lives only as long as
            # the turn that it applied to. Clear it so the next turn starts
            # from its own message/session scope again.
            live_conversation_id = str(
                (permission_context.get("conversation_id") or "")
                if isinstance(permission_context, dict)
                else ""
            )
            if live_conversation_id:
                clear_live_local_file_scope(live_conversation_id)
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            should_stream = turn_continuation.should_stream_budget_response(
                stop_reason=result.stop_reason,
                pending_queue_available=pending_queue is not None and session is not None,
                session_metadata=session_metadata,
                message_metadata=metadata,
            )
            # Push final content through stream so streaming channels (e.g. Feishu)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end and should_stream:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        try:
            await self._connect_mcp()
            logger.info("Agent loop started")

            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    # Preserve real task cancellation so shutdown can complete cleanly.
                    # Only ignore non-task CancelledError signals that may leak from integrations.
                    if not self._running or asyncio.current_task().cancelling():
                        raise
                    continue
                except Exception as e:
                    logger.warning("Error consuming inbound message: {}, continuing...", e)
                    continue

                effective_key = self._effective_session_key(msg)
                if await agent_context.handle_runtime_control(self, msg, self.tools):
                    continue
                deferred = False
                for label, coordinator in self._automation_turn_coordinators:
                    if coordinator.defer_if_active(
                        msg,
                        session_key=effective_key,
                        active_session_keys=self._pending_queues.keys(),
                    ):
                        logger.info(
                            "Deferred {} turn for active session {}",
                            label,
                            effective_key,
                        )
                        deferred = True
                        break
                if deferred:
                    continue
                # If this session already has an active pending queue (i.e. a task
                # is processing this session), route the message there for mid-turn
                # injection instead of creating a competing task.
                if effective_key in self._pending_queues:
                    pending_msg = msg
                    if effective_key != msg.session_key:
                        pending_msg = dataclasses.replace(
                            msg,
                            session_key_override=effective_key,
                        )
                    try:
                        self._pending_queues[effective_key].put_nowait(pending_msg)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Pending queue full for session {}, falling back to queued task",
                            effective_key,
                        )
                    else:
                        logger.info(
                            "Routed follow-up message to pending queue for session {}",
                            effective_key,
                        )
                        continue
                # Compute the effective session key before dispatching
                # This ensures /stop command can find tasks correctly when unified session is enabled
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(effective_key, []).append(task)
                task.add_done_callback(
                    lambda t, k=effective_key: self._active_tasks.get(k, [])
                    and self._active_tasks[k].remove(t)
                    if t in self._active_tasks.get(k, [])
                    else None
                )
        finally:
            # MCP stdio transports use AnyIO cancel scopes; close them from the task that opened them.
            await self.close_mcp()

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        gate = self._concurrency_gate or nullcontext()

        pending: asyncio.Queue[InboundMessage] | None = None
        deferred_messages: list[InboundMessage] = []
        completed_msg = msg
        chain_complete = False
        try:
            async with self._session_lock(session_key), gate:
                # Only the task that owns the session lock may publish the
                # active mid-turn injection queue for this session.
                try:
                    async with self._pending_turn_queue(session_key) as pending:
                        try:
                            on_stream = on_stream_end = None
                            if msg.metadata.get("_wants_stream"):
                                # Split one answer into distinct stream segments.
                                stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                                stream_segment = 0

                                def _current_stream_id() -> str:
                                    return f"{stream_base_id}:{stream_segment}"

                                async def on_stream(delta: str) -> None:
                                    await self.bus.publish_outbound(
                                        outbound_message_for_event(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            event=StreamDeltaEvent(
                                                content=delta,
                                                stream_id=_current_stream_id(),
                                            ),
                                            metadata=msg.metadata,
                                        )
                                    )

                                async def on_stream_end(*, resuming: bool = False) -> None:
                                    nonlocal stream_segment
                                    await self.bus.publish_outbound(
                                        outbound_message_for_event(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            event=StreamEndEvent(
                                                stream_id=_current_stream_id(),
                                                resuming=resuming,
                                            ),
                                            metadata=msg.metadata,
                                        )
                                    )
                                    stream_segment += 1

                            async def _process(current: InboundMessage) -> OutboundMessage | None:
                                return await self._process_message(
                                    current,
                                    on_stream=on_stream,
                                    on_stream_end=on_stream_end,
                                    pending_queue=pending,
                                )

                            response, completed_msg = (
                                await self._process_internal_continuation_chain(
                                    msg,
                                    pending=pending,
                                    process=_process,
                                    deferred_messages=deferred_messages,
                                )
                            )
                            chain_complete = True
                            completed_channel = completed_msg.channel
                            completed_chat_id = completed_msg.chat_id
                            if response is not None:
                                await self.bus.publish_outbound(response)
                                completed_channel = response.channel
                                completed_chat_id = response.chat_id
                            elif completed_msg.channel == "cli":
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=completed_msg.channel,
                                    chat_id=completed_msg.chat_id,
                                    content="",
                                    metadata=completed_msg.metadata or {},
                                ))
                            await self._runtime_events().turn_completed(
                                channel=completed_channel,
                                chat_id=completed_chat_id,
                                session_key=session_key,
                                metadata=completed_msg.metadata,
                            )
                            for _, coordinator in self._automation_turn_coordinators:
                                coordinator.complete(msg, response=response)
                        finally:
                            deferred_messages.extend(self._drain_pending_queue(pending))
                except asyncio.CancelledError:
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=asyncio.CancelledError())
                    logger.info("Task cancelled for session {}", session_key)
                    # Preserve partial context from the interrupted turn so
                    # the user does not lose tool results and assistant
                    # messages accumulated before /stop.  The checkpoint was
                    # already persisted to session metadata by
                    # _emit_checkpoint during tool execution; materializing
                    # it into session history now makes it visible in the
                    # next conversation turn.
                    try:
                        key = self._effective_session_key(msg)
                        session = self.sessions.get_or_create(key)
                        if self._restore_runtime_checkpoint(session):
                            self._clear_pending_user_turn(session)
                            self.sessions.save(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                except Exception as exc:
                    logger.exception("Error processing message for session {}", session_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
                    if not turn_continuation.internal_continuation_pending(msg.metadata):
                        await self._runtime_events().turn_completed(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            session_key=session_key,
                            metadata=msg.metadata,
                        )
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=exc)
                finally:
                    await self._republish_pending_messages(
                        deferred_messages,
                        session_key=session_key,
                    )
                    if (
                        not chain_complete
                        or not turn_continuation.internal_continuation_pending(
                            completed_msg.metadata
                        )
                    ):
                        await self._runtime_events().run_status_changed(
                            completed_msg,
                            session_key,
                            "idle",
                        )
                        self._runtime_events().clear_turn(session_key)
                    await self._publish_next_deferred_automation_turn(session_key)
        finally:
            if pending is None:
                await self._runtime_events().run_status_changed(
                    msg, session_key, "idle"
                )
                self._runtime_events().clear_turn(session_key)
                await self._publish_next_deferred_automation_turn(session_key)

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        await agent_context.close_mcp(self)

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_system_message(
        self,
        msg: InboundMessage,
        *,
        runtime: LLMRuntime,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
    ) -> OutboundMessage | None:
        """Process a system inbound message (e.g. subagent announce)."""
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        self._runtime_events().record_turn_runtime(key, runtime)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if self._restore_pending_user_turn(session):
            self.sessions.save(session)

        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            runtime=runtime,
            replay_max_messages=replay_max_messages_for_context(
                runtime.context_window_tokens
            ),
        )
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and self._persist_subagent_followup(session, msg):
            logger.debug("Subagent result persisted for session {}", key)
            self.sessions.save(session)
        current_role = "assistant" if is_subagent else "user"
        _hist_kwargs: dict[str, Any] = {
            "max_messages": replay_max_messages_for_context(runtime.context_window_tokens),
            "max_tokens": self._replay_token_budget(runtime),
            "extend_to_user": is_subagent,
        }
        history = session.get_history(**_hist_kwargs)
        workspace_scope = self.workspace_scopes.for_message(msg, session.metadata)

        messages = self.context.build_messages(
            history=history,
            current_message="" if is_subagent else msg.content,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
            sender_id=msg.sender_id,
            session_summary=None,
            session_metadata=session.metadata,
            workspace=workspace_scope.project_path,
            session_key=key,
            unified_session=self._unified_session,
        )
        t_wall = time.time()
        final_content, _, all_msgs, stop_reason, _ = await self._run_agent_loop(
            messages, session=session, channel=channel, chat_id=chat_id,
            runtime=runtime,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            original_user_text=None,
            pending_queue=pending_queue,
            hook_factories=hook_factories,
        )
        wall_done = time.time()
        latency_ms = max(0, int((wall_done - t_wall) * 1000))
        self._save_turn(session, all_msgs, 1 + len(history), turn_latency_ms=latency_ms)
        self._runtime_events().record_turn_latency(key, latency_ms)
        session.enforce_file_cap(
            on_archive=partial(self.context.memory.raw_archive, session_key=key)
        )
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        self._schedule_background(
            self.consolidator.maybe_consolidate_by_tokens(
                session,
                runtime=runtime,
                replay_max_messages=replay_max_messages_for_context(
                    runtime.context_window_tokens
                ),
            )
        )
        content = final_content or "Background task completed."
        outbound_metadata: dict[str, Any] = {}
        if channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        if origin_message_id := msg.metadata.get("origin_message_id"):
            outbound_metadata["origin_message_id"] = origin_message_id
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata=outbound_metadata,
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_superseded_response: Callable[[str], Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        runtime: LLMRuntime | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        if runtime is None:
            runtime = self.llm_runtime()

        if msg.channel == "system":
            return await self._process_system_message(
                msg,
                runtime=runtime,
                session_key=session_key,
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                pending_queue=pending_queue,
                hook_factories=hook_factories,
            )

        key = session_key or msg.session_key
        t0 = time.time()
        ctx = TurnContext(
            msg=msg,
            session=None,
            session_key=key,
            state=TurnState.RESTORE,
            turn_id=f"{key}:{time.time_ns()}",
            runtime=runtime,
            original_user_text=(
                None
                if turn_continuation.internal_continuation_inbound(msg.metadata)
                else msg.content
            ),
            turn_wall_started_at=t0,
            visible_run_started_at=turn_continuation.internal_continuation_run_started_at(
                msg.metadata,
            ),
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_superseded_response=on_superseded_response,
            pending_queue=pending_queue,
            ephemeral=ephemeral,
            run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            hooks=list(hooks or []),
            hook_factories=list(hook_factories or []),
            tools=tools,
        )

        while ctx.state is not TurnState.DONE:
            handler_name = f"_state_{ctx.state.name.lower()}"
            handler = getattr(self, handler_name, None)
            if handler is None:
                raise RuntimeError(f"Missing state handler for {ctx.state}")

            t0 = time.perf_counter()
            try:
                event = await handler(ctx)
            except Exception:
                duration = (time.perf_counter() - t0) * 1000
                ctx.trace.append(
                    StateTraceEntry(
                        state=ctx.state,
                        started_at=t0,
                        duration_ms=duration,
                        event="",
                        error="exception",
                    )
                )
                raise

            duration = (time.perf_counter() - t0) * 1000
            ctx.trace.append(
                StateTraceEntry(
                    state=ctx.state,
                    started_at=t0,
                    duration_ms=duration,
                    event=event,
                )
            )
            logger.debug(
                "[turn {}] State {} took {:.1f}ms -> event {}",
                ctx.turn_id,
                ctx.state.name,
                duration,
                event,
            )

            next_state = self._TRANSITIONS.get((ctx.state, event))
            if next_state is None:
                raise RuntimeError(
                    f"[turn {ctx.turn_id}] No transition from {ctx.state} "
                    f"on event {event!r}"
                )
            ctx.state = next_state

        logger.debug(
            "[turn {}] Turn completed after {} states",
            ctx.turn_id,
            len(ctx.trace),
        )
        return ctx.outbound

    def _assemble_outbound(
        self,
        msg: InboundMessage,
        final_content: str,
        all_msgs: list[dict[str, Any]],
        stop_reason: str,
        had_injections: bool,
        on_stream: Callable[[str], Awaitable[None]] | None,
        *,
        turn_latency_ms: int | None = None,
    ) -> OutboundMessage | None:
        """Assemble the final outbound message from turn results."""
        # MessageTool suppression
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        event = None
        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason not in {"error", "tool_error"}:
            event = StreamedResponseEvent()
        if turn_latency_ms is not None:
            meta["latency_ms"] = int(turn_latency_ms)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            event=event,
            metadata=meta,
        )

    async def _state_restore(self, ctx: TurnContext) -> TurnState:
        """Restore checkpoint / pending user turn; extract documents."""
        msg = ctx.msg

        if msg.media:
            new_content, image_only = self._prepare_message_media(msg.content, msg.media)
            ctx.msg = dataclasses.replace(msg, content=new_content, media=image_only)
            msg = ctx.msg

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Session is already fetched by the caller (_process_message) but
        # ensure it exists in case this handler is invoked independently.
        if ctx.session is None:
            ctx.session = self.sessions.get_or_create(ctx.session_key)
        await self._runtime_events().session_turn_started(msg, ctx.session_key)
        self.workspace_scopes.persist_message_scope(ctx.session, msg)

        if self._restore_runtime_checkpoint(ctx.session):
            self.sessions.save(ctx.session)
        if self._restore_pending_user_turn(ctx.session):
            self.sessions.save(ctx.session)

        return "ok"

    def _prepare_message_media(self, content: str, media: list[str]) -> tuple[str, list[str]]:
        if self._should_extract_document_text():
            return extract_documents(content, media)
        return reference_non_image_attachments(content, media)

    def _should_extract_document_text(self) -> bool:
        if self.channels_config is None:
            return True
        return self.channels_config.extract_document_text

    async def _state_compact(self, ctx: TurnContext) -> str:
        ctx.pending_summary = None
        return "ok"

    async def _state_build(self, ctx: TurnContext) -> str:
        replay_max_messages = replay_max_messages_for_context(
            ctx.runtime.context_window_tokens
        )
        if not ctx.ephemeral:
            await self.consolidator.maybe_consolidate_by_tokens(
                ctx.session,
                runtime=ctx.runtime,
                replay_max_messages=replay_max_messages,
            )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        _hist_kwargs: dict[str, Any] = {
            "max_messages": replay_max_messages,
            "max_tokens": self._replay_token_budget(ctx.runtime),
            "extend_to_user": False,
        }
        ctx.history = ctx.session.get_history(**_hist_kwargs)
        self._runtime_events().record_turn_runtime(
            ctx.session_key,
            ctx.runtime,
        )

        ctx.request_context = self._request_context_for_turn(ctx)
        ctx.runtime_context_blocks = await self._resolve_runtime_context_for_turn(ctx)
        ctx.initial_messages = self._build_initial_messages(
            ctx.msg,
            ctx.session,
            ctx.history,
            ctx.pending_summary,
            include_memory_recent_history=not ctx.ephemeral,
            runtime_context_blocks=ctx.runtime_context_blocks,
        )
        ctx.user_persisted_early = self._persist_user_message_early(
            ctx.msg,
            ctx.session,
            runtime_context_blocks=ctx.runtime_context_blocks,
        )

        if ctx.on_progress is None:
            ctx.on_progress = await self._build_bus_progress_callback(ctx.msg)
        if ctx.on_retry_wait is None:
            ctx.on_retry_wait = await self._build_retry_wait_callback(ctx.msg)

        return "ok"

    async def _state_run(self, ctx: TurnContext) -> str:
        if ctx.visible_run_started_at is None:
            ctx.visible_run_started_at = time.time()
        await self._runtime_events().run_status_changed(
            ctx.msg,
            ctx.session_key,
            "running",
            started_at=ctx.visible_run_started_at,
        )
        result = await self._run_agent_loop(
            ctx.initial_messages,
            runtime=ctx.runtime,
            on_progress=ctx.on_progress,
            on_stream=ctx.on_stream,
            on_stream_end=ctx.on_stream_end,
            on_superseded_response=ctx.on_superseded_response,
            on_retry_wait=ctx.on_retry_wait,
            session=ctx.session,
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            message_id=ctx.msg.metadata.get("message_id"),
            metadata=ctx.msg.metadata,
            session_key=ctx.session_key,
            original_user_text=ctx.original_user_text,
            pending_queue=ctx.pending_queue,
            ephemeral=ctx.ephemeral,
            run_extra_hooks_for_ephemeral=ctx.run_extra_hooks_for_ephemeral,
            hooks=ctx.hooks,
            hook_factories=ctx.hook_factories,
            turn_scopes=ctx.turn_scopes,
            tools=ctx.tools,
            request_context=ctx.request_context,
        )
        final_content, tools_used, all_msgs, stop_reason, had_injections = result
        ctx.final_content = final_content
        ctx.tools_used = tools_used
        ctx.all_messages = all_msgs
        ctx.stop_reason = stop_reason
        ctx.had_injections = had_injections
        await turn_continuation.maybe_continue_turn(ctx)
        return "ok"

    async def _state_save(self, ctx: TurnContext) -> str:
        turn_continuation.prepare_save_boundary(ctx)

        if (
            (ctx.final_content is None or not ctx.final_content.strip())
            and not ctx.suppress_response
        ):
            ctx.final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        latency_started_at = (
            ctx.visible_run_started_at
            if turn_continuation.internal_continuation_inbound(ctx.msg.metadata)
            and ctx.visible_run_started_at is not None
            else ctx.turn_wall_started_at
        )
        ctx.turn_latency_ms = max(0, int((time.time() - latency_started_at) * 1000))
        self._save_turn(
            ctx.session, ctx.all_messages, ctx.save_skip,
            turn_latency_ms=ctx.turn_latency_ms,
        )
        self._runtime_events().record_turn_latency(
            ctx.session_key,
            ctx.turn_latency_ms,
        )
        if not ctx.ephemeral:
            ctx.session.enforce_file_cap(
                on_archive=partial(self.context.memory.raw_archive, session_key=ctx.session_key)
            )
            self._schedule_background(
                self.consolidator.maybe_consolidate_by_tokens(
                    ctx.session,
                    runtime=ctx.runtime,
                    replay_max_messages=replay_max_messages_for_context(
                        ctx.runtime.context_window_tokens
                    ),
                )
            )
        self._clear_pending_user_turn(ctx.session)
        self._clear_runtime_checkpoint(ctx.session)
        self.sessions.save(ctx.session)
        return "ok"

    async def _state_respond(self, ctx: TurnContext) -> str:
        if ctx.suppress_response:
            ctx.outbound = None
            return "ok"
        ctx.outbound = self._assemble_outbound(
            ctx.msg,
            ctx.final_content,
            ctx.all_messages,
            ctx.stop_reason,
            ctx.had_injections,
            ctx.on_stream,
            turn_latency_ms=ctx.turn_latency_ms,
        )
        if ctx.ephemeral and ctx.outbound is not None:
            ctx.outbound.metadata["_stop_reason"] = ctx.stop_reason
        return "ok"

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        *,
        turn_latency_ms: int | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        declared_tool_call_ids = {
            str(tc["id"])
            for m in session.messages
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls") or []
            if isinstance(tc, dict) and tc.get("id")
        }
        last_assistant_idx: int | None = None
        for m in messages[skip:]:
            entry = dict(m)
            internal_meta = entry.pop("_meta", None)
            runtime_context_meta = (
                internal_meta.get(RUNTIME_CONTEXT_MESSAGE_META)
                if isinstance(internal_meta, dict)
                else None
            )
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                tool_call_id = entry.get("tool_call_id")
                if not tool_call_id or str(tool_call_id) not in declared_tool_call_ids:
                    # Undeclared tool results corrupt future provider requests.
                    logger.warning(
                        "Dropping orphaned tool result {} from session {} during persistence",
                        tool_call_id or "(missing id)",
                        session.key,
                    )
                    continue
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        # Preserve the tool_call/result pair after block filtering.
                        filtered = [
                            {"type": "text", "text": "[tool result omitted during persistence]"}
                        ]
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content)
                    if not filtered:
                        continue
                    entry["content"] = filtered
                if isinstance(runtime_context_meta, dict):
                    entry[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            if role == "assistant":
                last_assistant_idx = len(session.messages) - 1
                declared_tool_call_ids.update(
                    str(tc["id"])
                    for tc in entry.get("tool_calls") or []
                    if isinstance(tc, dict) and tc.get("id")
                )
        if turn_latency_ms is not None and last_assistant_idx is not None:
            session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
        session.updated_at = datetime.now()

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_superseded_response: Callable[[str], Awaitable[None]] | None = None,
        ephemeral: bool = False,
        _run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        persist_user_message: bool = True,
        runtime: LLMRuntime | None = None,
        permission_context: dict[str, Any] | None = None,
        workspace_scope: dict[str, str] | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        metadata: dict[str, Any] = dict(message_metadata or {})
        if permission_context:
            metadata["permission_context"] = dict(permission_context)
        if workspace_scope:
            metadata[WORKSPACE_SCOPE_METADATA_KEY] = dict(workspace_scope)
        if not persist_user_message:
            metadata[turn_continuation.SKIP_USER_PERSIST_META] = True
        msg = InboundMessage(
            channel=channel, sender_id=sender_id, chat_id=chat_id,
            content=content, media=media or [], metadata=metadata,
        )
        deferred_messages: list[InboundMessage] = []
        completed_msg = msg
        try:
            # Share one lock and one pending queue across the initial direct
            # turn and every invisible continuation slice it schedules.
            async with self._session_lock(session_key):
                async with self._pending_turn_queue(session_key) as pending:
                    kwargs: dict[str, Any] = {
                        "session_key": session_key,
                        "on_progress": on_progress,
                        "on_stream": on_stream,
                        "on_stream_end": on_stream_end,
                        "ephemeral": ephemeral,
                    }
                    process_parameters = inspect.signature(self._process_message).parameters
                    accepts_extra = any(
                        item.kind == inspect.Parameter.VAR_KEYWORD
                        for item in process_parameters.values()
                    )
                    if accepts_extra or "pending_queue" in process_parameters:
                        kwargs["pending_queue"] = pending
                    if accepts_extra or "on_superseded_response" in process_parameters:
                        kwargs["on_superseded_response"] = on_superseded_response
                    if _run_extra_hooks_for_ephemeral:
                        kwargs["run_extra_hooks_for_ephemeral"] = True
                    if hooks is not None:
                        kwargs["hooks"] = hooks
                    if hook_factories is not None:
                        kwargs["hook_factories"] = hook_factories
                    if tools is not None:
                        kwargs["tools"] = tools
                    if runtime is not None:
                        kwargs["runtime"] = runtime

                    async def _process(current: InboundMessage) -> OutboundMessage | None:
                        return await self._process_message(current, **kwargs)

                    try:
                        response, completed_msg = (
                            await self._process_internal_continuation_chain(
                                msg,
                                pending=pending,
                                process=_process,
                                deferred_messages=deferred_messages,
                            )
                        )
                    finally:
                        deferred_messages.extend(self._drain_pending_queue(pending))
                return response
        finally:
            # The queue registration and session lock have both been released,
            # so ordinary injected user messages can safely re-enter via the bus.
            await self._republish_pending_messages(
                deferred_messages,
                session_key=session_key,
            )
            await self._runtime_events().run_status_changed(
                completed_msg,
                session_key,
                "idle",
            )
            self._runtime_events().clear_turn(session_key)
