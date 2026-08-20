# Alpha 0.1.0-alpha.6 — release record

**Status (2026-08-20):** PUBLISHED. `v0.1.0-alpha.6` is the first public
installer release of Collie. Source of truth for behavior is the code; this
page is the record of how the release was produced and verified.

## What shipped

| Piece | Value |
| --- | --- |
| Tag | `v0.1.0-alpha.6` (annotated, at main `3d8c111`) |
| GitHub Release | published 2026-08-20 08:00 UTC, prerelease (alpha channel), 13 assets |
| Windows | `Collie-Setup-0.1.0-alpha.6.exe` (NSIS x64, assisted setup wizard) |
| macOS | `Collie-0.1.0-alpha.6-arm64.dmg` + `.zip` (arm64 only, **unsigned** — Gatekeeper requires right-click → Open; signing/notarization is the $99/yr Apple Developer follow-up) |
| Linux | `Collie-0.1.0-alpha.6.AppImage` (x64) |
| Auto-update feeds | `alpha.yml` (win), `alpha-mac.yml`, `alpha-linux.yml` |
| Integrity | `SHA256SUMS.txt`, `collie-build-provenance.json`, `collie-artifact-provenance.json` |

## Contents of this build (main since alpha.5 review)

- All 2026-08-18 pre-release security review fixes (see
  [alpha5-release-status.md](alpha5-release-status.md)): renderer secret
  boundary, Supabase config baked into installers, session auto-refresh,
  ambiguous API key detection without probing, storage hardening (Linux
  `basic_text` rejected; OAuth connectors gated off Windows), ruff/format
  drift, provenance register.
- **Desktop pet gated for release** (PR #66, `feat/pet-coming-soon`) —
  settings shows "coming soon".
- **Version bump** to `0.1.0-alpha.6` (PR #67).
- Release gates green: core pytest 3570 passed / 1 known load-flake /
  1 skipped / 5 deselected, coverage 76.0% (floor 42), UI vitest 352/352,
  typecheck clean, Windows core suite on Python 3.12, packaged-core smoke on
  all 3 OSes, ruff + release artifact validator.

## Verification (owner + agent, 2026-08-20)

- Owner's **Windows acceptance pass**: installer installs and runs on a clean
  profile; real API key connects; messaging works. ✅
- Website live chain: `GET https://heycollie.com/api/download-link?platform=windows|mac|linux`
  returns `ok:true` with per-platform asset URLs + sizes; `/download` and
  `/account` serve the release; asset URLs resolve (302 →
  objects.githubusercontent.com). ✅

## Release process notes (for the next alpha)

1. Version bump PR first (`collie-ui/package.json` — CI does NOT read the
   tag for the version; a tag without a bump ships the previous version
   label).
2. Push the annotated tag → `release.yml` qualifies, builds all 3 platforms,
   and creates a **draft** release.
3. **Publish the draft** (keeps assets/body/prerelease). ⚠️ Do NOT create a
   new release for the same tag in the UI — GitHub replaces the draft with
   an empty release (hit 2026-08-20; fixed by deleting the empty release and
   re-pushing the tag).
4. If a draft ever accumulates stale assets (a rebuild appends to an existing
   draft): delete the draft, re-run the `Draft GitHub Release` job
   (`gh run rerun --job <id>` — needs Actions:Write) — the action removes
   duplicates and re-uploads cleanly.
5. VM token `hermes-vm-pr` now has Actions + Contents write on
   FoxRick/Collie → the agent can publish drafts, delete releases, upload
   assets, and re-run CI jobs itself.
