"""Dream wiring tests (Gardener Foundations PR 3, two-phase review).

Covers ``collie_core/memory/dream.py`` against a fake loop/provider:
a Dream run stores a **pending proposal** instead of writing
``memory/MEMORY.md``, the cursor advances only on a completed run, the
proposal is applied only on explicit approval (with re-validation) and can
be dismissed, no-change/error runs are no-ops, rollback restores the prior
file, dream sessions are pruned, and the built-in automation seeds
correctly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from collie_core.automations.scheduler import (
    GARDENER_AUTOMATIONS,
    seed_gardener_automations,
)
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.memory.dream import (
    apply_dream_proposal,
    dismiss_dream_proposal,
    get_dream_pending,
    run_dream,
)
from collie_core.versions import VersionStore
from nanobot.agent.memory import MemoryStore
from nanobot.session.manager import SessionManager


class FakeRunner:
    """Stands in for ``SubagentManager.runner``; returns queued results."""

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
    """Minimal AgentLoop stand-in with a real MemoryStore."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.context = SimpleNamespace(memory=MemoryStore(workspace))
        self.subagents = FakeSubagents()

    def llm_runtime(self) -> Any:
        return SimpleNamespace(model="fake-model")

    @property
    def sessions(self) -> Any:
        return SimpleNamespace(sessions_dir=Path(self.workspace) / "sessions")


def _result(content: str, stop_reason: str = "completed") -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        final_content=content,
        tools_used=[],
        messages=[],
        error=None,
    )


def _seed_dream_sessions(workspace: Path, count: int) -> None:
    sessions_dir = workspace / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        key = f"dream:20260805-{100000 + i:06d}"
        path = sessions_dir / f"{SessionManager._storage_key(key)}.jsonl"
        path.write_text('{"_type": "metadata"}\n', encoding="utf-8")


@pytest.fixture()
def db(tmp_path: Path) -> CollieDB:
    d = CollieDB(tmp_path / "collie.db")
    yield d
    d.close()


def _proposal_path(workspace: Path) -> Path:
    return workspace / "memory" / ".dream-proposal.json"


async def test_dream_proposes_pending_and_advances_cursor(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("User prefers concise replies.")
    built = store.build_dream_prompt()
    assert built is not None
    _, cursor = built

    proposed = "# Long-term Memory\n- User prefers concise replies.\n- Projects: Collie"
    loop.subagents.runner.responses.append(_result(proposed))

    versions = VersionStore(db)
    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=versions)

    assert outcome["changed"] is True
    assert outcome["pending"] is True

    # Two-phase review: MEMORY.md is NOT written; the proposal is pending.
    memory_file = workspace / "memory" / "MEMORY.md"
    assert not memory_file.exists()
    assert _proposal_path(workspace).exists()
    proposal = json.loads(_proposal_path(workspace).read_text(encoding="utf-8"))
    assert proposal["proposed"].strip() == proposed.strip()
    assert proposal["before"] == ""
    assert proposal["cursor"] == cursor

    # Bounded, read-only, tool-less turn: exactly one model call.
    spec = loop.subagents.runner.specs[0]
    assert spec.max_iterations == 1
    assert spec.execution_posture == "read_only"
    assert spec.session_key.startswith("dream:")
    assert not spec.tools.get_definitions()

    # Cursor advanced on a completed proposal (history never re-processed).
    assert store.get_last_dream_cursor() == cursor

    # Nothing versioned yet — the version row lands on apply.
    assert db.list_artifact_versions(artifact_type="memory_dream") == []


async def test_apply_dream_proposal_writes_and_versions(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("User prefers concise replies.")
    proposed = "# Long-term Memory\n- User prefers concise replies."
    loop.subagents.runner.responses.append(_result(proposed))

    versions = VersionStore(db)
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=versions)

    memory_file = workspace / "memory" / "MEMORY.md"
    assert not memory_file.exists()

    result = await asyncio.to_thread(
        apply_dream_proposal, workspace=workspace, version_store=versions
    )
    assert result["applied"] is True
    assert result["version_id"] is not None
    assert "-" in result["diff_text"] and "+" in result["diff_text"]
    assert memory_file.read_text(encoding="utf-8").strip() == proposed.strip()
    assert not _proposal_path(workspace).exists()

    # Versioned as memory_dream with a diff, evidence carries the cursor.
    rows = db.list_artifact_versions(artifact_type="memory_dream")
    assert len(rows) == 1
    assert rows[0]["artifact_key"] == "MEMORY.md"
    assert rows[0]["source"] == "collie"
    assert "-" in rows[0]["diff_text"] and "+" in rows[0]["diff_text"]
    evidence = json.loads(rows[0]["evidence_json"])
    assert evidence["cursor"] == store.get_last_dream_cursor()


async def test_apply_refuses_when_memory_changed_since_proposal(
    tmp_path: Path, db: CollieDB
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    proposed = "# Long-term Memory\n- Entry."
    loop.subagents.runner.responses.append(_result(proposed))
    versions = VersionStore(db)
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=versions)

    # A manual edit in between invalidates the proposal.
    memory_file = workspace / "memory" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("# Long-term Memory\n- Manually edited.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed since the review"):
        apply_dream_proposal(workspace=workspace, version_store=versions)
    # Nothing applied, proposal still pending for a fresh decision.
    assert _proposal_path(workspace).exists()
    assert db.list_artifact_versions(artifact_type="memory_dream") == []


async def test_dismiss_dream_proposal_discards_without_writing(
    tmp_path: Path, db: CollieDB
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(_result("# Long-term Memory\n- Entry."))
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))

    result = dismiss_dream_proposal(workspace=workspace)
    assert result["dismissed"] is True
    assert not (workspace / "memory" / "MEMORY.md").exists()
    assert not _proposal_path(workspace).exists()
    assert db.list_artifact_versions(artifact_type="memory_dream") == []

    assert get_dream_pending(workspace=workspace) == {"pending": False}


async def test_run_dream_refuses_when_a_proposal_is_already_pending(
    tmp_path: Path, db: CollieDB
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(_result("# Long-term Memory\n- Entry."))
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))

    # Second run while a proposal awaits review: no new model call, no
    # duplicate proposal.
    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert outcome["changed"] is False
    assert outcome["reason"] == "pending_exists"
    assert len(loop.subagents.runner.specs) == 1
    assert len(list((workspace / "memory").glob(".dream-proposal.json"))) == 1


async def test_get_dream_pending_reports_diff(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(_result("# Long-term Memory\n- Entry."))
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))

    pending = get_dream_pending(workspace=workspace)
    assert pending["pending"] is True
    assert "-" in pending["diff_text"] and "+" in pending["diff_text"]
    assert pending["created_at"]


async def test_dream_no_new_history_is_noop(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert outcome["changed"] is False
    assert outcome["reason"] == "no_new_history"
    assert not (workspace / "memory" / "MEMORY.md").exists()
    assert not _proposal_path(workspace).exists()


async def test_dream_no_change_second_run_advances_cursor(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("First entry.")
    built = store.build_dream_prompt()
    _, cursor1 = built
    proposed = "# Long-term Memory\n- First entry."
    loop.subagents.runner.responses.append(_result(proposed))
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert store.get_last_dream_cursor() == cursor1
    # Apply the pending proposal so memory now matches the proposed content.
    await asyncio.to_thread(
        apply_dream_proposal, workspace=workspace, version_store=VersionStore(db)
    )

    # New history arrives, but the model proposes identical content.
    store.append_history("Second entry.")
    built = store.build_dream_prompt()
    assert built is not None
    _, cursor2 = built
    loop.subagents.runner.responses.append(_result(proposed))
    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert outcome["changed"] is False
    assert outcome["reason"] == "no_content_change"
    # History is processed even when nothing changed — no re-processing loop.
    assert store.get_last_dream_cursor() == cursor2
    # No extra proposal or version rows for a no-op run.
    assert not _proposal_path(workspace).exists()
    assert len(db.list_artifact_versions(artifact_type="memory_dream")) == 1


async def test_dream_error_does_not_advance_cursor_or_write(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(_result("", stop_reason="error"))

    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert outcome["changed"] is False
    assert store.get_last_dream_cursor() == 0
    assert not (workspace / "memory" / "MEMORY.md").exists()
    assert not _proposal_path(workspace).exists()
    assert db.list_artifact_versions(artifact_type="memory_dream") == []


async def test_dream_rollback_restores_prior_memory(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("User prefers concise replies.")
    proposed = "# Long-term Memory\n- User prefers concise replies."
    loop.subagents.runner.responses.append(_result(proposed))
    versions = VersionStore(db)
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=versions)
    await asyncio.to_thread(apply_dream_proposal, workspace=workspace, version_store=versions)

    memory_file = workspace / "memory" / "MEMORY.md"
    current = memory_file.read_text(encoding="utf-8")
    result = versions.rollback("memory_dream", "MEMORY.md", current_text=current)
    memory_file.write_text(result["restored_text"], encoding="utf-8")
    assert memory_file.read_text(encoding="utf-8").strip() == ""
    assert db.list_artifact_versions(artifact_type="memory_dream")[0]["status"] == "rolled_back"


async def test_dream_prune_keeps_10(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _seed_dream_sessions(workspace, 12)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(_result("# Long-term Memory\n- Entry."))
    await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))

    remaining = list((workspace / "sessions").glob("*.jsonl"))
    assert len(remaining) == 10


async def test_dream_fenced_response_is_extracted(tmp_path: Path, db: CollieDB) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    loop = FakeLoop(workspace)
    store = loop.context.memory
    store.append_history("Entry.")
    loop.subagents.runner.responses.append(
        _result("```markdown\n# Long-term Memory\n- Entry.\n```")
    )
    outcome = await run_dream(workspace=workspace, db=db, loop=loop, version_store=VersionStore(db))
    assert outcome["changed"] is True
    await asyncio.to_thread(
        apply_dream_proposal, workspace=workspace, version_store=VersionStore(db)
    )
    memory_file = workspace / "memory" / "MEMORY.md"
    assert "# Long-term Memory" in memory_file.read_text(encoding="utf-8")
    assert "```" not in memory_file.read_text(encoding="utf-8")


def test_seed_gardener_automations_once_and_never_resurrects(tmp_path: Path, db: CollieDB) -> None:
    seed_gardener_automations(db)
    automations = {a["id"]: a for a in db.list_automations()}
    assert "collie-memory-maintenance" in automations
    assert "collie-gardener-suggestions" in automations
    assert automations["collie-memory-maintenance"]["action_type"] == "memory_maintenance"
    assert automations["collie-gardener-suggestions"]["action_type"] == "gardener"

    # Deleting one built-in never resurrects it on later seeds.
    db.delete_automation("collie-memory-maintenance")
    seed_gardener_automations(db)
    ids = {a["id"] for a in db.list_automations()}
    assert "collie-memory-maintenance" not in ids
    assert "collie-gardener-suggestions" in ids

    # The seed data matches the builtin list shape.
    assert {a["id"] for a in GARDENER_AUTOMATIONS} == {
        "collie-memory-maintenance",
        "collie-gardener-suggestions",
    }


async def test_ipc_run_dream_and_history(
    tmp_path: Path, db: CollieDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeConn:
        async def send(self, payload: dict[str, Any]) -> None:
            pass

    async def fake_dream_runner() -> dict[str, Any]:
        return {"changed": False, "reason": "no_new_history", "message": "All tidy."}

    # Keep the new IPC commands on scratch state, never the real home.
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))

    srv = CollieIPCServer(db, port=0, dream_runner=fake_dream_runner)
    conn = _FakeConn()
    outcome = await srv._cmd_run_dream(conn, {})
    assert outcome["changed"] is False
    history = await srv._cmd_get_dream_history(conn, {})
    assert history["versions"] == []

    pending = await srv._cmd_get_dream_pending(conn, {})
    assert pending["pending"] is False
    applied = await srv._cmd_apply_dream_proposal(conn, {})
    assert applied["applied"] is False
    assert applied["reason"] == "no_pending"
    dismissed = await srv._cmd_dismiss_dream_proposal(conn, {})
    assert dismissed["dismissed"] is True

    unset = CollieIPCServer(db, port=0)
    with pytest.raises(ValueError):
        await unset._cmd_run_dream(conn, {})
