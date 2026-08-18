"""Curated consumer connector catalog.

Only official provider endpoints or official APIs belong here. Availability
means the complete authorization and live-probe path is implemented. Alpha
enablement (``release_status="alpha"``) means the route is live and labeled
with its pending packaged-app verification — it is not a release claim.
"""

from __future__ import annotations

import sys

from collie_core.connectors.models import ConnectorDefinition, ConnectorDriverKind

# OAuth connectors persist their tokens in CredentialStore, which is
# Windows-DPAPI-only for now. On macOS/Linux they surface as coming-soon
# instead of failing at connect time.
_OAUTH_AVAILABLE = sys.platform == "win32"

__all__ = ["CONNECTOR_CATALOG", "connector_def"]


def _mcp(
    connector_id: str,
    name: str,
    category: str,
    description: str,
    endpoint: str,
    *,
    capabilities: tuple[str, ...],
    permissions: tuple[str, ...],
    featured: bool = False,
    available: bool = True,
    note: str = "",
    overrides: dict[str, str] | None = None,
    scopes: tuple[str, ...] = (),
) -> ConnectorDefinition:
    from urllib.parse import urlparse

    host = (urlparse(endpoint).hostname or "").lower()
    return ConnectorDefinition(
        id=connector_id,
        name=name,
        category=category,
        description=description,
        driver=ConnectorDriverKind.OFFICIAL_MCP,
        auth_type="oauth",
        endpoint=endpoint,
        capabilities=capabilities,
        permissions=permissions,
        scopes=scopes,
        featured=featured,
        available=available,
        release_status="alpha" if available else "coming_soon",
        note=note,
        trusted_hosts=(host,),
        tool_overrides=overrides or {},
    )


_COMING_SOON = "Coming soon — the official provider route is still being verified."
_PACKAGED_VERIFICATION_REQUIRED = (
    "Coming soon — this connection has not passed Collie's packaged-app verification yet."
)
_ALPHA_VERIFICATION = (
    "Alpha — live against the official provider endpoint; final packaged-app "
    "sign-in verification is still pending."
)

# Least-privilege OAuth scopes per provider, verified against each
# provider's live RFC 8414 authorization-server / RFC 9728 resource
# metadata (2026-08-02). Requesting no scope makes the MCP SDK omit the
# parameter, which the authorization servers interpret as "everything
# advertised" — an explicit, narrow set keeps consent honest.
_SCOPES: dict[str, tuple[str, ...]] = {
    # Notion's authorization server supports exactly one scope: "default".
    "notion": ("default",),
    "linear": ("read", "write"),
    "todoist": ("data:read_write",),
    # Atlassian MCP (authv2) resource metadata vocabulary — Jira issues,
    # Confluence pages, search, identity, and offline refresh only.
    "atlassian": (
        "read:me",
        "read:jira-work",
        "write:jira-work",
        "search:confluence",
        "read:page:confluence",
        "write:page:confluence",
        "offline_access",
    ),
    "airtable": ("data.records:read", "data.records:write", "schema.bases:read"),
}

CONNECTOR_CATALOG: tuple[ConnectorDefinition, ...] = (
    _mcp(
        "notion",
        "Notion",
        "Notes & Tasks",
        "Find notes and create or update pages.",
        "https://mcp.notion.com/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read pages", "search", "create and update with approval"),
        featured=True,
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["notion"],
    ),
    _mcp(
        "linear",
        "Linear",
        "Work",
        "Find issues and keep project work moving.",
        "https://mcp.linear.app/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read issues", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        overrides={"delete_issue": "destructive"},
        scopes=_SCOPES["linear"],
    ),
    _mcp(
        "todoist",
        "Todoist",
        "Notes & Tasks",
        "Find tasks and keep your lists up to date.",
        "https://ai.todoist.net/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read tasks", "create and complete with approval"),
        featured=True,
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["todoist"],
    ),
    _mcp(
        "atlassian",
        "Jira & Confluence",
        "Work",
        "Search Jira and Confluence and update team work.",
        "https://mcp.atlassian.com/v1/mcp/authv2",
        capabilities=("Read", "Create", "Update"),
        permissions=("read Jira and Confluence", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["atlassian"],
    ),
    _mcp(
        "gmail",
        "Gmail",
        "Mail & Calendar",
        "Find mail and create drafts.",
        "https://gmailmcp.googleapis.com/mcp/v1",
        capabilities=("Read", "Draft", "Send"),
        permissions=("read mail", "draft", "send with approval"),
        featured=True,
        available=False,
        note="Google's official route is in preview; the Collie fallback is not ready yet.",
    ),
    _mcp(
        "google-calendar",
        "Google Calendar",
        "Mail & Calendar",
        "Check your schedule and create events.",
        "https://calendarmcp.googleapis.com/mcp/v1",
        capabilities=("Read", "Create", "Update"),
        permissions=("read events", "create and update with approval"),
        featured=True,
        available=False,
        note=_COMING_SOON,
    ),
    _mcp(
        "google-drive",
        "Google Drive",
        "Files & Data",
        "Find and read files from Drive.",
        "https://drivemcp.googleapis.com/mcp/v1",
        capabilities=("Read",),
        permissions=("find and read files",),
        featured=True,
        available=False,
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="outlook-email",
        name="Outlook Email",
        category="Mail & Calendar",
        description="Find mail and create drafts in Outlook.",
        driver=ConnectorDriverKind.OFFICIAL_API,
        auth_type="oauth",
        capabilities=("Read", "Draft", "Send"),
        permissions=("read mail", "draft", "send with approval"),
        featured=True,
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="outlook-calendar",
        name="Outlook Calendar",
        category="Mail & Calendar",
        description="Check and update your Outlook calendar.",
        driver=ConnectorDriverKind.OFFICIAL_API,
        auth_type="oauth",
        capabilities=("Read", "Create", "Update"),
        permissions=("read events", "create and update with approval"),
        featured=True,
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="onedrive",
        name="OneDrive",
        category="Files & Data",
        description="Find and read files from OneDrive.",
        driver=ConnectorDriverKind.OFFICIAL_API,
        auth_type="oauth",
        capabilities=("Read",),
        permissions=("find and read files",),
        featured=True,
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="slack",
        name="Slack",
        category="Communication",
        description="Find conversations and send messages with approval.",
        driver=ConnectorDriverKind.OFFICIAL_MCP,
        auth_type="oauth",
        capabilities=("Read", "Send"),
        permissions=("read messages", "send with approval"),
        featured=True,
        note="Coming soon — Collie's Slack app must be approved first.",
    ),
    ConnectorDefinition(
        id="dropbox",
        name="Dropbox",
        category="Files & Data",
        description="Find and read files from Dropbox.",
        driver=ConnectorDriverKind.OFFICIAL_API,
        auth_type="oauth",
        capabilities=("Read",),
        permissions=("find and read files",),
        featured=True,
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="airtable",
        name="Airtable",
        category="Files & Data",
        description="Read and update Airtable records.",
        driver=ConnectorDriverKind.OFFICIAL_MCP,
        auth_type="oauth",
        endpoint="https://mcp.airtable.com/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read records", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        release_status="alpha" if _OAUTH_AVAILABLE else "coming_soon",
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["airtable"],
    ),
    ConnectorDefinition(
        id="github",
        name="GitHub",
        category="Work",
        description="Search repositories, issues, and pull requests.",
        driver=ConnectorDriverKind.BUNDLED_MCP,
        auth_type="oauth",
        capabilities=("Read", "Create", "Update"),
        permissions=("read repositories", "create and update with approval"),
        note=_COMING_SOON,
    ),
    ConnectorDefinition(
        id="google-sheets",
        name="Google Sheets",
        category="Files & Data",
        description="Read and update spreadsheets.",
        driver=ConnectorDriverKind.OFFICIAL_API,
        auth_type="oauth",
        capabilities=("Read", "Update"),
        permissions=("read sheets", "update with approval"),
        note=_COMING_SOON,
    ),
)

_BY_ID = {connector.id: connector for connector in CONNECTOR_CATALOG}


def connector_def(provider_id: str) -> ConnectorDefinition | None:
    return _BY_ID.get((provider_id or "").strip().lower())
