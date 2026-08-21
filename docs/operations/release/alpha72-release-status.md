# Alpha 0.1.0-alpha.7.2 — release record

**Status (2026-08-21):** PUBLISHED. `v0.1.0-alpha.7.2` is the current public
installer release of Collie (patch bump on top of `v0.1.0-alpha.7.1`).

## What shipped

| Piece | Value |
| --- | --- |
| Tag | `v0.1.0-alpha.7.2` (annotated, at `release/bump-v72` `b48ac62`, same tree as main-after-merge) |
| GitHub Release | published 2026-08-21, prerelease (alpha channel), 13 assets |
| Windows | `Collie-Setup-0.1.0-alpha.7.2.exe` (NSIS x64) |
| macOS | `Collie-0.1.0-alpha.7.2-arm64.dmg` + `.zip` (arm64 only, **unsigned**) |
| Linux | `Collie-0.1.0-alpha.7.2.AppImage` (x64) |
| Auto-update feeds | `alpha.yml` (win), `alpha-mac.yml`, `alpha-linux.yml` |
| Integrity | `SHA256SUMS.txt`, `collie-build-provenance.json`, `collie-artifact-provenance.json` |

## Contents of this build (main since alpha.7.1)

- **feat(ui): smooth continuous Collie Circle motion** (PR #97) — crossfaded
  gaze, organic blinks, auto-framing so the chat-head portrait stays centered.
- **security/hardening (issue #93 checklist)** (PR #96) — npm audit 0,
  catalogue URL pinned to models.dev feed, secrets.json / auth.json written
  owner-only 0600, pytest-timeout gate (120s/thread).
- **restore ruff format gate** (PR #95) + **pypdf 6.x bump** (PR #94, drops
  ~26 known CVEs).
- Plus the earlier alpha.7.1 fixes (pypdf, format gate, updater patch-level gate).

## Release process notes

1. Version bump `0.1.0-alpha.7.1` → `0.1.0-alpha.7.2` in `collie-ui/package.json`
   + `package-lock.json` via branch `release/bump-v72`, PR #98.
2. Tag `v0.1.0-alpha.7.2` pushed at the bump commit → `release.yml` ran green:
   Qualify (core pytest + ruff + UI typecheck/vitest) → all three OS builds →
   Draft GitHub Release.
3. Draft published via API (draft:false → public prerelease). 13/13 assets
   verified, all named `-0.1.0-alpha.7.2.*`, no stale files.
4. Website `FALLBACK_RELEASE` in `worker/index.ts` bumped to 7.2 assets (exact
   sizes from the published release), SSR fallback test updated in the same
   change, deployed to Cloudflare main; both heycollie.com and workers.dev now
   serve `tag_name: v0.1.0-alpha.7.2`.

## Verification (2026-08-21)

- CI gates green: Qualify + Windows (Python 3.12 full suite) + macOS-arm64 +
  Linux builds, all `success`.
- Draft carried all 13 assets; SHA256SUMS.txt covers the exe/dmg/zip/AppImage
  + blockmaps + feeds.
- Website live chain: `GET https://heycollie.com/api/download-link?platform=windows|mac|linux`
  → `ok:true`, `tag_name: v0.1.0-alpha.7.2`, per-platform URLs + sizes.
  `/download`, `/account`, `/roadmap`, `/get-started` all 200 on heycollie.com;
  the three installer URLs resolve (302 → release-assets).
