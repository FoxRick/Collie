# Collie 0.1.0-alpha.7.7

**Channel:** alpha prerelease.
**Status:** published on 2026-11-11 after tagged builds and downloaded-artifact validation.

- Tagged source: `f422b02ca472ecd7ad571a56c79e72888af3eeda`.
- [GitHub release](https://github.com/FoxRick/Collie/releases/tag/v0.1.0-alpha.7.7).
- [Successful release workflow](https://github.com/FoxRick/Collie/actions/runs/42963111694).
- All 13 attached files accounted for: 12 SHA-256 entries, all three updater
  feeds and their sizes/SHA-512, clean tagged-source provenance, and packaged
  native notices/source inventory passed validation.
- Windows installer: 366,117,813 bytes; macOS DMG: 440,467,092 bytes;
  Linux AppImage: 476,448,587 bytes.
- Windows installer SHA-256:
  `2c45a4c9e47d89e5658747331d7bb8507ee62562f363a82adeaaaff90f39610b`.

## Scope

Includes desktop email signup and sign-in through the website handoff, local
chat when continuing as a guest, draft retention through model setup, and
validated provider setup including Claude API keys. These are the merged
changes in PRs #209, #211, #212, and #213.

## Website and distribution

The homepage and download page serve alpha.7.7 directly before JavaScript.
The download API returns matching Windows, macOS, and Linux assets and sizes.
Website PR #11 updates the fallback release and release-scoped browser cache.
Production HTTPS headers, desktop sign-in route, and www canonical redirect
were checked after Cloudflare deployment.

## Verification and limits

Tagged CI passed core/UI qualification, the complete Windows Python 3.12
suite, three-platform packaging, and packaged-core smoke checks. Before
tagging, 606 desktop tests passed with one skipped, including 24 account
callback tests. The website passed 32 rendered HTML and Worker tests and lint
with no errors.

Fresh standalone website magic-link signup was verified against production.
The official Windows NSIS installer completed successfully, and installed
files report alpha.7.7 with the expected tagged-source provenance. In a fresh
isolated profile, that website-created account signed into the installed app,
DeepSeek onboarding completed, and the model returned a useful meeting plan.
After restarting the installed app, account identity, provider connection,
and complete history persisted; a follow-up reply correctly used the saved
conversation. Account fields remained OS-encrypted on disk.

The website was deployed through the authenticated maintainer CLI. Its GitHub
Actions deployment job still requires the repository's Cloudflare API-token
secret; that automation failure did not prevent the verified live deployment.

Accounts do not automatically sync chats or provider settings. Memory backup
is optional and off by default; this acceptance pass does not qualify its
cross-device backup/restore flow.

macOS remains Apple Silicon only, ad-hoc signed and not notarized. Live macOS
and Linux email sign-in were not tested. Windows signing continues to depend
on the configured release certificate.
