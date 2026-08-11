import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import collie_core.tools.local_files as local_files
from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext, Risk
from collie_core.permissions.store import PermissionStore
from collie_core.tools.local_files import LocalFilesTool
from collie_core.undo.journal import undo_entries
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    clear_live_local_file_scope,
    reset_workspace_scope,
    set_live_local_file_scope,
)


@pytest.fixture
def scoped_tool(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    token = bind_workspace_scope(
        build_workspace_scope(root, "restricted", source_channel="websocket")
    )
    try:
        yield LocalFilesTool(), root
    finally:
        reset_workspace_scope(token)


@pytest.mark.asyncio
async def test_local_files_create_read_edit_and_list_are_bounded(scoped_tool) -> None:
    tool, root = scoped_tool

    created = await tool.execute(operation="create", path="meeting.md", content="Agenda\n- welcome")
    assert not created.is_error
    assert (root / "meeting.md").read_text(encoding="utf-8") == "Agenda\n- welcome"

    edited = await tool.execute(
        operation="edit", path="meeting.md", old_text="welcome", new_text="introductions"
    )
    assert not edited.is_error
    read = await tool.execute(operation="read", path="meeting.md")
    assert json.loads(read)["content"] == "Agenda\n- introductions"
    assert len(json.loads(read)["sha256"]) == 64

    listing = await tool.execute(operation="list", path=".")
    assert json.loads(listing)["entries"] == [{"name": "meeting.md", "kind": "file"}]


@pytest.mark.asyncio
async def test_local_files_rejects_traversal_binary_and_duplicate_edits(
    scoped_tool, tmp_path: Path
) -> None:
    tool, root = scoped_tool
    (tmp_path / "outside.txt").write_text("private", encoding="utf-8")
    (root / "twice.txt").write_text("one one", encoding="utf-8")

    escaped = await tool.execute(operation="read", path="../outside.txt")
    assert escaped.is_error
    assert "outside" in escaped

    binary = await tool.execute(operation="create", path="report.docx", content="not a docx")
    assert binary.is_error
    assert "supported text artifact" in binary

    ambiguous = await tool.execute(
        operation="edit", path="twice.txt", old_text="one", new_text="two"
    )
    assert ambiguous.is_error
    assert (root / "twice.txt").read_text(encoding="utf-8") == "one one"


@pytest.mark.asyncio
async def test_local_files_rejects_symlink_escape(scoped_tool, tmp_path: Path) -> None:
    tool, root = scoped_tool
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = await tool.execute(operation="read", path="linked.txt")

    assert result.is_error
    assert "Symlink" in result


@pytest.mark.asyncio
async def test_local_files_rejects_symlink_even_with_full_file_access(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    token = bind_workspace_scope(build_workspace_scope(root, "full", source_channel="websocket"))
    try:
        result = await LocalFilesTool().execute(operation="read", path="linked.txt")
    finally:
        reset_workspace_scope(token)

    assert result.is_error
    assert "Symlink" in result


@pytest.mark.asyncio
async def test_local_files_rejects_mapped_windows_drive(
    scoped_tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, root = scoped_tool
    (root / "meeting.md").write_text("Agenda", encoding="utf-8")
    monkeypatch.setattr(local_files, "is_local_filesystem_path", lambda _path: False)

    result = await tool.execute(operation="read", path="meeting.md")

    assert result.is_error
    assert "Network drives" in result


@pytest.mark.asyncio
async def test_local_files_create_never_overwrites_and_save_is_atomic(scoped_tool) -> None:
    tool, root = scoped_tool
    (root / "report.txt").write_text("before", encoding="utf-8")

    duplicate = await tool.execute(operation="create", path="report.txt", content="after")
    assert duplicate.is_error
    assert (root / "report.txt").read_text(encoding="utf-8") == "before"

    saved = await tool.execute(operation="save", path="report.txt", content="after")
    assert not saved.is_error
    assert (root / "report.txt").read_text(encoding="utf-8") == "after"
    assert not list(root.glob(".collie-*-*"))


@pytest.mark.asyncio
async def test_local_files_expected_hash_prevents_stale_overwrite(scoped_tool) -> None:
    tool, root = scoped_tool
    target = root / "report.txt"
    target.write_text("before", encoding="utf-8")

    result = await tool.execute(
        operation="overwrite",
        path="report.txt",
        content="after",
        expected_sha256="0" * 64,
    )

    assert result.is_error
    assert target.read_text(encoding="utf-8") == "before"


def test_local_files_permission_metadata_is_local_and_in_scope(scoped_tool) -> None:
    tool, root = scoped_tool

    write = tool.permission_request({"operation": "save", "path": "draft.txt", "content": "hello"})
    assert write.action == "local_file.write"
    assert write.resource == str((root / "draft.txt").resolve())
    assert write.risk.value == "local_write"
    # A brand-new file inside the granted folder is reversible bounded work:
    # the folder grant covers it without a card.
    assert write.approval_free is True
    assert write.approve_for_me is True
    assert write.data_leaving_device == ()
    assert write.redacted_parameters["allowed_local_roots"] == [str(root.resolve())]

    with request_context(
        RequestContext(
            channel="test",
            chat_id="test",
            metadata={"permission_context": {"model_provider": "ChatGPT"}},
        )
    ):
        read = tool.permission_request({"operation": "read", "path": "draft.txt"})
    # Reading inside a granted folder is consent for the content there; the
    # provider is still disclosed honestly in the summary and metadata.
    assert read.risk == Risk.READ
    assert read.resource == str((root / "draft.txt").resolve())
    assert read.data_leaving_device == ("ChatGPT",)
    assert read.redacted_parameters["model_provider"] == "ChatGPT"
    assert read.hard_approval is False
    assert read.approval_free is False
    assert read.approve_for_me is False

    (root / "draft.txt").write_text("existing", encoding="utf-8")
    overwrite = tool.permission_request(
        {"operation": "save", "path": "draft.txt", "content": "replacement"}
    )
    # Overwriting an existing file destroys prior content: still a card
    # (or run-approvable), never silently automatic.
    assert overwrite.reversible is False
    assert overwrite.approval_free is False
    assert overwrite.approve_for_me is True

    with pytest.raises(PermissionDeniedError):
        tool.permission_request({"operation": "save", "path": "../outside.txt", "content": "no"})
    with pytest.raises(PermissionDeniedError):
        tool.permission_request({"operation": "read", "path": "../outside.txt"})


@pytest.mark.asyncio
async def test_local_file_read_inside_granted_scope_is_authorized_silently(
    scoped_tool, tmp_path: Path
) -> None:
    """Folder selection is consent for in-scope reads: no card is raised.

    The provider is still disclosed honestly on the request itself
    (summary + data_leaving_device) even though no approval is needed.
    """
    tool, root = scoped_tool
    target = root / "private.txt"
    target.write_text("private", encoding="utf-8")
    db = CollieDB(tmp_path / "collie.db")
    events: list[dict] = []

    async def broadcaster(payload: dict) -> None:
        events.append(payload)

    broker = ApprovalBroker(
        db,
        PermissionEvaluator(PermissionStore(db)),
        broadcaster=broadcaster,
        timeout_seconds=1,
    )
    with request_context(
        RequestContext(
            channel="test",
            chat_id="test",
            metadata={"permission_context": {"model_provider": "ChatGPT"}},
        )
    ):
        request = tool.permission_request({"operation": "read", "path": "private.txt"})
        assert request.risk == Risk.READ
        assert request.hard_approval is False
        assert request.data_leaving_device == ("ChatGPT",)
        assert "send its text to ChatGPT" in request.summary
        assert request.redacted_parameters["allowed_local_roots"] == [str(root.resolve())]

        await broker.authorize(
            ExecutionContext(run_id="run-1"),
            SimpleNamespace(id="call-1", name="local_files"),
            tool,
            {"operation": "read", "path": "private.txt"},
        )
    assert events == []
    assert broker.db.list_pending_approvals() == []
    db.close()


@pytest.mark.asyncio
async def test_live_file_access_override_applies_mid_turn(tmp_path: Path) -> None:
    """A scope change while the turn runs applies to the very next tool call."""
    root = tmp_path / "project"
    root.mkdir()
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    token = bind_workspace_scope(
        build_workspace_scope(root, "restricted", source_channel="websocket")
    )
    set_live_local_file_scope("conv-live", (desktop,), False)
    try:
        with request_context(
            RequestContext(
                channel="collie",
                chat_id="conv-live",
                metadata={"permission_context": {"conversation_id": "conv-live"}},
            )
        ):
            tool = LocalFilesTool()
            # The target is outside the turn-bound project, but the live
            # override (the user just granted the Desktop folder) admits it.
            request = tool.permission_request(
                {"operation": "save", "path": str(desktop / "draft.txt"), "content": "hi"}
            )
            assert request.resource == str((desktop / "draft.txt").resolve())
            assert request.redacted_parameters["allowed_local_roots"] == [str(desktop.resolve())]
            result = await tool.execute(
                operation="save", path=str(desktop / "draft.txt"), content="hi"
            )
            assert not result.is_error
            assert (desktop / "draft.txt").read_text(encoding="utf-8") == "hi"
    finally:
        clear_live_local_file_scope("conv-live")
        reset_workspace_scope(token)


def test_live_file_access_override_is_conversation_scoped(tmp_path: Path) -> None:
    """An override for one conversation never leaks into another."""
    root = tmp_path / "project"
    root.mkdir()
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    token = bind_workspace_scope(
        build_workspace_scope(root, "restricted", source_channel="websocket")
    )
    set_live_local_file_scope("conv-a", (desktop,), False)
    try:
        with request_context(
            RequestContext(
                channel="collie",
                chat_id="conv-b",
                metadata={"permission_context": {"conversation_id": "conv-b"}},
            )
        ):
            with pytest.raises(PermissionDeniedError):
                LocalFilesTool().permission_request(
                    {
                        "operation": "save",
                        "path": str(desktop / "draft.txt"),
                        "content": "no",
                    }
                )
    finally:
        clear_live_local_file_scope("conv-a")
        reset_workspace_scope(token)


@pytest.mark.asyncio
async def test_local_files_writes_journal_undoable_entries(
    scoped_tool, tmp_path: Path, monkeypatch
) -> None:
    """Writes inside a conversation snapshot the pre-write state; undo restores."""
    tool, root = scoped_tool
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))

    with request_context(
        RequestContext(
            channel="collie",
            chat_id="conv-undo",
            metadata={"permission_context": {"conversation_id": "conv-undo"}},
        )
    ):
        created = await tool.execute(operation="create", path="notes.md", content="draft")
        assert not created.is_error
        created_id = json.loads(created).get("undo_entry_id")
        assert created_id

        edited = await tool.execute(
            operation="edit", path="notes.md", old_text="draft", new_text="final"
        )
        assert not edited.is_error
        edited_id = json.loads(edited).get("undo_entry_id")
        assert edited_id
        assert (root / "notes.md").read_text(encoding="utf-8") == "final"

        undone = undo_entries("conv-undo")
        assert {item["id"] for item in undone["undone"]} == {created_id, edited_id}
        # Undo order is newest-first: the edit restores "draft", then the
        # create entry removes the file entirely.
        assert not (root / "notes.md").exists()
        assert undone["errors"] == []


@pytest.mark.asyncio
async def test_local_files_writes_without_conversation_skip_journaling(
    scoped_tool, tmp_path: Path, monkeypatch
) -> None:
    """No conversation id in scope -> write still works, no undo entry."""
    tool, root = scoped_tool
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))

    created = await tool.execute(operation="create", path="notes.md", content="draft")
    assert not created.is_error
    assert "undo_entry_id" not in json.loads(created)


@pytest.mark.asyncio
async def test_local_files_failed_write_discards_phantom_entry(
    scoped_tool, tmp_path: Path, monkeypatch
) -> None:
    """A write that fails must not leave an undoable journal entry behind."""
    tool, root = scoped_tool
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))

    with request_context(
        RequestContext(
            channel="collie",
            chat_id="conv-undo",
            metadata={"permission_context": {"conversation_id": "conv-undo"}},
        )
    ):
        created = await tool.execute(operation="create", path="notes.md", content="draft")
        assert not created.is_error
        created_id = json.loads(created).get("undo_entry_id")
        assert created_id

        # old_text does not match -> the edit fails after record_write ran.
        failed = await tool.execute(
            operation="edit", path="notes.md", old_text="missing", new_text="nope"
        )
        assert failed.is_error
        # The phantom entry from the failed edit was discarded; the create
        # entry remains the only undoable one, and undoing it removes the file.
        entries = undo_entries("conv-undo")
        assert [item["id"] for item in entries["undone"]] == [created_id]
        assert not (root / "notes.md").exists()
