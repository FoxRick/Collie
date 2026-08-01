"""Curated Windows alpha service catalog.

Only integrations that can satisfy the packaged-application contract may set
``available=True``. A catalogue tile is never evidence that a connector works.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

__all__ = [
    "SERVICE_CATALOG",
    "CredentialField",
    "McpTemplate",
    "ServiceDef",
    "ServiceOAuth",
    "platform_supported",
    "service_def",
]


@dataclass(frozen=True)
class CredentialField:
    key: str
    label: str
    secret: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class ServiceOAuth:
    auth_url: str
    token_url: str
    scopes: tuple[str, ...] = ()
    client_id_env: str = ""
    client_secret_env: str | None = None
    default_client_id: str = ""
    pkce: bool = True
    extra_auth_params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class McpTemplate:
    transport: str | None = None
    command: str = ""
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    tool_timeout: int = 60


@dataclass(frozen=True)
class ServiceDef:
    id: str
    name: str
    category: str
    description: str
    auth: str
    mcp: McpTemplate = field(default_factory=McpTemplate)
    oauth: ServiceOAuth | None = None
    fields: tuple[CredentialField, ...] = ()
    platforms: tuple[str, ...] = ()
    available: bool = False
    note: str = "Coming soon"
    permissions: tuple[str, ...] = field(default_factory=tuple)
    release_status: str = "coming_soon"


def _google_oauth(*scopes: str) -> ServiceOAuth:
    return ServiceOAuth(
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=scopes,
        client_id_env="COLLIE_GOOGLE_CLIENT_ID",
        client_secret_env="COLLIE_GOOGLE_CLIENT_SECRET",
        extra_auth_params=(("access_type", "offline"), ("prompt", "consent")),
    )


_PACKAGING_NOTE = "Coming soon — connector verification is still in progress."

SERVICE_CATALOG: tuple[ServiceDef, ...] = (
    ServiceDef(
        id="gmail",
        name="Gmail",
        category="Email",
        description="Read, search, draft, and send email.",
        auth="oauth",
        oauth=_google_oauth("https://www.googleapis.com/auth/gmail.modify"),
        permissions=("read email", "search email", "draft", "send with approval"),
        note=_PACKAGING_NOTE,
    ),
    ServiceDef(
        id="google-calendar",
        name="Google Calendar",
        category="Calendar",
        description="Check your schedule, add events, and find free time.",
        auth="oauth",
        oauth=_google_oauth("https://www.googleapis.com/auth/calendar"),
        permissions=("read events", "create with approval"),
        note=_PACKAGING_NOTE,
    ),
    ServiceDef(
        id="outlook",
        name="Outlook / Hotmail",
        category="Email",
        description="Read and send Microsoft email and work with your calendar.",
        auth="oauth",
        oauth=ServiceOAuth(
            auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            scopes=("offline_access", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"),
            client_id_env="COLLIE_MICROSOFT_CLIENT_ID",
        ),
        permissions=("read email", "send with approval", "calendar"),
        note=_PACKAGING_NOTE,
    ),
    ServiceDef(
        id="google-drive",
        name="Google Drive",
        category="Files",
        description="Find and read your Drive files and docs.",
        auth="oauth",
        permissions=("read", "search"),
    ),
    ServiceDef(
        id="notion",
        name="Notion",
        category="Notes",
        description="Create and search pages in your Notion workspace.",
        auth="api_key",
        permissions=("read", "create with approval", "search"),
    ),
    ServiceDef(
        id="todoist",
        name="Todoist",
        category="Tasks",
        description="Manage Todoist tasks and projects.",
        auth="api_key",
        permissions=("read", "create with approval", "complete with approval"),
    ),
    ServiceDef(
        id="dropbox",
        name="Dropbox",
        category="Files",
        description="Find and read your Dropbox files.",
        auth="oauth",
        permissions=("read", "search"),
    ),
    ServiceDef(
        id="spotify",
        name="Spotify",
        category="Music",
        description="Control playback and work with playlists.",
        auth="oauth",
        permissions=("playback", "playlists"),
    ),
)

_BY_ID = {service.id: service for service in SERVICE_CATALOG}


def service_def(service_id: str) -> ServiceDef | None:
    return _BY_ID.get((service_id or "").strip().lower())


def platform_supported(service: ServiceDef, platform: str | None = None) -> bool:
    if not service.platforms:
        return True
    return (platform or sys.platform) in service.platforms
