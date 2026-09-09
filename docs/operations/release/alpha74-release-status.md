# Alpha 0.1.0-alpha.7.4 — release record

**Status (2026-09-09):** PUBLISHED. `v0.1.0-alpha.7.4` is the current public
installer release of Collie (patch bump on top of `v0.1.0-alpha.7.3`).

## What shipped

| Piece | Value |
| --- | --- |
| Tag | `v0.1.0-alpha.7.4` (annotated, at main `d7924d7`, merged via PR #180) |
| GitHub Release | published 2026-09-09, prerelease (alpha channel), 13 assets |
| Windows | `Collie-Setup-0.1.0-alpha.7.4.exe` (NSIS x64) |
| macOS | `Collie-0.1.0-alpha.7.4-arm64.dmg` + `.zip` (arm64 only, **unsigned**) |
| Linux | `Collie-0.1.0-alpha.7.4.AppImage` (x64) |
| Auto-update feeds | `alpha.yml` (win), `alpha-mac.yml`, `alpha-linux.yml` |
| Integrity | `SHA256SUMS.txt`, `collie-build-provenance.json`, `collie-artifact-provenance.json` |

## Contents of this build (main since alpha.7.3)

This build carries the 72 commits merged to `main` after the 7.3 tag,
including:

- **feat: requester-owned shared sessions and local archive recovery** (V16/V17 migrations).
- **feat: gated Collie AI provider alongside BYOK (managed inference)**.
- **feat: connections foundation** — pinned recipe snapshots, installed connection
  inventory, staged connection specification and migration guidance.
- **feat: account onboarding + reviewed onboarding inventory**.
- **feat(ui): chat stream polish** (preserve scroll pause near the bottom).
- **feat(ui): stack sidebar footer (collapse, feedback, settings)**.
- **fix(connectors): honest remote revocation; OAuth flow lock affinity + closed-DB authority degradation.**
- **fix(ui): keep focus inside the feedback dialog after a failed submit.**
- **fix(core): make the MCP reconnect-shutdown repro deterministic.**
- **fix(providers): tolerate transient Claude refresh failures; preserve stream errors during cleanup.**
- **fix(core): snap spring-forward gap times; require loopback for keyless custom providers.**
- **fix(metrics): cap anonymous source count per day/install.**
- **docs(readme): refresh for published alpha.7.4 + real app screenshots.**

## Release process notes

1. Version bump `0.1.0-alpha.7.3` → `0.1.0-alpha.7.4` in
   `collie-ui/package.json` + `package-lock.json` (plus README badge and the
   regenerated repo snapshot) via branch `release/bump-v74`, PR #180.
2. Tag `v0.1.0-alpha.7.4` pushed at the bump commit `d7924d7` → `release.yml` ran
   green: Qualify (core pytest + ruff + UI typecheck/vitest) → all three OS builds
   → Draft GitHub Release (run #34349201546).
3. Draft verified (provenance `gitSha d7924d7`, `dirty: false`,
   `productVersion 0.1.0-alpha.7.4`; SHA256SUMS covers all 12 installer/feed
   assets) then published via `gh release edit ... --draft=false`.
4. Website `FALLBACK_RELEASE` (`app/download/fallback-release.ts`) bumped to 7.4
   assets (exact sizes from the published release) + updated SSR test, merged into
   `collie-webiste` main (`281889a`) and deployed to Cloudflare; both
   heycollie.com and workers.dev now serve `tag_name: v0.1.0-alpha.7.4`.

## Verification (2026-09-09)

- CI gates green: Qualify + Windows (Python 3.12 full suite) + macOS-arm64 +
  Linux builds, all `success` (run #34349201546).
- Draft carried all 13 assets; SHA256SUMS.txt covers the exe/dmg/zip/AppImage
  + blockmaps + feeds; build provenance points at tag commit `d7924d7`.
- Website live chain: `GET https://heycollie.com/api/download-link?platform=windows|mac|linux`
  → `ok:true`, `tag_name: v0.1.0-alpha.7.4`, per-platform URLs + sizes; the same
  on `collie-website.patrickfuchs.workers.dev`. The three installer URLs resolve
  (200) and `/download` returns 200 on both hosts.
