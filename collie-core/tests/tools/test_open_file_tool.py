"""Tests for the bounded default-app opener tool (``open_file``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import collie_core.tools.open_file as open_file_module
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext, Risk
from collie_core.tools.open_file import OpenFileTool
from nanobot.agent.tools.loader import ToolLoader
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)


@pytest.fixture
def scoped_tool(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    token = bind_workspace_scope(
        build_workspace_scope(root, "restricted", source_channel="websocket")
    )
    try:
        yield OpenFileTool(), root
    finally:
        reset_workspace_scope(token)


@pytest.fixture
def fake_launcher(monkeypatch: pytest.MonkeyPatch):
    launched: list[Path] = []

    def _fake(path: Path) -> None:
        launched.append(path)

    monkeypatch.setattr(open_file_module, "_open_with_default_app", _fake)
    return launched


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opens_allowlisted_text_file_with_default_app(scoped_tool, fake_launcher) -> None:
    tool, root = scoped_tool
    (root / "features.md").write_text("# My features", encoding="utf-8")

    result = await tool.execute(path="features.md")

    assert not result.is_error
    payload = json.loads(result)
    assert payload["opened"] == str(root / "features.md")
    assert payload["kind"] == "file"
    assert payload["default_app"] is True
    assert fake_launcher == [root / "features.md"]


@pytest.mark.asyncio
async def test_opens_binary_document_with_default_app(scoped_tool, fake_launcher) -> None:
    """The opener may launch Word/PDF viewers even though local_files can't read them."""
    tool, root = scoped_tool
    (root / "report.pdf").write_bytes(b"%PDF-1.4 fake")

    result = await tool.execute(path="report.pdf")

    assert not result.is_error
    assert json.loads(result)["kind"] == "file"
    assert fake_launcher == [root / "report.pdf"]


@pytest.mark.asyncio
async def test_opens_folder_in_file_explorer(scoped_tool, fake_launcher) -> None:
    tool, root = scoped_tool

    result = await tool.execute(path=".")

    assert not result.is_error
    payload = json.loads(result)
    assert payload["kind"] == "folder"
    assert payload["opened"] == str(root)
    assert fake_launcher == [root]


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["tool.exe", "setup.msi", "run.bat", "evil.ps1", "go.sh", "x.lnk", "x.url", "x.js"]
)
async def test_rejects_executables_scripts_and_shortcuts(
    scoped_tool, fake_launcher, name: str
) -> None:
    tool, root = scoped_tool
    (root / name).write_bytes(b"payload")

    result = await tool.execute(path=name)

    assert result.is_error
    assert "harmless file types" in result
    assert fake_launcher == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["page.html", "page.htm", "logo.svg"])
async def test_rejects_browser_rendered_types(scoped_tool, fake_launcher, name: str) -> None:
    """html/svg can execute scripts or reach the network in the default
    handler, so they are refused like executables."""

    tool, root = scoped_tool
    (root / name).write_bytes(b"payload")

    result = await tool.execute(path=name)

    assert result.is_error
    assert "harmless file types" in result
    assert fake_launcher == []


@pytest.mark.asyncio
async def test_rejects_out_of_scope_path(scoped_tool, tmp_path: Path, fake_launcher) -> None:
    tool, _root = scoped_tool
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")

    result = await tool.execute(path=str(outside))

    assert result.is_error
    assert "outside" in result
    assert fake_launcher == []


@pytest.mark.asyncio
async def test_rejects_nonexistent_file(scoped_tool, fake_launcher) -> None:
    tool, _root = scoped_tool

    result = await tool.execute(path="missing.md")

    assert result.is_error
    assert "does not exist" in result
    assert fake_launcher == []


@pytest.mark.asyncio
async def test_rejects_symlink_escape(scoped_tool, tmp_path: Path, fake_launcher) -> None:
    tool, root = scoped_tool
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = await tool.execute(path="linked.md")

    assert result.is_error
    assert "Symlink" in result
    assert fake_launcher == []


@pytest.mark.asyncio
async def test_rejects_mapped_windows_drive(
    scoped_tool, monkeypatch: pytest.MonkeyPatch, fake_launcher
) -> None:
    import collie_core.tools.local_files as local_files

    tool, root = scoped_tool
    (root / "meeting.md").write_text("Agenda", encoding="utf-8")
    monkeypatch.setattr(local_files, "is_local_filesystem_path", lambda _path: False)

    result = await tool.execute(path="meeting.md")

    assert result.is_error
    assert "Network drives" in result
    assert fake_launcher == []


@pytest.mark.asyncio
async def test_friendly_error_when_no_default_opener_exists(
    scoped_tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, root = scoped_tool
    (root / "notes.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(open_file_module.sys, "platform", "linux")
    monkeypatch.setattr(open_file_module.shutil, "which", lambda _name: None)

    result = await tool.execute(path="notes.md")

    assert result.is_error
    assert "No default-app opener" in result


@pytest.mark.asyncio
async def test_linux_launcher_uses_xdg_open(scoped_tool, monkeypatch: pytest.MonkeyPatch) -> None:
    tool, root = scoped_tool
    (root / "notes.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(open_file_module.sys, "platform", "linux")
    monkeypatch.setattr(open_file_module.shutil, "which", lambda _name: "/usr/bin/xdg-open")
    launched: list[str] = []

    def _run(argv, **kwargs):
        launched.append(argv[-1])
        return type("C", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(open_file_module.subprocess, "run", _run)

    result = await tool.execute(path="notes.md")

    assert not result.is_error
    assert launched == [str(root / "notes.md")]


@pytest.mark.asyncio
async def test_windows_launcher_uses_startfile(
    scoped_tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, root = scoped_tool
    (root / "notes.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(open_file_module.sys, "platform", "win32")
    started: list[str] = []
    monkeypatch.setattr(open_file_module.os, "startfile", started.append, raising=False)

    result = await tool.execute(path="notes.md")

    assert not result.is_error
    assert started == [str(root / "notes.md")]


# ---------------------------------------------------------------------------
# permission posture
# ---------------------------------------------------------------------------


def test_permission_request_is_read_only_local_open() -> None:
    request = OpenFileTool().permission_request(
        {"path": "/home/rick/.collie/workspace/features.md"}
    )

    assert request.action == "local_file.open"
    assert request.risk == Risk.READ
    assert request.reversible is True
    assert request.data_leaving_device == ()
    assert request.hard_approval is False
    assert request.approval_free is False
    assert request.approve_for_me is False
    assert request.redacted_parameters["local_only"] is True
    assert request.redacted_parameters["default_app"] is True
    # Scope metadata matches local_files so granted-folder opens hit the
    # evaluator's read-allow path instead of asking.
    assert request.redacted_parameters["allowed_local_roots"] == []
    assert request.redacted_parameters["unrestricted_local_files"] is False


def test_read_risk_opens_are_allowed_by_default_evaluator() -> None:
    evaluator = PermissionEvaluator()
    request = OpenFileTool().permission_request(
        {"path": "/home/rick/.collie/workspace/features.md"}
    )
    context = ExecutionContext(execution_mode="execute")

    decision = evaluator.evaluate(context, request)

    assert decision.effect.value == "allow"


def test_description_claims_are_truthful() -> None:
    """Browser-rendered types (html/svg) can trigger network activity in the
    default app, so the description must promise provider-silence, not
    blanket network silence."""

    description = OpenFileTool().description

    assert "never sent to Collie or your model provider" in description
    assert "Nothing is sent anywhere" not in description


def test_open_file_tool_is_discoverable() -> None:
    import collie_core.tools as collie_tools

    names = {tool.__name__ for tool in ToolLoader(collie_tools).discover()}
    assert "OpenFileTool" in names
