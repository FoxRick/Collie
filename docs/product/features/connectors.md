# Collie Connectors Update

**Status:** active product direction
**Date:** 2026-08-01

## Objective

Replace Collie's placeholder connector experience with real, working integrations:

- Official brand assets rather than invented logos
- One-click browser authorization with the actual provider
- Secure token storage
- Live connection status and tool discovery
- Clear disconnect, reconnect, and error recovery
- Confirmation before consequential or destructive actions

The initial competitive stack is:

1. Google Workspace
2. Microsoft 365
3. Direct hosted MCP providers

## Google Workspace

Use Google's official hosted MCP servers for:

- Gmail
- Google Calendar
- Google Drive
- Google Docs
- Google Sheets
- Google Slides
- Google Chat
- Google Contacts / People

The user should see one Google Workspace connector in Collie, with individual services and permissions selectable underneath it.

### Required from the owner

- A Google Cloud project owned by Collie
- An OAuth application named **Collie**
- `heycollie.com` configured as an authorized domain
- A user-support email
- A Google account added as an initial test user
- OAuth credentials stored in secure local or backend configuration, never committed to the repository or pasted into chat
- Public product, privacy-policy, terms-of-service, and support pages for production verification

Google Workspace MCP is currently a Developer Preview. A test connection may be possible immediately once the Cloud project, APIs, OAuth consent screen, and test user are configured. Public access can require Google's sensitive/restricted-scope verification and is not guaranteed to be available on the same day.

## Microsoft 365

Use delegated Microsoft Graph authorization for the initial Microsoft bundle:

- Outlook Mail
- Outlook Calendar
- OneDrive
- Microsoft Contacts
- Microsoft To Do
- Excel workbooks

The app should support work/school accounts and personal Microsoft accounts where the corresponding Graph feature is available.

### Required from the owner

- A Microsoft Entra tenant, preferably owned through a Collie work/business account
- A multi-tenant app registration named **Collie**
- Collie's verified domain associated with the tenant
- The application/client ID and tenant configuration stored securely
- A Microsoft account with sample mail, calendar, file, and task data for testing

The development connection can likely be tested immediately after registration. Public organizational adoption should include Microsoft publisher verification, because some tenants prevent users from consenting to new unverified multi-tenant applications.

## Direct hosted MCP connectors

Prioritize everyday, high-value services:

- Notion
- GitHub
- Canva
- Stripe
- Linear
- Airtable

Todoist should not be a headline connector. It can remain a later catalog entry if it is inexpensive to support.

Use official remote MCP endpoints where a provider operates one. Avoid maintaining custom API wrappers when a stable official MCP implementation already exists.

## Logo and brand-asset policy

- Obtain logos from each provider's official press kit, brand site, developer resources, or an explicitly licensed repository.
- Bundle assets locally so the connector catalog is reliable offline.
- Preserve the original aspect ratio, safe area, and provider-specified colors.
- Maintain an asset manifest containing source URL, retrieval date, license or usage-policy URL, and any required attribution.
- Never generate, approximate, redraw, or silently recolor another company's logo.
- Do not treat a generic icon library as the authoritative brand source unless the provider explicitly recommends it.

## Product behavior

Clicking **Connect** should:

1. Open the provider's real authorization page in the system browser.
2. Display the exact permissions Collie is requesting.
3. Return to Collie through a secure OAuth callback.
4. Store tokens in the operating-system credential vault.
5. Connect to the provider and discover its available MCP tools or API capabilities.
6. Run a harmless verification request.
7. Show a truthful status: `Connected`, `Needs authorization`, `Unavailable`, or `Error`.

Static catalog flags must not be presented as proof that a connector works.

## Initial permission posture

Use least-privilege delegated access. For the alpha:

- Email: read/search and create drafts; sending requires explicit confirmation
- Calendar: read and manage events; destructive actions require confirmation
- Files and documents: read and edit; deletion requires confirmation
- Payments: read access first; payment-changing actions disabled until separately designed and approved
- Never request tenant-wide application permissions when user-delegated permissions can satisfy the feature

## Cost expectations

The alpha should be mostly free to configure:

- Google and Microsoft app registration normally has no connector-registration fee.
- Core Microsoft Graph mail, calendar, file, and task operations are not expected to require metered Graph billing.
- Official hosted MCP servers do not require a connector-platform vendor such as Composio.
- Users still need accounts and any applicable subscriptions for the connected products.
- Collie needs a small secure OAuth callback or token-exchange service where a provider requires a confidential client secret.

**Google policy caveat (checked 2026-08-01):** Google's Workspace API quota
rules changed from 2026-05-01 and Google describes billing for use above
standard thresholds later in 2026. Alpha-scale use may still be inexpensive,
but Collie must verify the current project quota, billing, and applicable API
terms before describing a Google connection as free or enabling it broadly.
This specification intentionally does not assume a price.

Potential later costs include hosting, provider-specific paid plans, and a third-party security assessment if Google classifies the production data access as requiring one.

## Delivery reality

Can be implemented without provider approval:

- Real logos and the brand-asset manifest
- Connector catalog and grouped Google/Microsoft UX
- OAuth callback infrastructure
- Secure credential storage
- Live health checks and tool discovery
- Direct hosted MCP integrations
- Microsoft Graph adapters
- Confirmation and safety controls

Requires owner participation:

- Approximately 30–60 minutes to create or authorize the Google and Microsoft application registrations
- Acceptance of provider terms
- Test-account authorization through the providers' browser flows
- Approval and deployment of public identity and policy pages
- Production verification submissions

An invited-user alpha can potentially work the same day once the registrations exist. A public Google/Microsoft launch cannot honestly be promised for the same day because provider review and customer tenant policies are external dependencies.

## Recommended first release

- Google Workspace: Gmail, Calendar, Drive, Docs, Sheets, Slides
- Microsoft 365: Outlook Mail, Calendar, OneDrive, Excel, To Do
- Direct MCP: Notion, GitHub, Canva, Stripe
- Read and draft/create workflows enabled
- Send, delete, payment, and other consequential operations gated by explicit user confirmation

## Official references

- [Google Workspace MCP configuration](https://developers.google.com/workspace/guides/configure-mcp-servers)
- [Google Workspace tools and API usage](https://developers.google.com/workspace/tools-safety)
- [Google OAuth verification requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
- [Microsoft application registration](https://learn.microsoft.com/en-us/graph/auth-register-app-v2)
- [Microsoft publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview)
- [Microsoft Graph overview](https://learn.microsoft.com/en-us/graph/overview)
- [Microsoft official MCP catalog](https://github.com/microsoft/mcp)
