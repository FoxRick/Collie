"""Atomic, retry-safe plan approval and execution claims."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer


class _Outbound:
    content = "done"


def _create_plan(
    db: CollieDB,
    *,
    conversation_id: str | None,
    plan_id: str = "plan-atomic",
) -> dict:
    return db.create_plan(
        title="Atomic plan",
        goal="Run exactly once",
        plan={
            "steps": [
                {"key": "one", "title": "First"},
                {"key": "two", "title": "Second"},
            ]
        },
        conversation_id=conversation_id,
        plan_id=plan_id,
    )


def test_claim_plan_execution_approves_inserts_and_seeds_atomically(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))

    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    assert claim["created"] is True
    assert claim["plan"]["status"] == "approved"
    assert claim["run"]["idempotency_key"] == "plan:plan-atomic:v1"
    assert [step["step_key"] for step in db.list_run_steps(claim["run"]["id"])] == [
        "one",
        "two",
    ]

    duplicate = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])
    assert duplicate["created"] is False
    assert duplicate["run"]["id"] == claim["run"]["id"]
    assert len(db.list_runs()) == 1
    db.close()


def test_claim_allows_plan_already_approved_for_a_routine(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))
    db.approve_plan(plan["id"], plan["version"], plan["plan_hash"])

    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    assert claim["created"] is True
    assert claim["plan"]["status"] == "approved"
    assert len(db.list_runs()) == 1
    db.close()


@pytest.mark.parametrize("conversation_id", [None, "deleted-conversation"])
def test_claim_rejects_missing_conversation_without_mutation(
    tmp_path: Path, conversation_id: str | None
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    plan = _create_plan(db, conversation_id=conversation_id)

    with pytest.raises(ValueError, match="conversation"):
        db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    assert db.get_plan(plan["id"], plan["version"])["status"] == "draft"
    assert db.list_runs() == []
    db.close()


def test_claim_rejects_wrong_hash_without_mutation(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))

    with pytest.raises(ValueError, match="plan changed"):
        db.claim_plan_execution(plan["id"], plan["version"], "wrong")

    assert db.get_plan(plan["id"], plan["version"])["status"] == "draft"
    assert db.list_runs() == []
    db.close()


def test_claim_rejects_missing_plan_version_without_creating_run(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    with pytest.raises(ValueError, match="plan version not found"):
        db.claim_plan_execution("missing-plan", 7, "missing-hash")
    assert db.list_runs() == []
    db.close()


def test_seed_failure_rolls_back_plan_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))

    def fail_seed(*_args, **_kwargs) -> None:
        raise RuntimeError("forced seed failure")

    monkeypatch.setattr(db, "_seed_run_steps_with", fail_seed)
    with pytest.raises(RuntimeError, match="forced seed failure"):
        db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    assert db.get_plan(plan["id"], plan["version"])["status"] == "draft"
    assert db.list_runs() == []
    db.close()


def test_two_database_connections_return_one_execution_run(tmp_path: Path) -> None:
    path = tmp_path / "collie.db"
    first_db = CollieDB(path)
    conversation = first_db.create_conversation("Plan")
    plan = _create_plan(first_db, conversation_id=str(conversation["id"]))
    second_db = CollieDB(path)
    barrier = Barrier(2)

    def claim(db: CollieDB) -> dict:
        barrier.wait(timeout=5)
        return db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, (first_db, second_db)))

    assert sorted(item["created"] for item in claims) == [False, True]
    assert len({item["run"]["id"] for item in claims}) == 1
    assert len(first_db.list_runs()) == 1
    assert len(first_db.list_run_steps(claims[0]["run"]["id"])) == 2
    second_db.close()
    first_db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["runner", "provider", "active"])
async def test_ipc_preconditions_do_not_approve_plan(
    tmp_path: Path, failure: str
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))

    async def runner(*_args, **_kwargs):
        return _Outbound()

    server = CollieIPCServer(
        db,
        chat_runner=None if failure == "runner" else runner,
        status_provider=(
            (lambda: {"configured": False})
            if failure == "provider"
            else (lambda: {"configured": True})
        ),
    )
    active_task = None
    if failure == "active":
        active_task = asyncio.create_task(asyncio.Event().wait())
        server._chat_tasks[str(conversation["id"])] = active_task

    try:
        with pytest.raises(ValueError):
            await server._cmd_approve_plan(
                None,  # type: ignore[arg-type]
                {
                    "plan_id": plan["id"],
                    "version": plan["version"],
                    "plan_hash": plan["plan_hash"],
                },
            )
        assert db.get_plan(plan["id"], plan["version"])["status"] == "draft"
        assert db.list_runs() == []
    finally:
        if active_task is not None:
            active_task.cancel()
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_rapid_duplicate_approval_returns_same_run_without_relaunch(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))
    release = asyncio.Event()
    calls = 0

    async def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        return _Outbound()

    server = CollieIPCServer(
        db,
        chat_runner=runner,
        status_provider=lambda: {"configured": True},
    )
    frame = {
        "plan_id": plan["id"],
        "version": plan["version"],
        "plan_hash": plan["plan_hash"],
    }
    try:
        first = await server._cmd_approve_plan(None, frame)  # type: ignore[arg-type]
        second = await server._cmd_approve_plan(None, frame)  # type: ignore[arg-type]
        await asyncio.sleep(0)

        assert first["created"] is True
        assert second["created"] is False
        assert second["run"]["id"] == first["run"]["id"]
        assert len(db.list_runs()) == 1
        assert calls == 1
    finally:
        release.set()
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_task_start_failure_marks_run_failed_and_retry_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))

    async def runner(*_args, **_kwargs):
        await asyncio.Event().wait()
        return _Outbound()

    server = CollieIPCServer(
        db,
        chat_runner=runner,
        status_provider=lambda: {"configured": True},
    )
    original_start = server._start_plan_execution_task

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("task factory unavailable")

    monkeypatch.setattr(server, "_start_plan_execution_task", fail_start)
    with pytest.raises(ValueError, match="couldn't start"):
        await server._cmd_approve_plan(  # type: ignore[arg-type]
            None,
            {
                "plan_id": plan["id"],
                "version": plan["version"],
                "plan_hash": plan["plan_hash"],
            },
        )

    failed = db.list_runs()[0]
    assert db.get_plan(plan["id"], plan["version"])["status"] == "approved"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "task_start_failed"
    assert failed["attempt"] == 1

    monkeypatch.setattr(server, "_start_plan_execution_task", original_start)
    retry = await server._cmd_retry_plan_execution(  # type: ignore[arg-type]
        None, {"run_id": failed["id"]}
    )
    assert retry["run"]["id"] == failed["id"]
    assert retry["run"]["attempt"] == 2
    assert retry["run"]["status"] == "queued"
    assert len(db.list_runs()) == 1
    assert {step["retry_count"] for step in db.list_run_steps(failed["id"])} == {1}

    await server.stop()
    db.close()


@pytest.mark.asyncio
async def test_retry_rejects_superseded_plan_without_mutating_failed_run(
    tmp_path: Path,
) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))
    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])
    failed = db.transition_run(
        claim["run"]["id"],
        "failed",
        error_code="task_start_failed",
        error_message="no task",
    )
    _create_plan(
        db,
        conversation_id=str(conversation["id"]),
        plan_id=str(plan["id"]),
    )

    async def runner(*_args, **_kwargs):
        return _Outbound()

    server = CollieIPCServer(
        db,
        chat_runner=runner,
        status_provider=lambda: {"configured": True},
    )
    with pytest.raises(ValueError, match="changed"):
        await server._cmd_retry_plan_execution(  # type: ignore[arg-type]
            None, {"run_id": failed["id"]}
        )
    unchanged = db.get_run(failed["id"])
    assert unchanged["status"] == "failed"
    assert unchanged["attempt"] == 1
    db.close()


@pytest.mark.asyncio
async def test_retry_rejects_active_conversation_without_requeue(tmp_path: Path) -> None:
    db = CollieDB(tmp_path / "collie.db")
    conversation = db.create_conversation("Plan")
    plan = _create_plan(db, conversation_id=str(conversation["id"]))
    claim = db.claim_plan_execution(plan["id"], plan["version"], plan["plan_hash"])
    failed = db.transition_run(
        claim["run"]["id"],
        "failed",
        error_code="task_start_failed",
        error_message="no task",
    )

    async def runner(*_args, **_kwargs):
        return _Outbound()

    server = CollieIPCServer(
        db,
        chat_runner=runner,
        status_provider=lambda: {"configured": True},
    )
    active = asyncio.create_task(asyncio.Event().wait())
    server._chat_tasks[str(conversation["id"])] = active
    try:
        with pytest.raises(ValueError, match="current turn"):
            await server._cmd_retry_plan_execution(  # type: ignore[arg-type]
                None, {"run_id": failed["id"]}
            )
        unchanged = db.get_run(failed["id"])
        assert unchanged["status"] == "failed"
        assert unchanged["attempt"] == 1
    finally:
        await server.stop()
        db.close()
