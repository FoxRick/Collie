# Release information

The current public source release is `v0.1.0-alpha.4`. It contains the Windows
desktop application source and uses the official Collie portrait consistently
for the executable, system tray, window, and in-app brand mark.

This repository does not currently publish a Windows installer or GitHub
Release. Building from source does not create an official distribution, and
mutable local `dist/` output must never be represented as one.

- `release-artifact-validation.md` documents the repeatable artifact checks
  required before an installer can be published.
- `asset-provenance.md` records branding and third-party mark boundaries.
- `installer-ux.md` describes the Windows setup wizard (page flow, per-user
  no-admin choice, voice rules) and how to build it.

Release status must be stated from verified source and artifacts, not from a
local build directory.
