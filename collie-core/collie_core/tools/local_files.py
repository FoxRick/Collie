"""Bounded, local-only text file tools for everyday document work.

This deliberately is not a general filesystem or shell capability.  It only
handles small UTF-8 text artifacts, never follows a user-supplied link, and
uses the workspace scope bound to the current turn.
"""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk, Scope
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.security.workspace_access import current_tool_workspace, is_local_filesystem_path
from nanobot.security.workspace_policy import WorkspaceBoundaryError, is_path_within

__all__ = ["LocalFilesTool"]

_MAX_FILE_BYTES = 1_000_000
_MAX_READ_CHARS = 50_000
_MAX_LIST_ENTRIES = 200
_TEXT_SUFFIXES = frozenset(
    {
        ".csv",
        ".css",
        ".html",
        ".htm",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".rtf",
        ".svg",
        ".toml",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_WRITE_OPERATIONS = frozenset({"create", "overwrite", "edit", "save"})


class _LocalFileError(ValueError):
    """A user-facing local file validation failure."""


def _configured_model_provider() -> str:
    """Return the trusted, user-configured provider label for approval copy."""
    context = current_request_context()
    metadata = context.metadata if context is not None else {}
    permission_context = metadata.get("permission_context", {}) if isinstance(metadata, dict) else {}
    provider = (
        permission_context.get("model_provider")
        if isinstance(permission_context, dict)
        else None
    )
    return str(provider or "configured model provider").strip()


def _is_unc_or_device_path(path: Path) -> bool:
    """Reject network and device namespaces even when full local access was selected."""
    raw = os.fspath(path)
    # pathlib does not consistently recognize Windows device namespaces on
    # non-Windows test hosts, so check the raw spelling as well.
    return raw.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))


def _is_filesystem_root(path: Path) -> bool:
    anchor = Path(path.anchor) if path.anchor else None
    return bool(anchor and path == anchor)


def _has_reparse_component(path: Path) -> bool:
    """Refuse symlink/junction hops instead of authorizing through them."""
    if not path.is_absolute():
        return True
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if current.is_symlink() or bool(
            getattr(details, "st_file_attributes", 0) & 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return True
    return False


def _scope_roots(access: Any) -> tuple[list[Path], bool]:
    """Return explicit file roots, accepting the staged scope API defensively."""
    scope = getattr(access, "scope", None)
    unrestricted = bool(
        getattr(access, "unrestricted_local_files", False)
        or getattr(scope, "unrestricted_local_files", False)
    )
    raw_roots = (
        getattr(access, "allowed_local_file_roots", None)
        or getattr(access, "local_file_roots", None)
        or getattr(scope, "allowed_local_file_roots", None)
        or getattr(scope, "local_file_roots", None)
        or ()
    )
    roots: list[Path] = []
    for raw_root in raw_roots:
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if (
            root.is_dir()
            and is_local_filesystem_path(root)
            and not _is_unc_or_device_path(root)
            and not _is_filesystem_root(root)
        ):
            roots.append(root)

    # Until all callers supply the new fields, a selected/restricted project
    # remains a safe compatibility root.  A legacy full turn is intentionally
    # still bounded to its selected project; only the explicit new flag grants
    # disk-wide local-file access.
    if not roots and not unrestricted:
        fallback = getattr(access, "allowed_root", None) or getattr(access, "project_path", None)
        if fallback is not None:
            try:
                root = Path(fallback).expanduser().resolve(strict=True)
            except (OSError, RuntimeError, TypeError, ValueError):
                root = None
            if (
                root is not None
                and root.is_dir()
                and is_local_filesystem_path(root)
                and not _is_unc_or_device_path(root)
            ):
                roots.append(root)
    return roots, unrestricted


def _resolve_local_path(path: str, *, allow_directory: bool) -> tuple[Path, bool]:
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise _LocalFileError("Please provide a valid local file path.")
    entered = Path(path).expanduser()
    if _is_unc_or_device_path(entered):
        raise _LocalFileError("Network and device paths are not available to local file tools.")

    access = current_tool_workspace(None)
    roots, unrestricted = _scope_roots(access)
    anchor = roots[0] if roots else getattr(access, "project_path", None)
    if anchor is None:
        raise _LocalFileError("Local file access has not been configured for this task.")
    anchor = Path(anchor).expanduser().resolve(strict=True)
    candidate = entered if entered.is_absolute() else anchor / entered
    if _is_unc_or_device_path(candidate):
        raise _LocalFileError("Network and device paths are not available to local file tools.")
    if _has_reparse_component(candidate):
        raise _LocalFileError("Symlink and junction paths are not available to local file tools.")
    try:
        target = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _LocalFileError("That local path could not be resolved safely.") from exc
    if _is_unc_or_device_path(target):
        raise _LocalFileError("Network and device paths are not available to local file tools.")
    if not is_local_filesystem_path(target):
        raise _LocalFileError("Network drives are not available to local file tools.")
    if _is_filesystem_root(target):
        raise _LocalFileError("A drive root is not a valid local file target.")
    if not unrestricted and not any(is_path_within(target, root) for root in roots):
        raise _LocalFileError("That path is outside the folders allowed for this task.")
    if not allow_directory and target.exists() and target.is_dir():
        raise _LocalFileError("That path is a folder, not a text file.")
    return target, bool(unrestricted or any(is_path_within(target, root) for root in roots))


def _require_text_artifact(path: Path) -> None:
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        supported = ", ".join(sorted(_TEXT_SUFFIXES))
        raise _LocalFileError(f"Use a supported text artifact ({supported}).")


def _encoded_content(content: Any) -> bytes:
    if not isinstance(content, str):
        raise _LocalFileError("File content must be text.")
    payload = content.encode("utf-8")
    if len(payload) > _MAX_FILE_BYTES:
        raise _LocalFileError("That text artifact is too large (limit: 1 MB).")
    return payload


def _content_hash(path: Path) -> str:
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise _LocalFileError("That text artifact is too large to check safely (limit: 1 MB).")
    return sha256(path.read_bytes()).hexdigest()


def _verify_expected_hash(path: Path, expected_hash: Any) -> None:
    if expected_hash is None:
        return
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise _LocalFileError("expected_sha256 must be the 64-character hash returned by read.")
    if _content_hash(path) != expected_hash.lower():
        raise _LocalFileError("That file changed since it was read. Read it again before replacing it.")


def _atomic_replace(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".collie-write-", suffix=path.suffix, dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise _LocalFileError("I could not save that local text artifact safely.") from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _atomic_create(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".collie-create-", suffix=path.suffix, dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Linking is an atomic no-clobber publish on the same local volume.
        os.link(temporary, path)
    except FileExistsError as exc:
        raise _LocalFileError("That file already exists. Use overwrite, edit, or save instead.") from exc
    except OSError as exc:
        raise _LocalFileError("I could not create that local text artifact safely.") from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["list", "read", "create", "overwrite", "edit", "save"],
            },
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
            "content": {"type": ["string", "null"], "maxLength": _MAX_FILE_BYTES},
            "old_text": {"type": ["string", "null"], "maxLength": _MAX_FILE_BYTES},
            "new_text": {"type": ["string", "null"], "maxLength": _MAX_FILE_BYTES},
            "expected_sha256": {"type": ["string", "null"], "maxLength": 64},
            "max_chars": {"type": ["integer", "null"], "minimum": 1, "maximum": _MAX_READ_CHARS},
            "max_entries": {"type": ["integer", "null"], "minimum": 1, "maximum": _MAX_LIST_ENTRIES},
        },
        "required": ["operation", "path"],
        "additionalProperties": False,
    }
)
class LocalFilesTool(Tool):
    """Read and safely edit small local text artifacts in the approved folders."""

    @property
    def name(self) -> str:
        return "local_files"

    @property
    def description(self) -> str:
        return (
            "Work with small local text artifacts in the folders allowed for this task. "
            "List folders, read text files, or create, overwrite, edit, and save .txt, .md, "
            ".csv, .rtf, JSON, and similar text files. This tool never sends files anywhere, "
            "cannot delete files, and does not handle Word or PDF binaries. Text read from a "
            "file may be processed by the configured model provider to complete the request."
        )

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        operation = str(params.get("operation") or "").lower()
        is_write = operation in _WRITE_OPERATIONS
        try:
            target, in_scope = _resolve_local_path(
                str(params.get("path") or ""), allow_directory=operation == "list"
            )
            resource = str(target)
        except _LocalFileError:
            resource, target, in_scope = str(params.get("path") or "local file"), None, False
        safe_write = bool(is_write and in_scope)
        reversible = bool(
            is_write
            and operation == "create"
            or (operation == "save" and target is not None and not target.exists())
        )
        verb = {"list": "List", "read": "Read", "create": "Create", "overwrite": "Overwrite", "edit": "Edit", "save": "Save"}.get(operation, "Use")
        if operation == "read":
            provider = _configured_model_provider()
            return PermissionRequest(
                action="local_file.read",
                resource=resource,
                risk=Risk.SENSITIVE,
                summary=f"Read {resource} and send its text to {provider}",
                reversible=True,
                data_leaving_device=(provider,),
                suggested_scope=Scope.ONCE,
                redacted_parameters={
                    "operation": operation,
                    "path": resource,
                    "model_provider": provider,
                    "local_only": True,
                },
                # Selecting a Files scope limits where the tool may look; it
                # is not consent to disclose this file's contents externally.
                hard_approval=True,
            )
        return PermissionRequest(
            action="local_file.write" if is_write else "local_file.read",
            resource=resource,
            risk=Risk.LOCAL_WRITE if is_write else Risk.READ,
            summary=f"{verb} local-only file: {resource}",
            reversible=reversible,
            data_leaving_device=("configured model provider",) if not is_write else (),
            suggested_scope=Scope.FOLDER,
            redacted_parameters={
                "operation": operation,
                "path": resource,
                "local_only": True,
            },
            # A folder selection defines where files may be touched, not an
            # automatic consent to edit them.  Workstream A can offer an
            # explicit "approve for me" run rule for these bounded writes.
            # The execution path revalidates scope before every change.
            approval_free=False,
            approve_for_me=safe_write,
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        operation = str(params.get("operation") or "").lower()
        if operation in {"create", "overwrite", "save"} and not isinstance(params.get("content"), str):
            errors.append(f"content is required for {operation}")
        if operation == "edit":
            if not isinstance(params.get("old_text"), str) or not params.get("old_text"):
                errors.append("old_text is required for edit")
            if not isinstance(params.get("new_text"), str):
                errors.append("new_text is required for edit")
        return errors

    async def execute(self, **kwargs: Any) -> ToolResult:
        operation = str(kwargs.get("operation") or "").lower()
        try:
            target, _in_scope = _resolve_local_path(
                str(kwargs.get("path") or ""), allow_directory=operation == "list"
            )
            if operation == "list":
                return self._list(target, int(kwargs.get("max_entries") or 100))
            _require_text_artifact(target)
            if operation == "read":
                return self._read(target, int(kwargs.get("max_chars") or 12_000))
            if operation == "create":
                if not target.parent.is_dir():
                    raise _LocalFileError("The destination folder does not exist.")
                _atomic_create(target, _encoded_content(kwargs.get("content")))
            elif operation == "overwrite":
                if not target.is_file():
                    raise _LocalFileError("That file does not exist. Use create or save instead.")
                _verify_expected_hash(target, kwargs.get("expected_sha256"))
                _atomic_replace(target, _encoded_content(kwargs.get("content")))
            elif operation == "save":
                if not target.parent.is_dir():
                    raise _LocalFileError("The destination folder does not exist.")
                payload = _encoded_content(kwargs.get("content"))
                if target.exists():
                    _verify_expected_hash(target, kwargs.get("expected_sha256"))
                    _atomic_replace(target, payload)
                else:
                    _atomic_create(target, payload)
            elif operation == "edit":
                return self._edit(target, str(kwargs.get("old_text")), str(kwargs.get("new_text")))
            else:
                raise _LocalFileError("Choose list, read, create, overwrite, edit, or save.")
        except (OSError, UnicodeError, WorkspaceBoundaryError, _LocalFileError) as exc:
            return ToolResult.error(f"I couldn't work with that local file: {exc}")
        return ToolResult(json.dumps({"operation": operation, "path": str(target), "local_only": True}))

    @staticmethod
    def _list(path: Path, max_entries: int) -> ToolResult:
        if not path.is_dir():
            raise _LocalFileError("That path is not a folder to list.")
        entries: list[dict[str, Any]] = []
        for entry in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            if len(entries) >= max_entries:
                break
            if entry.is_symlink():
                continue
            entries.append({"name": entry.name, "kind": "directory" if entry.is_dir() else "file"})
        return ToolResult(json.dumps({"path": str(path), "entries": entries, "local_only": True}))

    @staticmethod
    def _read(path: Path, max_chars: int) -> ToolResult:
        if not path.is_file():
            raise _LocalFileError("That text artifact does not exist.")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise _LocalFileError("That text artifact is too large to read safely (limit: 1 MB).")
        text = path.read_text(encoding="utf-8")
        return ToolResult(
            json.dumps(
                {
                    "path": str(path),
                    "content": text[:max_chars],
                    "sha256": _content_hash(path),
                    "truncated": len(text) > max_chars,
                    "local_only": True,
                },
                ensure_ascii=False,
            )
        )

    @staticmethod
    def _edit(path: Path, old_text: str, new_text: str) -> ToolResult:
        if not path.is_file():
            raise _LocalFileError("That text artifact does not exist.")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise _LocalFileError("That text artifact is too large to edit safely (limit: 1 MB).")
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old_text)
        if occurrences == 0:
            raise _LocalFileError("I couldn't find that exact text to edit.")
        if occurrences != 1:
            raise _LocalFileError("That text appears more than once. Read the file and use a more specific edit.")
        _atomic_replace(path, _encoded_content(text.replace(old_text, new_text, 1)))
        return ToolResult(json.dumps({"operation": "edit", "path": str(path), "local_only": True}))
