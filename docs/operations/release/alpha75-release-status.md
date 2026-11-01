# Collie 0.1.0-alpha.7.5

**Channel:** alpha prerelease, patch increment from `0.1.0-alpha.7.4`.

## Source and downloads

- Tag: `v0.1.0-alpha.7.5`.
- Tagged source: `720fbd705210764e2ea2945fe9b44620d8a3a988` (version bump PR #203).
- [GitHub release](https://github.com/FoxRick/Collie/releases/tag/v0.1.0-alpha.7.5).
- Windows x64: `Collie-Setup-0.1.0-alpha.7.5.exe`.
- macOS Apple Silicon: `Collie-0.1.0-alpha.7.5-arm64.dmg` and `.zip`.
- Linux x64: `Collie-0.1.0-alpha.7.5.AppImage`.
- Updater feeds: `alpha.yml`, `alpha-mac.yml`, and `alpha-linux.yml`.
- Integrity: `SHA256SUMS.txt`, `collie-build-provenance.json`, and
  `collie-artifact-provenance.json`.

The tag includes merged changes through PR #200, including recoverable skill
editing, temporary text chats, explicit chat recall and task handoff, dictation
improvements, provider recovery, chat titles, Connections contrast, shopping
persistence, and the isolated visible browser interaction preview. Unmerged
browser, native editor, and computer-control work is outside this release.

## Release verification

[Release workflow run 40264965494](https://github.com/FoxRick/Collie/actions/runs/40264965494)
owns qualification, the three platform builds, packaged-core smoke tests, and
creation of the draft release. Publish only after every job passes and the
attached assets, checksums, updater feeds, and clean tagged-source provenance
have been verified. See [artifact validation](release-artifact-validation.md).

Website downloads at [heycollie.com/download](https://heycollie.com/download)
resolve published GitHub assets. The separately maintained website also keeps
verified fallback URLs and exact installer sizes for first-paint downloads and
GitHub outages.

macOS remains arm64-only, ad-hoc signed, and not notarized. Windows signing
requires the repository's configured Authenticode certificate; unsigned alpha
builds may trigger SmartScreen.
