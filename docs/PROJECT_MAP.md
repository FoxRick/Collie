# Collie project map

**Status:** canonical
**Last structural review:** 2026-08-02

This is the public-source orientation map. It describes stable ownership and
runtime boundaries rather than every file.

## Repository layout

```text
Collie/
|-- AGENTS.md       Workspace-wide contributor instructions
|-- README.md       Public product and development entry point
|-- collie-core/    Python runtime
|-- collie-ui/      Electron and React desktop shell
|-- docs/           Public product, engineering, and release documentation
`-- tools/          Repository validation and asset utilities
```

The website at [heycollie.com](https://heycollie.com) is maintained in a
separate repository. Private business material, user data, runtime output,
recovery artifacts, internal investigations, and local reference repositories
are not part of this public source tree.

## Runtime ownership

### Python core: `collie-core/`

- `collie_core/` owns storage, settings, memory, permissions, plans, tools,
  connectors, agents, routines, messengers, pet control, IPC, provider
  onboarding (catalogue + key validation), telemetry (run records),
  runtime composition, and the internal headless engine
  mode (`collie_core/headless.py` — one task, one JSON result document,
  exit; an engineering-only benchmark entry, not a user-facing CLI).
- **Storage** (architecture: `docs/engineering/architecture/storage-domains.md`):
  `collie_core/db.py` owns the connection, schema, migrations, shared row
  helpers and the run/plan/telemetry cluster. One storage domain per module
  lives under `collie_core/db_domains/` (settings, conversations, life tools,
  automations, checklists, connectors, approvals, providers, artifacts) and
  those mixins are composed into `CollieDB`, which remains the single public
  interface: call sites keep using `db.<method>`. `collie_core/db_primitives.py`
  holds `collie_home()` / `utc_now()` / `new_id()`, re-exported from `db.py`.
- **Self-improvement stack** (Gardener Foundations, architecture:
  `docs/engineering/architecture/gardener-foundations.md`):
  - `collie_core/versions.py` — `VersionStore`: every user-visible artifact
    edit (subagent files, `VISION.md`/`AGENTS.md`/`MEMORY.md`, dream
    consolidations, Gardener applies) is snapshotted into the
    `artifact_versions` table (schema V14) with a before/after text pair +
    unified diff; one-action rollback that never clobbers newer owner
    edits. Wired into `SubagentLoader`, `ProfileStore`, IPC `write_file`
    (workspace artifacts), Dream, and Gardener applies.
  - `collie_core/memory/dream.py` — `run_dream()`: bounded, read-only
    consolidation of nanobot's long-term `memory/MEMORY.md` (vendored
    Dream machinery: cursor, prompt builder, session pruning); versioned
    as `memory_dream`, undoable in Settings → Memory.
  - `collie_core/gardener/` — the self-improvement loop:
    `evidence.py` (read-only telemetry queries: repeated tool failures,
    repeated workflows, stopped turns, memory bloat), `propose.py`
    (bounded subagent turn + deterministic validation: allowlisted
    artifact types only, keyword gate against permissions/settings/
    secrets/connectors, size budgets), `runner.py` (`run_gardener` +
    `apply_suggestion` through the versioned rollback rail).
  - Triggers: built-in automations (`memory_maintenance` Sun 09:00,
    `gardener` Sun 10:00, seeded once, disabled by default) + manual IPC
    (`run_dream`, `run_gardener`) from Settings → Memory → "Collie's
    self-review". Review cards (`gardener_suggestion`) render in chat with
    Approve/Dismiss/Undo.

- `nanobot/` contains the adapted upstream engine. Changes should remain
  surgical and preserve third-party attribution.
- `tests/` contains Python unit, integration, IPC, safety, and end-to-end
  checks.
- `AGENTS.md` contains core-specific contributor instructions.

### Desktop shell: `collie-ui/`

- `src/main/` owns Electron lifecycle, Python-core supervision, protected
  secret storage, tray and window behavior, and privileged IPC handlers.
- `src/preload/` exposes the narrow renderer bridge.
- `src/renderer/` contains the React application and product UI.
- `scripts/` contains staging, packaging, smoke, and release verification.
- `electron-builder.yml` defines Windows artifact composition.

### Feedback intake: `tools/feedback/`

The desktop's guarded feedback IPC sends only the user's message and a random
submission ID to a dedicated Cloudflare Worker. The Worker stores feedback in
the existing Supabase project and notifies the team through Resend; server
credentials remain in Worker secrets. See [in-app feedback](product/feedback.md)
for deployment ownership and the live verification gate.

### Shared conversations

`collie-core/collie_core/collaboration/` owns durable local shared-event storage
and verified local archives. Electron main owns authenticated collaboration
transport; the renderer receives product data without account tokens.
`supabase/migrations/` owns membership, canonical event order, requester device
leases, quota reservations, and archive receipts. `supabase/functions/` owns
Slack OAuth, signed event intake, and bounded workers. See
[shared session authority and recovery](engineering/architecture/shared-sessions.md)
for privacy boundaries and release gates. An offline participant blocks cloud
purge until their durable archive is verified.

## Primary data flow

```text
React renderer
  -> narrow Electron preload bridge
  -> Electron main process
  -> authenticated localhost WebSocket
  -> collie_core.ipc.server
  -> agent loop, tools, and connectors
  -> local SQLite and workspace state
  -> streamed events back to the renderer
```

Connection recipes remain in `collie_core/connectors/catalog.py`. Installed
configuration snapshots and account/tool inventory live in SQLite through the
storage layer (`collie_core/db.py` plus the `ConnectorsDomain` module in
`collie_core/db_domains/`); `ConnectorManager` resolves existing accounts
against those snapshots while retaining catalogue availability controls.
Credential references
point to the existing protected store. The connection specification in
`docs/product/features/connectors.md` separates this foundation from later driver
and desktop delivery.

Electron owns OS integration and protected secret storage. Python owns agent
behavior, durable product state, tool execution, and central permission
evaluation. The renderer must not receive decrypted long-lived secrets.

Packaged desktop product metrics use content-free daily counters in local
SQLite, read through `get_product_metrics` by Electron and uploaded alongside
install presence. Owner reporting lives in `tools/weekly_product_metrics.py`;
definitions, privacy boundaries, and rollout are documented in
[install heartbeat](engineering/architecture/install-heartbeat.md#weekly-product-metrics).

### Local-file access boundary

The chat's `file_access_scope` travels through the renderer bridge and Electron
main process into the core runtime for that turn. Core canonicalizes and
validates the allowed roots, and the `local_files` tool revalidates them while
executing; the `open_file` tool (default-app open of harmless file types and
folders) shares that same canonical resolution. Subagents inherit the same
immutable scope and cannot broaden it.
A composer change also reaches the core immediately via the
`set_file_access_scope` IPC command; local file tools consult that live
per-conversation override before the turn-bound scope, so a folder granted
mid-task applies to the running turn (the override is cleared when the turn
ends).
Full local-file access remains session-only and does not grant connector,
network, or external-write authority.

### Managed inference pilot

`tools/inference/` owns the disabled-by-default hosted Collie AI worker,
separate backend allowance schema, and offline verification. Electron's
`src/main/managed-inference.ts` forwards core requests through the authenticated
keychain bridge using a Collie account session; supplier credentials remain
server-only. The core's `providers/managed.py` adds a fixed hosted provider
alongside saved BYOK records. See
[managed inference](product/features/managed-inference.md) for the zero-cost
deployment gate and current context/capacity limits.

## Documentation ownership

- `docs/VISION.md`: durable product intent.
- `docs/PROJECT_MAP.md`: component ownership, paths, interfaces, and invariants.
- `docs/WORKFLOW.md`: delivery and documentation maintenance.
- `docs/product/`: active product decisions and feature specifications.
- `docs/engineering/`: architecture, security, and durable technical decisions.
- `docs/operations/`: public release policy and validation procedures.
- `docs/generated/`: deterministic repository inventory.

Implemented behavior is defined by code and tests. When documentation and
behavior disagree, verify the implementation and update the smallest canonical
document whose truth changed.
