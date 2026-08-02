"""Fire-and-forget run telemetry (PR 1 — run records).

The recorder is deliberately defensive: every write is enqueued onto a
dedicated writer thread and **never awaited**, so telemetry can neither
break nor slow down an agent turn. A single FIFO consumer preserves write
ordering (start before finish), and every write is wrapped so failures
are logged and swallowed. All redaction/sanitization/truncation happens
inside the writer thread — the event loop only enqueues.
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
            text = json.dumps(
                redact_parameters(value), ensure_ascii=False, default=str
            )
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
    FIFO order. Use ``for_db`` to share one recorder per database (loop
    rebuilds reuse the same writer thread).
    """

    _RECORDERS: "weakref.WeakKeyDictionary[CollieDB, RunRecorder]" = (
        weakref.WeakKeyDictionary()
    )

    @classmethod
    def for_db(cls, db: CollieDB) -> "RunRecorder":
        """Return the shared recorder for a database (creates on first use)."""
        recorder = cls._RECORDERS.get(db)
        if recorder is None:
            recorder = cls(db)
            cls._RECORDERS[db] = recorder
        return recorder

    def __init__(self, db: CollieDB) -> None:
        self._db = db
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
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
            try:
                item()
            except Exception:
                logger.exception("telemetry write failed (swallowed)")

    def _enqueue(self, fn: Callable[[], None]) -> None:
        try:
            self._queue.put_nowait(fn)
        except Exception:
            logger.exception("telemetry enqueue failed (swallowed)")

    def flush(self, timeout: float = 5.0) -> None:
        """Block until all previously enqueued writes have been applied."""
        done = threading.Event()
        self._enqueue(done.set)
        done.wait(timeout)

    def shutdown(self) -> None:
        """Stop the writer thread (drains remaining writes first)."""
        try:
            self._queue.put(None)
            self._thread.join(timeout=1.0)
        except Exception:
            logger.exception("telemetry shutdown failed")

    # -- turn lifecycle -------------------------------------------------------

    def start_turn(
        self,
        *,
        turn_id: str,
        session_key: str | None = None,
        conversation_id: str | None = None,
        turn_kind: str = "chat",
    ) -> None:
        db = self._db

        def _write() -> None:
            try:
                provider, model = self.provider_model()
                db.record_turn_event(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    session_key=session_key,
                    turn_kind=turn_kind,
                    provider=provider,
                    model=model,
                    status="running",
                    started_at=utc_now(),
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
    ) -> None:
        db = self._db

        def _write() -> None:
            try:
                db.record_turn_event(
                    turn_id=turn_id,
                    status=status,
                    error_message=(
                        sanitize_text(error_message) if error_message else None
                    ),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    tool_count=tool_count,
                    finished_at=utc_now(),
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
                    started_at=utc_now(),
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

        def _write() -> None:
            try:
                db.record_tool_event(
                    tool_id=tool_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    status=status,
                    output_summary=(
                        summarize(result, TOOL_OUTPUT_LIMIT)
                        if status == "ok"
                        else None
                    ),
                    error_message=(
                        sanitize_text(error_message) if error_message else None
                    ),
                    latency_ms=latency_ms,
                    finished_at=utc_now(),
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

        def _write() -> None:
            try:
                now = utc_now()
                db.record_tool_event(
                    tool_id=tool_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    status=status,
                    error_message=(
                        sanitize_text(reason) if reason else None
                    ),
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
