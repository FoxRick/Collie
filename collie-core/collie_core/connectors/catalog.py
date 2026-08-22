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
    # Wave-2 providers — scope vocabularies verified against each provider's
    # live authorization-server metadata (2026-08-22). Providers whose AS
    # advertises no scopes_supported are left empty: the MCP SDK then omits
    # the parameter and the provider applies its own MCP defaults.
    "asana": (),  # AS advertises no scopes_supported; server-side MCP default set.
    "clickup": ("read", "write"),
    "monday": (
        "me:read",
        "boards:read",
        "boards:write",
        "items:read",
        "items:write",
        "updates:read",
        "updates:write",
        "docs:read",
        "account:read",
        "users:read",
        "workspaces:read",
        "tags:read",
        "assets:read",
    ),
    "cal": (),
    "figma": ("mcp:connect",),
    "canva": (),
    "gitlab": ("read_api", "read_user", "profile", "mcp"),
    "circleci": (),
    # Netlify's `write` vocabulary stays unrequested — this route is
    # read-only (deploys/sites inspection).
    "netlify": ("offline_access", "read"),
    "supabase": (
        "organizations:read",
        "projects:read",
        "database:read",
        "edge_functions:read",
    ),
    "neon": ("read",),
    "sentry": ("org:read", "project:write", "team:write", "event:write"),
    "cloudflare": (),
    "paypal": (),
    "square": (),
    # Ramp publishes a fine-grained spend vocabulary; read-only slice keeps
    # Collie to viewing spend while writes stay behind approval anyway.
    "ramp": (
        "bills:read",
        "cards:read",
        "transactions:read",
        "vendors:read",
        "memos:read",
        "limits:read",
        "entities:read",
        "departments:read",
        "locations:read",
        "spend_programs:read",
        "users:read",
        "bank_accounts:read",
    ),
    "klaviyo": (),
    # Vimeo's `edit`/`upload`/`create` vocabularies stay unrequested — this
    # route is browse-and-read only.
    "vimeo": ("public", "private", "stats"),
    "webflow": (),
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
        trusted_hosts=("mcp.airtable.com",),
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
    # ------------------------------------------------------------------
    # Wave 2 (2026-08-22): official hosted MCP routes with dynamic client
    # registration — the user signs in with their existing account and no
    # Collie-owned OAuth application is required. Scope vocabularies were
    # verified against each provider's live RFC 8414 metadata on 2026-08-22.
    # ------------------------------------------------------------------
    _mcp(
        "asana",
        "Asana",
        "Work",
        "Find tasks and keep projects moving.",
        "https://mcp.asana.com/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read tasks", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["asana"],
    ),
    _mcp(
        "clickup",
        "ClickUp",
        "Work",
        "Find tasks and update work across spaces.",
        "https://mcp.clickup.com/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read tasks", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["clickup"],
    ),
    _mcp(
        "monday",
        "monday.com",
        "Work",
        "Check boards and keep items up to date.",
        "https://mcp.monday.com/mcp",
        capabilities=("Read", "Create", "Update"),
        permissions=("read boards and items", "create and update with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["monday"],
    ),
    _mcp(
        "cal",
        "Cal.com",
        "Mail & Calendar",
        "Check your schedule and create booking links.",
        "https://mcp.cal.com/mcp",
        capabilities=("Read", "Create"),
        permissions=("read bookings", "create with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["cal"],
    ),
    _mcp(
        "figma",
        "Figma",
        "Design & Media",
        "Find designs and read file details.",
        "https://mcp.figma.com/mcp",
        capabilities=("Read",),
        permissions=("read designs",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["figma"],
    ),
    _mcp(
        "canva",
        "Canva",
        "Design & Media",
        "Find designs and read their contents.",
        "https://mcp.canva.com/mcp",
        capabilities=("Read",),
        permissions=("read designs",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["canva"],
    ),
    _mcp(
        "gitlab",
        "GitLab",
        "Work",
        "Search projects, issues, and merge requests.",
        "https://gitlab.com/api/v4/mcp",
        capabilities=("Read",),
        permissions=("read projects and issues",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["gitlab"],
    ),
    _mcp(
        "circleci",
        "CircleCI",
        "Developer Tools",
        "Check pipeline status and build results.",
        "https://mcp.circleci.com/mcp",
        capabilities=("Read",),
        permissions=("read builds and pipelines",),
        available=False,
        note=(
            "CircleCI's MCP endpoint isn't answering yet — parked until the "
            "official route is verified."
        ),
        scopes=_SCOPES["circleci"],
    ),
    _mcp(
        "netlify",
        "Netlify",
        "Developer Tools",
        "Check sites, deploys, and build logs.",
        "https://mcp.netlify.com/mcp",
        capabilities=("Read",),
        permissions=("read sites and deploys",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["netlify"],
    ),
    _mcp(
        "supabase",
        "Supabase",
        "Developer Tools",
        "Check projects, databases, and edge functions.",
        "https://mcp.supabase.com/mcp",
        capabilities=("Read",),
        permissions=("read project and database details",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["supabase"],
    ),
    _mcp(
        "neon",
        "Neon",
        "Developer Tools",
        "Check Postgres projects and databases.",
        "https://mcp.neon.tech/mcp",
        capabilities=("Read",),
        permissions=("read projects and branches",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["neon"],
    ),
    _mcp(
        "sentry",
        "Sentry",
        "Developer Tools",
        "Check errors and release health.",
        "https://mcp.sentry.dev/mcp",
        # Sentry's server has no read-only scope variant — say what the
        # connection can really do instead of claiming a narrower card.
        capabilities=("Read", "Update"),
        permissions=("read issues and events", "update issues with approval"),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["sentry"],
    ),
    _mcp(
        "cloudflare",
        "Cloudflare",
        "Developer Tools",
        "Check zones, DNS, and workers.",
        "https://mcp.cloudflare.com/mcp",
        capabilities=("Read",),
        permissions=("read zones and workers",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["cloudflare"],
    ),
    _mcp(
        "paypal",
        "PayPal",
        "Money",
        "Check transactions and payment details.",
        "https://mcp.paypal.com/mcp",
        capabilities=("Read",),
        permissions=("read transaction history",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["paypal"],
    ),
    _mcp(
        "square",
        "Square",
        "Money",
        "Check payments, orders, and catalog items.",
        "https://mcp.squareup.com/mcp",
        capabilities=("Read",),
        permissions=("read payments and orders",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["square"],
    ),
    _mcp(
        "ramp",
        "Ramp",
        "Money",
        "Check spend, cards, and bills.",
        "https://mcp.ramp.com/mcp",
        capabilities=("Read",),
        permissions=("read spend and cards",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["ramp"],
    ),
    _mcp(
        "klaviyo",
        "Klaviyo",
        "Marketing",
        "Check campaigns, flows, and metrics.",
        "https://mcp.klaviyo.com/mcp",
        capabilities=("Read",),
        permissions=("read campaigns and metrics",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["klaviyo"],
    ),
    _mcp(
        "vimeo",
        "Vimeo",
        "Design & Media",
        "Find videos and read their stats.",
        "https://mcp.vimeo.com/mcp",
        capabilities=("Read",),
        permissions=("read videos and stats",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["vimeo"],
    ),
    _mcp(
        "webflow",
        "Webflow",
        "Design & Media",
        "Check sites, pages, and CMS content.",
        "https://mcp.webflow.com/mcp",
        capabilities=("Read",),
        permissions=("read sites and CMS items",),
        available=_OAUTH_AVAILABLE,
        note=_ALPHA_VERIFICATION,
        scopes=_SCOPES["webflow"],
    ),
)

_BY_ID = {connector.id: connector for connector in CONNECTOR_CATALOG}


def connector_def(provider_id: str) -> ConnectorDefinition | None:
    return _BY_ID.get((provider_id or "").strip().lower())
