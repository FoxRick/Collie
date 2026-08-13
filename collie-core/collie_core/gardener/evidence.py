"""Gardener evidence queries — read-only signals over run-record telemetry.

The Gardener's evidence layer answers four questions from the telemetry
tables (``turn_events`` / ``tool_events``) plus the workspace files:

1. **Repeated tool failures** — tools that error or are denied often enough
   to be worth a fix (e.g. a tool description tweak).
2. **Repeated workflows** — tool sequences that appear again and again, a
   sign the user repeats a multi-step task that could become a routine.
3. **Denied / stopped turns** — turns the user stopped or that the model
   ended early, a signal of friction.
4. **Memory bloat** — ``memory/MEMORY.md`` (long-term) and ``MEMORY.md``
   (profile mirror) growing past sensible sizes or accumulating duplicate
   headings.

Everything here is **read-only**: no writes to the DB and no writes to the
workspace. The Gardener's proposal step consumes the summary and the
apply step goes through the versioned rollback rail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "collect_evidence",
    "memory_bloat",
    "recent_failures",
    "repeated_workflows",
    "user_stops",
]

# A tool is "failing" when it errors or is denied by the permission layer.
_FAILING_STATUSES = ("error", "denied", "timeout")

# Tool sequences shorter than this are too trivial to be a workflow.
_MIN_WORKFLOW_LEN = 2
# A workflow must appear at least this often to be "repeated".
_MIN_WORKFLOW_REPEATS = 3
# A failing tool must trip at least this often before the Gardener bothers.
_MIN_FAILURE_COUNT = 2

# Memory files the Gardener watches for bloat.
_MEMORY_FILES = (
    ("memory", "memory/MEMORY.md"),  # nanobot long-term memory (Dream target)
    ("profile", "MEMORY.md"),  # ProfileStore mirror
)

# Soft caps (characters) — above these the Gardener may suggest tidying.
_SOFT_CAPS: dict[str, int] = {"memory": 12_000, "profile": 12_000}


def _since(days: int = 14) -> str:
    """ISO timestamp ``days`` ago (UTC), the default evidence window."""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def recent_failures(
    db: Any, since: str | None = None, min_count: int = _MIN_FAILURE_COUNT
) -> list[dict[str, Any]]:
    """Per-tool failure counts with sample messages, most failing first."""
    events = db.list_tool_events(limit=500)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        started = str(event.get("started_at") or "")
        if since and started and started < since:
            continue
        if str(event.get("status") or "") not in _FAILING_STATUSES:
            continue
        name = str(event.get("tool_name") or "unknown")
        buckets.setdefault(name, []).append(event)

    rows: list[dict[str, Any]] = []
    for name, items in buckets.items():
        if len(items) < min_count:
            continue
        samples = [
            (str(item.get("error_message") or "") or None)
            for item in items[:3]
            if item.get("error_message")
        ]
        rows.append(
            {
                "tool_name": name,
                "failures": len(items),
                "statuses": sorted({str(i.get("status") or "") for i in items}),
                "sample_errors": [s for s in samples if s][:3],
                "last_seen": max(str(i.get("started_at") or "") for i in items),
            }
        )
    rows.sort(key=lambda r: r["failures"], reverse=True)
    return rows


def repeated_workflows(
    db: Any,
    since: str | None = None,
    min_repeats: int = _MIN_WORKFLOW_REPEATS,
) -> list[dict[str, Any]]:
    """Tool sequences (by turn) that recur often enough to suggest a routine.

    A workflow is the ordered list of tool names in one turn (collapsed
    repeats). Sequences shorter than ``_MIN_WORKFLOW_LEN`` are ignored.
    """
    turns = db.list_turn_events(limit=200)
    sequences: dict[tuple[str, ...], int] = {}
    for turn in turns:
        started = str(turn.get("started_at") or "")
        if since and started and started < since:
            continue
        tool_events = db.list_tool_events(turn_id=turn["id"], limit=100)
        names: list[str] = []
        for event in tool_events:
            name = str(event.get("tool_name") or "")
            if name and (not names or names[-1] != name):
                names.append(name)
        if len(names) >= _MIN_WORKFLOW_LEN:
            key = tuple(names)
            sequences[key] = sequences.get(key, 0) + 1

    return [
        {
            "workflow": list(key),
            "repeats": count,
        }
        for key, count in sorted(sequences.items(), key=lambda item: item[1], reverse=True)
        if count >= min_repeats
    ]


def user_stops(db: Any, since: str | None = None) -> list[dict[str, Any]]:
    """Turns the user stopped or that never completed (friction signals)."""
    turns = db.list_turn_events(limit=200)
    stopped: list[dict[str, Any]] = []
    for turn in turns:
        started = str(turn.get("started_at") or "")
        if since and started and started < since:
            continue
        if str(turn.get("status") or "") in ("stopped", "cancelled"):
            stopped.append(
                {
                    "turn_id": turn["id"],
                    "status": turn["status"],
                    "started_at": started,
                    "conversation_id": str(turn.get("conversation_id") or ""),
                }
            )
    stopped.sort(key=lambda r: r["started_at"], reverse=True)
    return stopped


def memory_bloat(workspace: Path) -> list[dict[str, Any]]:
    """Size + duplicate-heading signals for the memory files.

    Returns one entry per watched file that exists, with ``bloated`` set
    when the file exceeds its soft cap or contains repeated ``#``-headings.
    """
    workspace = Path(workspace)
    findings: list[dict[str, Any]] = []
    for kind, rel in _MEMORY_FILES:
        path = workspace / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        headings = [
            line.strip().lstrip("#").strip().lower()
            for line in text.splitlines()
            if line.strip().startswith("#") and line.strip().lstrip("#").strip()
        ]
        dupes = {heading for heading in headings if headings.count(heading) > 1}
        bloated = len(text) > _SOFT_CAPS.get(kind, 12_000) or bool(dupes)
        findings.append(
            {
                "kind": kind,
                "path": rel,
                "chars": len(text),
                "lines": len(text.splitlines()),
                "duplicate_headings": sorted(dupes)[:10],
                "bloated": bloated,
            }
        )
    return findings


def collect_evidence(db: Any, workspace: Path, since: str | None = None) -> dict[str, Any]:
    """Aggregate the Gardener's evidence into one bounded summary dict."""
    since = since or _since()
    return {
        "since": since,
        "failures": recent_failures(db, since=since),
        "workflows": repeated_workflows(db, since=since),
        "user_stops": user_stops(db, since=since),
        "memory": memory_bloat(workspace),
    }
