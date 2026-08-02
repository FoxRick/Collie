"""Fire-and-forget run telemetry (PR 1 — run records).

The recorder is deliberately defensive: every write is wrapped so a
telemetry failure can never break or slow down an agent turn.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from collie_core.db import CollieDB, utc_now
from collie_core.permissions.classifier import redact_parameters

TURN_INPUT_LIMIT = 500
TOOL_OUTPUT_LIMIT = 1000


def summarize(value: Any, limit: int) -> str | None:
    """Redact and truncate a value for telemetry storage.

    Dicts are run through ``redact_parameters`` (secrets become
    ``[redacted]``); everything else is stringified. Output never exceeds
    ``limit`` characters. Core invariant: secrets never reach telemetry.
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
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."
    except Exception:
        logger.exception("telemetry summarize failed")
        return None


class RunRecorder:
    """Best-effort writer of turn/tool telemetry rows."""

    def __init__(self, db: CollieDB) -> None:
        self._db = db

    def provider_model(self) -> tuple[str | None, str | None]:
        try:
            provider = self._db.get_setting("provider.name") or None
            model = self._db.get_setting("provider.model") or None
            return provider, model
        except Exception:
            logger.exception("telemetry provider resolution failed")
            return None, None

    # -- turn lifecycle -------------------------------------------------------

    def start_turn(
        self,
        *,
        turn_id: str,
        session_key: str | None = None,
        conversation_id: str | None = None,
        turn_kind: str = "chat",
    ) -> None:
        try:
            provider, model = self.provider_model()
            self._db.record_turn_event(
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
        try:
            self._db.record_turn_event(
                turn_id=turn_id,
                status=status,
                error_message=error_message,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                tool_count=tool_count,
                finished_at=utc_now(),
            )
        except Exception:
            logger.exception("telemetry finish_turn failed")

    # -- tool lifecycle -------------------------------------------------------

    def start_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        input_summary: str | None = None,
    ) -> None:
        try:
            self._db.record_tool_event(
                tool_id=tool_id,
                turn_id=turn_id,
                tool_name=tool_name,
                input_summary=input_summary,
                status="running",
                started_at=utc_now(),
            )
        except Exception:
            logger.exception("telemetry start_tool failed")

    def finish_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        output_summary: str | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        try:
            self._db.record_tool_event(
                tool_id=tool_id,
                turn_id=turn_id,
                tool_name=tool_name,
                status=status,
                output_summary=output_summary,
                error_message=error_message,
                latency_ms=latency_ms,
                finished_at=utc_now(),
            )
        except Exception:
            logger.exception("telemetry finish_tool failed")
