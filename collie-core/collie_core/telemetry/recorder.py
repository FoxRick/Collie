"""Fire-and-forget run telemetry (PR 1 — run records).

The recorder is deliberately defensive: every write is wrapped so a
telemetry failure can never break or slow down an agent turn.
"""

from __future__ import annotations

import json
import re
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
    # OpenAI/Anthropic-style sk- keys
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
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

    # -- tool lifecycle -------------------------------------------------------

    def start_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        input_summary: str | None = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> None:
        try:
            self._db.record_tool_event(
                tool_id=tool_id,
                turn_id=turn_id,
                tool_name=tool_name,
                input_summary=input_summary,
                action=action,
                resource=sanitize_text(resource) if resource else None,
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
                error_message=(
                    sanitize_text(error_message) if error_message else None
                ),
                latency_ms=latency_ms,
                finished_at=utc_now(),
            )
        except Exception:
            logger.exception("telemetry finish_tool failed")

    def blocked_tool(
        self,
        *,
        tool_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        reason: str | None,
        input_summary: str | None = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> None:
        """Record a tool that never executed (denied / prep / lookup block).

        These paths return before ``before_execute_tool`` fires, so the
        row is written complete in a single insert.
        """
        try:
            now = utc_now()
            self._db.record_tool_event(
                tool_id=tool_id,
                turn_id=turn_id,
                tool_name=tool_name,
                status=status,
                error_message=(
                    sanitize_text(reason) if reason else None
                ),
                input_summary=input_summary,
                action=action,
                resource=sanitize_text(resource) if resource else None,
                started_at=now,
                finished_at=now,
            )
        except Exception:
            logger.exception("telemetry blocked_tool failed")
