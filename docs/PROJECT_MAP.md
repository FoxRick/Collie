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

Electron owns OS integration and protected secret storage. Python owns agent
behavior, durable product state, tool execution, and central permission
evaluation. The renderer must not receive decrypted long-lived secrets:
stored secrets are decrypted and pushed to the core by the main process only
(`src/main/core-client.ts`, fired on core-ready); the renderer sees a bare
count (`collie:stored-secret-count`). See
[docs/engineering/security/renderer-trust-boundary.md](engineering/security/renderer-trust-boundary.md).

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
