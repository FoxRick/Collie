# Collie connections

**Status: accepted implementation direction. Delivery is incremental; unimplemented steps are requirements, not availability claims.**

Date: 2026-09-09. Product: Collie.

Baseline: live `FoxRick/Collie` main at [`b54311aaae24d12f92832fcfc68167c8fe2c81c6`](https://github.com/FoxRick/Collie/commit/b54311aaae24d12f92832fcfc68167c8fe2c81c6).

This is the canonical connection specification, adopted from the approved implementation plan against the baseline above. Steps 1–2 establish the storage foundation. Remote add/import, flexible authentication, provider adapters, and local execution remain later delivery steps. Existing catalogue availability is not evidence of real-account or packaged acceptance.

## 1. Outcome and decisions

Collie should let a user connect a compatible MCP server without a Collie code release, while providing tested, plain-language sign-in for popular services. The normal experience is: find the service, connect an account, see what Collie can do, and use it in chat.

Accepted decisions:

- Build general MCP support and curated provider integrations on one connection lifecycle.
- Support remote Streamable HTTP first, legacy SSE for compatibility, and managed local stdio servers in a subsequent delivery.
- Support no-auth, protected API tokens/headers, and OAuth with both automatic and pre-registered client identities.
- Deliver Slack through its official MCP and registered Collie application; deliver Microsoft Teams through delegated Microsoft Graph by default. Treat Microsoft-hosted Teams MCP as an additional eligible-tenant route.
- Keep a searchable curated directory, but stop making membership in that directory a prerequisite for connecting.
- Preserve local state, OS-protected credentials, central permission enforcement, and no mandatory Collie account for direct connections.
- Keep a managed integration provider optional and independently evaluated. It must not block direct MCP or Slack/Microsoft delivery.

Broad compatibility does not imply every service exposes an API/MCP, every account has access, or every server supports every operation. Product language must name supported transports and working capabilities, not promise universal authorization.

## 2. Why this matches other harnesses

Cline accepts remote endpoints and local server configurations, including credentials. Claude Code accepts arbitrary HTTP/stdio servers, imports MCP configurations, and supports OAuth and pre-registered clients. Collie should offer comparable interoperability behind a graphical and conversational setup flow. [Cline](https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx), [Claude Code](https://code.claude.com/docs/en/mcp)

The baseline connection manager implemented only its official MCP driver. Step 3 adds a custom remote MCP driver and shared remote discovery while preserving the existing engine runtime. Bundled MCP and API drivers remain later delivery steps. [Manager](https://github.com/FoxRick/Collie/blob/b54311aaae24d12f92832fcfc68167c8fe2c81c6/collie-core/collie_core/connectors/manager.py), [Engine transport support](https://github.com/FoxRick/Collie/blob/b54311aaae24d12f92832fcfc68167c8fe2c81c6/collie-core/nanobot/agent/tools/mcp.py)

## 3. Architecture and ownership

| Component | Planned responsibility |
| --- | --- |
| `collie_core/connectors/models.py` | Connection definition, transport/auth configuration, provenance, capabilities and lifecycle contracts |
| `collie_core/db.py` | Persistent custom definitions, connection instances, discovered tool metadata, migrations |
| `collie_core/connectors/manager.py` | Resolve definitions, dispatch drivers, coordinate connect/test/reconnect/remove and runtime attachment |
| `collie_core/connectors/drivers/` | Shared remote MCP driver, local process driver, provider API adapters |
| `collie_core/connectors/auth.py` | OAuth discovery, registration/profile selection, browser callbacks, refresh and reauthorization |
| `collie_core/services/credentials.py` plus Electron main | OS-protected credential storage and narrowly scoped secret submission |
| `collie_core/connectors/policy.py` and existing permissions | Tool classification, user authority and per-action approvals |
| `collie_core/runtime.py`, `settings.py`, adapted MCP engine | Connection attachment, removal, tool routing and isolation |
| `collie_core/ipc/server.py`, Electron/preload bridge, renderer `lib/ipc.ts` | Typed connection commands and authoritative status events |
| `ConnectorsScreen.tsx` and connector components | Search, add/import, authentication, capability selection, status, repair and removal |
| Existing chat connector tools | Equivalent approved setup actions from conversation |

Keep the existing connector manager as the authoritative entry point. Inventory the older `services/` catalogue/facade and migrate or retain explicit compatibility adapters; do not add a third catalogue or another token store. Changes to adapted `nanobot/` remain narrow.

### Data contracts

Separate these concepts:

1. **Recipe:** versioned installation/auth instructions and service presentation; curated or imported.
2. **Definition:** concrete server/API configuration selected by the user, including transport, endpoint or package, authentication profile and recipe version.
3. **Connection:** one authenticated account/instance of a definition, with its own credential reference, granted capabilities, status and lifecycle revision.
4. **Tool inventory:** namespaced tool identities, schemas/descriptions needed for execution, schema hashes, enabled state and policy classification.

Extend `connector_connections` and `connector_tool_cache` where appropriate; add a definition table rather than overloading `provider_id` with URLs or commands. Choose the next migration number at implementation time. No secrets belong in definition rows, exported configuration, logs, or cached tool metadata.

Existing provider IDs and connections must resolve unchanged after migration. A recipe update must not silently change an installed endpoint, executable package, requested scopes, or trust designation. Such changes require review/reconnection as applicable.

Keep provider availability separate from account status. Distinguish unsupported implementation, missing local requirements, account authentication, organization approval, and failed health checks. Existing statuses can remain on the wire with structured reason codes and additive fields.

## 4. Step-by-step delivery

### Step 1 — Lock scope and establish regression evidence

Inventory the latest implementation, current dependencies, packaged runtime, and provider catalogue. Confirm the shared driver contract and the schema above. Capture regression cases for existing working connections before refactoring.

First-release scope: remote MCP, flexible auth, add/import UI, Slack internal testing, and Microsoft Teams delegated Graph testing. Managed local servers follow. Continuous event subscriptions and chatting with Collie inside Slack/Teams are separate features; this plan initially concerns Collie acting on those services from its desktop chat.

**Exit:** accepted architecture, capability matrix, and documented provider setup prerequisites; no enabled-only placeholder entries counted as working.

### Step 2 — Add persistent definitions and backward-compatible migration

Implement custom definitions, credential references, namespaced tool inventory, and per-connection operation revisions. Migrate existing catalogue-backed connections without changing their permissions or losing credentials. Validate exports and user-data deletion for the new tables.

**Exit:** fresh-install and upgrade tests pass; existing accounts resolve to equivalent runtime configuration; interrupted migration recovery is documented. Ship migrations separately from broad feature changes where practical.

### Step 3 — Generalize the remote driver

Extract the common discovery/probing logic from `OfficialMcpDriver`. Accept validated definitions rather than requiring a hardcoded provider ID. Support Streamable HTTP and explicit legacy SSE. Reuse the adapted engine's transports, reconnect handling and schema normalization.

Discover the complete tool inventory, including pagination. Handle protocol negotiation, timeouts, cancellation, malformed responses, duplicate names and partial availability. Use per-connection namespaces, not display names, to avoid collisions between accounts.

Endpoint checks must cover initial URLs, redirects and discovered OAuth metadata. Permit localhost/private-network servers through an explicit local/private connection choice, without silently extending that access to unrelated destinations.

**Exit:** an unknown-to-Collie remote server can be added, queried and removed without modifying the catalogue.

### Step 4 — Complete authentication strategies

Implement explicit strategies for:

- Servers requiring no authentication.
- API keys/bearer tokens/custom header credentials stored through the protected secret path.
- OAuth discovery and PKCE, automatic client registration/client metadata where supported by the negotiated specification and SDK.
- Pre-registered provider clients, fixed callbacks where required, and provider-specific scope/refresh behavior.

Audit actual SDK support before selecting an upgrade; do not assume every mechanism is already implemented. Bind client registration and tokens to their issuer and intended resource. Prevent credentials following cross-origin redirects. Use one refresh coordinator per account and avoid unsolicited browser prompts during tasks.

Secret fields may accept user input transiently, but must never be returned in account details, stored in renderer state beyond submission, or sent through model-visible chat. Explain expiry, cancelled consent and administrator blocks in ordinary language.

**Exit:** connect, refresh, restart, expired refresh token, denied consent and reauthorization work for representative auth strategies. [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)

### Step 5 — Preserve permissions for dynamically discovered tools

Route every new tool through existing central policy. Imported servers remain untrusted; read-only annotations alone do not grant authority. Distinguish granting access to a service from approving a send, publish, deletion or financial action.

Do not promote a third-party server to trusted merely because it appears in a registry. Re-check newly discovered or materially changed tools before allowing prior approval preferences to apply. Account switching must not reuse another connection's grants. Server instructions are data, not authority to alter Collie's policy.

**Exit:** unknown tools cannot bypass approval; tool/schema changes and multiple-account cases have focused tests.

### Step 6 — Add IPC and runtime lifecycle support

Add typed commands for validating/importing a definition, saving it, beginning/cancelling auth, inspecting tools/capabilities, reconnecting, and removal. Reuse existing commands where their semantics fit. Return sanitized errors and operation IDs/revisions so late auth/test results cannot resurrect a removed connection.

Disconnect prevents new calls immediately and handles in-flight calls explicitly. Attach new connections at a safe boundary without disrupting unrelated chat. Preserve tool identity and connection scope in subagents and routines.

For many connected services, search/select relevant tools rather than loading every schema into every prompt. Reuse an existing tool-discovery mechanism if available; otherwise introduce a bounded lookup that returns only enabled tools for the authorized connection.

**Exit:** add/remove/reconnect during active work behaves predictably; large tool inventories stay bounded; unrelated accounts and conversations remain unaffected.

### Step 7 — Build the frontend add/import experience

Keep **Connected** and **Explore**, adding **Add connection** with plain-language options:

- Find a service.
- Connect using a link.
- Import a connection file.
- Install a local connector, when Step 11 ships.

Import common `mcpServers` configurations, map supported fields, and show unsupported options rather than silently dropping them. Import is a preview: it must not execute a command, install a package or contact a server automatically. Extract imported secrets into the protected submission path and exclude them from summaries/exports.

Flow: identify connection → preview publisher/location/access → authenticate → discover capabilities → safe test → connected. Show the actual account/workspace, useful example tasks, and capabilities available to that account.

An API-only adapter does not need to expose technical MCP terminology. Advanced fields stay available in expandable details. Chat-based setup calls the same backend flow.

**Exit:** a user adds a new compatible remote connection without editing JSON or using a terminal; accessibility, cancellation, retry, refresh and duplicate-account behavior are covered.

### Step 8 — Make connection failures actionable

Replace generic coming-soon/failure messages with a truthful next step: sign in again, obtain workspace approval, install a runtime, correct an endpoint, or use an available alternative route. Keep service implementation status visible separately from user account health.

Add per-account test, reconnect and disconnect controls. Explain local credential removal versus remote revocation. Diagnostics must show sanitized stage/error information without disclosing credentials or message contents.

**Exit:** every supported failure class provides an accurate recovery action, and cancelled/removed connections cannot return as connected from stale events.

### Step 9 — Finish Slack as a curated connection

Register/configure a Collie Slack application, permitted callbacks and the minimum user scopes for search, read, and sending. Validate desktop PKCE with Slack's MCP-specific OAuth endpoints and refresh behavior. Fill the actual endpoint and profile into the recipe; keep send actions centrally gated.

Test internal-workspace access first. Complete the Marketplace distribution path before claiming public out-of-box availability: Slack currently allows Marketplace-published and internal apps, not unlisted distributed apps, and does not support dynamic client registration. If public distribution is blocked, report that dependency instead of changing availability flags or claiming another API bypasses it.

**Exit:** real search/read and approved-send tests, expiry/reconnect tests, workspace denial handling, and evidence for the intended distribution audience. App eligibility is an external dependency. [Slack MCP](https://docs.slack.dev/ai/slack-mcp-server/), [PKCE](https://docs.slack.dev/authentication/using-pkce/)

### Step 10 — Finish Microsoft Teams and reusable Microsoft sign-in

Implement an official API driver backed by Microsoft Graph and a Collie multitenant Entra application with public-client desktop authentication. Design sign-in once, then expose workload-specific capabilities without requesting every Microsoft permission up front.

First capabilities: list/read the user's chats, read permitted channel content, and send chat/channel messages with approval. Resolve exact delegated scopes and account support against current endpoint documentation; distinguish user-consentable operations from admin-required access. Support partial capabilities when broader consent is unavailable.

Use this foundation for Outlook email/calendar and OneDrive in subsequent provider slices, each with separate scopes and acceptance tests. Do not imply Teams work/school capabilities are available on personal Microsoft accounts. Add tenant-specific hosted Teams MCP only after verifying that customer's Work IQ licensing, admin grants and selected integration path.

**Exit:** authenticated account/tenant is visible; permitted reads and approved sends work; admin-required features show precise restrictions; refresh and tenant/account switching pass. [Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference), [Desktop auth](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration), [Current Work IQ requirements](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/get-started)

### Step 11 — Add managed local MCP servers

Implement the stdio driver with a per-connection process lifecycle, scoped environment, bounded logs, stop/restart behavior and Windows packaging support. Audit which Node/Python/package runtimes the installer actually supplies; do not depend on a developer machine's PATH.

Curated recipes specify pinned package versions and supported installation methods. Installation gets a separate review because it executes third-party software. Launch processes without shell-string concatenation and without visible terminal windows. Define enforceable filesystem/network/process containment for supported packages; tool-call permission prompts do not sandbox a server's own process. If a package requires broader host access, describe it explicitly instead of calling it contained.

Arbitrary imported local commands may require advanced setup; universal dependency compatibility is not an acceptance claim. Provide the managed no-terminal experience for a documented supported package set first.

**Exit:** clean Windows installation can install/start/use/update/remove supported local connectors, with failed installs, unavailable dependencies, crashes and permission boundaries tested.

### Step 12 — Expand discovery without coupling it to releases

Retain a bundled directory as an offline baseline. Add a versioned updateable recipe feed and optional external registry discovery. Show publisher/provenance and distinguish Collie-tested recipes from community results. Verify feed authenticity and schema, cache the last valid version, and tolerate feed outages.

Registry listing is discovery evidence, not proof of compatibility or trust. A selected recipe goes through the same validation/auth/probe flow. An update must never silently install packages or expand access on existing connections.

**Exit:** adding a recipe can make it discoverable without a binary release; existing connections work when discovery services are offline. [Official registry](https://registry.modelcontextprotocol.io/docs)

### Step 13 — Evaluate optional managed-provider coverage

Run a separate, bounded evaluation of a provider such as Pipedream for otherwise uncovered services. Verify exact actions, account types, OAuth distribution rights, consent requirements, production limits, cost, data handling, disconnect behavior and latency. Catalogue size alone is not an acceptance test.

If adopted, keep server-side integration credentials out of the desktop binary, provide explicit user identity/account isolation, and mark which service handles the connection. Direct connections continue to work without this provider or a mandatory Collie account. A managed route cannot bypass workplace policy and must not be silently substituted for a direct route.

**Exit:** documented product decision and demonstrated target workflows before any dependency is introduced. This step can be deferred without blocking Steps 1–12. [Pipedream MCP](https://pipedream.com/docs/connect/mcp), [OAuth production options](https://pipedream.com/docs/connect/managed-auth/oauth-clients)

### Step 14 — Packaged acceptance, rollout and documentation

Test on clean Windows machines, not just development environments. Exercise real account sign-in, useful read, approved write in a test destination, restart, refresh, reconnect, cancellation and removal. Mock servers cover deterministic transport/auth/protocol failures; real provider tests cover platform behavior that mocks cannot prove.

Roll out remote MCP first, then independently validated Slack/Teams routes, followed by local packages and broader discovery. Keep independent route controls so one broken integration does not disable all connections. These controls are rollout/recovery tools, not permanent substitutes for missing implementations.

Back up migration-sensitive state before rollout, define compatible rollback versions, and avoid dropping new user definitions during rollback. Endpoint/recipe rollback must not downgrade trust or broaden permissions. Publish the verified capability matrix and update the canonical product/architecture documentation only as implementation truth changes.

**Exit:** all release criteria below pass for each advertised route; installer/version and provider-test evidence are recorded.

## 5. Dependency order and reviewable work packages

| Package | Steps | Depends on | Review result |
| --- | --- | --- | --- |
| A | 1–2 | Implementation authorization | Contracts, migration and compatibility evidence |
| B | 3–5 | A | General remote transports, authentication and central policy |
| C | 6–8 | B; frontend mocks can start after A | Complete desktop/chat add-connect-use-recover flow |
| D | 9 | B–C; begin app-registration preparation after approval | Slack internal acceptance, then separately public eligibility |
| E | 10 | A–C; Microsoft registration preparation after approval | Teams Graph acceptance and reusable Microsoft sign-in |
| F | 11 | A–C | Managed local package support |
| G | 12 | C; local recipes depend on F | Updateable discovery with provenance |
| H | 13 | Independent evaluation; integration depends on C | Optional managed-provider decision |
| Release | 14 | Each selected package's acceptance | Incremental verified rollout |

Provider registration/review can proceed alongside software development after authorization. Do not make generic MCP wait for Slack Marketplace review. Do not estimate external review completion as engineering time. Re-estimate engineering effort after A establishes actual SDK/runtime gaps.

## 6. Release acceptance matrix

| Area | Required proof |
| --- | --- |
| Existing connections | Upgrade preserves IDs, credentials, capabilities and approval preferences |
| Unknown remote server | Connect and call tools without adding a provider to source code |
| Authentication | No-auth, token/header and OAuth cases; rejected consent, expiry, refresh and reauthorization |
| Interoperability | Negotiation, paginated discovery, malformed tools, name collisions, HTTP/SSE and disconnects |
| Permissions | Imported tool hints cannot bypass policy; changed tools require appropriate review |
| Isolation | Separate accounts, issuer-bound tokens, scoped subagent/routine use and no cross-account grants |
| Lifecycle | Cancel/remove wins against late completion; restart restores correct state; in-flight calls are handled |
| Frontend | Add/import/search, keyboard use, accessible status, actionable failures and secret-safe diagnostics |
| Slack | Real account search/read/approved send plus audience-appropriate app eligibility |
| Teams | Actual delegated capabilities, approved send, partial consent and tenant restrictions |
| Local packages | Clean Windows setup, managed dependencies, process cleanup and stated containment |
| Scale | Bounded prompt/tool loading and no connection storms with a large installed inventory |
| Privacy | No secrets in model context, SQLite definitions, exports, diagnostics or logs; truthful data routing |

## 7. Owner-controlled dependencies and unresolved choices

- Slack application ownership, internal test workspace, and public Marketplace eligibility/review.
- Microsoft application ownership, test tenant, publisher/tenant consent requirements and delegated scope validation.
- Test accounts and explicit authorization for real write tests; keep them in designated test conversations/resources.
- Supported local package runtime set and enforcement mechanism, decided before advertising managed local execution.
- Hosting/ownership of a recipe feed if dynamic discovery ships.
- Optional managed provider's cost and data-processing decision; default proposal is to defer adoption until evaluated.

Implementation is authorized. Provider account consent, designated real-write test destinations, application ownership, distribution approval, and clean-machine acceptance remain explicit external dependencies. No provider is newly advertised by the storage foundation.



## 8. Presentation and brand assets

Use official, locally bundled provider brand assets with a manifest recording source,
retrieval date, and usage terms. Preserve their aspect ratio and required attribution.
Do not generate or approximate another company's logo. Keep Connected and Explore
as the main views, with advanced transport/authentication details behind disclosure.
Google Workspace, Outlook, OneDrive, and other workload bundles remain separately
scoped follow-on work; this plan does not imply their availability.

## 9. Foundation capability baseline

The Step 1 inventory against the baseline commit found 34 bundled recipes;
23 were enabled as alpha on Windows with protected credential storage available.
These flags describe implementation routing, not verified provider acceptance.

| Boundary | Baseline implementation | Delivery still required |
| --- | --- | --- |
| Driver | `OfficialMcpDriver`, Streamable HTTP, OAuth, one-page discovery | Arbitrary definitions, SSE, pagination, no-auth/header and registered-client auth |
| Manager | One authoritative lifecycle with legacy `services/` compatibility | Revision-aware IPC completion and generalized drivers |
| Storage | Schema V15; connection/tool tables introduced in V7 | Definition snapshots, credential references, operation revisions, inventory identities |
| Permissions | Central conservative classification and approval preferences | Material tool-change review and broader account isolation coverage |
| Desktop | Connected/Explore, curated search, sign-in/test/remove | Add link/import preview and richer recovery |
| Providers | Curated hosted MCP route | Slack registration/acceptance; Teams Graph driver and Entra registration/acceptance |
| Packaging | CPython 3.12 staging and Node probe runtime (`24.18.0` package engine declaration) | Managed local package installation, containment and clean-machine acceptance |

Dependencies remain unchanged: `mcp>=1.26.0,<2.0.0` and
`httpx>=0.28.0,<1.0.0`. The local regression environment used CPython 3.12.14
and MCP 1.29.0; this is development evidence, not a packaged SDK guarantee.
The baseline passed 97 tests across `test_connectors.py`, `test_services.py`,
`test_db.py`, `test_connect_validation.py`, `test_mcp_connection.py`, and
`test_mcp_reconnect_crash.py`. The existing local environment lacked
`pytest-timeout`, producing configuration warnings; no real-provider or installer
acceptance was performed. These suites cover lifecycle, credential loss, removal,
cancellation, policy, legacy compatibility, and engine reconnect behavior.

The shared driver contract retains `connect_and_probe`, noninteractive `probe`,
and best-effort `revoke`, returning `ProbeResult`. New installed-definition
snapshots adapt to that contract for existing official routes. The manager remains
authoritative; no second catalogue, token store, or runtime is introduced.

### Upgrade and recovery contract

Schema V16 is an additive migration. Back up the closed application's SQLite
state and protected credential directory together before rollout; do not copy a
live WAL database as if its main file were a complete backup. Credential blobs
remain tied to their operating-system protection context.

The migration runner commits each schema change and version bump in one
`BEGIN IMMEDIATE` transaction. If V16 fails before commit, reopen with the fixed
build to retry from V15; do not manually advance `schema_version` or drop tables.
Installed snapshots unresolved after schema migration are completed by the manager
when their legacy provider is known. Unknown provider rows remain stored and
removable, with no guessed endpoint or newly granted authority.

Definitions and connection metadata are included in user-data export, while
credential references contain no credential payload. User-data deletion covers
the definition table. Tool inventory identities are additive storage identities;
existing runtime tool names remain stable in this slice. Operation revisions are
storage primitives here; stale IPC completion protection is Step 6 acceptance.

Downgrading to a pre-V16 build is not a supported rollback path: old writes can
leave newly added metadata inconsistent. Use a V16-compatible corrective build,
or restore the complete pre-upgrade backup with the app closed, accepting that
post-backup user changes are lost. Export any new definitions before a deliberate
restore. No automatic destructive downgrade is introduced.


## 10. Remote backend delivery (Step 3)

`ConnectorManager.connect_definition` accepts validated custom/imported no-auth
remote definitions. It creates an installed account through the existing lifecycle,
without a bundled catalogue entry. Streamable HTTP and explicitly selected legacy
SSE share transport/discovery helpers with the official driver and runtime. The
backend can connect, retest, restore after restart, and remove these accounts.
Desktop and chat add/import commands remain Steps 6�7; this backend entry point is
not yet a user-facing setup flow.

Each custom account gets its own full connection namespace and permission resource,
including when two accounts have the same endpoint or display name. Custom servers
remain untrusted: tool hints cannot grant read authority, and imported host/tool
trust overrides are rejected. Existing curated accounts keep their runtime names
and pinned snapshots. No-auth accounts do not require a credential blob; custom
token/header/OAuth strategies are rejected until Step 4 implements them.

The private-network choice is an explicit boolean persisted in the immutable
definition's existing configuration JSON; it needs no additional schema migration.
It applies only to the selected origin, including its scheme and port. Redirects
and discovered authorization destinations do not inherit private access merely
because the starting endpoint has it. Direct requests connect to a validated IP
while preserving the original HTTP host and TLS identity. Existing system-proxy
routes retain per-request URL checks; the configured proxy controls destination
DNS resolution on those routes.

Discovery follows pagination with bounded page/tool counts. Duplicate identities,
malformed inventories, and incomplete pagination fail discovery rather than
publishing a partial inventory. Each server attaches independently, so one failed
server does not prevent unrelated connections from attaching. Initialization,
discovery, cancellation, and runtime reconnects use the shared SDK/engine path.

Deterministic fake-server and transport tests establish backend behavior. They do
not establish real-provider authentication, installer acceptance, or universal MCP
compatibility. Steps 4�8 remain the next delivery sequence.
