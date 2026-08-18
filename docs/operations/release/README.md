# Release information

The current public source release is `v0.1.0-alpha.4`. It contains the Windows
desktop application source and uses the official Collie portrait consistently
for the executable, system tray, window, and in-app brand mark.

**In-progress release (alpha.5):** fixes from the 2026-08-18 pre-release
security review are merged (or pending merge) — see
[alpha5-release-status.md](alpha5-release-status.md) for the current status,
what was verified, and the remaining sequence (merge, re-deploy, tag, owner
acceptance).

This repository does not currently publish a Windows installer or GitHub
Release. Building from source does not create an official distribution, and
mutable local `dist/` output must never be represented as one.

## Release pipeline

Tag-triggered CI (`.github/workflows/release.yml`, tags `v*`) builds the
installers on GitHub-hosted runners — Windows NSIS x64, macOS dmg/zip
(arm64 only for alpha; the staged Python runtime is native-arm64, so Intel
x64 bundles are a follow-up), and Linux AppImage x64 — and attaches them
to a draft GitHub Release with `SHA256SUMS.txt`. A `qualify` job gates the
build: core pytest + ruff (with the coverage floor) and collie-ui
typecheck/vitest run before any package is built, the Windows build job
runs the full core suite on Python 3.12 (including the DPAPI/service tests
deselected on Linux), and the staged packaged core is smoke-tested
(`smoke:packaged-core`) before upload. Test-on-push CI
(`.github/workflows/ci.yml`) runs the collie-core pytest + ruff gates and the
collie-ui typecheck/vitest/build gates on every push and pull request, on
both Ubuntu (Python 3.11) and Windows (Python 3.12 — the supported runtime).

Publishing a release still requires a maintainer to push the version tag and
publish the draft. The draft must not be published until:

- the asset provenance register below records a clearance for branded art and
  third-party marks (an installer-distribution blocker), and
- the release candidate passes the repeatable artifact checks documented in
  `release-artifact-validation.md` (the Windows job runs the repo's own
  provenance + checksum tooling; macOS signing/notarization is a follow-up
  once Apple Developer certificates exist).

- `release-artifact-validation.md` documents the repeatable artifact checks
  required before an installer can be published.
- `asset-provenance.md` records branding and third-party mark boundaries.
- `installer-ux.md` describes the Windows setup wizard (page flow, per-user
  no-admin choice, voice rules) and how to build it.

Release status must be stated from verified source and artifacts, not from a
local build directory.
