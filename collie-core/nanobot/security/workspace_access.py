"""Workspace access scope and sandbox capability helpers."""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

WorkspaceAccessMode = Literal["restricted", "full"]
LocalFileAccessMode = Literal["selected_folder", "chosen_folders", "full_file_access"]
WORKSPACE_SCOPE_METADATA_KEY = "workspace_scope"
_ACCESS_MODES = {"restricted", "full"}
_LOCAL_FILE_ACCESS_MODES = {"selected_folder", "chosen_folders", "full_file_access"}
_MAX_LOCAL_FILE_ROOTS = 16
_WINDOWS_LOCAL_DRIVE_TYPES = frozenset({2, 3, 6})

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}
_PROVIDER_LABELS = {
    "none": "None",
    "unknown": "Unknown system sandbox",
    "macos_app_sandbox": "macOS App Sandbox",
    "bwrap": "Bubblewrap",
}

_CURRENT_WORKSPACE_SCOPE: ContextVar["WorkspaceScope | None"] = ContextVar(
    "nanobot_workspace_scope",
    default=None,
)


class WorkspaceScopeError(ValueError):
    """Raised when a requested WebUI workspace scope is invalid."""

    status = 400

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class WorkspaceSandboxStatus:
    """Resolved workspace sandbox state for runtime display and tooling."""

    restrict_to_workspace: bool
    workspace_root: str
    level: str
    enforced: bool
    provider: str
    provider_label: str
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "restrict_to_workspace": self.restrict_to_workspace,
            "workspace_root": self.workspace_root,
            "level": self.level,
            "enforced": self.enforced,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class WorkspaceScope:
    """Effective project root and access mode for one agent turn."""

    project_path: Path
    access_mode: WorkspaceAccessMode
    restrict_to_workspace: bool
    sandbox_status: WorkspaceSandboxStatus
    source_channel: str | None = None
    local_file_roots: tuple[Path, ...] = ()
    unrestricted_local_files: bool = False
    local_file_access_mode: LocalFileAccessMode | None = None

    @property
    def project_name(self) -> str:
        return self.project_path.name or str(self.project_path)

    def metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "project_path": str(self.project_path),
            "access_mode": self.access_mode,
        }
        if self.local_file_access_mode == "full_file_access":
            metadata["file_access_scope"] = {"mode": "full_file_access"}
        elif self.local_file_access_mode is not None:
            metadata["file_access_scope"] = {"mode": self.local_file_access_mode}
            if self.local_file_access_mode == "chosen_folders":
                metadata["file_access_scope"]["roots"] = [
                    str(root) for root in self.local_file_roots
                ]
        return metadata

    def payload(self) -> dict[str, Any]:
        return {
            **self.metadata(),
            "project_name": self.project_name,
            "restrict_to_workspace": self.restrict_to_workspace,
            "sandbox_status": self.sandbox_status.as_dict(),
        }


@dataclass(frozen=True)
class ToolWorkspace:
    """Workspace policy resolved for a tool call."""

    project_path: Path | None
    restrict_to_workspace: bool
    scope: WorkspaceScope | None = None
    local_file_roots: tuple[Path, ...] = ()
    unrestricted_local_files: bool = False

    @property
    def allowed_root(self) -> Path | None:
        if self.restrict_to_workspace and self.project_path is not None:
            return self.project_path
        return None

    @property
    def allowed_local_file_roots(self) -> tuple[Path, ...]:
        """Canonical local roots available to local filesystem tools."""

        if self.unrestricted_local_files:
            return ()
        if self.local_file_roots:
            return self.local_file_roots
        return (self.allowed_root,) if self.allowed_root is not None else ()


@dataclass(frozen=True)
class WorkspaceScopeResolver:
    """Resolve the effective workspace scope at an agent turn boundary."""

    default_workspace: str | Path
    default_restrict_to_workspace: bool
    scoped_channel: str = "websocket"

    @property
    def sandbox_status(self) -> WorkspaceSandboxStatus:
        return self.default().sandbox_status

    def default(self) -> WorkspaceScope:
        return default_workspace_scope(
            self.default_workspace,
            self.default_restrict_to_workspace,
        )

    def for_message(
        self,
        msg: Any,
        session_metadata: Any,
    ) -> WorkspaceScope:
        return self.for_turn(
            channel=getattr(msg, "channel", None),
            message_metadata=getattr(msg, "metadata", None),
            session_metadata=session_metadata,
        )

    def for_turn(
        self,
        *,
        channel: str | None,
        message_metadata: Any,
        session_metadata: Any,
    ) -> WorkspaceScope:
        if channel not in {self.scoped_channel, "collie"}:
            return self.default()
        return resolve_effective_workspace_scope(
            message_metadata=message_metadata,
            session_metadata=session_metadata,
            default_workspace=self.default_workspace,
            default_restrict_to_workspace=self.default_restrict_to_workspace,
            source_channel=channel,
        )

    def persist_message_scope(self, session: Any, msg: Any) -> None:
        if getattr(msg, "channel", None) not in {self.scoped_channel, "collie"}:
            return
        metadata = getattr(msg, "metadata", None)
        if not isinstance(metadata, dict):
            return
        raw = metadata.get(WORKSPACE_SCOPE_METADATA_KEY)
        if isinstance(raw, dict):
            persisted = dict(raw)
            local_file_scope = persisted.get("file_access_scope")
            if (
                isinstance(local_file_scope, dict)
                and local_file_scope.get("mode") == "full_file_access"
            ):
                # Full local-file access is for the active in-memory turn
                # only. Keeping it in session metadata would silently revive
                # broad authority after a restart or later scope omission.
                persisted.pop("file_access_scope", None)
            session.metadata[WORKSPACE_SCOPE_METADATA_KEY] = persisted


def workspace_sandbox_status(
    *,
    restrict_to_workspace: bool,
    workspace: str | Path,
    environ: dict[str, str] | None = None,
) -> WorkspaceSandboxStatus:
    """Return how workspace restriction is enforced in the current host."""

    workspace_root = str(Path(workspace).expanduser().resolve(strict=False))
    provider = _env_system_provider(environ)
    if not restrict_to_workspace:
        return WorkspaceSandboxStatus(
            restrict_to_workspace=False,
            workspace_root=workspace_root,
            level="off",
            enforced=False,
            provider="none",
            provider_label=_provider_label("none"),
            summary="Workspace restriction is disabled.",
        )

    if provider:
        label = _provider_label(provider)
        return WorkspaceSandboxStatus(
            restrict_to_workspace=True,
            workspace_root=workspace_root,
            level="system",
            enforced=True,
            provider=provider,
            provider_label=label,
            summary=f"Workspace restriction is system-enforced by {label}.",
        )

    return WorkspaceSandboxStatus(
        restrict_to_workspace=True,
        workspace_root=workspace_root,
        level="application",
        enforced=False,
        provider="none",
        provider_label=_provider_label("none"),
        summary="Workspace restriction uses nanobot application-level guards.",
    )


def default_access_mode(restrict_to_workspace: bool) -> WorkspaceAccessMode:
    return "restricted" if restrict_to_workspace else "full"


def build_workspace_scope(
    project_path: str | Path,
    access_mode: str,
    *,
    source_channel: str | None = None,
    local_file_roots: tuple[Path, ...] | None = None,
    unrestricted_local_files: bool | None = None,
    local_file_access_mode: LocalFileAccessMode | None = None,
) -> WorkspaceScope:
    mode = _normalize_access_mode(access_mode)
    root = Path(project_path).expanduser().resolve(strict=False)
    restrict = mode == "restricted"
    # Legacy WebUI ``full`` scopes keep their broad local-file behavior. New
    # product file access always sets these fields explicitly and leaves
    # ``access_mode`` restricted so it cannot affect network policy.
    unrestricted = mode == "full" if unrestricted_local_files is None else unrestricted_local_files
    roots = (root,) if local_file_roots is None and not unrestricted else (local_file_roots or ())
    return WorkspaceScope(
        project_path=root,
        access_mode=mode,
        restrict_to_workspace=restrict,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=restrict,
            workspace=root,
        ),
        source_channel=source_channel,
        local_file_roots=roots,
        unrestricted_local_files=unrestricted,
        local_file_access_mode=local_file_access_mode,
    )


def default_workspace_scope(
    workspace: str | Path,
    restrict_to_workspace: bool,
    *,
    source_channel: str | None = None,
) -> WorkspaceScope:
    return build_workspace_scope(
        workspace,
        default_access_mode(restrict_to_workspace),
        source_channel=source_channel,
    )


def validate_workspace_scope_payload(
    raw: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
    source_channel: str | None = None,
) -> WorkspaceScope:
    """Validate a client-requested workspace scope."""
    if raw is None:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=source_channel,
        )
    if not isinstance(raw, dict):
        raise WorkspaceScopeError("workspace_scope must be an object")

    raw_path = raw.get("project_path") or raw.get("path")
    if raw_path is None or raw_path == "":
        raw_path = str(Path(default_workspace).expanduser().resolve(strict=False))
    if not isinstance(raw_path, str):
        raise WorkspaceScopeError("project_path must be a string")
    if "\0" in raw_path:
        raise WorkspaceScopeError("project_path contains invalid characters")

    project = Path(raw_path).expanduser()
    if not project.is_absolute():
        raise WorkspaceScopeError("project_path must be absolute")
    project = project.resolve(strict=False)
    if not project.is_dir():
        raise WorkspaceScopeError("project_path must be an existing directory")

    raw_mode = raw.get("access_mode")
    if raw_mode is None:
        raw_mode = default_access_mode(default_restrict_to_workspace)
    if not isinstance(raw_mode, str):
        raise WorkspaceScopeError("access_mode must be a string")
    mode = _normalize_access_mode(raw_mode)
    local_file_scope = raw.get("file_access_scope")
    if local_file_scope is None:
        return build_workspace_scope(project, mode, source_channel=source_channel)
    if mode != "restricted":
        raise WorkspaceScopeError("file_access_scope requires restricted access_mode")
    roots, unrestricted = validate_local_file_access_scope_payload(
        local_file_scope,
        selected_folder=project,
    )
    return build_workspace_scope(
        project,
        mode,
        source_channel=source_channel,
        local_file_roots=roots,
        unrestricted_local_files=unrestricted,
        local_file_access_mode=local_file_scope["mode"],
    )


def validate_local_file_access_scope_payload(
    raw: Any,
    *,
    selected_folder: str | Path | None,
) -> tuple[tuple[Path, ...], bool]:
    """Validate one explicit product local-file access selection.

    The return value is ``(roots, unrestricted_local_files)``. It intentionally
    does not alter the generic workspace access mode, which is also used by
    network safety decisions elsewhere in the engine.
    """

    if not isinstance(raw, dict):
        raise WorkspaceScopeError("file_access_scope must be an object")
    mode = raw.get("mode")
    if not isinstance(mode, str) or mode not in _LOCAL_FILE_ACCESS_MODES:
        raise WorkspaceScopeError(
            "file_access_scope.mode must be selected_folder, chosen_folders, or full_file_access"
        )
    roots = raw.get("roots")
    if mode == "full_file_access":
        if roots not in (None, []):
            raise WorkspaceScopeError("full_file_access does not accept roots")
        return (), True
    if mode == "selected_folder":
        if roots not in (None, []):
            raise WorkspaceScopeError("selected_folder does not accept roots")
        if selected_folder is None:
            # Desktop General Chat has no selected project. The engine turns
            # this marker into its configured runtime workspace at the turn
            # boundary, which also revokes any older broader session scope.
            return (), False
        return (_validate_local_file_root(selected_folder, field="project_path"),), False
    if not isinstance(roots, list) or not roots:
        raise WorkspaceScopeError("chosen_folders requires one or more roots")
    if len(roots) > _MAX_LOCAL_FILE_ROOTS:
        raise WorkspaceScopeError(f"chosen_folders supports at most {_MAX_LOCAL_FILE_ROOTS} roots")
    canonical: list[Path] = []
    seen: set[str] = set()
    for value in roots:
        root = _validate_local_file_root(value, field="file_access_scope.roots")
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            canonical.append(root)
    if not canonical:
        raise WorkspaceScopeError("chosen_folders requires one or more roots")
    return tuple(canonical), False


def workspace_scope_from_metadata(
    metadata: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
    source_channel: str | None = None,
) -> WorkspaceScope:
    """Resolve persisted metadata, falling back safely for old or stale sessions."""
    if not isinstance(metadata, dict):
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=source_channel,
        )
    try:
        return validate_workspace_scope_payload(
            metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
            source_channel=source_channel,
        )
    except WorkspaceScopeError:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=source_channel,
        )


def resolve_effective_workspace_scope(
    *,
    message_metadata: Any,
    session_metadata: Any,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
    source_channel: str | None = None,
) -> WorkspaceScope:
    if isinstance(message_metadata, dict) and WORKSPACE_SCOPE_METADATA_KEY in message_metadata:
        return workspace_scope_from_metadata(
            message_metadata,
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
            source_channel=source_channel,
        )
    return workspace_scope_from_metadata(
        session_metadata,
        default_workspace=default_workspace,
        default_restrict_to_workspace=default_restrict_to_workspace,
        source_channel=source_channel,
    )


def bind_workspace_scope(scope: WorkspaceScope) -> Token[WorkspaceScope | None]:
    return _CURRENT_WORKSPACE_SCOPE.set(scope)


def reset_workspace_scope(token: Token[WorkspaceScope | None]) -> None:
    _CURRENT_WORKSPACE_SCOPE.reset(token)


def current_workspace_scope() -> WorkspaceScope | None:
    return _CURRENT_WORKSPACE_SCOPE.get()


# A live, per-conversation file-access override for the in-flight turn. The
# renderer can widen (or narrow) local-file access while a turn is running;
# local file tools consult this before the turn-bound scope so a freshly
# granted folder applies immediately instead of on the next message. The
# agent loop clears the entry when the turn ends so it can never leak into
# later turns (the next turn's scope always comes from its own metadata).
_live_local_file_scope: dict[str, tuple[tuple[Path, ...], bool]] = {}


def set_live_local_file_scope(
    conversation_id: str, roots: tuple[Path, ...], unrestricted: bool
) -> None:
    """Record the most recent file-access selection for one conversation."""
    _live_local_file_scope[str(conversation_id)] = (tuple(roots), unrestricted)


def clear_live_local_file_scope(conversation_id: str) -> None:
    _live_local_file_scope.pop(str(conversation_id), None)


def live_local_file_scope(
    conversation_id: str,
) -> tuple[tuple[Path, ...], bool] | None:
    """Return ``(roots, unrestricted)`` for a live override, or ``None``."""
    return _live_local_file_scope.get(str(conversation_id))


def current_tool_workspace(
    default_workspace: str | Path | None,
    *,
    restrict_to_workspace: bool = False,
    sandbox_restricts_workspace: bool = False,
) -> ToolWorkspace:
    """Return the workspace/access policy for the current tool call."""

    scope = current_workspace_scope()
    project_path = (
        scope.project_path
        if scope is not None
        else Path(default_workspace).expanduser() if default_workspace is not None else None
    )
    restrict = (
        scope.restrict_to_workspace
        if scope is not None
        else bool(restrict_to_workspace)
    ) or sandbox_restricts_workspace
    return ToolWorkspace(
        project_path=project_path,
        restrict_to_workspace=restrict,
        scope=scope,
        local_file_roots=scope.local_file_roots if scope is not None else (),
        unrestricted_local_files=(
            scope.unrestricted_local_files if scope is not None else False
        ),
    )


def current_scope_allows_loopback(*, enabled: bool) -> bool:
    """Return True when the current WebUI Full Access turn may touch loopback URLs."""

    scope = current_workspace_scope()
    return bool(
        enabled
        and scope is not None
        and scope.source_channel == "websocket"
        and scope.access_mode == "full"
        and not scope.restrict_to_workspace
    )


def _env_system_provider(environ: dict[str, str] | None = None) -> str | None:
    env = environ if environ is not None else os.environ
    explicit_provider = env.get("NANOBOT_WORKSPACE_SANDBOX_PROVIDER")
    enforced = env.get("NANOBOT_WORKSPACE_SANDBOX_ENFORCED")
    compatibility = env.get("NANOBOT_SANDBOX_ENFORCED")

    marker = enforced if enforced is not None else compatibility
    if marker is None:
        return None

    normalized_marker = marker.strip().lower()
    if normalized_marker in _FALSE_VALUES:
        return None
    if normalized_marker in _TRUE_VALUES:
        return _normalize_provider(explicit_provider)
    return _normalize_provider(marker)


def _normalize_provider(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized or "unknown"


def _provider_label(provider: str) -> str:
    if provider in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider]
    return provider.replace("_", " ").title()


def _validate_local_file_root(value: Any, *, field: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise WorkspaceScopeError(f"{field} must contain directory paths")
    raw = str(value).strip()
    if not raw or "\0" in raw:
        raise WorkspaceScopeError(f"{field} contains an invalid directory path")
    if raw.startswith(("\\\\", "//")):
        raise WorkspaceScopeError(f"{field} cannot use a network directory")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise WorkspaceScopeError(f"{field} directories must be absolute")
    resolved = path.resolve(strict=False)
    if str(resolved).startswith(("\\\\", "//")):
        raise WorkspaceScopeError(f"{field} cannot use a network directory")
    if not is_local_filesystem_path(resolved):
        raise WorkspaceScopeError(f"{field} must be on a local drive")
    if not resolved.is_dir():
        raise WorkspaceScopeError(f"{field} directories must exist")
    return resolved


def is_local_filesystem_path(path: Path) -> bool:
    """Return whether a canonical path is on a local filesystem.

    Windows mapped drives are not UNC spellings, but they still point at a
    network share.  The local-file boundary must reject them just like UNC
    roots.  If Windows cannot classify a drive, fail closed.
    """

    if not _running_on_windows():
        return True
    anchor = str(path.anchor)
    if not anchor:
        return False
    drive_type = _windows_drive_type(anchor)
    return drive_type in _WINDOWS_LOCAL_DRIVE_TYPES


def _windows_drive_type(anchor: str) -> int | None:
    """Return a Windows drive type, or ``None`` if it cannot be determined."""

    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(anchor))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _running_on_windows() -> bool:
    return os.name == "nt"


def _normalize_access_mode(value: str) -> WorkspaceAccessMode:
    mode = value.strip().lower().replace("_", "-")
    if mode == "restrict":
        mode = "restricted"
    if mode == "full-access":
        mode = "full"
    if mode not in _ACCESS_MODES:
        raise WorkspaceScopeError("access_mode must be restricted or full")
    return mode  # type: ignore[return-value]
