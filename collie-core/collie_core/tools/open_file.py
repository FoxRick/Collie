"""Open local files and folders with the operating system's default app.

Deliberately bounded like ``local_files``: only existing, allowlisted
artifact types inside the folders allowed for the current turn, opened by
the OS default handler.  Nothing is written, and the file's contents are
never sent to Collie or a model provider — the local default app renders
it. No executable, script, or shortcut type can be launched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk, Scope

# Reuse the same canonical scope + path-safety resolution as ``local_files``
# (workspace roots, symlink/junction refusal, UNC/device refusal) so the two
# tools can never disagree about what a safe local target is.
from collie_core.tools.local_files import (
    _LocalFileError,
    _resolve_local_path,
    _scope_roots,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.security.workspace_access import current_tool_workspace
from nanobot.security.workspace_policy import WorkspaceBoundaryError

__all__ = ["OpenFileTool"]

# File types that are safe to hand to the OS default handler.  The default
# app for these (viewer/editor/browser) reads the file locally; nothing is
# sent to a model provider or anywhere else on the network.
_OPENABLE_SUFFIXES = frozenset(
    {
        # documents & data
        ".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".rtf",
        ".html", ".htm", ".json", ".xml", ".yaml", ".yml", ".log", ".toml",
        ".ini",
        # images
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
        ".avif", ".tif", ".tiff",
        # audio
        ".mp3", ".wav", ".m4a", ".ogg", ".oga", ".flac", ".opus", ".aac",
        # video
        ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ogv",
    }
)

# Defense in depth: types that must NEVER be handed to a default handler,
# even if a future edit accidentally adds them to the allowlist.  These
# execute code or redirect to an arbitrary target.
_BLOCKED_SUFFIXES = frozenset(
    {
        ".appref-ms", ".bat", ".cpl", ".cmd", ".com", ".exe", ".gadget",
        ".hta", ".jar", ".jse", ".lnk", ".msi", ".msp", ".pif", ".ps1",
        ".psm1", ".reg", ".scr", ".sh", ".bash", ".url", ".vbe", ".vbs",
        ".wsf", ".wsh",
    }
)

_LAUNCH_TIMEOUT_S = 30


class _OpenError(RuntimeError):
    """A user-facing failure to launch the default app."""


def _open_with_default_app(path: Path) -> None:
    """Launch the OS default handler for an existing local path."""
    if sys.platform == "win32":
        os.startfile(os.fspath(path))  # type: ignore[attr-defined]  # Windows only
        return
    if sys.platform == "darwin":
        subprocess.run(
            ["open", os.fspath(path)], check=False, capture_output=True, timeout=_LAUNCH_TIMEOUT_S
        )
        return
    opener = shutil.which("xdg-open")
    if opener is None:
        raise _OpenError("No default-app opener is available on this system.")
    try:
        completed = subprocess.run(
            [opener, os.fspath(path)],
            capture_output=True,
            text=True,
            timeout=_LAUNCH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # The handler was invoked; xdg-open just never reported back.  Treat
        # a launch that consumed the full budget as a successful open.
        return
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise _OpenError(f"The default app could not open it (exit {completed.returncode}). {detail}")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local file or folder to open with the default app. Must exist and be "
                    "inside the folders allowed for this task."
                ),
                "minLength": 1,
                "maxLength": 4096,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
)
class OpenFileTool(Tool):
    """Open an existing local file or folder with the operating system's default app."""

    @property
    def name(self) -> str:
        return "open_file"

    @property
    def description(self) -> str:
        return (
            "Open an existing local file or folder with the operating system's default app — "
            "for example a Markdown, PDF, Office, image, audio, or video file in its viewer, "
            "or a folder in the file explorer. Only harmless, allowlisted file types inside "
            "the folders allowed for this task can be opened; executables, scripts, and "
            "shortcuts are refused, as are files outside the allowed folders. The file's contents are "
            "never sent to Collie or your model provider — it opens locally in your default app. Use this when the user asks "
            "you to open, show, or launch a file they created or that lives in an allowed folder."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        target: Path | None = None
        try:
            target, _in_scope = _resolve_local_path(
                str(params.get("path") or ""), allow_directory=True
            )
            resource = str(target)
        except (OSError, UnicodeError, WorkspaceBoundaryError, _LocalFileError):
            resource = str(params.get("path") or "local file")
        roots, unrestricted = _scope_roots(current_tool_workspace(None))
        allowed_roots = [str(root) for root in roots] if roots else []
        kind = "folder" if (target is not None and target.is_dir()) else "file"
        label = target.name if target is not None else Path(resource).name or resource
        return PermissionRequest(
            action="local_file.open",
            resource=resource,
            risk=Risk.READ,
            summary=f"Open {kind} “{label}” with its default app",
            reversible=True,
            data_leaving_device=(),
            suggested_scope=Scope.ONCE,
            redacted_parameters={
                "path": resource,
                "local_only": True,
                "default_app": True,
                # Same scope metadata as local_files, so a target inside a
                # user-granted folder is recognized as granted by the
                # evaluator's read-allow path instead of asking.
                "allowed_local_roots": allowed_roots,
                "unrestricted_local_files": unrestricted,
            },
            # Read-only and bounded to allowlisted types inside the approved
            # folders: no data leaves the device, nothing is written.  The
            # execution path revalidates scope and type before every launch.
            hard_approval=False,
            approval_free=False,
            approve_for_me=False,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw = str(kwargs.get("path") or "")
        try:
            target, _in_scope = _resolve_local_path(raw, allow_directory=True)
        except (OSError, UnicodeError, WorkspaceBoundaryError, _LocalFileError) as exc:
            return ToolResult.error(f"I couldn't open that: {exc}")
        if not target.exists():
            return ToolResult.error("That file or folder does not exist yet.")
        if target.is_dir():
            return self._launch(target, "folder")
        suffix = target.suffix.lower()
        if suffix in _BLOCKED_SUFFIXES or suffix not in _OPENABLE_SUFFIXES:
            return ToolResult.error(
                "I can only open harmless file types with the default app — documents, "
                "images, audio, and video. Executables, scripts, and shortcuts stay "
                "off-limits."
            )
        return self._launch(target, "file")

    @staticmethod
    def _launch(target: Path, kind: str) -> ToolResult:
        try:
            _open_with_default_app(target)
        except (_OpenError, OSError) as exc:
            return ToolResult.error(f"I couldn't open that {kind}: {exc}")
        return ToolResult(
            json.dumps({"opened": str(target), "kind": kind, "default_app": True})
        )
