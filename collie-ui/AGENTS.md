# Collie desktop instructions

These rules apply to the Electron and React desktop application.

## Boundaries

- `src/main/` is privileged: process lifecycle, core supervision, OS secret
  storage, filesystem access, windows, tray, updater, and IPC handlers.
- `src/preload/` exposes the smallest typed bridge the renderer needs.
- `src/renderer/` is unprivileged product UI and must not receive decrypted
  long-lived secrets or arbitrary Node access.
- `scripts/` and `electron-builder.yml` define packaging and release behavior;
  path changes must be verified against packaged output, not only dev mode.

The Python runtime currently lives at sibling `../collie-core`. Path-sensitive
launch and staging logic must move together in a dedicated migration.

## Product and safety

- Keep UI language warm, plain, and useful to nontechnical users.
- Do not present static catalogue state as a working provider or connector.
- Consequential actions remain governed by the Python permission engine; UI
  confirmation is an interaction layer, not an authorization bypass.
- Keep external navigation, renderer IPC, attachments, and secret handling
  narrow and validated.
- Preserve conversation isolation for streamed events, task progress,
  approvals, routines, and messenger activity.

## Checks

From `collie-ui/`, run the narrow relevant test first, then as appropriate:

```powershell
npm test
npm run typecheck
npm run build
```

For packaging or core-launch changes also run the staging and packaged-core
smoke checks described by the active release documentation.
