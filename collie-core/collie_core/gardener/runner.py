"""Gardener runner — evidence → proposals → review cards → versioned apply.

The runner ties the Gardener story together:

1. :func:`run_gardener` collects evidence (read-only telemetry queries),
   runs the bounded proposal turn, validates every suggestion, and returns
   the suggestion list for the review surface (chat cards with
   Approve/Dismiss).
2. :func:`apply_suggestion` applies ONE approved suggestion through the
   versioned rollback rail: the current artifact text is snapshotted with
   the proposed text (``source="gardener"``), the new text is written, and
   dependent state (the subagent loader's disk→DB mirror) is re-synced.
   Rejected-while-applying is impossible: the suggestion is re-validated
   here, so even a hand-crafted IPC frame cannot bypass the scope guard.

The MVP deliberately has **no sandbox replay** (running a proposed change
in a sandbox before applying it) — that needs run-record history volume the
product doesn't have yet, and the deterministic scope guard + human
approval stand in for it. This is a documented deferral, not a gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from collie_core.gardener.evidence import collect_evidence
from collie_core.gardener.propose import propose, validate_suggestion
from collie_core.versions import make_diff

__all__ = ["apply_suggestion", "run_gardener"]

_MAX_SUGGESTIONS_IN_CARD = 3


def _target_path(workspace: Path, artifact_type: str, key: str) -> Path:
    """Resolve a validated artifact type+key to its workspace path."""
    base = Path(workspace)
    if artifact_type == "subagent":
        return base / "subagents" / key
    if artifact_type == "memory_dream":
        return base / "memory" / key
    return base / key


def apply_suggestion(
    *,
    workspace: Path,
    suggestion: dict[str, Any],
    version_store: Any = None,
    subagent_loader: Any = None,
) -> dict[str, Any]:
    """Apply one approved (re-validated) suggestion, versioned + undoable.

    Returns ``{"applied": True, "version_id": ..., "diff_text": ...}``.
    Raises :class:`ProposalValidationError` when the suggestion is out of
    scope, and :class:`ValueError` when the target cannot be written.
    """
    # Re-validate at apply time: the review card data could have been
    # forged or edited between propose and approve.
    cleaned = validate_suggestion(suggestion)

    workspace = Path(workspace)
    target = _target_path(workspace, cleaned["artifact_type"], cleaned["artifact_key"])
    before = target.read_text(encoding="utf-8") if target.exists() else ""

    proposed = cleaned["proposed_text"]
    new_text = proposed if proposed.endswith("\n") else proposed + "\n"
    if before == new_text:
        return {
            "applied": True,
            "no_change": True,
            "version_id": None,
            "diff_text": "",
            "artifact_type": cleaned["artifact_type"],
            "artifact_key": cleaned["artifact_key"],
        }

    version_id: str | None = None
    if version_store is not None:
        try:
            version_id = version_store.snapshot(
                cleaned["artifact_type"],
                cleaned["artifact_key"],
                before,
                new_text,
                evidence={
                    "evidence_ids": cleaned.get("evidence_ids") or [],
                    "rationale": cleaned.get("rationale") or "",
                },
                source="gardener",
            )
        except Exception:
            # Versioning is a rollback rail — never block the apply.
            logger.exception("gardener apply version snapshot failed (swallowed)")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"I couldn't write the change: {exc}") from exc

    # Subagent edits live in both the file and the loader's DB mirror;
    # reconcile disk -> DB so the two never disagree.
    if cleaned["artifact_type"] == "subagent" and subagent_loader is not None:
        try:
            subagent_loader.sync()
        except Exception:
            logger.exception("subagent re-sync after gardener apply failed (swallowed)")

    return {
        "applied": True,
        "no_change": False,
        "version_id": version_id,
        "diff_text": make_diff(before, new_text, cleaned["artifact_key"]),
        "artifact_type": cleaned["artifact_type"],
        "artifact_key": cleaned["artifact_key"],
    }


async def run_gardener(
    *,
    workspace: Path,
    db: Any,
    loop: Any,
    version_store: Any = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Run one Gardener pass: evidence → proposals → validated suggestions.

    Returns ``{"suggestions": [...], "rejected": [...], "message": str,
    "evidence": {...}}``. ``suggestions`` is the validated list for the
    review cards; ``rejected`` reports what the scope guard refused.
    """
    workspace = Path(workspace)
    evidence = collect_evidence(db, workspace, since=since)

    if not (
        evidence["failures"]
        or evidence["workflows"]
        or evidence["user_stops"]
        or any(m.get("bloated") for m in evidence["memory"])
    ):
        return {
            "suggestions": [],
            "rejected": [],
            "evidence": evidence,
            "message": "No improvement signals this week — run records look healthy.",
        }

    valid, rejected = await propose(loop, workspace, evidence)
    suggestions = valid[:_MAX_SUGGESTIONS_IN_CARD]
    if not suggestions:
        message = "I looked at the run records but nothing rose to a suggestion worth your time."
    else:
        message = (
            f"I found {len(suggestions)} suggestion"
            f"{'' if len(suggestions) == 1 else 's'} from the last two weeks "
            "of run records. Review each one — you can approve or dismiss."
        )
    return {
        "suggestions": suggestions,
        "rejected": rejected,
        "evidence": evidence,
        "message": message,
    }
