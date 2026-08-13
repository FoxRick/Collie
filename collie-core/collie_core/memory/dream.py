"""Collie's Dream runner — episodic memory consolidation (Gardener PR 3).

Wires nanobot's already-vendored Dream machinery (cursor, prompt builder,
session keys, pruning) into Collie:

1. Build the Dream prompt from unprocessed conversation history via
   ``MemoryStore.build_dream_prompt()``.
2. Run a **bounded, read-only** loop turn under a ``dream:<ts>`` session key
   using the subagent runner machinery (one model call, no tools, no
   approvals, read-only posture). Collie has no file-editing tools, so the
   model outputs the full proposed new ``memory/MEMORY.md`` as its final
   response.
3. If the proposed content differs from the current file, write it and
   snapshot it via the version store (``artifact_type="memory_dream"``) so
   the change is reviewable and undoable in Settings → Memory.
4. Advance the dream cursor **only on success** (a completed turn — even
   one that produced no change — so history is never re-processed).
5. Prune old Dream sessions (keep 10) after each run.

Never touches ProfileStore (the structured profile memory) — Dream
consolidates nanobot's long-term ``memory/MEMORY.md`` only.
"""

from __future__ import annotations

import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.runner import AgentRunSpec
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.turn_hooks import AgentTurnHookContext

__all__ = ["run_dream"]

# Collie has no file-editing tools; the Dream model must return the file
# content directly instead of editing on disk.
_DREAM_OUTPUT_CONTRACT = """

## Collie output contract
Collie has no file-editing tools, so you cannot edit files directly. Do not
try to call any tool. Analyze the Conversation History and the current
memory files above, then output ONLY the full new content of
``memory/MEMORY.md`` — the complete file, nothing else. No commentary, no
preamble, no markdown code fences. Preserve every fact that is still true,
prune stale entries, and keep facts atomic and non-duplicated.
"""

_DREAM_SYSTEM_PROMPT = (
    "You are Collie's memory consolidation engine. Follow the instructions "
    "in the user message exactly, and output only the requested file content."
)

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.DOTALL)


def _extract_file_content(response: str) -> str:
    """Pull the proposed file content out of a model response."""
    text = (response or "").strip()
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="MEMORY.md.", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        Path(tmp_name).replace(path)
    finally:
        with suppress(FileNotFoundError):
            Path(tmp_name).unlink()


def _build_turn_hook(loop: Any, session_key: str) -> Any | None:
    """Compose the loop's telemetry hook factories (best-effort, like
    ``SubagentManager._run_subagent``) so Dream turns are recorded."""
    try:
        factories = getattr(loop.subagents, "hook_factories", None) or []
        if not factories:
            return None
        from nanobot.agent.hook import CompositeHook

        turn_context = AgentTurnHookContext(
            on_progress=None,
            workspace=Path(loop.workspace or "."),
            channel="collie",
            chat_id="dream",
            message_id=None,
            session_key=session_key,
            metadata={"turn_kind": "automation"},
        )
        extra = [factory(turn_context) for factory in factories]
        extra = [hook for hook in extra if hook is not None]
        return CompositeHook(extra) if extra else None
    except Exception:
        logger.exception("dream telemetry hook build failed (swallowed)")
        return None


async def run_dream(
    *,
    workspace: Path,
    db: Any,
    loop: Any,
    version_store: Any = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    """Run one Dream consolidation pass; returns a result summary dict."""
    from collie_core.versions import make_diff

    store: MemoryStore = loop.context.memory
    built = store.build_dream_prompt()
    if built is None:
        return {
            "changed": False,
            "reason": "no_new_history",
            "message": "Nothing new to review — memory is already up to date.",
        }

    prompt, cursor = built
    dream_key = session_key or MemoryStore.dream_session_key()

    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": _DREAM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt + _DREAM_OUTPUT_CONTRACT},
        ],
        tools=ToolRegistry(),  # no tools: bounded, read-only by construction
        runtime=loop.llm_runtime(),
        max_iterations=1,
        max_tool_result_chars=4000,
        hook=_build_turn_hook(loop, dream_key),
        session_key=dream_key,
        workspace=workspace,
        execution_posture="read_only",
    )
    try:
        result = await loop.subagents.runner.run(spec)
    except Exception:
        logger.exception("Dream turn failed")
        return {
            "changed": False,
            "reason": "turn_error",
            "message": "The memory review hit a snag — I'll try again later.",
        }

    if result.stop_reason != "completed":
        logger.warning(
            "Dream turn did not complete (stop_reason={}); cursor not advanced",
            result.stop_reason,
        )
        return {
            "changed": False,
            "reason": f"stop_reason:{result.stop_reason}",
            "message": "The memory review didn't finish — I'll try again later.",
        }

    proposed = _extract_file_content(result.final_content or "")
    if not proposed:
        return {
            "changed": False,
            "reason": "empty_response",
            "message": "The memory review came back empty — I'll try again later.",
        }

    memory_file = Path(workspace) / "memory" / "MEMORY.md"
    before = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    new_text = proposed.rstrip() + "\n"

    if before.strip() == new_text.strip():
        # The run succeeded; history is processed even when nothing changed.
        store.set_last_dream_cursor(cursor)
        _prune(workspace)
        return {
            "changed": False,
            "reason": "no_content_change",
            "cursor": cursor,
            "message": "Memory review done — everything was already tidy.",
        }

    _atomic_write(memory_file, new_text)
    version_id = None
    if version_store is not None:
        try:
            version_id = version_store.snapshot(
                "memory_dream",
                "MEMORY.md",
                before,
                new_text,
                evidence={"cursor": cursor, "session_key": dream_key},
                source="collie",
            )
        except Exception:
            logger.exception("dream version snapshot failed (swallowed)")

    # Success path only: the content is durable and versioned, so the
    # processed history is marked done.
    store.set_last_dream_cursor(cursor)
    _prune(workspace)

    return {
        "changed": True,
        "version_id": version_id,
        "cursor": cursor,
        "diff": make_diff(before, proposed, "memory/MEMORY.md"),
        "message": "Memory review done — I tidied up my long-term memory.",
    }


def _prune(workspace: Path) -> None:
    """Keep only the 10 most recent Dream sessions (best-effort)."""
    try:
        MemoryStore.prune_dream_sessions(Path(workspace) / "sessions", keep=10)
    except Exception:
        logger.exception("dream session prune failed (swallowed)")
