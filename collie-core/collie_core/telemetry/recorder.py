"""Fire-and-forget run telemetry (PR 1 — run records).

The recorder is deliberately defensive: every write is enqueued onto a
dedicated writer thread and **never awaited**, so telemetry can neither
break nor slow down an agent turn. A single FIFO consumer preserves write
ordering (start before finish), and every write is wrapped so failures
are logged and swallowed.

Guarantees / lifecycle:
- ``for_db`` shares one recorder per database; ``active_for`` looks it up
  without creating one.
- ``suspend()`` drops queued + future writes (used by ``clear_all`` so a
  wipe can never be resurrected by pending telemetry); ``resume()``
  re-enables recording.
- ``flush()`` blocks until previously enqueued writes are applied;
  ``shutdown()`` flushes, stops the writer thread, and unregisters the
  recorder so closed databases are not retained.
- The queue is bounded (``MAX_QUEUED_WRITES``); overflow and suspended/
  stopped writes are dropped with a diagnostic counter.
- Timestamps and provider/model are captured at enqueue time (event
  time), so backlog never corrupts chronological evidence.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import weakref
from collections.abc import Callable
from typing import Any

from loguru import logger

from collie_core.db import CollieDB, utc_now
from collie_core.permissions.classifier import redact_parameters

TURN_INPUT_LIMIT = 500
TOOL_OUTPUT_LIMIT = 1000

# Cap on in-flight telemetry writes. A healthy turn enqueues a handful;
# under a stuck database the queue drops (never blocks, never grows).
MAX_QUEUED_WRITES = 500

# Secret-shaped patterns scrubbed from ANY telemetry text (outputs, errors,
# resources). Keys are already handled by ``redact_parameters``; this layer
# catches secrets that appear inside string values.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authorization headers and bare Bearer tokens
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # OpenAI/Anthropic-style sk- keys (hyphens allowed: sk-proj-, sk-ant-api03-)
    re.compile(r"\bsk-[A-Za-z0-9-]{16,}"),
    # Google API keys (AIza...) and OAuth tokens (ya29...)
    re.compile(r"\bAIza[0-9A-Za-z_\-]{28,}"),
    re.compile(r"\bya29\.[A-Za-z0-9_\-\.]{10,}"),
    # GitHub tokens
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    # AWS access key ids
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Slack tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    # Generic key=value / key: value with a long value
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|credential)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=!@#$%^&*-]{12,}"
    ),
    # PEM private keys
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def sanitize_text(text: str) -> str:
    """Scrub secret-shaped values from arbitrary telemetry text."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def summarize(value: Any, limit: int) -> str | None:
    """Redact and truncate a value for telemetry storage.

    Dicts are run through ``redact_parameters`` (secret-named keys become
    ``[redacted]``); everything is then passed through ``sanitize_text`` so
    secret-shaped values inside innocent keys or bare strings never reach
    the database. Output never exceeds ``limit`` characters. Core
    invariant: secrets never reach telemetry.
    """
    try:
        if value is None:
            return None
        if isinstance(value, dict):
            text = json.dumps(redact_parameters(value), ensure_ascii=False, default=str)
        else:
            text = str(value)
        text = sanitize_text(text)
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."
    except Exception:
        logger.exception("telemetry summarize failed")
        return None


class RunRecorder:
    """Best-effort writer of turn/tool telemetry rows.

    Thread-safe and fire-and-forget: the caller enqueues a write and
    returns immediately; a single daemon writer thread applies writes in
    FIFO order.
    """

    _RECORDERS: weakref.WeakKeyDictionary[CollieDB, RunRecorder] = weakref.WeakKeyDictionary()

    @classmethod
    def for_db(cls, db: CollieDB) -> RunRecorder:
        """Return the shared live recorder for a database (creates one)."""
        recorder = cls._RECORDERS.get(db)
        if recorder is None or recorder._stopped:
            recorder = cls(db)
            cls._RECORDERS[db] = recorder
        return recorder

    @classmethod
    def active_for(cls, db: CollieDB) -> RunRecorder | None:
        """Return the shared live recorder for a database, if any."""
        recorder = cls._RECORDERS.get(db)
        if recorder is not None and not recorder._stopped:
            return recorder
        return None

    def __init__(self, db: CollieDB) -> None:
        self._db = db
        self._stopped = False
        self._suspended = False
        self.dropped_writes = 0
        self.last_turn_stats: dict[str, int] = {}
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=MAX_QUEUED_WRITES)
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="collie-telemetry",
            daemon=True,
        )
        self._thread.start()

    # -- writer thread -------------------------------------------------------

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self._suspended or self._stopped:
                self.dropped_writes += 1
                continue
            try:
                item()
            except Exception:
                logger.exception("telemetry write failed (swallowed)")

    def _enqueue(self, fn: Callable[[], None]) -> None:
        if self._stopped or self._suspended:
            self.dropped_writes += 1
            return
        try:
            self._queue.put_nowait(fn)
        except queue.Full:
            self.dropped_writes += 1
            if self.dropped_writes == 1:
                logger.warning(
                    "telemetry queue full — dropping writes (bounded at {})",
                    MAX_QUEUED_WRITES,
                )

    # -- lifecycle ------------------------------------------------------------

    def suspend(self) -> None:
        """Drop queued and future writes (used by ``clear_all``)."""
        self._suspended = True

    def resume(self) -> None:
        """Re-enable recording after a suspend."""
        self._suspended = False

    def suspend_and_drain(self) -> None:
        """Suspend and wait until the queue is empty (drops everything)."""
        self.suspend()
        self.flush()

    def flush(self, timeout: float = 5.0) -> None:
        """Block until all previously enqueued writes have been applied."""
        if self._stopped:
            return
        done = threading.Event()
        try:
            self._queue.put_nowait(done.set)
        except queue.Full:
            return
        done.wait(timeout)

    def shutdown(self) -> None:
        """Flush pending writes, stop the writer, unregister the recorder."""
        if self._stopped:
            return
        self.flush()
        self._stopped = True
        try:
            self._queue.put(None)
            self._thread.join(timeout=2.0)
        except Exception:
            logger.exception("telemetry shutdown failed")
        # Break the registry -> recorder -> database retention cycle so a
        # closed database can be garbage-collected.
        try:
            if self._RECORDERS.get(self._db) is self:
                del self._RECORDERS[self._db]
        except Exception:
            logger.exception("telemetry registry cleanup failed")

    # -- turn lifecycle -------------------------------------------------------

    def start_turn(
        self,
        *,
        turn_id: str,
        session_key: str | None = None,
        conversation_id: str | None = None,
        turn_kind: str = "chat",
        prompt_hash: str | None = None,
        tool_schema_hash: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        db = self._db
        # Event-time snapshot: captured before enqueueing so backlog cannot
        # shift timestamps or provider/model into the future.
        started_at = utc_now()
        provider, model = self.provider_model()

        def _write() -> None:
            try:
                db.record_turn_event(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    session_key=session_key,
                    turn_kind=turn_kind,
                    provider=provider,
                    model=model,
                    status="running",
                    started_at=started_at,
                    prompt_hash=prompt_hash,
                    tool_schema_hash=tool_schema_hash,
                    config_hash=config_hash,
                )
            except Exception:
                logger.exception("telemetry start_turn failed")

        self._enqueue(_write)

    def finish_turn(
        self,
        *,
        turn_id: str,
        status: str,
        error_message: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int | None = None,
        tool_count: int = 0,
        model_calls: int = 0,
        retries: int = 0,
        no_action_turns: int = 0,
    ) -> None:
        db = self._db
        finished_at = utc_now()
        # Event-time snapshot for the headless result document (never
        # persisted — the run record stores tool_count; the call counters
        # are engineering evidence for the bench harness).
        self.last_turn_stats = {
            "model_calls": model_calls,
            "tool_calls": tool_count,
            "retries": retries,
            "no_action_turns": no_action_turns,
        }

        def _write() -> None:
            try:
                db.record_turn_event(
                    turn_id=turn_id,
                    status=status,
                    error_message=(sanitize_text(error_message) if error_message else None),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    tool_count=tool_count,
                    finished_at=finished_at,
                )
            except Exception:
                logger.exception("telemetry finish_turn failed")

        self._enqueue(_write)

    # -- tool lifecycle -------------------------------------------------------

    def start_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        params: Any = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> None:
        db = self._db
        started_at = utc_now()

        def _write() -> None:
            try:
                db.record_tool_event(
                    tool_id=tool_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    input_summary=summarize(params, TURN_INPUT_LIMIT),
                    action=action,
                    resource=sanitize_text(resource) if resource else None,
                    status="running",
                    started_at=started_at,
                )
            except Exception:
                logger.exception("telemetry start_tool failed")

        self._enqueue(_write)

    def finish_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        result: Any = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        db = self._db
        finished_at = utc_now()

        def _write() -> None:
            try:
                db.record_tool_event(
                    tool_id=tool_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    status=status,
                    output_summary=(
                        summarize(result, TOOL_OUTPUT_LIMIT) if status == "ok" else None
                    ),
                    error_message=(sanitize_text(error_message) if error_message else None),
                    latency_ms=latency_ms,
                    finished_at=finished_at,
                )
            except Exception:
                logger.exception("telemetry finish_tool failed")

        self._enqueue(_write)

    def blocked_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        reason: str | None = None,
        params: Any = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> None:
        """Record a tool that never executed (denied / prep / lookup block).

        These paths return before ``before_execute_tool`` fires, so the
        row is written complete in a single insert.
        """
        db = self._db
        now = utc_now()

        def _write() -> None:
            try:
                db.record_tool_event(
                    tool_id=tool_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    status=status,
                    error_message=(sanitize_text(reason) if reason else None),
                    input_summary=summarize(params, TURN_INPUT_LIMIT),
                    action=action,
                    resource=sanitize_text(resource) if resource else None,
                    started_at=now,
                    finished_at=now,
                )
            except Exception:
                logger.exception("telemetry blocked_tool failed")

        self._enqueue(_write)

    def provider_model(self) -> tuple[str | None, str | None]:
        try:
            provider = self._db.get_setting("provider.name") or None
            model = self._db.get_setting("provider.model") or None
            return provider, model
        except Exception:
            logger.exception("telemetry provider resolution failed")
            return None, None
