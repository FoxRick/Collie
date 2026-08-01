# Collie

Collie is a local-first, chat-first Windows AI assistant for nontechnical
users. It helps people turn plain-English requests into understandable,
reviewable work while keeping meaningful actions under their control.

> **Early access status:** Collie is an invited Windows 11 x64 alpha. This
> repository currently publishes source code only; there is **no public
> installer or GitHub Release** yet.

## What the alpha includes

- Streamed chat with conversation history and attachments.
- ChatGPT/OpenAI sign-in, Claude sign-in, and compatible API connections.
- Editable specialist Agents, progressively loaded Skills, Routines, and
  reviewable plans.
- Built-in local capabilities including files, weather, memory, and reminders.
- Guided Telegram setup with sender pairing and revocation.
- A desktop Border Collie companion with responsive activity states.

External OAuth connectors are not enabled in the current alpha source state.
Every connector catalogue entry remains **Coming soon** until its exact
packaged application passes authentication, tool discovery, live health, and
release verification checks. Collie does not claim that every provider or
integration works out of the box.

## Safety and data handling

Collie's friendly language never changes its permissions. External writes,
sensitive operations, destructive actions, financial actions, sends, and
publishing remain subject to the central approval system.

Collie keeps its database, workspace memory, settings, and runtime state on
the user's device. Provider requests are sent to the model provider a user
connects, so users should review that provider's privacy terms before sharing
sensitive information. Telegram exchanges data only when that feature is
configured and used. Alpha software should not be used for irreplaceable or
high-risk work.

## Getting early access

Join the waitlist at [heycollie.com](https://heycollie.com). Invitations are
sent in small groups. A published installer will be available only through a
versioned GitHub Release with its release notes and checksum.

## Development

The supported alpha platform is Windows 11 x64. Development also requires a
full Python 3.12 installation, Node.js, and npm.

```powershell
cd collie-core
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check nanobot collie_core tests

cd ..\collie-ui
npm install
npm test
npm run typecheck
npm run build
npm run dev
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.

## Repository layout

```text
collie-core/           Python runtime, agent loop, tools, approvals, and tests
collie-ui/             Electron/React desktop application and packaging
```

The Python engine includes adapted code from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot), under the MIT License. The
vendored `nanobot` namespace is retained for compatibility. See
[LICENSE](LICENSE) and
[collie-core/THIRD_PARTY_NOTICES.md](collie-core/THIRD_PARTY_NOTICES.md) for
the applicable license and notices. See [NOTICE.md](NOTICE.md) for the scope
of the MIT license and the separate treatment of Collie branding, artwork, and
third-party marks.

## More information

- [Contribution guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [License and brand notice](NOTICE.md)

## License

The MIT License applies to the repository's code and documentation as
described in [NOTICE.md](NOTICE.md). Adapted upstream code retains its required
attribution and third-party notices.
