# Alpha 0.1.0-alpha.7 — release record

**Status (2026-08-20):** PUBLISHED. `v0.1.0-alpha.7` is the second public
installer release of Collie, following `v0.1.0-alpha.6` the same day.

## What shipped

| Piece | Value |
| --- | --- |
| Tag | `v0.1.0-alpha.7` (annotated, at main `2e8e3d6`) |
| GitHub Release | published 2026-08-20, prerelease (alpha channel), 13 assets |
| Windows | `Collie-Setup-0.1.0-alpha.7.exe` (NSIS x64, assisted setup wizard) |
| macOS | `Collie-0.1.0-alpha.7-arm64.dmg` + `.zip` (arm64 only, **unsigned** — Gatekeeper requires right-click → Open; ad-hoc signed so the app launches) |
| Linux | `Collie-0.1.0-alpha.7.AppImage` (x64) |
| Auto-update feeds | `alpha.yml` (win), `alpha-mac.yml`, `alpha-linux.yml` |
| Integrity | `SHA256SUMS.txt`, `collie-build-provenance.json`, `collie-artifact-provenance.json` |

## Contents of this build (main since alpha.6)

- **fix(oauth): current Claude Code OAuth config** (PR #77) — authorize via
  `console.anthropic.com/oauth/authorize`, redirect `http://localhost:54545/callback`,
  scope `org:create_api_key user:profile`; the legacy `claude.ai/oauth/authorize`
  surface rejected Collie's request shape. Claude Pro/Max sign-in works again.
- **fix(mac): ad-hoc sign the `.app` bundle** (PRs #74 + #86) — afterPack
  hook at electron-builder config root signs the bundle so Gatekeeper reports
  "unidentified developer" (right-click → Open) instead of "damaged".
- **fix(ui): remember pill floats above composer** with consistent 6s duration (PR #78).
- **fix(memory): Dream proposes before writing** — approve to apply (PR #73).
- **fix(ui): in-content "Back to chat" button** on every settings tab (PR #80).
- **fix(core): never store a sentence as the user's name** (PR #81).
- **ux(account): warm safety-framed copy** for secure-storage failures (PR #82).

## Release process notes (deltas vs alpha.6)

1. **First CI run failed on the mac signing hook** — `context.appOutDir` is
   the PARENT directory (`dist/mac-arm64`), not the `.app` bundle; `codesign`
   on the directory aborts with `bundle format unrecognized, invalid, or
   unsuitable`. The hook was added in #74/#75 AFTER alpha.6 shipped, so it
   had never run on CI. Fix (PR #86): sign
   `${appOutDir}/${packager.appInfo.productFilename}.app`. Windows/Linux
   builds were unaffected.
2. After the fix merged, the tag was **deleted and re-pushed** (delete + recreate)
   to trigger a clean rebuild — the same pattern documented in alpha.6's notes.
3. The release record (`README.md`) is updated in this same docs change.

## Verification (2026-08-20)

- CI gates green: qualify (core pytest + ruff + UI typecheck/vitest) on the
  fixed main; Windows full suite on Python 3.12; packaged-core smoke on all
  3 OSes.
- Draft release carried all 13 assets; SHA256SUMS.txt verified to cover the
  mac dmg/zip, win exe, linux AppImage + blockmaps + feeds.
- Website live chain: `GET https://heycollie.com/api/download-link?platform=mac|windows|linux`
  returns `ok:true` with `tag_name: v0.1.0-alpha.7` and per-platform asset
  URLs + sizes; `/download`, `/account`, `/roadmap`, `/get-started` all 200
  on heycollie.com; the mac dmg URL resolves (302 → release-assets 200).
- Claude sign-in: `tests/collie/test_auth.py` — 21 passed / 1 skipped
  locally on the VM, including all 11 Claude/OAuth-path tests.
