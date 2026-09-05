# Alpha 0.1.0-alpha.7.3 — release record

**Status (2026-08-27):** PUBLISHED. `v0.1.0-alpha.7.3` is the current public
installer release of Collie (patch bump on top of `v0.1.0-alpha.7.2`).

## What shipped

| Piece | Value |
| --- | --- |
| Tag | `v0.1.0-alpha.7.3` (annotated, at main `f3ee2e6`, merged via PR #157) |
| GitHub Release | published 2026-08-27, prerelease (alpha channel), 13 assets |
| Windows | `Collie-Setup-0.1.0-alpha.7.3.exe` (NSIS x64) |
| macOS | `Collie-0.1.0-alpha.7.3-arm64.dmg` + `.zip` (arm64 only, **unsigned**) |
| Linux | `Collie-0.1.0-alpha.7.3.AppImage` (x64) |
| Auto-update feeds | `alpha.yml` (win), `alpha-mac.yml`, `alpha-linux.yml` |
| Integrity | `SHA256SUMS.txt`, `collie-build-provenance.json`, `collie-artifact-provenance.json` |

## Contents of this build (main since alpha.7.2)

- **fix(ui): broker core IPC through main — never expose the per-boot token
  to the renderer** (PR #156).
- **fix(win): wire Authenticode update signing + publisher verification**
  (PR #155).
- **fix(account): make cloud-sync restore transactional** (PR #154).
- **fix(versions): make artifact rollback race-safe** (per-artifact lock +
  mark-after-write) (PR #153).
- **fix(account): bind sign-in callback to flow with OAuth state nonce**
  (PR #152).
- **feat(connectors): enable OAuth connectors on macOS/Linux via safeStorage
  keychain bridge** (PR #151).
- **fix: reject a model the provider doesn't offer (and roll back to the
  previous one)** (PR #150).

## Release process notes

1. Version bump `0.1.0-alpha.7.2` → `0.1.0-alpha.7.3` in
   `collie-ui/package.json` + `package-lock.json` via branch
   `release/bump-v73`, PR #157.
2. Tag `v0.1.0-alpha.7.3` pushed at the bump commit → `release.yml` ran green:
   Qualify (core pytest + ruff + UI typecheck/vitest) → all three OS builds →
   Draft GitHub Release.
3. Draft published via `gh release edit v0.1.0-alpha.7.3 --draft=false` →
   public prerelease. 13/13 assets verified, all named `-0.1.0-alpha.7.3.*`,
   no stale files.
4. Website `FALLBACK_RELEASE` in `worker/index.ts` bumped to 7.3 assets
   (exact sizes from the published release), SSR fallback test updated in the
   same change, deployed to Cloudflare main; both heycollie.com and workers.dev
   now serve `tag_name: v0.1.0-alpha.7.3`.

## Verification (2026-08-27)

- CI gates green: Qualify + Windows (Python 3.12 full suite) + macOS-arm64 +
  Linux builds, all `success` (run #33047386303).
- Draft carried all 13 assets; SHA256SUMS.txt covers the exe/dmg/zip/AppImage
  + blockmaps + feeds.
- Website live chain: `GET https://heycollie.com/api/download-link?platform=windows|mac|linux`
  → `ok:true`, `tag_name: v0.1.0-alpha.7.3`, per-platform URLs + sizes.
  `/download`, `/account`, `/roadmap`, `/get-started` all 200 on heycollie.com;
  the three installer URLs resolve (302 → release-assets).
