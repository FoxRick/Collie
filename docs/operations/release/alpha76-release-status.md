# Collie 0.1.0-alpha.7.6

**Channel:** alpha prerelease, patch increment from `0.1.0-alpha.7.5`.
**Status:** published on 2026-11-01 after tagged builds and downloaded-artifact validation.

- Tagged source: `ba02b5b6ea41d2a0f0a910128b15ebcfaae4be53`.
- [GitHub release](https://github.com/FoxRick/Collie/releases/tag/v0.1.0-alpha.7.6).
- [Successful release workflow](https://github.com/FoxRick/Collie/actions/runs/41281783715).
- All 13 attached assets verified: 12 combined SHA-256 entries, all three
  updater feeds and their sizes/SHA-512, clean tagged-source provenance,
  macOS arm64/Linux x64 headers, and packaged native notices/source inventory.
- Windows installer: 366,010,246 bytes; macOS DMG: 440,343,682 bytes;
  Linux AppImage: 476,280,125 bytes.

## Scope

This incremental release includes the five reviewed changes merged through
`bde11234cce25be3499eb416f30ce582cdc89b81`: collapsed sidebar/theme fixes,
painted composer expressions, a single native tray with recent chats, scoped
local browser journeys, and the approved Windows computer-control preview.
Browser fixes enforce native file selection, make the initial Open stoppable,
and avoid duplicate approval resolution. Experimental native editor PR #198
is excluded.

## Downloads

- Tag: `v0.1.0-alpha.7.6`.
- Windows x64: `Collie-Setup-0.1.0-alpha.7.6.exe`.
- macOS Apple Silicon: `Collie-0.1.0-alpha.7.6-arm64.dmg` and `.zip`.
- Linux x64: `Collie-0.1.0-alpha.7.6.AppImage`.
- Updater feeds: `alpha.yml`, `alpha-mac.yml`, `alpha-linux.yml`.
- Integrity: combined `SHA256SUMS.txt` and Windows source/artifact provenance.

The tag-triggered Release workflow qualified, built and smoke-tested all three
platforms. Downloaded artifacts passed checksum, updater and provenance validation
before draft publication. Website fallback URLs and sizes use those verified
release assets. A local build directory is not a release.

## Native dependency and artwork records

The three pinned Windows CUA binaries match the upstream
`cua-driver-rs-v0.37.0` distribution byte for byte, at source commit
`2d2f99228d4d2508dc6fcc84c176b296e146714a`. `runtime-sbom.json` records a
conservative, locked Windows normal/build dependency closure of 389 packages
for those three binaries. Development edges are excluded; Cargo feature
unification can include additional packages. The inventory is a source-level
dependency record, not a claim of reproducible upstream compiler output.

`DEPENDENCY-NOTICES.md` retains 238 distinct license/notice texts, including
MPL-2.0 UniFFI notices and exact-version source availability links. Crate
checksums and notice hashes are retained in the inventory. Archive-missing
licenses were fetched from the VCS commits recorded in the published crates.
`tools/update_cua_licenses.py` regenerates the records from Cargo metadata for
the exact upstream tag, `--locked --filter-platform x86_64-pc-windows-msvc`.
No GPL/AGPL/LGPL license declaration occurs in this selected closure.

Composer expression atlases and their owner-created source sheets are recorded
in the artwork provenance register and hash manifest.

## Verification and limits

Before the version bump, the integrated source passed 3,937 backend tests,
594 UI tests, typecheck/build, lint/format, real Windows control acceptance,
two real Edge journeys, and all three acceptance tests from staged resources.
Portability and authenticated packaged-core IPC smoke passed. Tagged CI repeated
qualification and successfully packaged/smoke-tested all three platform artifacts.

Windows computer control remains a local preview and is Windows-only. Browser
agent control is text-based; screenshots are for human inspection. These
features require their existing per-action approvals. Installed-app real-account
acceptance, broader DPI/graphics coverage and autonomous visual-model support
are not claimed. macOS remains arm64-only, ad-hoc signed and not notarized;
Windows signing depends on the configured release certificate.
