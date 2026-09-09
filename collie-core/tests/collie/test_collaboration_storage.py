from __future__ import annotations

import hashlib
import json

import pytest

from collie_core.collaboration import (
    ArchiveError,
    ArchiveManager,
    CollaborationStore,
    SharedExecutionContext,
    SyncConflict,
)
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, PermissionRequest, Risk
from collie_core.db import CollieDB


def _event(seq: int, *, event_id: str | None = None, content: str = "hello") -> dict:
    return {
        "event_id": event_id or f"event-{seq}", "session_id": "session-1", "seq": seq,
        "kind": "message", "message_id": f"message-{seq}", "author_id": "alice",
        "role": "user", "content": content, "revision": 1,
        "created_at": "2026-09-09T00:00:00+00:00",
    }


def test_page_and_cursor_commit_atomically_and_deduplicate(tmp_path):
    store = CollaborationStore(tmp_path / "collaboration.db")
    store.bind_account("alice")
    assert store.apply_page("session-1", [_event(1)], next_cursor=1) == 1
    assert store.apply_page("session-1", [_event(1)], next_cursor=1) == 1
    with pytest.raises(SyncConflict):
        store.apply_page("session-1", [_event(2, event_id="event-1", content="changed")], next_cursor=2)
    assert store.cursor("session-1") == 1
    assert [item["content"] for item in store.materialized_messages("session-1")] == ["hello"]


def test_outbox_rejects_event_id_reuse(tmp_path):
    store = CollaborationStore(tmp_path / "collaboration.db")
    store.bind_account("alice")
    draft = _event(0)
    store.queue_event("session-1", draft)
    with pytest.raises(SyncConflict):
        store.queue_event("session-1", draft | {"content": "different"})
    assert store.materialized_messages("session-1")[0]["sync_state"] == "local"


def test_local_cache_is_partitioned_by_signed_in_account(tmp_path):
    store = CollaborationStore(tmp_path / "collaboration.db")
    store.bind_account("alice")
    store.queue_event("session-1", _event(0))
    store.bind_account("bob")
    assert store.pending() == []
    assert store.materialized_messages("session-1") == []


def test_routine_result_can_be_queued_for_creator_while_another_account_is_bound(tmp_path):
    store = CollaborationStore(tmp_path / "collaboration.db")
    store.bind_account("bob")
    store.queue_event_for_account("alice", "session-1", _event(0))
    assert store.pending() == []
    store.bind_account("alice")
    assert [item["event_id"] for item in store.pending()] == ["event-0"]


def test_bootstrap_cache_is_durable_and_account_partitioned(tmp_path):
    path = tmp_path / "collaboration.db"
    store = CollaborationStore(path)
    store.bind_account("alice")
    store.cache_bootstrap({"revision": 4, "organizations": [{"id": "org"}]})
    store.close()
    reopened = CollaborationStore(path)
    reopened.bind_account("alice")
    assert reopened.cached_bootstrap()["revision"] == 4
    reopened.bind_account("bob")
    assert reopened.cached_bootstrap() is None


def test_archive_ack_data_requires_durable_verified_readback(tmp_path):
    manager = ArchiveManager(tmp_path / "archives")
    manager.bind_account("alice")
    manifest = {
        "version": 1, "session_id": "session-1", "membership_revision": 2,
        "final_seq": 1, "recipients": ["alice"], "events": [_event(1)], "files": [],
    }
    raw = json.dumps(manifest, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    receipt = manager.write(raw, digest)
    assert receipt["final_seq"] == 1
    assert manager.verify(receipt["path"])["recipients"] == ["alice"]
    with pytest.raises(ArchiveError):
        manager.write(raw, "0" * 64)


def test_archive_accepts_draft_final_sequence_alias_without_changing_digest(tmp_path):
    manager = ArchiveManager(tmp_path / "archives")
    manager.bind_account("alice")
    manifest = {
        "version": 1, "session_id": "session-legacy", "membership_revision": 1,
        "final_sequence": 1, "recipients": ["alice"], "events": [_event(1)], "files": [],
    }
    raw = json.dumps(manifest, separators=(",", ":"))
    receipt = manager.write(raw, hashlib.sha256(raw.encode()).hexdigest())
    assert receipt["final_seq"] == 1


def test_shared_claim_must_match_enrolled_requester_and_device():
    claim = {
        "session_id": "s", "requester_id": "alice", "credential_owner_id": "alice",
        "executor_device_id": "device-a", "audience_revision": 1, "context_cutoff": 4,
        "run_id": "run", "lease_token": "fence",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
    }
    SharedExecutionContext.from_claim(
        claim, enrolled_account_id="alice", enrolled_device_id="device-a"
    )
    with pytest.raises(ValueError):
        SharedExecutionContext.from_claim(
            claim, enrolled_account_id="bob", enrolled_device_id="device-a"
        )


def test_shared_memory_write_always_requires_requester_approval():
    context = ExecutionContext(
        run_id="run", requester_id="alice", credential_owner_id="alice",
        executor_device_id="device", shared_session_id="session",
        audience_revision=1, context_cutoff=2, lease_token="lease",
    )
    request = PermissionRequest(
        action="memory.write", resource="profile", risk=Risk.LOCAL_WRITE,
        summary="Remember", reversible=True, approval_free=True,
    )
    assert PermissionEvaluator().evaluate(context, request).effect == Effect.ASK


def test_routine_shared_delivery_config_and_status_are_separate_from_run(tmp_path):
    db = CollieDB(tmp_path / "collie.db")
    routine = db.add_automation("Daily note", automation_id="routine-1")
    configured = db.set_routine_shared_delivery(routine["id"], {
        "session_id": "session-1", "audience_revision": 3,
        "creator_account_id": "alice",
    })
    assert json.loads(configured["shared_delivery"])["audience_revision"] == 3
    db.mark_routine_result(routine["id"], success=True)
    db.record_routine_shared_delivery(
        routine["id"], status="pending", event_id="event-1", error="offline"
    )
    saved = db.get_automation(routine["id"])
    assert saved["last_success_at"]
    assert saved["shared_delivery_status"] == "pending"
    assert saved["shared_delivery_event_id"] == "event-1"
