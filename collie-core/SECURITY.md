# Collie Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities in public issues, discussions, or
social-media posts. Send a concise report to
[security@heycollie.com](mailto:security@heycollie.com) instead.

Include the affected Collie version, a reproducible description, impact, and
any proof of concept or suggested mitigation. Please avoid accessing other
people's data or disrupting a service while investigating.

We will acknowledge reports within five business days and will coordinate a
fix and disclosure timeline with you. If email is unavailable, open a GitHub
private security advisory for the Collie repository.

## Supported versions

Security fixes are assessed for the current Collie alpha release. Alpha builds
are unsigned while the release program is being established; download them only
from the Collie GitHub Releases page and verify the published checksum.

## Keeping Collie data safe

- Collie stores its local database under the user's Collie data directory.
- The Electron shell keeps provider and messenger secrets in OS-protected
  storage and injects them into the managed core process when needed. Do not
  put tokens in source code, screenshots, issue reports, or logs.
- Treat prompts, attachments, and local files as sensitive. Connected model
  providers receive the content required for a request; review their privacy
  policies before use.
- Telegram access uses sender pairing. Keep the bot token private, approve only
  expected senders, and revoke a sender or rotate the token if it is exposed.

## Security development expectations

Contributors should use narrowly scoped changes, keep dependencies current,
avoid logging secrets, and test authorization and approval paths. Report a
suspected secret committed to the repository through the private route above;
do not reproduce it in a public issue.

## Attribution and notices

Collie includes adapted code from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot), licensed under MIT. The
vendored `nanobot` Python namespace remains for compatibility. See `LICENSE`
and `THIRD_PARTY_NOTICES.md` for the applicable notices.

**Last updated:** 2026-07-29
