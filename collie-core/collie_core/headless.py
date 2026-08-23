"""Headless engine mode — one task, one JSON result document, exit.

Internal engineering capability for the evaluation lab (see
``docs/engineering/evaluation/benchmarking-and-prompt-optimization.md``).
Runs exactly one task through the real agent loop against an isolated
``COLLIE_HOME``, prints ONE JSON document to stdout, and exits. It is NOT
a user-facing CLI: no ``[project.scripts]`` entry point exists
(pyproject: "No CLI entry points"); ``python -m collie_core.headless`` is
the entry.

Parity rules (non-negotiable):
- This module only *calls* ``CollieRuntime`` methods (``_build_loop``,
  ``_configure_provider_candidate``, ``_chat``, ``_conversation_target``).
  It never re-composes the loop, tools, prompts, permissions, or config.
- ``runtime.run()`` is never called: the IPC server, scheduler, and
  reminder loop stay off; the lifecycle is driven manually and torn down
  in the same order ``run()`` uses.

Exit codes: 0 ok, 1 task/engine error, 2 timeout, 3 usage/config error.

The API key MUST come from the environment (``--api-key-env``, default
``COLLIE_PROVIDER_API_KEY``) — never from argv, and it is never printed
or logged. ``build_config`` reads ``COLLIE_<PROVIDER>_API_KEY`` /
``COLLIE_PROVIDER_API_KEY`` directly, and ``_configure_provider_candidate``
takes the key from the candidate dict (the exact UI path).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from collie_core.db import CollieDB
from collie_core.runtime import CollieRuntime
from collie_core.telemetry.prompt_hashes import (
    current_config_hash,
    current_tool_schema_hash,
)
from collie_core.telemetry.recorder import RunRecorder, sanitize_text

__all__ = ["main", "run_one"]

_SCHEMA_VERSION = 1
_HARNESS = "collie"

# Exit codes — stable contract (see module docstring).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2
EXIT_USAGE = 3

# The engine clamps tool iterations to 1..2000 in ``build_config``; never
# let a garbage flag value reach the engine.
MAX_ITERATIONS_MIN = 1
MAX_ITERATIONS_MAX = 2000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m collie_core.headless",
        description=(
            "Run one task through the real Collie agent loop and print one "
            "JSON result document. Internal engineering capability only."
        ),
    )
    parser.add_argument("--task", required=True, help="The task/user prompt to run.")
    parser.add_argument(
        "--home",
        default=None,
        help=(
            "COLLIE_HOME directory (created if missing). Defaults to "
            "$COLLIE_HOME or a fresh temporary directory removed after the run."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id (e.g. deepseek/deepseek-chat). Defaults to the provider record / settings.",
    )
    parser.add_argument(
        "--provider",
        default="deepseek",
        help="Provider name (e.g. deepseek). Default: deepseek.",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="Custom OpenAI-compatible base URL. Defaults to the provider default.",
    )
    parser.add_argument(
        "--api-key-env",
        default="COLLIE_PROVIDER_API_KEY",
        help="Env var holding the API key. Default: COLLIE_PROVIDER_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Wall-clock seconds for the whole turn. Default: 300.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="agent.max_tool_iterations (clamped 1..2000). Default: 50.",
    )
    parser.add_argument(
        "--approval-preset",
        choices=("allow", "ask", "deny"),
        default="allow",
        help=(
            "Local-write approval preset, wired through the SAME "
            "set_local_write_preset path as the product (never a bypass). "
            "Default: allow (the bench sandbox is disposable)."
        ),
    )
    parser.add_argument(
        "--session-key",
        default=None,
        help=(
            "Engine session key label. The engine resolves the canonical "
            "desktop session key from the conversation (collie:<id>) — the "
            "reported session_key is the resolved one, so it joins run records."
        ),
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Also write the result document to this file (stdout still gets it).",
    )
    return parser.parse_args(argv)


async def _noop_stream(_text: str) -> None:
    """Headless turns have no UI to stream to."""


async def _noop_progress(*_args: Any, **_kwargs: Any) -> None:
    """Headless turns have no UI to report progress to."""


def _git_commit() -> str:
    """Best-effort repo HEAD sha, or 'unknown' outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _resolve_home(home_flag: str | None) -> tuple[Path, bool]:
    """Resolve the bench home and report whether this process owns it."""
    root = home_flag or os.environ.get("COLLIE_HOME")
    owned = not root
    path = Path(root).expanduser() if root else Path(tempfile.mkdtemp(prefix="collie-bench-"))
    path.mkdir(parents=True, exist_ok=True)
    return path, owned


@contextlib.contextmanager
def _headless_home(home_flag: str | None):
    """Set ``COLLIE_HOME`` for one run and clean up only owned temp homes."""
    previous_home = os.environ.get("COLLIE_HOME")
    home, owned = _resolve_home(home_flag)
    os.environ["COLLIE_HOME"] = str(home)
    try:
        yield home
    finally:
        if previous_home is None:
            os.environ.pop("COLLIE_HOME", None)
        else:
            os.environ["COLLIE_HOME"] = previous_home
        if owned:
            shutil.rmtree(home, ignore_errors=True)


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Map provider usage keys to the stable contract keys (zero-fill)."""
    details = usage.get("prompt_tokens_details") or {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_tokens": int(
            details.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0
        ),
        "cache_write_tokens": int(
            details.get("cache_creation_input_tokens")
            or usage.get("cache_creation_input_tokens")
            or 0
        ),
    }


def _empty_document(
    task: str,
    provider: str,
    *,
    model: str | None = None,
    exit_state: str = "error",
    error: str | None = None,
) -> dict[str, Any]:
    """A complete schema document (all keys present, zero-filled)."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "harness": _HARNESS,
        "commit": _git_commit(),
        "model": model or "",
        "provider": provider,
        "task": task,
        "session_key": "",
        "conversation_id": "",
        "prompt_hash": None,
        "tool_schema_hash": None,
        "config_hash": None,
        "final_text": "",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
        "calls": {
            "model_calls": 0,
            "tool_calls": 0,
            "retries": 0,
            "no_action_turns": 0,
        },
        "latency_ms": 0,
        "exit_state": exit_state,
        "error": error,
    }


async def run_one(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Run one task through the real agent loop and return (exit_code, doc).

    ``COLLIE_HOME`` is set from ``--home`` / the environment BEFORE any
    ``CollieDB`` is built, so the bench never touches the real user home.
    """
    with _headless_home(args.home) as home:
        return await _run_one_in_home(args, home)


async def _run_one_in_home(args: argparse.Namespace, home: Path) -> tuple[int, dict[str, Any]]:
    """Run one task inside an already-scoped headless home."""

    document = _empty_document(args.task, args.provider)
    exit_code = EXIT_OK

    # -- usage/config errors ------------------------------------------------
    key = os.environ.get(args.api_key_env) or ""
    if not key:
        document["error"] = (
            f"No API key found in ${args.api_key_env}. Pass it via the "
            "environment — the headless entry never accepts keys on argv."
        )
        exit_code = EXIT_USAGE
        _emit(document, args.json_out)
        return exit_code, document

    db: CollieDB | None = None
    runtime: CollieRuntime | None = None
    try:
        db = CollieDB(home / "collie.db")
        # Approval preset is persisted BEFORE the runtime is constructed so the
        # PermissionEvaluator reads it at init — the exact same setting the IPC
        # path writes, never a bypass. (argparse already restricts to
        # allow|ask|deny.)
        db.set_setting("permissions.local_write_preset", args.approval_preset)
        # --max-iterations maps to agent.max_tool_iterations; build_config clamps
        # to 1..2000, and the flag must never reach the engine outside that range.
        db.set_setting(
            "agent.max_tool_iterations",
            min(MAX_ITERATIONS_MAX, max(MAX_ITERATIONS_MIN, args.max_iterations)),
        )
        runtime = CollieRuntime(port=0, db=db)
        # -- provider ---------------------------------------------------------
        # The exact transactional path the UI uses: validate + persist +
        # rebuild the loop. A bench key flows purely through the environment.
        candidate: dict[str, Any] = {
            "provider_id": args.provider,
            "name": args.provider,
            "auth_type": "api-key",
            "model": args.model,
            "runtime_name": args.provider,
            "secret_name": args.provider,
            "protocol": "openai",
            "api_base": args.api_base,
            "api_key": key,
        }
        configured = await runtime._configure_provider_candidate(candidate)
        if not configured.get("configured"):
            document["error"] = sanitize_text(
                str(configured.get("error") or "provider configuration failed")
            )
            exit_code = EXIT_USAGE
            _emit(document, args.json_out)
            return exit_code, document

        document["model"] = str(configured.get("model") or args.model or "")

        # -- session + conversation -------------------------------------------
        conversation = db.create_conversation(title="Bench")
        conversation_id = str(conversation["id"])
        # The engine resolves the canonical desktop key for this conversation;
        # report THAT key so the result joins the telemetry run records.
        engine_session_key = runtime._conversation_target(conversation_id)[0]
        document["session_key"] = engine_session_key
        document["conversation_id"] = conversation_id

        # -- run one turn ------------------------------------------------------
        run_id = uuid.uuid4().hex
        started = time.monotonic()
        outbound: Any = None
        try:
            outbound = await asyncio.wait_for(
                runtime._chat(
                    args.task,
                    conversation_id=conversation_id,
                    on_stream=_noop_stream,
                    on_progress=_noop_progress,
                    execution_mode="execute",
                    run_id=run_id,
                ),
                timeout=args.timeout,
            )
        except TimeoutError:
            exit_code = EXIT_TIMEOUT
            document["exit_state"] = "timeout"
            document["error"] = f"Turn timed out after {args.timeout}s."
        except Exception as error:  # task/engine error
            exit_code = EXIT_ERROR
            document["exit_state"] = "error"
            document["error"] = sanitize_text(str(error))
        else:
            document["exit_state"] = "ok"
        document["latency_ms"] = int((time.monotonic() - started) * 1000)

        # -- evidence ----------------------------------------------------------
        # Flush telemetry so the run record (hashes, tool count) is durable
        # before we read it back; mirrors run()'s flush-then-close ordering.
        recorder = RunRecorder.active_for(runtime.db)
        if recorder is not None:
            recorder.flush()

        usage = getattr(runtime.loop, "_last_usage", None) or {}
        document["usage"] = _normalize_usage(dict(usage))

        record = {}
        try:
            turns = db.list_turn_events(conversation_id=conversation_id, limit=1)
            if turns:
                record = turns[0]
        except Exception:
            pass
        document["prompt_hash"] = record.get("prompt_hash")
        document["tool_schema_hash"] = record.get("tool_schema_hash")
        document["config_hash"] = record.get("config_hash")
        # Fall back to the live loop's fingerprints if the row is missing.
        if document["tool_schema_hash"] is None:
            document["tool_schema_hash"] = current_tool_schema_hash()
        if document["config_hash"] is None:
            document["config_hash"] = current_config_hash()

        final_text = str(getattr(outbound, "content", "") or "")
        document["final_text"] = sanitize_text(final_text)

        stats = getattr(recorder, "last_turn_stats", None) or {}
        document["calls"] = {
            "model_calls": int(stats.get("model_calls") or 0),
            "tool_calls": int(record.get("tool_count") or 0),
            "retries": int(stats.get("retries") or 0),
            "no_action_turns": int(stats.get("no_action_turns") or 0),
        }

        _emit(document, args.json_out)
        return exit_code, document
    finally:
        # Mirror run()'s shutdown ordering: cancel in-flight work first,
        # then stop telemetry, then close the database.
        if runtime is not None:
            with contextlib.suppress(Exception):
                await runtime._shutdown_loop()
            runtime.approvals.cancel_all()
            recorder = RunRecorder.active_for(runtime.db)
            if recorder is not None:
                recorder.shutdown()
        if db is not None:
            db.close()


def _emit(document: dict[str, Any], json_out: str | None) -> None:
    """Print the document as one JSON line; optionally also write it."""
    line = json.dumps(document, ensure_ascii=False)
    print(line, flush=True)
    if json_out:
        Path(json_out).write_text(line + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, run one task, return the exit code."""
    args = _parse_args(argv)
    exit_code, _document = asyncio.run(run_one(args))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
