"""``save_thing`` tool: register a finished deliverable as one of the user's "things".

The model calls this when it has COMPLETED a deliverable the user asked for
(a flyer, document, spreadsheet, PDF, image, or web page). The file must
already exist on disk — the tool only records metadata, publishes an
:class:`ArtifactEvent` on the bus, and never touches the file's contents.
The desktop UI renders the event as a card in the "Your things" panel;
messenger channels (Telegram, Discord, …) fall back to the message text
(``📎 Made: <title> · Open``), so nothing is ever called an "artifact" to
the user.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from collie_core.things.store import ThingStore
from collie_core.tools.local_files import _LocalFileError, _resolve_local_path
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.bus.outbound_events import ArtifactEvent, outbound_message_for_event
from nanobot.bus.queue import MessageBus
from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_policy import WorkspaceBoundaryError, is_path_within

__all__ = ["SaveThingTool", "bind_things"]

_MAX_TITLE_CHARS = 120
_MAX_TITLE_BYTES = 500

# Extension → friendly kind shown in the panel ("Image · 2.1 MB"). Unknown
# suffixes resolve to "file".
_KIND_BY_SUFFIX: dict[str, str] = {
    # images
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".svg": "image",
    ".ico": "image",
    ".avif": "image",
    ".tif": "image",
    ".tiff": "image",
    # documents
    ".md": "document",
    ".txt": "document",
    ".docx": "document",
    ".doc": "document",
    ".rtf": "document",
    ".odt": "document",
    # spreadsheets
    ".xlsx": "sheet",
    ".csv": "sheet",
    ".ods": "sheet",
    # pdf
    ".pdf": "pdf",
    # web previews
    ".html": "web",
    ".htm": "web",
}

_thing_store: ThingStore | None = None
_thing_bus: MessageBus | None = None


def bind_things(*, store: ThingStore | None = None, bus: MessageBus | None = None) -> None:
    """Wire the shared store and/or bus into the tool (called by the runtime).

    The store is bound once at startup; the bus is rebound per agent-loop
    build (each loop owns its own :class:`MessageBus`). Passing ``None`` for
    a key leaves that binding untouched.
    """
    global _thing_store, _thing_bus
    if store is not None:
        _thing_store = store
    if bus is not None:
        _thing_bus = bus


def _clean_title(raw: str) -> str | None:
    """Validate + normalize a human title; None means invalid."""
    if not isinstance(raw, str):
        return None
    title = " ".join(raw.split())
    if not title:
        return None
    if len(title) > _MAX_TITLE_CHARS or len(title.encode("utf-8")) > _MAX_TITLE_BYTES:
        return None
    if any(ord(char) < 32 for char in title):
        return None
    return title


def _resolve_thing_path(raw: str) -> tuple[Path | None, str | None]:
    """Resolve the deliverable path against the workspace scope.

    Returns ``(path, None)`` on success and ``(None, error_message)`` on
    failure. The normal rules come from :func:`_resolve_local_path`
    (workspace roots, symlink/junction refusal, UNC/device refusal). One
    deliberate carve-out: files Collie itself produced in its media directory
    (e.g. image-generation output) are always registrable even though that
    directory is outside the task's folder scope — the file was created by
    the assistant for this exact purpose.
    """
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return None, "Please provide a valid file path."
    try:
        target, _in_scope = _resolve_local_path(raw, allow_directory=False)
    except (OSError, UnicodeError, WorkspaceBoundaryError, _LocalFileError) as exc:
        media = _media_candidate(raw)
        if media is not None:
            return media, None
        return None, str(exc)
    return target, None


def _media_candidate(raw: str) -> Path | None:
    """Allow an absolute path inside Collie's media root (assistant-made)."""
    entered = Path(raw).expanduser()
    if not entered.is_absolute():
        return None
    try:
        media_root = get_media_dir().resolve()
        if not is_path_within(entered, media_root):
            return None
        candidate = entered.resolve(strict=False)
        if not is_path_within(candidate, media_root):
            return None
    except OSError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Short human-friendly title the user will recognize — e.g. "
                    '"Dog walk flyer". No file extension, no path, no technical '
                    "jargon."
                ),
                "minLength": 1,
                "maxLength": 120,
            },
            "path": {
                "type": "string",
                "description": (
                    "Path to the finished file (relative to an allowed folder, or "
                    "absolute). The file must already exist."
                ),
                "minLength": 1,
                "maxLength": 4096,
            },
            "kind": {
                "type": "string",
                "enum": ["image", "document", "sheet", "pdf", "web", "file"],
                "description": (
                    "What kind of thing it is: image, document, sheet, pdf, web "
                    "(a web page), or file (anything else)."
                ),
            },
        },
        "required": ["title", "path", "kind"],
        "additionalProperties": False,
    }
)
class SaveThingTool(Tool):
    """Save a finished deliverable into the user's \"Your things\" panel."""

    @property
    def name(self) -> str:
        return "save_thing"

    @property
    def description(self) -> str:
        return (
            'Register a finished deliverable as one of the user\'s "things" so it '
            'appears in their "Your things" panel and can be opened again later. '
            "Call this when you have COMPLETED a deliverable the user asked for — a "
            "flyer, document, spreadsheet, PDF, image, or web page you created or "
            "saved for them — and the file already exists on disk. Pass a short, "
            "human-friendly title (no extension, no path) and the path to the "
            "finished file. Only call it for real deliverables the user will want "
            "to open again — never for temporary working files. After it succeeds, "
            "tell the user it's ready and mention it's in \"Your things\"."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        title = _clean_title(str(params.get("title") or "")) or "a file"
        resource = str(params.get("path") or "your file")
        return PermissionRequest(
            action="things.save",
            resource=resource,
            risk=Risk.LOCAL_WRITE,
            summary=f"Add “{title}” to your things",
            reversible=True,
            data_leaving_device=(),
            suggested_scope=Scope.ONCE,
            redacted_parameters={"path": resource, "local_only": True},
            # Metadata-only: records a path + publishes a UI event; the file's
            # contents never leave the device and nothing is modified.
            hard_approval=False,
            approval_free=True,
            approve_for_me=False,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        title = _clean_title(str(kwargs.get("title") or ""))
        if title is None:
            return ToolResult.error(
                "Please give the thing a short human-friendly title (max 120 characters)."
            )
        raw_path = str(kwargs.get("path") or "")
        declared_kind = str(kwargs.get("kind") or "")
        if declared_kind not in _KIND_BY_SUFFIX.values() and declared_kind != "file":
            return ToolResult.error(
                f"Kind must be one of image, document, sheet, pdf, web, or file (got {declared_kind!r})."
            )

        target, error = _resolve_thing_path(raw_path)
        if error is not None:
            return ToolResult.error(f"I couldn't save that as a thing: {error}")
        assert target is not None
        if not target.exists():
            return ToolResult.error("That file does not exist yet.")
        if target.is_dir():
            return ToolResult.error("That path is a folder, not a finished file.")
        if not target.is_file():
            return ToolResult.error("That path is not a readable file.")

        actual_kind = _KIND_BY_SUFFIX.get(target.suffix.lower(), "file")
        if declared_kind != "file" and actual_kind != "file" and declared_kind != actual_kind:
            return ToolResult.error(
                f"That file is a {actual_kind}, not a {declared_kind} — "
                f"call save_thing again with kind={actual_kind!r}."
            )
        kind = actual_kind if declared_kind == "file" else declared_kind

        ctx = current_request_context()
        if ctx is None or not ctx.chat_id:
            return ToolResult.error("I can't register a thing right now — no active conversation.")
        conversation_id = str(ctx.chat_id)
        if _thing_store is None or _thing_bus is None:
            return ToolResult.error("Things are not available in this session yet.")

        artifact_id = f"th_{uuid.uuid4().hex[:12]}"
        created_at = time.time()
        size_bytes = target.stat().st_size
        record = _thing_store.register(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            title=title,
            kind=kind,
            path=str(target),
            size_bytes=size_bytes,
            created_at=created_at,
            status="new",
            version=1,
        )

        await _thing_bus.publish_outbound(
            outbound_message_for_event(
                channel=ctx.channel,
                chat_id=conversation_id,
                event=ArtifactEvent(
                    artifact_id=artifact_id,
                    title=title,
                    kind=kind,
                    file_path=str(target),
                    size_bytes=size_bytes,
                    created_at=created_at,
                    status="new",
                    version=1,
                ),
                content=f"📎 Made: {title} · Open",
            )
        )

        return ToolResult(
            json.dumps(
                {
                    "thing_id": record["id"],
                    "title": record["title"],
                    "kind": record["kind"],
                    "conversation_id": conversation_id,
                },
                ensure_ascii=False,
            )
        )
