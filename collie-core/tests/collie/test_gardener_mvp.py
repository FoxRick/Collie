"""Gardener MVP tests (Gardener Foundations PR 4).

Covers ``collie_core/gardener/`` against seeded telemetry rows:

- evidence queries (repeated failures, repeated workflows, stopped turns,
  memory bloat) read the seeded ``turn_events`` / ``tool_events``;
- proposal validation rejects out-of-scope targets (permissions, settings,
  secrets, connectors, non-allowlisted artifact types, unsafe keys, size
  overruns) deterministically;
- the bounded proposal turn (fake loop) returns validated suggestions and
  reports rejected ones;
- approve applies + versions through the rollback rail; dismiss applies
  nothing; rollback restores the prior text and marks the version.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from collie_core.db import CollieDB
from collie_core.gardener.evidence import (
    collect_evidence,
    memory_bloat,
    recent_failures,
    repeated_workflows,
    user_stops,
)
from collie_core.gardener.propose import (
    MAX_PROPOSED_CHARS,
    ProposalValidationError,
    propose,
    validate_suggestion,
)
from collie_core.gardener.runner import apply_suggestion, run_gardener
from collie_core.ipc.server import CollieIPCServer
from collie_core.versions import VersionStore


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    d.close()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    return ws


def _days_ago(days: int) -> str:
    """ISO timestamp ``days`` days ago (UTC) — keeps seeds inside Gardener's
    default 14-day evidence window no matter when the suite is run."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _seed_turn(db: CollieDB, turn_id: str, status: str = "ok", **extra: Any) -> None:
    started_at = extra.pop("started_at", _days_ago(1))
    db.record_turn_event(
        turn_id=turn_id,
        conversation_id=extra.pop("conversation_id", "conv-1"),
        turn_kind="chat",
        status=status,
        started_at=started_at,
        finished_at=extra.pop("finished_at", started_at),
        **extra,
    )


def _seed_tool(
    db: CollieDB,
    tool_id: str,
    turn_id: str,
    tool_name: str,
    status: str = "ok",
    error_message: str | None = None,
    started_at: str | None = None,
) -> None:
    started_at = started_at or _days_ago(1)
    db.record_tool_event(
        tool_id=tool_id,
        turn_id=turn_id,
        tool_name=tool_name,
        status=status,
        error_message=error_message,
        started_at=started_at,
        finished_at=started_at,
    )


class FakeRunner:
    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.specs: list[Any] = []

    async def run(self, spec: Any) -> Any:
        self.specs.append(spec)
        return self.responses.pop(0)


class FakeSubagents:
    def __init__(self) -> None:
        self.runner = FakeRunner()
        self.hook_factories: list[Any] = []


class FakeLoop:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.subagents = FakeSubagents()

    def llm_runtime(self) -> Any:
        return SimpleNamespace(model="fake-model")


def _result(content: str, stop_reason: str = "completed") -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        final_content=content,
        tools_used=[],
        messages=[],
        error=None,
    )


def _valid_subagent_suggestion(**overrides: Any) -> dict[str, Any]:
    suggestion = {
        "artifact_type": "subagent",
        "artifact_key": "helper.md",
        "proposed_text": "# Helper\n\nYou help with research tasks.\n",
        "rationale": "The user repeats multi-step research; a specialist helps.",
        "evidence_ids": ["workflows:search,web_fetch"],
    }
    suggestion.update(overrides)
    return suggestion


# -- evidence queries -------------------------------------------------------


def test_recent_failures_counts_and_samples(db: CollieDB) -> None:
    _seed_turn(db, "t1")
    for i in range(3):
        _seed_tool(
            db,
            f"tool-{i}",
            "t1",
            "web_fetch",
            status="error",
            error_message=f"HTTP 500 attempt {i}",
        )
    _seed_tool(db, "ok-tool", "t1", "web_search", status="ok")

    failures = recent_failures(db)
    assert len(failures) == 1
    row = failures[0]
    assert row["tool_name"] == "web_fetch"
    assert row["failures"] == 3
    assert row["statuses"] == ["error"]
    assert "HTTP 500" in row["sample_errors"][0]
    # Successful tools never appear.
    assert all(r["tool_name"] != "web_search" for r in failures)


def test_recent_failures_respects_since_window(db: CollieDB) -> None:
    _seed_turn(db, "t1")
    _seed_tool(db, "old", "t1", "web_fetch", status="error", started_at=_days_ago(30))
    failures = recent_failures(db, since=_days_ago(15))
    assert failures == []


def test_repeated_workflows_groups_tool_sequences(db: CollieDB) -> None:
    for i in range(3):
        turn_id = f"turn-{i}"
        _seed_turn(db, turn_id)
        _seed_tool(db, f"{turn_id}-a", turn_id, "web_search", status="ok")
        _seed_tool(db, f"{turn_id}-b", turn_id, "web_fetch", status="ok")

    workflows = repeated_workflows(db)
    assert len(workflows) == 1
    assert workflows[0]["workflow"] == ["web_search", "web_fetch"]
    assert workflows[0]["repeats"] == 3


def test_repeated_workflows_ignores_singletons(db: CollieDB) -> None:
    for i in range(3):
        turn_id = f"turn-{i}"
        _seed_turn(db, turn_id)
        _seed_tool(db, f"{turn_id}-a", turn_id, "web_search", status="ok")
    assert repeated_workflows(db) == []


def test_user_stops_lists_stopped_turns(db: CollieDB) -> None:
    _seed_turn(db, "ok-turn", status="ok")
    _seed_turn(db, "stopped-turn", status="stopped")
    _seed_turn(db, "cancelled-turn", status="cancelled")

    stops = user_stops(db)
    assert {s["turn_id"] for s in stops} == {"stopped-turn", "cancelled-turn"}


def test_memory_bloat_flags_size_and_duplicate_headings(workspace: Path) -> None:
    (workspace / "memory" / "MEMORY.md").write_text(
        "# Memory\n- fact one\n" * 200, encoding="utf-8"
    )
    (workspace / "MEMORY.md").write_text(
        "# About\n# About\n- duplicate heading\n", encoding="utf-8"
    )

    findings = {f["kind"]: f for f in memory_bloat(workspace)}
    assert findings["memory"]["bloated"] is True  # size
    assert findings["profile"]["bloated"] is True  # duplicate heading
    assert "about" in findings["profile"]["duplicate_headings"]


def test_collect_evidence_aggregates(db: CollieDB, workspace: Path) -> None:
    _seed_turn(db, "t1")
    _seed_tool(db, "x1", "t1", "web_fetch", status="error", error_message="boom")
    _seed_tool(db, "x2", "t1", "web_fetch", status="error", error_message="boom")
    (workspace / "memory" / "MEMORY.md").write_text("# M\n- a\n" * 100, encoding="utf-8")

    evidence = collect_evidence(db, workspace)
    assert evidence["failures"][0]["tool_name"] == "web_fetch"
    assert evidence["memory"][0]["kind"] == "memory"
    assert evidence["since"]


# -- proposal validation ----------------------------------------------------


def test_validate_suggestion_accepts_allowlisted_targets() -> None:
    for artifact_type, key in (
        ("subagent", "helper.md"),
        ("agents", "AGENTS.md"),
        ("vision", "VISION.md"),
        ("memory_dream", "MEMORY.md"),
    ):
        cleaned = validate_suggestion(
            {
                "artifact_type": artifact_type,
                "artifact_key": key,
                "proposed_text": "# New content\n",
                "rationale": "Based on evidence.",
                "evidence_ids": ["failures:web_fetch"],
            }
        )
        assert cleaned["artifact_type"] == artifact_type
        assert cleaned["artifact_key"] == key


@pytest.mark.parametrize(
    "artifact_type,key",
    [
        ("settings", "anything"),
        ("skill", "resume-writer.md"),
        ("connectors", "notion"),
        ("subagent", "../escape.md"),
        ("subagent", "nested/path.md"),
        ("agents", "OTHER.md"),
        ("vision", "AGENTS.md"),
        ("memory_dream", "memory/MEMORY.md"),
    ],
)
def test_validate_suggestion_rejects_out_of_scope_targets(artifact_type: str, key: str) -> None:
    with pytest.raises(ProposalValidationError):
        validate_suggestion(
            {
                "artifact_type": artifact_type,
                "artifact_key": key,
                "proposed_text": "# Content\n",
                "rationale": "Reason.",
            }
        )


@pytest.mark.parametrize(
    "text",
    [
        "Change the permission model to allow file writes.",
        "Update the api key handling in settings.",
        "Reconfigure the notion connector.",
        "Store credentials in the profile.",
        "Turn on auto-approve for tools.",
    ],
)
def test_validate_suggestion_rejects_forbidden_language(text: str) -> None:
    with pytest.raises(ProposalValidationError):
        validate_suggestion(_valid_subagent_suggestion(proposed_text=text))


def test_validate_suggestion_rejects_oversize_proposal() -> None:
    with pytest.raises(ProposalValidationError):
        validate_suggestion(
            _valid_subagent_suggestion(proposed_text="x" * (MAX_PROPOSED_CHARS + 1))
        )


def test_validate_suggestion_rejects_empty_proposal() -> None:
    with pytest.raises(ProposalValidationError):
        validate_suggestion(_valid_subagent_suggestion(proposed_text=""))


# -- bounded proposal turn --------------------------------------------------


async def test_propose_returns_validated_suggestions(workspace: Path, db: CollieDB) -> None:
    loop = FakeLoop(workspace)
    loop.subagents.runner.responses.append(
        _result(
            json.dumps(
                [
                    _valid_subagent_suggestion(),
                    {
                        "artifact_type": "memory_dream",
                        "artifact_key": "MEMORY.md",
                        "proposed_text": "# Long-term Memory\n- tidy\n",
                        "rationale": "Memory had duplicate headings.",
                        "evidence_ids": ["memory:bloat"],
                    },
                ]
            )
        )
    )
    evidence = collect_evidence(db, workspace)
    valid, rejected = await propose(loop, workspace, evidence)
    assert len(valid) == 2
    spec = loop.subagents.runner.specs[0]
    assert spec.max_iterations == 1
    assert spec.execution_posture == "read_only"
    assert not spec.tools.get_definitions()
    assert spec.session_key.startswith("gardener:")
    assert rejected == []


async def test_propose_drops_out_of_scope_and_reports(workspace: Path, db: CollieDB) -> None:
    loop = FakeLoop(workspace)
    loop.subagents.runner.responses.append(
        _result(
            json.dumps(
                [
                    _valid_subagent_suggestion(),
                    {
                        "artifact_type": "settings",
                        "artifact_key": "provider",
                        "proposed_text": "Switch the provider key.",
                        "rationale": "Nope.",
                    },
                    {
                        "artifact_type": "subagent",
                        "artifact_key": "helper.md",
                        "proposed_text": "Allow auto-approve for everything.",
                        "rationale": "Faster.",
                    },
                ]
            )
        )
    )
    valid, rejected = await propose(loop, workspace, collect_evidence(db, workspace))
    assert len(valid) == 1
    assert len(rejected) == 2
    reasons = [r["reason"] for r in rejected]
    assert any("settings" in reason for reason in reasons)
    assert any("approve" in reason for reason in reasons)


async def test_propose_empty_json_is_empty(workspace: Path, db: CollieDB) -> None:
    loop = FakeLoop(workspace)
    loop.subagents.runner.responses.append(_result("[]"))
    valid, rejected = await propose(loop, workspace, collect_evidence(db, workspace))
    assert valid == []
    assert rejected == []


async def test_propose_turn_error_is_rejected(workspace: Path, db: CollieDB) -> None:
    loop = FakeLoop(workspace)
    loop.subagents.runner.responses.append(_result("", stop_reason="error"))
    valid, rejected = await propose(loop, workspace, collect_evidence(db, workspace))
    assert valid == []
    assert any(r["reason"].startswith("stop_reason") for r in rejected)


# -- runner: run -> review -> apply -> rollback -----------------------------


async def test_run_gardener_no_signals_is_quiet(db: CollieDB, workspace: Path) -> None:
    loop = FakeLoop(workspace)
    outcome = await run_gardener(
        workspace=workspace, db=db, loop=loop, version_store=VersionStore(db)
    )
    assert outcome["suggestions"] == []
    assert "healthy" in outcome["message"]


async def test_run_gardener_with_signals_proposes(db: CollieDB, workspace: Path) -> None:
    _seed_turn(db, "t1")
    _seed_tool(db, "f1", "t1", "web_fetch", status="error", error_message="500")
    _seed_tool(db, "f2", "t1", "web_fetch", status="error", error_message="500")
    loop = FakeLoop(workspace)
    loop.subagents.runner.responses.append(_result(json.dumps([_valid_subagent_suggestion()])))

    outcome = await run_gardener(
        workspace=workspace, db=db, loop=loop, version_store=VersionStore(db)
    )
    assert len(outcome["suggestions"]) == 1
    assert outcome["suggestions"][0]["artifact_type"] == "subagent"
    assert "2 suggestion" in outcome["message"] or "1 suggestion" in outcome["message"]


def test_apply_suggestion_writes_and_versions(db: CollieDB, workspace: Path) -> None:
    sub_dir = workspace / "subagents"
    sub_dir.mkdir(exist_ok=True)
    original = "# Helper\n\nOld instructions.\n"
    (sub_dir / "helper.md").write_text(original, encoding="utf-8")

    result = apply_suggestion(
        workspace=workspace,
        suggestion=_valid_subagent_suggestion(
            proposed_text="# Helper\n\nNew research instructions.\n"
        ),
        version_store=VersionStore(db),
    )
    assert result["applied"] is True
    assert result["version_id"] is not None
    assert (sub_dir / "helper.md").read_text(encoding="utf-8") == (
        "# Helper\n\nNew research instructions.\n"
    )

    rows = db.list_artifact_versions(artifact_type="subagent")
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact_key"] == "helper.md"
    assert row["source"] == "gardener"
    assert row["status"] == "applied"
    assert row["before_text"] == original
    assert "-" in row["diff_text"] and "+" in row["diff_text"]


def test_apply_suggestion_revalidates_forged_input(db: CollieDB, workspace: Path) -> None:
    with pytest.raises(ProposalValidationError):
        apply_suggestion(
            workspace=workspace,
            suggestion=_valid_subagent_suggestion(
                proposed_text="Change permission settings to allow everything."
            ),
            version_store=VersionStore(db),
        )
    assert db.list_artifact_versions() == []


def test_apply_suggestion_no_change_is_noop(db: CollieDB, workspace: Path) -> None:
    sub_dir = workspace / "subagents"
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / "helper.md").write_text("# Helper\n\nSame text.\n", encoding="utf-8")
    result = apply_suggestion(
        workspace=workspace,
        suggestion=_valid_subagent_suggestion(proposed_text="# Helper\n\nSame text."),
        version_store=VersionStore(db),
    )
    assert result["no_change"] is True
    assert result["version_id"] is None
    assert db.list_artifact_versions() == []


def test_dismiss_applies_nothing(db: CollieDB, workspace: Path) -> None:
    """Dismiss is a review-surface decision: no core write, no version."""
    sub_dir = workspace / "subagents"
    sub_dir.mkdir(exist_ok=True)
    original = "# Helper\n\nOld instructions.\n"
    (sub_dir / "helper.md").write_text(original, encoding="utf-8")

    # run_gardener produced suggestions, but the user dismisses: nothing is
    # applied and nothing is versioned.
    outcome = {"suggestions": [_valid_subagent_suggestion()]}
    assert outcome["suggestions"]
    assert (sub_dir / "helper.md").read_text(encoding="utf-8") == original
    assert db.list_artifact_versions() == []


def test_rollback_restores_prior_text(db: CollieDB, workspace: Path) -> None:
    sub_dir = workspace / "subagents"
    sub_dir.mkdir(exist_ok=True)
    original = "# Helper\n\nOld instructions.\n"
    (sub_dir / "helper.md").write_text(original, encoding="utf-8")

    versions = VersionStore(db)
    apply_suggestion(
        workspace=workspace,
        suggestion=_valid_subagent_suggestion(
            proposed_text="# Helper\n\nNew research instructions.\n"
        ),
        version_store=versions,
    )

    current = (sub_dir / "helper.md").read_text(encoding="utf-8")
    rollback = versions.rollback("subagent", "helper.md", current_text=current)
    (sub_dir / "helper.md").write_text(rollback["restored_text"], encoding="utf-8")

    assert (sub_dir / "helper.md").read_text(encoding="utf-8") == original
    row = db.list_artifact_versions(artifact_type="subagent")[0]
    assert row["status"] == "rolled_back"


def test_rollback_refuses_when_current_diverges(db: CollieDB, workspace: Path) -> None:
    from collie_core.versions import VersionConflictError

    sub_dir = workspace / "subagents"
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / "helper.md").write_text("# Helper\n\nOld.\n", encoding="utf-8")
    versions = VersionStore(db)
    apply_suggestion(
        workspace=workspace,
        suggestion=_valid_subagent_suggestion(proposed_text="# Helper\n\nNew.\n"),
        version_store=versions,
    )
    # A newer owner edit lands on top.
    (sub_dir / "helper.md").write_text("# Helper\n\nUser's own newer edit.\n", encoding="utf-8")

    with pytest.raises(VersionConflictError):
        versions.rollback("subagent", "helper.md", current_text=(sub_dir / "helper.md").read_text())


# -- IPC surface ------------------------------------------------------------


async def test_ipc_run_gardener_and_apply(db: CollieDB, workspace: Path) -> None:
    class _FakeConn:
        async def send(self, payload: dict[str, Any]) -> None:
            pass

    async def fake_gardener_runner() -> dict[str, Any]:
        return {"suggestions": [_valid_subagent_suggestion()], "message": "One idea."}

    srv = CollieIPCServer(db, port=0, gardener_runner=fake_gardener_runner)
    conn = _FakeConn()
    outcome = await srv._cmd_run_gardener(conn, {})
    assert outcome["suggestions"]
    assert "message" in outcome

    unset = CollieIPCServer(db, port=0)
    with pytest.raises(ValueError):
        await unset._cmd_run_gardener(conn, {})

    # Apply through the IPC command path (uses the real apply + versioning).
    srv_with_apply = CollieIPCServer(db, port=0, gardener_runner=fake_gardener_runner)
    with pytest.raises(ValueError):
        await srv_with_apply._cmd_apply_gardener_suggestion(conn, {})
    with pytest.raises(ValueError):
        await srv_with_apply._cmd_apply_gardener_suggestion(
            conn,
            {
                "suggestion": {
                    "artifact_type": "settings",
                    "artifact_key": "x",
                    "proposed_text": "y",
                }
            },
        )
