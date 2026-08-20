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
3. If the proposed content differs from the current file, store it as a
   **pending proposal** (``memory/.dream-proposal.json``) — ``MEMORY.md``
   itself is NOT touched. The user reviews the diff in Settings → Memory
   and explicitly applies or dismisses it (``apply_dream_proposal`` /
   ``dismiss_dream_proposal``).
4. Advance the dream cursor **only on success** (a completed turn — even one
   that produced no change — so history is never re-processed). The cursor
   advances when the proposal is created; applying or dismissing never
   re-runs the model.
5. Prune old Dream sessions (keep 10) after each run.

On apply, the proposal is re-validated against the current file before the
write (a manual edit in between invalidates it), written atomically, and
snapshotted via the version store (``artifact_type="memory_dream"``) so the
change stays reviewable and undoable in Settings → Memory.

Never touches ProfileStore (the structured profile memory) — Dream
consolidates nanobot's long-term ``memory/MEMORY.md`` only.
"""

from __future__ import annotations

import json
import re
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.runner import AgentRunSpec
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.turn_hooks import AgentTurnHookContext

__all__ = [
    "apply_dream_proposal",
    "dismiss_dream_proposal",
    "get_dream_pending",
    "run_dream",
]

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

# The pending proposal lives next to MEMORY.md. It is the only artifact a
# Dream run may write — MEMORY.md itself changes only on explicit apply.
_DREAM_PROPOSAL_FILE = ".dream-proposal.json"


def _proposal_path(workspace: Path) -> Path:
    return Path(workspace) / "memory" / _DREAM_PROPOSAL_FILE


def read_dream_proposal(workspace: Path) -> dict[str, Any] | None:
    """The pending Dream proposal, or None when nothing awaits review."""
    path = _proposal_path(workspace)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Unreadable dream proposal at {}", path)
        return None


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
    """Run one Dream consolidation pass; returns a result summary dict.

    Never writes ``MEMORY.md``: a changed proposal is stored as pending for
    the user to review and apply (or dismiss) in Settings → Memory.
    """
    from collie_core.versions import make_diff

    if _proposal_path(workspace).exists():
        return {
            "changed": False,
            "reason": "pending_exists",
            "message": "You already have a memory review waiting — open Settings → Memory to review it.",
        }

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

    _atomic_write(
        _proposal_path(workspace),
        json.dumps(
            {
                "before": before,
                "proposed": new_text,
                "cursor": cursor,
                "session_key": dream_key,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
    )

    # Success path only: the proposal is durable, so the processed history
    # is marked done. MEMORY.md itself stays untouched until the user
    # approves the proposal (apply_dream_proposal).
    store.set_last_dream_cursor(cursor)
    _prune(workspace)

    return {
        "changed": True,
        "pending": True,
        "diff": make_diff(before, proposed, "memory/MEMORY.md"),
        "message": "Memory review done — I have changes ready for you to review.",
    }


def apply_dream_proposal(*, workspace: Path, version_store: Any) -> dict[str, Any]:
    """Approve the pending Dream proposal: re-validate, write, version.

    The proposal is only applied while ``MEMORY.md`` still matches the
    content the review started from — a manual edit in between invalidates
    it and the caller must run a fresh review.
    """
    from collie_core.versions import make_diff

    proposal = read_dream_proposal(workspace)
    if proposal is None:
        return {"applied": False, "reason": "no_pending"}

    memory_file = Path(workspace) / "memory" / "MEMORY.md"
    current = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    if current != proposal["before"]:
        raise ValueError("Your memory changed since the review — run a new review to refresh it.")

    proposed = proposal["proposed"]
    _atomic_write(memory_file, proposed)
    version_id = None
    if version_store is not None:
        try:
            version_id = version_store.snapshot(
                "memory_dream",
                "MEMORY.md",
                proposal["before"],
                proposed,
                evidence={
                    "cursor": proposal.get("cursor"),
                    "session_key": proposal.get("session_key"),
                },
                source="collie",
            )
        except Exception:
            logger.exception("dream version snapshot failed (swallowed)")

    _proposal_path(workspace).unlink(missing_ok=True)
    return {
        "applied": True,
        "version_id": version_id,
        "diff_text": make_diff(proposal["before"], proposed, "memory/MEMORY.md"),
    }


def dismiss_dream_proposal(*, workspace: Path) -> dict[str, Any]:
    """Dismiss the pending Dream proposal without applying it."""
    path = _proposal_path(workspace)
    if path.exists():
        path.unlink()
    return {"dismissed": True}


def get_dream_pending(*, workspace: Path) -> dict[str, Any]:
    """Current pending Dream proposal state (Settings → Memory)."""
    from collie_core.versions import make_diff

    proposal = read_dream_proposal(workspace)
    if proposal is None:
        return {"pending": False}
    return {
        "pending": True,
        "diff_text": make_diff(proposal["before"], proposal["proposed"], "memory/MEMORY.md"),
        "created_at": proposal.get("created_at"),
    }


def _prune(workspace: Path) -> None:
    """Keep only the 10 most recent Dream sessions (best-effort)."""
    try:
        MemoryStore.prune_dream_sessions(Path(workspace) / "sessions", keep=10)
    except Exception:
        logger.exception("dream session prune failed (swallowed)")
