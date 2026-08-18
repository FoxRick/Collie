<p align="center">
  <img src="collie-ui/src/renderer/src/assets/portrait/happy.webp" alt="Collie, the Border Collie companion" width="190">
</p>

# Collie — your personal AI. With a dog. 🐾

**Collie is the first AI harness for non-coders**: a friendly, local-first
personal AI for Windows that turns plain-English requests into real,
reviewable work — and keeps you in control the whole way. No terminal.
No prompt engineering. No forced subscription.

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
- **It uses *your* AI — not a subscription.** Sign in with your existing
  ChatGPT or Claude account, paste an API key (DeepSeek, OpenRouter, Groq,
  and any OpenAI-compatible provider), or use a local model. No mandatory
  Collie account, no forced monthly fee.
- **It remembers — on your machine.** Collie keeps its notes, settings, and
  history locally. Nothing is silently shipped off your device.
- **It has a dog.** A little Border Collie lives in your corner of the
  screen, with moods and reactions of its own. (Friendly, but never in
  charge — the permissions always are.)

Collie is built for people who want the leverage of agentic AI *without* the
terminals, API keys, MCP servers, or prompt-engineering vocabulary. Experts
are welcome too — but the normal path never requires them.

## Complete feature list

**💬 Chat & conversation**

- Chat-first by design — streamed conversations with history and
  attachments; no prompt syntax, no templates to learn
- **Skeleton streaming** — you see Collie thinking as it writes, instead of a
  frozen spinner
- **Quick-recap cards** — long answers end with a short summary card of what
  just happened, so you always know where you are
- **"Remember" pill** — Collie visibly shows you when it stores something new
  about you
- **Starter conversation** — on first run Collie greets you, learns your
  name, and you're talking; `/get-started` if you want a tour

**🧠 Real work, reviewed**

- Plain-English requests become **multi-step plans** you read and approve
  before anything runs
- Central **approval gate** for every consequential action — destructive,
  financial, external writes, sends, and publishing are never silent
- **Per-folder file consent** — grant Collie access to exactly the folders it
  needs; in-scope work runs smoothly, everything else asks first
- **One-tap undo** for every local file change — writes are journaled and
  reversible
- **"Your things" panel** — every deliverable (documents, spreadsheets,
  files, summaries) lands in one reviewable place, named in plain language
- **Subagent observability** — watch live agents working, then get a friendly
  pet popup when they settle
- **Gardener mode** — Collie proposes improvements to its own instructions
  and memory, always as a reviewable, reversible change

**🔌 Bring your own AI**

- Sign in with your existing **ChatGPT or Claude** account
- Paste any **API key** — DeepSeek, OpenRouter, Groq, and any
  OpenAI-compatible provider
- Use a **local model** via Ollama
- **Optional Collie account** — sign in from Settings in your system browser
  (password or magic link); it's identity only, and your chats and files stay
  on your machine
- Bundled **models.dev catalogue** — every provider and model at your
  fingertips, auto-refreshed weekly
- Switch models anytime, right from the chat; connections are validated
  before you start

**🧩 Connectors & services**

- **Five official connectors live in alpha**: Notion, Linear, Todoist,
  Atlassian, and Airtable
- A curated connector catalogue — every other entry honestly labeled
  *Coming soon* until it passes verification
- Google and Microsoft service bundles in progress

**📱 Collie wherever you are**

- **Telegram messenger** with sender pairing — talk to Collie from your phone
- WhatsApp, Slack, and Discord companions designed and on the way

**⚡ Everyday tools, built in**

- Files, weather, reminders, and memory — local capabilities that need no
  extra accounts
- **Routines & automations** — scheduled tasks that always ask before acting
- **Skills & specialist agents as plain-text files** — read, edit, and share
  them; no programming required

**🎨 Made for humans**

- A **Border Collie companion** in the corner of your screen, with moods and
  reactions (friendly, never in charge)
- **Collapsible sidebar** — an icon rail that gives chat more room
- Fast, calm desktop app — Electron + React
- **Voice input/output** on the way

**🛡️ Safety & reliability**

- **Local-first everything** — memory, settings, and history in local SQLite;
  no mandatory account, no silent uploads. The optional Collie account is
  identity only — nothing leaves your machine.
- Permissions engine with a broker → classifier → evaluator → store pipeline
- **Rollback-safe updates** — a new version must boot healthy, or Collie
  rolls back to the last good one
- Automatic **recovery from out-of-memory and renderer crashes**
- **CI-qualified releases** — tagged releases pass documented clean-machine
  and immutable-artifact checks

## What you can do with it

| | |
|---|---|
| **💬 Chat about anything** | Streamed conversations with history and attachments. Ask for help, delegate a task, or just talk. |
| **🧠 Real work, reviewed** | Collie turns plain-English requests into multi-step work with visible progress and reviewable plans. |
| **🛡️ Approval where it matters** | Consequential actions — destructive, financial, external writes, sends, publishing — stay centrally gated and approved by you. |
| **↩️ Undo anything** | Local file changes are journaled — one tap reverts them. |
| **📦 Your things, in one place** | Every deliverable lands in a reviewable "Your things" panel, named in plain language. |
| **✨ Sees you thinking** | Skeleton streaming, quick-recap cards, and the "remember" pill keep you oriented. |
| **📝 Agents, skills & routines as files** | Specialist agents, skills, and routines are plain-text files you can read, edit, and share — no programming required. |
| **🧩 Connect the services you use** | Five official connectors are live in alpha (Notion, Linear, Todoist, Atlassian, Airtable); every other catalogue entry stays labeled **Coming soon** until it passes verification. |
| **📱 Collie on your phone** | Talk to Collie from Telegram, wherever you are. WhatsApp, Slack, and Discord companions are designed and on the way. |
| **📁 Everyday tools built in** | Files, weather, reminders, memory, and more — local capabilities that work without extra accounts. |
| **🔁 Stays current** | Rollback-safe built-in updates mean future releases can arrive in the app, no reinstalls. |

## Built on nanobot 🧬

Collie's Python engine is an **adapted fork of
[nanobot](https://github.com/HKUDS/nanobot)** (MIT) — the ultra-lightweight,
open-source, self-hosted personal AI agent framework by HKUDS. We inherited
its agent loop, providers, tools, MCP client, and WebSocket transport, keep
the vendored engine surgical, and preserve the upstream namespace so
improvements can flow both ways. Attribution and third-party notices live in
[collie-core/THIRD_PARTY_NOTICES.md](collie-core/THIRD_PARTY_NOTICES.md).

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
There's also an optional free Collie account (Settings → **Account**) for
identity — it's never required, and your chats and files stay on your
machine.

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

## Architecture

```text
collie-core/   Python 3.11+ runtime (adapted from nanobot)
  ├─ agent loop, providers, tools, MCP client, WebSocket transport  (vendored nanobot)
  ├─ permissions engine — broker → classifier → evaluator → store
  ├─ SQLite settings & memory — local-first, no account
  ├─ services + OAuth, connectors catalogue, routines & automations
  ├─ subagents, plans, Gardener, voice, desktop Border Collie pet
  └─ IPC server (localhost WebSocket) + Telegram messenger

collie-ui/     Electron 43 + React 19 + Tailwind 4 (electron-vite)
  ├─ electron-builder + electron-updater (rollback-safe)
  └─ scripts/stage-core.cjs bundles the Python runtime into the app
```

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

- **Shipped (alpha):** Gardener self-improvement mode; "Your things" panel;
  one-tap undo for file changes; per-folder file consent; collapsible
  sidebar; subagent observability; skeleton streaming, quick-recap cards, and
  the remember pill; rollback-safe updates with crash recovery; onboarding
  (paste-key connect + models.dev catalogue + starter conversation); five
  connectors (Notion, Linear, Todoist, Atlassian, Airtable); Telegram
  messenger; optional Collie account sign-in (identity only, browser
  magic-link).
- **In progress:** Google and Microsoft service bundles (awaiting Collie-owned
  OAuth app registrations), the installer pipeline, the Windows release
  itself, and voice.

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
