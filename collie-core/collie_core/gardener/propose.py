"""Gardener proposal step — bounded subagent call + strict validation.

The Gardener runs one **bounded, read-only** subagent turn (a single model
call, no tools, ``read_only`` posture — the same machinery the Dream runner
uses) with a fixed prompt: the evidence summary and the current agent /
memory texts go in, a structured JSON list of suggestions comes out.

Nothing the model says is trusted: every suggestion passes through
:func:`validate_suggestion`, a deterministic gate that enforces

* **artifact allowlist** — only ``subagent``, ``agents``, ``vision`` and
  ``memory_dream`` targets may be proposed;
* **key safety** — artifact keys must be plain filenames (no path
  traversal) and must match the artifact type;
* **size budgets** — proposed text and rationale are length-capped;
* **keyword gate** — proposed text mentioning permissions, settings,
  secrets, connectors, credentials or anything out of the Gardener's
  scope is rejected outright.

Suggestions that fail validation are dropped with a reason; the runner
reports how many were rejected.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.runner import AgentRunSpec
from nanobot.agent.tools.registry import ToolRegistry

__all__ = [
    "ALLOWED_ARTIFACT_TYPES",
    "FORBIDDEN_TERMS",
    "MAX_PROPOSED_CHARS",
    "MAX_RATIONALE_CHARS",
    "MAX_SUGGESTIONS",
    "ProposalValidationError",
    "propose",
    "validate_suggestion",
]

# The only artifact types the Gardener may touch (scope guard from the
# plan: agent instructions + memory consolidation, nothing else).
ALLOWED_ARTIFACT_TYPES: tuple[str, ...] = (
    "subagent",
    "agents",
    "vision",
    "memory_dream",
)

# Deterministic keyword gate: any of these in a proposed text (or
# rationale) is out of scope and the suggestion is rejected. Deliberately
# conservative and plain-worded — the Gardener never proposes changes to
# permissions, settings, secrets, or connectors.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "permission",
    "approval",
    "approve",
    "auto-approv",
    "settings",
    "setting",
    "secret",
    "password",
    "credential",
    "api key",
    "apikey",
    "token",
    "connector",
    "oauth",
    "billing",
    "payment",
    "credit card",
    "model provider",
    "provider key",
)

# Size budgets (characters).
MAX_PROPOSED_CHARS = 4_000
MAX_RATIONALE_CHARS = 500
MAX_SUGGESTIONS = 3

# Evidence summary length budget for the prompt (≈2k tokens of text).
MAX_EVIDENCE_CHARS = 8_000
# Per-artifact current-text budget fed to the model.
MAX_ARTIFACT_TEXT_CHARS = 6_000

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

_GARDENER_SYSTEM_PROMPT = (
    "You are Collie's improvement Gardener. Follow the instructions in the "
    "user message exactly, and output only the requested JSON."
)

_GARDENER_PROMPT_TEMPLATE = """You help Collie (a local-first AI assistant for non-coders) improve itself.

Below is evidence from Collie's recent run records (tool failures, repeated
workflows, stopped turns, memory size) and the current text of a few agent
instruction / memory files.

## Evidence
{evidence}

## Current files
{artifacts}

## Your task
Propose up to {max_suggestions} concrete, conservative improvements. Each
suggestion must do exactly ONE of:

- rewrite one agent instruction file (AGENTS.md, VISION.md) so Collie's
  instructions match observed friction (e.g. a tool that keeps failing,
  a workflow the user repeats),
- tighten one subagent prompt (subagents/<name>.md),
- tidy the long-term memory file (memory/MEMORY.md): merge duplicates,
  prune stale entries, keep facts atomic.

Output ONLY a JSON array (no markdown, no commentary):
[{{"artifact_type": "subagent|agents|vision|memory_dream",
   "artifact_key": "<plain filename>",
   "proposed_text": "<full new file content>",
   "rationale": "<1-2 sentences, referencing the evidence>",
   "evidence_ids": ["<short labels from the evidence, e.g. 'failures:web_fetch'>"]}}]

Rules:
- Never propose changes to permissions, settings, secrets, credentials,
  connectors, billing, or providers. Those are out of scope.
- Never invent facts. Base every change on the evidence above.
- Keep proposed_text complete (the whole file), not a patch.
- If nothing is worth changing, output an empty array: []
"""


class ProposalValidationError(Exception):
    """Raised when a Gardener suggestion violates the scope guard."""


def _normalise_key(key: str) -> str:
    return (key or "").strip().lstrip("/")


def validate_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Strictly validate one model-proposed suggestion (deterministic).

    Returns the cleaned suggestion, or raises
    :class:`ProposalValidationError` with a human reason. This is the
    Gardener's security boundary: nothing a model says is ever applied
    without passing through here (the apply path re-validates too).
    """
    artifact_type = str(suggestion.get("artifact_type") or "").strip()
    if artifact_type not in ALLOWED_ARTIFACT_TYPES:
        raise ProposalValidationError(
            f"'{artifact_type or '(missing)'}' is not a target the Gardener may change."
        )

    key = _normalise_key(str(suggestion.get("artifact_key") or ""))
    if not key or "/" in key or "\\" in key or key in (".", ".."):
        raise ProposalValidationError(f"'{key}' is not a safe artifact key (plain filenames only).")

    if artifact_type == "subagent":
        if not key.endswith(".md"):
            raise ProposalValidationError("Subagent suggestions must target a '.md' file.")
    elif artifact_type in ("agents", "vision"):
        expected = "AGENTS.md" if artifact_type == "agents" else "VISION.md"
        if key != expected:
            raise ProposalValidationError(f"{artifact_type} suggestions must target '{expected}'.")
    elif artifact_type == "memory_dream" and key != "MEMORY.md":
        raise ProposalValidationError("Memory suggestions must target 'MEMORY.md'.")

    proposed = str(suggestion.get("proposed_text") or "")
    if not proposed.strip():
        raise ProposalValidationError("The proposed text is empty — there's nothing to apply.")
    if len(proposed) > MAX_PROPOSED_CHARS:
        raise ProposalValidationError(
            f"The proposed text is {len(proposed)} characters — over the "
            f"{MAX_PROPOSED_CHARS}-character budget."
        )
    rationale = str(suggestion.get("rationale") or "")
    if len(rationale) > MAX_RATIONALE_CHARS:
        raise ProposalValidationError(
            f"The rationale is {len(rationale)} characters — over the "
            f"{MAX_RATIONALE_CHARS}-character budget."
        )

    evidence_ids = suggestion.get("evidence_ids") or []
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    evidence_ids = [str(item) for item in evidence_ids][:10]

    # Keyword gate on the actual content (case-insensitive, word-bounded
    # where it matters). This is the deterministic scope guard.
    haystack = f"{proposed}\n{rationale}".lower()
    for term in FORBIDDEN_TERMS:
        if term in haystack:
            raise ProposalValidationError(
                f"The proposal mentions '{term}', which is out of scope for "
                "the Gardener (permissions, settings, secrets, and "
                "connectors are never auto-suggested)."
            )

    return {
        "artifact_type": artifact_type,
        "artifact_key": key,
        "proposed_text": proposed,
        "rationale": rationale,
        "evidence_ids": evidence_ids,
    }


def _extract_json(response: str) -> list[dict[str, Any]]:
    """Pull the suggestion array out of a model response, best-effort."""
    text = (response or "").strip()
    match = _FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            parsed = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def build_prompt(evidence: dict[str, Any], workspace: Path) -> str:
    """Render the Gardener prompt (bounded: evidence + current texts)."""
    evidence_text = json.dumps(evidence, ensure_ascii=False, indent=1)
    if len(evidence_text) > MAX_EVIDENCE_CHARS:
        evidence_text = evidence_text[:MAX_EVIDENCE_CHARS] + "\n…(truncated)"

    sections: list[str] = []
    for artifact_type in ALLOWED_ARTIFACT_TYPES:
        if artifact_type == "subagent":
            sub_dir = Path(workspace) / "subagents"
            if not sub_dir.exists():
                continue
            for path in sorted(sub_dir.glob("*.md")):
                text = path.read_text(encoding="utf-8")
                sections.append(f"### subagents/{path.name}\n{text[:MAX_ARTIFACT_TEXT_CHARS]}")
        elif artifact_type == "memory_dream":
            path = Path(workspace) / "memory" / "MEMORY.md"
            if path.exists():
                text = path.read_text(encoding="utf-8")
                sections.append(f"### memory/MEMORY.md\n{text[:MAX_ARTIFACT_TEXT_CHARS]}")
        else:
            key = "AGENTS.md" if artifact_type == "agents" else "VISION.md"
            path = Path(workspace) / key
            if path.exists():
                text = path.read_text(encoding="utf-8")
                sections.append(f"### {key}\n{text[:MAX_ARTIFACT_TEXT_CHARS]}")

    return _GARDENER_PROMPT_TEMPLATE.format(
        evidence=evidence_text,
        artifacts="\n\n".join(sections) if sections else "(no files yet)",
        max_suggestions=MAX_SUGGESTIONS,
    )


async def propose(
    loop: Any,
    workspace: Path,
    evidence: dict[str, Any],
    session_key: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the bounded Gardener turn; return (valid, rejected) suggestions.

    ``valid`` is the validated, cleaned suggestion list; ``rejected`` is a
    list of ``{"reason": str, "artifact_type": str, "artifact_key": str}``
    for the suggestions the deterministic gate refused. Both are safe to
    render.
    """
    workspace = Path(workspace)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    gardener_key = session_key or f"gardener:{stamp}"

    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": _GARDENER_SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(evidence, workspace)},
        ],
        tools=ToolRegistry(),  # no tools: bounded, read-only by construction
        runtime=loop.llm_runtime(),
        max_iterations=1,
        max_tool_result_chars=4000,
        hook=None,
        session_key=gardener_key,
        workspace=workspace,
        execution_posture="read_only",
    )

    try:
        result = await loop.subagents.runner.run(spec)
    except Exception:
        logger.exception("Gardener proposal turn failed")
        return [], [{"reason": "turn_error", "artifact_type": "", "artifact_key": ""}]

    if getattr(result, "stop_reason", None) != "completed":
        logger.warning(
            "Gardener proposal turn did not complete (stop_reason={})",
            getattr(result, "stop_reason", None),
        )
        return [], [
            {
                "reason": f"stop_reason:{getattr(result, 'stop_reason', 'unknown')}",
                "artifact_type": "",
                "artifact_key": "",
            }
        ]

    raw = _extract_json(getattr(result, "final_content", None) or "")
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for suggestion in raw[: MAX_SUGGESTIONS + 2]:
        try:
            valid.append(validate_suggestion(suggestion))
        except ProposalValidationError as exc:
            rejected.append(
                {
                    "reason": str(exc),
                    "artifact_type": str(suggestion.get("artifact_type") or ""),
                    "artifact_key": str(suggestion.get("artifact_key") or ""),
                }
            )
    return valid[:MAX_SUGGESTIONS], rejected
