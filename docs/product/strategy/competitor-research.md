# Competitor research

**Status:** living document — updated as new harnesses/agents are found
**Last updated:** 2026-08-02
**Scope:** AI agent harnesses, coding agents, and consumer AI shells relevant to Collie (AI Harness for Non-Coders).

## How to add a competitor

1. Fetch repo meta via GitHub API: description, stars, forks, language, license, topics, homepage.
2. Read the README (raw.githubusercontent.com) — capture positioning, interface, providers, memory, skills, agents, permissions, sandboxing, channels, automations, MCP, sessions, versioning, self-improvement, extensibility, non-coder friendliness, differentiators.
3. Add one row to the traction table + one column/entry to the feature matrix, and a short entry in the notes section.
4. Pull request, same as any other doc change.

## Traction snapshot (live, 2026-08-02)

| Repo | ⭐ | Language | License | Positioning |
|---|---|---|---|---|
| OpenClaw | 385k | TS | — | Consumer/self-hosted agent, 25+ channels |
| Hermes (Nous) | 224k | Python | MIT | Self-improving personal AI agent |
| OpenCode | 192k | TS | — | Dev coding agent (Anomaly) |
| Gemini CLI | 106k | TS | Apache-2.0 | Dev coding agent (Google) |
| Codex CLI | 103k | Rust | Apache-2.0 | Dev coding agent (OpenAI) |
| OpenHands | 83k | TS | MIT | Dev coding agent |
| Goose | 52k | Rust | Apache-2.0 | Dev coding agent (Block) |
| nanobot (our upstream) | 46k | Python | MIT | Personal agent substrate |
| **little-coder** | **2.2k** | TS | Apache-2.0 | Coding agent tuned for small local LLMs |
| **Collie** | 0 | Python | MIT | AI harness for non-coders |

## Feature matrix — Collie vs the field

### A. Core harness

| | **Collie** (nanobot fork) | **Codex** | **OpenCode** | **Hermes** | **OpenClaw** | **little-coder** |
|---|---|---|---|---|---|---|
| Audience | Non-coders, desktop | Developers | Developers | Power users | Consumers, self-hosters | Developers (local-model enthusiasts) |
| Interface | Desktop GUI + chat | TUI/IDE/desktop/web | TUI + desktop (beta) | TUI + desktop + gateway | Desktop/phone apps + web dashboard | Terminal TUI only |
| Providers | Multi (OpenAI/Claude/API key) | OpenAI only | 75+ | Any (Portal/OpenRouter) | Any + OAuth subscriptions | Multi: local (llama.cpp/Ollama/LM Studio) + cloud (Anthropic/OpenAI) |
| Permissions | **Deterministic engine + plan/approve** | OS sandbox + modes + auto-review | Per-tool allow/ask/deny | Command allowlist + pairing | Pairing codes + allowlists | None documented (edits directly) |
| Sandboxing | ❌ none | ✅ Seatbelt/bwrap/Win | ❌ rules-only | ✅ Docker/SSH/serverless | ✅ Docker/SSH | ❌ none |
| Channels | 4 messengers | Slack (cloud) | — | 6-7 | 25+ | — (terminal only) |
| Automations | ✅ cron + NL parsing | ✅ scheduled tasks | ❌ | ✅ cron + NL | ✅ cron + webhooks | ❌ |
| MCP | ✅ client | ✅ client + servers | ✅ client + servers | ✅ | ✅ | Not documented (pi substrate may add) |
| API/SDK | ❌ WS IPC only | ✅ SDK + MCP server | ✅ OpenAPI + TS SDK | ✅ RPC | ✅ RPC | ❌ |
| Sessions/search | ✅ search_messages | ✅ resume, no search | ✅ resume/compact/share | ✅ FTS5 cross-session | ✅ sessions tools | ✅ sessions + /resume + prompt history |

### B. Memory & learning

| | **Collie** | **Codex** | **OpenCode** | **Hermes** | **OpenClaw** | **little-coder** |
|---|---|---|---|---|---|---|
| Memory | Profile facts only | ✅ auto memories + consolidation | ❌ | ✅ persistent + profile + Honcho | SOUL.md + memories | Session/prompt history only |
| Skills | ✅ SKILL.md on-demand | ✅ open agentskills + Record&Replay | ✅ SKILL.md multi-format | ✅ auto-creates + self-improves | ✅ SKILL.md + ClawHub | ✅ 30 bundled SKILL.md files |
| Custom agents | ✅ .md files, posture-enforced | ✅ TOML agents | ✅ .md agents | ✅ subagents | ✅ multi-agent routing | ✅ sub-coders (read-only research dispatch) |
| Self-improvement | 🔜 Gardener (spec'd only) | ✅ memory flywheel | ❌ | ✅ built-in learning loop | tagline only | ❌ |
| Versioning/rollback | ❌ | ✅ enterprise audit API | ✅ git-snapshot undo | ❌ | ❌ | ❌ |
| Run records | ❌ usage aggregates | ✅ JSONL events | ❌ | ✅ trajectory export | ✅ /usage | ✅ token/cache stats in status line |

## Notes

### little-coder (itayinbarr/little-coder) — NEW 2026-08-02

- **What it is:** a coding agent "tuned for small local models", built on the [pi](https://pi.dev) substrate (agent loop, multi-provider API, TUI, session tree, compaction, extension model). pi is a plain dependency; everything custom lives in `.pi/extensions/` (~30 extensions), `skills/` (30 SKILL.md files), and `benchmarks/`.
- **Model thesis:** default model is `qwen3.6-35b-a3b` served via llama.cpp. The research writeup (*Honey, I Shrunk the Coding Agent*, Substack) claims a 9.7B Qwen beat frontier entries on Aider Polyglot — the "scaffold–model fit" argument: a harness optimized for small models outperforms a generic harness on a big model.
- **Cold-start discipline:** launcher runs pi with `--no-extensions` and wires in exactly the bundled set — cold-start context ~7k tokens, predictable behavior, no workspace drift mid-task.
- **Notable features:** Plan Mode (ctrl+q — sub-coders research, 1–3 clarifying questions with suggested answers, writes a plan, edits nothing), Deep Research (f2 — read-only sub-coders → cited markdown report), sub-coder dispatch, read-before-edit, cache-aware status line (cache-hit rate, token billing), sessions auto-named + `/resume`.
- **Install:** `curl | bash` one-liner, `npm i -g`, or bun. Node 22.19+ required.
- **Traction:** 2.2k ⭐ / 149 forks, created 2026-04-11, Apache-2.0, TS. Actively pushed (last push 2026-07-31).
- **Relevance to Collie:**
  - It's a harness, like us — evidence the harness layer is the competitive surface, not just the model.
  - The scaffold–model-fit research matters for our cost story: non-coders can run Collie on cheap/small models if the harness is disciplined (deterministic permission engine already keeps us lean on tool calls).
  - Not a direct audience overlap: terminal-first, developer-oriented, no permission layer, no messengers, no GUI. It validates "harness quality > model size" but doesn't threaten the non-coder desktop position.
  - Their ~7k-token cold-start + cache discipline is a concrete target for our own context-budget work.

### Earlier session findings (2026-08-02)

- 4 of 8 major coding agents (Codex, OpenCode, Gemini CLI, OpenHands) now ship desktop apps — the "consumer shell" space Collie claims is getting crowded.
- **We already win (verified — no competitor documents this):** deterministic permission engine + plan/approve UX, editable .md agents, connector OAuth + health-check catalogue, channel pairing. The trust layer is the moat for non-coders.
- **Biggest gaps, in build order:** 1) run records, 2) versioning + rollback, 3) episodic memory (nanobot "Dream" port), 4) Gardener (self-improvement with gated diff→approve→rollback — the more trustworthy version of Hermes' loop), 5) sandboxing (post-alpha), 6) OpenAI-compatible API + SDK (when a community exists).
- **Watching:** ACP (Goose/OpenHands/OpenCode) as the emerging cross-agent standard; our MCP-first stance is fine for now.

### Positioning reality check

They're at 2.2k–385k ⭐ with huge velocity — don't out-feature them. The defensible line: *the only harness where the AI improves itself only with your explicit approval*, for non-coders, on the desktop.
