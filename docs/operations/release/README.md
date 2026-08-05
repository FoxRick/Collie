# Release information

The current public source release is `v0.1.0-alpha.4`. It contains the Windows
desktop application source and uses the official Collie portrait consistently
for the executable, system tray, window, and in-app brand mark.

This repository does not currently publish a Windows installer or GitHub
Release. Building from source does not create an official distribution, and
mutable local `dist/` output must never be represented as one.

## Release pipeline

Tag-triggered CI (`.github/workflows/release.yml`, tags `v*`) builds the
installers on GitHub-hosted runners — Windows NSIS x64, macOS dmg/zip
(arm64 only for alpha; the staged Python runtime is native-arm64, so Intel
x64 bundles are a follow-up), and Linux AppImage x64 — and attaches them
to a draft GitHub Release with `SHA256SUMS.txt`. Test-on-push CI
(`.github/workflows/ci.yml`) runs the collie-core pytest + ruff gates and the
collie-ui typecheck/vitest/build gates on every push and pull request.

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

Release status must be stated from verified source and artifacts, not from a
local build directory.
