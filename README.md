<p align="center">
  <img src="collie-ui/src/renderer/src/assets/portrait/happy.webp" alt="Collie, the Border Collie companion" width="190">
</p>

# Collie — your personal AI. With a dog. 🐾

**Collie is the first AI harness for non-coders**: a friendly, local-first
personal AI for Windows that turns plain-English requests into real,
reviewable work — and keeps you in control the whole way.

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="License: MIT"></a>
  <a href="https://github.com/FoxRick/Collie/releases"><img src="https://img.shields.io/badge/Platform-Windows%2011%20x64-0078d6" alt="Platform: Windows 11 x64"></a>
  <a href="https://github.com/FoxRick/Collie/tree/v0.1.0-alpha.4"><img src="https://img.shields.io/badge/Alpha-v0.1.0--alpha.4-purple" alt="Alpha: v0.1.0-alpha.4"></a>
  <a href="https://github.com/FoxRick/Collie/actions/workflows/ci.yml"><img src="https://github.com/FoxRick/Collie/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://heycollie.com"><img src="https://img.shields.io/badge/Website-heycollie.com-2ea44f" alt="heycollie.com"></a>
  <a href="https://github.com/FoxRick/Collie"><img src="https://img.shields.io/github/stars/FoxRick/Collie" alt="GitHub stars"></a>
</p>

> **Early access** — Collie is an invited **Windows 11 x64 alpha**. This
> repository currently publishes **source code only**; there is **no public
> installer or GitHub Release** yet. Join the waitlist at
> [heycollie.com](https://heycollie.com) and you'll be first to know when the
> installer ships.

## What is Collie?

Collie is a chat-first personal AI that lives on your PC. You talk to it the
way you'd talk to a smart friend, and it quietly handles the technical parts
underneath:

- **It plans before it acts.** Big or broad requests become short, reviewable
  plans you can read and approve before anything happens.
- **It asks before risky things.** Deleting, sending, paying, publishing, or
  touching your data always goes through a central approval gate — no matter
  how friendly the chat gets.
- **It remembers — on your machine.** Collie keeps its notes, settings, and
  history locally. There is no mandatory Collie account, and nothing is
  silently shipped off your device.
- **It has a dog.** A little Border Collie lives in your corner of the screen,
  with moods and reactions of its own. (Friendly, but never in charge — the
  permissions always are.)

Collie is built for people who want the leverage of agentic AI *without* the
terminals, API keys, MCP servers, or prompt-engineering vocabulary. Experts
are welcome too — but the normal path never requires them.

## What you can do with it

| | |
|---|---|
| **💬 Chat about anything** | Streamed conversations with history and attachments. Ask for help, delegate a task, or just talk. |
| **🧠 Real work, reviewed** | Collie turns plain-English requests into multi-step work with visible progress and reviewable plans. |
| **🛡️ Approval where it matters** | Consequential actions — destructive, financial, external writes, sends, publishing — stay centrally gated and approved by you. |
| **📝 Agents, skills & routines as files** | Specialist agents, skills, and routines are plain-text files you can read, edit, and share — no programming required. |
| **🧩 Connect the services you use** | Five official connectors are live in alpha (Notion, Linear, Todoist, Atlassian, Airtable); every other catalogue entry stays labeled **Coming soon** until it passes verification. |
| **📱 Collie on your phone** | Talk to Collie from Telegram, wherever you are. WhatsApp, Slack, and Discord companions are designed and on the way. |
| **📁 Everyday tools built in** | Files, weather, reminders, memory, and more — local capabilities that work without extra accounts. |
| **🔁 Stays current** | A built-in updater means future releases can arrive in the app, no reinstalls. |

## Getting started

### 1. Get Collie

Collie is in invited alpha on **Windows 11 x64**. Join the waitlist at
[heycollie.com](https://heycollie.com) — invitations go out in small groups,
and a published installer will appear on
[GitHub Releases](https://github.com/FoxRick/Collie/releases) with release
notes and a checksum. Until then, the source is here to explore and build
(see [From source](#from-source-for-developers)).

### 2. Connect your AI

Collie needs a model provider to think with — bring your own, no Collie
account required:

- **Sign in** with your existing ChatGPT or Claude account, or
- **Paste an API key** — DeepSeek, OpenRouter, Groq, and other
  OpenAI-compatible providers, or
- **Use a local model** such as Ollama.

Collie validates the connection and tells you what it found — *"DeepSeek
connected and selected."* — and you can change models anytime from the chat.

### 3. Just talk

That's it. Collie greets you, asks your name, and remembers it. You don't
write prompts, learn syntax, or configure anything technical first — you just
start talking, and Collie figures out the rest.

## From source (for developers)

The supported alpha platform is **Windows 11 x64** (the app also builds and
runs from source on Linux for development; macOS is not supported). You need
Python 3.12, Node.js, and npm:

```powershell
cd collie-core
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check nanobot collie_core tests

cd ..\collie-ui
npm ci
npm test
npm run typecheck
npm run build
npm run dev
```

Do not publish an installer from a local `dist` directory: release candidates
must pass the documented clean-machine and immutable-artifact checks in
[docs/operations/release/](docs/operations/release/).

## Documentation

The public docs live in this repository — start at
[docs/README.md](docs/README.md):

- [Vision](docs/VISION.md) — what Collie is for, and the principles behind it
- [Project map](docs/PROJECT_MAP.md) — repository layout and data flow
- [Security & approval matrix](docs/engineering/security/approval-matrix.md)
  — what needs approval, and why
- [Security policy](SECURITY.md) — how to report a vulnerability
- [Release information](docs/operations/release/README.md) — release status
  and artifact validation
- [Contribution guide](CONTRIBUTING.md)

## Roadmap

Public releases and announcements go through [heycollie.com](https://heycollie.com)
first; the active product decisions live in [docs/product/](docs/product/).
Honest status of what's next:

- **Shipped (alpha):** *Gardener* — a self-improvement mode that proposes
  better instructions and cleaner memory, always as a reviewable, reversible
  change ([foundations](docs/engineering/architecture/gardener-foundations.md),
  [spec](docs/product/features/agent-system.md)); no sandbox replay yet.
- **In progress:** Google and Microsoft service bundles (awaiting Collie-owned
  OAuth app registrations), the installer pipeline, and the Windows release
  itself.

## Contributing

Thanks for helping build Collie! For bugs and feature ideas, open an
[issue](https://github.com/FoxRick/Collie/issues) — search first, and never
report vulnerabilities in public issues (use [SECURITY.md](SECURITY.md)).
For changes, read [CONTRIBUTING.md](CONTRIBUTING.md) and the workspace
instructions in [AGENTS.md](AGENTS.md) before you start. Keep claims honest:
a provider, connector, installer, or release is only "available" after its
verified acceptance checks pass.

## Repository layout

```text
collie-core/           Python runtime: agent loop, tools, approvals, memory, tests
collie-ui/             Electron/React desktop app and packaging
docs/                  Product, engineering, and release documentation
```

The public website is maintained separately (its source is intentionally not
part of this repository).

## License

MIT — see [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md) for the full terms and
the separate treatment of Collie branding, artwork, and third-party marks.
The Python engine includes adapted code from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot) (MIT); attribution and
third-party notices are in
[collie-core/THIRD_PARTY_NOTICES.md](collie-core/THIRD_PARTY_NOTICES.md).
