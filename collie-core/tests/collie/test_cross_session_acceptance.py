"""One focused acceptance scenario: actual committed evidence and safe handoff."""
import asyncio
from types import SimpleNamespace

import pytest

from collie_core.cross_session import CrossSession
from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.tools.cross_session import ReadChatsTool, StartChatTool, bind_cross_session
from nanobot.agent.tools.context import RequestContext, request_context


@pytest.mark.asyncio
async def test_committed_recall_and_idempotent_independent_handoff(tmp_path):
    db = CollieDB(tmp_path / "collie.db")
    calls = []

    async def runner(content, **kwargs):
        calls.append((content, kwargs))
        return SimpleNamespace(content="Finished target task")

    server = CollieIPCServer(db, chat_runner=runner)
    service = server.cross_session
    bind_cross_session(service)
    for cid in ("one", "two", "private", "shared-secret", "temp-secret"):
        db.create_conversation(conv_id=cid)
        db.add_message(cid, "user", f"orchid evidence {cid}")
        db.add_message(cid, "assistant", f"committed output {cid}")
    db.set_setting("cross_session.private.private", True)
    before = db.get_messages("one")
    with request_context(RequestContext(channel="collie", chat_id="one", session_key="collie:one",
                                        message_id="request-1", original_user_text="Start this in a new session")):
        found = await ReadChatsTool().execute(query="orchid")
        assert {m["conversation_id"] for m in found["matches"]} == {"one", "two"}
        snapshot = await ReadChatsTool().execute(conversation_id="two")
        assert snapshot["captured_at"] and "Untrusted" in snapshot["trust"]
        assert snapshot["messages"][0]["message_id"] == db.get_messages("two")[0]["id"]
        assert snapshot["messages"][0]["link"].startswith("#chat/two/")
        assert snapshot["messages"][-1]["content"] == "committed output two"
        first, retry = await asyncio.gather(StartChatTool().execute(brief="Prepare an orchid checklist"),
                                            StartChatTool().execute(brief="Prepare an orchid checklist"))
    ipc_snapshot = await server._cmd_cross_session_read(None, {"conversation_id": "one", "target_id": "two"})
    assert ipc_snapshot["messages"] == snapshot["messages"]
    cid = first["conversation_id"]
    assert retry["conversation_id"] == cid and retry["replayed"] is False
    if cid in server._chat_tasks:
        await server._chat_tasks[cid]
    assert len(calls) == 1 and calls[0][0] == "Prepare an orchid checklist"
    assert calls[0][1]["message_metadata"]["message_id"] == db.get_messages(cid)[0]["id"]
    assert calls[0][1].get("project_path") is None
    assert calls[0][1]["file_access_scope"] == {"mode": "selected_folder"}
    assert [m["content"] for m in db.get_messages(cid)] == ["Prepare an orchid checklist", "Finished target task"]
    assert db.get_messages("one") == before
    restarted = CrossSession(db, server._launch_cross_session, lambda: set())
    assert (await restarted.start("one", "different brief", "request-1", "new chat"))["conversation_id"] == cid
    assert len(calls) == 1
    for blocked in ("private", "shared-secret", "temp-secret", "not-persisted"):
        with pytest.raises(ValueError):
            service.read("one", target=blocked)
        with pytest.raises(ValueError):
            await service.start(blocked, "secret", "r", "new chat")
    with pytest.raises(ValueError):
        await service.start(cid, "recursive", "r", "new chat")
    with pytest.raises(ValueError):
        await service.start("one", "no authorization", "r", "orchid evidence")
    # An ambiguous dispatch failure retains its reservation; retry never executes again.
    attempts = []
    async def broken(target, brief):
        attempts.append(target)
        raise RuntimeError("interrupted before acknowledgement")
    recovering = CrossSession(db, broken, lambda: set())
    with pytest.raises(RuntimeError):
        await recovering.start("two", "recoverable brief", "failure", "new chat")
    recovered = await recovering.start("two", "recoverable brief", "failure", "new chat")
    assert len(attempts) == 1 and recovered["status"] == "already_reserved"
    assert db.get_messages(recovered["conversation_id"]) == []
    await server.stop()
    db.close()
