# Gardener Foundations — run records, versioned artifacts, Dream memory, and the Gardener MVP

> **Status (2026-08-05):** fully landed. PR 1 (run records) merged on
> `main`; PRs 2–4 (version store, Dream wiring, Gardener MVP) ship together
> on `feat/gardener` as one branch. This document records the decisions and
> the *shipped* shape — where it differs from the original plan, the
> difference is deliberate and noted below.

## Goal

Give Collie a safe, evidence-driven self-improvement story:

1. **Run records** — per-turn and per-tool telemetry (already merged, PR 1).
2. **Version store** — every user-visible artifact edit is snapshotted with
   a before/after text pair and a unified diff, and can be rolled back with
   one action **without ever clobbering a newer owner edit**.
3. **Episodic memory (Dream)** — wire nanobot's already-vendored Dream
   machinery into Collie's automation scheduler with a review + undo surface.
4. **Gardener MVP** — evidence queries → bounded proposal turn → review
   cards with Approve/Dismiss → versioned, undoable applies.

The four pieces are deliberately additive: three narrow packages in
`collie_core/` (plus the telemetry package already on `main`), additive IPC
commands, and no edits to the nanobot orchestration core.

## Architecture

### 1. Run records (`collie_core/telemetry/`, merged on main)

- Schema V11: `turn_events` + `tool_events`; V12 adds prompt-hash columns
  (`prompt_hash`, `tool_schema_hash`, `config_hash`) for the evaluation lab.
- `RunRecorder` writes through a fire-and-forget writer thread (bounded
  queue, drop counter, event-time timestamps) — telemetry never delays or
  breaks a turn.
- `TelemetryHook` observes every turn kind (chat, plan, routine, cron,
  subagent, automation) through the existing `AgentHook` chain. Zero loop
  edits.
- All stored summaries pass through `redact_parameters` + truncation;
  secrets never enter telemetry.

The Gardener's evidence layer is the first real consumer of these tables.

### 2. Version store (`collie_core/versions.py`, this branch)

Schema V14 adds `artifact_versions`:

| column | meaning |
| --- | --- |
| `artifact_type` | `subagent` \| `vision` \| `agents` \| `memory_profile` \| `memory_dream` \| `skill` |
| `artifact_key` | plain filename (subagent file name, `VISION.md`, `AGENTS.md`, `MEMORY.md`, …) |
| `version` | per-(type, key) monotonic counter |
| `before_text` / `after_text` | full text pair |
| `diff_text` | `difflib.unified_diff` |
| `evidence_json` | Gardener trigger evidence (run ids, tool stats) |
| `source` | `user` \| `collie` \| `gardener` |
| `status` | `applied` \| `rolled_back` |

`VersionStore.snapshot()` returns `None` when the texts are identical
(nothing changed → no version row). `VersionStore.rollback()` restores the
`before_text` of the target version **only when the artifact's current text
still equals that version's `after_text`** — otherwise it raises
`VersionConflictError` ("Undo the most recent change first"). The caller
writes the restored text; each artifact type knows its own re-sync rules.

**Wired write paths** (every one snapshots *before* writing):

- `SubagentLoader.create/update/delete` → `subagent` versions; rollback
  re-runs `loader.sync()` so the disk→DB mirror stays consistent.
- `ProfileStore` mutation methods (`set`, `delete`, person/date CRUD) →
  `memory_profile` versions of workspace `MEMORY.md`.
- IPC `_cmd_write_file` classifies workspace files
  (`VISION.md`, `AGENTS.md`, `MEMORY.md`, `memory/MEMORY.md`,
  `subagents/*.md`) and snapshots automatically, returning
  `version_id` + `diff_text` so the caller can offer Undo. This is the
  suggest-card apply path: `SuggestionCard` reads, merges, writes, and
  renders the returned diff with an **Undo** button.
- Dream consolidations → `memory_dream` versions (`source="collie"`).
- Gardener applies → `source="gardener"` with the evidence + rationale in
  `evidence_json`.

**UI (minimal but real):** `DiffView` (collapsible, color-coded unified
diff), Undo on `SuggestionCard`, a History expansion in
`AgentsScreen` and Settings → Memory (per-version diff + Undo), and a
rollback rail in Settings → Memory → History.

> **Schema note:** the original plan numbered the version store V12. By the
> time it landed, V12 (prompt hashes) and V13 (`memory_journal`) were
> already shipped on `main`, so `artifact_versions` is **V14**. Shipped
> migrations are never edited.

### 3. Dream memory (`collie_core/memory/dream.py`, this branch)

Nanobot's Dream machinery (cursor, prompt builder, session keys, pruning)
was vendored but dead; Collie had zero references. `run_dream()` wires it:

1. `MemoryStore.build_dream_prompt()` — nothing new to review → early
   return, cursor untouched.
2. One **bounded, read-only** subagent turn under a `dream:<ts>` session
   key: `max_iterations=1`, an **empty tool registry** (no tools, so
   read-only by construction), `execution_posture="read_only"`. Collie has
   no file-editing tools, so the output contract asks the model to return
   the full proposed `memory/MEMORY.md` content (fence-tolerant parser).
3. If the proposed content differs, store it as a **pending proposal**
   (`memory/.dream-proposal.json`) — `MEMORY.md` is **not** written.
   The user reviews the diff in Settings → Memory and explicitly applies
   or dismisses it. On apply, the proposal is re-validated against the
   current file, written atomically, and snapshotted via `VersionStore`
   (`memory_dream` / `MEMORY.md`, `source="collie"`), so the change stays
   undoable. On dismiss, the proposal is discarded.
4. Advance the dream cursor **only on success** (a completed turn — even a
   no-change one — so history is never re-processed); failed/incomplete
   turns leave the cursor put. The cursor advances when the proposal is
   created; applying or dismissing never re-runs the model.
5. `prune_dream_sessions(keep=10)` after each run.

Dream never touches `ProfileStore` — it consolidates nanobot's long-term
`memory/MEMORY.md` only.

**Trigger:** two built-in automations seeded once under their own flag
(`seed_gardener_automations`, never resurrects deletions), both disabled by
default so nothing runs without the user enabling it:

- **Memory maintenance** (`memory_maintenance` action type, `Sun 09:00`)
- **Improvement suggestions** (`gardener` action type, `Sun 10:00`)

The runtime's `_run_automation` dispatches these action types to their own
bounded pipelines instead of a free-form prompt turn. Results land in the
🔔 conversation and are broadcast like any automation.

**Manual triggers:** IPC `run_dream` (Settings → Memory → "Review memory
now") and `run_gardener` ("Suggest improvements") — both available from the
Settings → Memory → "Collie's self-review" section.

### 4. Gardener MVP (`collie_core/gardener/`, this branch)

The loop: **evidence → propose → review → apply (versioned)**.

**Evidence (`evidence.py`)** — read-only queries over the telemetry tables
plus workspace files:

- `recent_failures` — per-tool `error`/`denied`/`timeout` counts with
  sample messages (default window: 14 days, min 2 failures).
- `repeated_workflows` — per-turn ordered tool sequences appearing ≥ 3×.
- `user_stops` — turns with status `stopped`/`cancelled`.
- `memory_bloat` — size + duplicate-heading signals for `memory/MEMORY.md`
  and `MEMORY.md`.

**Propose (`propose.py`)** — one bounded subagent turn (same machinery as
Dream: no tools, `read_only`, `max_iterations=1`) with a fixed prompt: the
evidence summary (≤ ~2k tokens) + current agent/memory texts. Output is a
JSON array of suggestions. **Nothing the model says is trusted** — every
suggestion passes `validate_suggestion()`, a deterministic gate:

- **Allowlist:** only `subagent`, `agents`, `vision`, `memory_dream`
  artifact types may be proposed.
- **Key safety:** plain filenames only (no path separators / traversal);
  `agents` → `AGENTS.md`, `vision` → `VISION.md`,
  `memory_dream` → `MEMORY.md`, `subagent` → `*.md`.
- **Size budgets:** proposed text ≤ 4 000 chars, rationale ≤ 500 chars,
  ≤ 3 suggestions per run.
- **Keyword gate:** proposed text/rationale containing permissions,
  approvals, settings, secrets, credentials, connectors, providers,
  billing, or token-like language is rejected outright.

Rejected suggestions are reported (reason + target) but never applied.

**Runner (`runner.py`)** — `run_gardener()` collects evidence, proposes,
and returns `{suggestions, rejected, evidence, message}`. `apply_suggestion()`
**re-validates at apply time** (a forged or edited card cannot bypass the
scope guard), snapshots through the version store (`source="gardener"`),
writes the file, and re-syncs the subagent loader when needed.

**Review surface:** suggestions render as `gardener_suggestion` chat cards
(`GardenerCard`) in the 🔔 conversation with rationale, evidence labels,
proposed text, **Approve / Dismiss**, and — after approval — the diff plus
**Undo** (rollback through `apply_gardener_suggestion` /
`rollback_artifact` IPC). Dismiss is a pure UI decision: no core write, no
version row.

**Scope guard (enforced in code, per the plan):** the Gardener only
proposes agent instruction and memory consolidation changes. It never
touches permissions, settings, secrets, or connectors — enforced by the
allowlist + keyword gate, not by prompt wording.

## Deferred (explicit, not forgotten)

- **Sandbox replay.** The plan's ideal loop would run a proposed change in
  a sandbox before applying it. The MVP deliberately ships without it: it
  needs run-record history volume the product doesn't have yet, and the
  deterministic scope guard + human approval stand in for it. Revisit when
  telemetry has accumulated.
- **Dream token-budget cap on injected memory** (`ContextBuilder` memory
  section) — the vendored Dream machinery and the 3 000-token recent-history
  cap bound the surface for now; a hard truncation of injected `MEMORY.md`
  can follow with the memory hygiene work.
- **Gardener "learn from approval" feedback loop** — the apply path records
  evidence + rationale in `evidence_json` (so a later pass can learn which
  suggestion classes get approved), but no learning model consumes it yet.

## Files

| path | role |
| --- | --- |
| `collie_core/telemetry/` | run records (PR 1, on `main`) |
| `collie_core/versions.py` | `VersionStore` + `make_diff` |
| `collie_core/memory/dream.py` | `run_dream()` consolidation runner |
| `collie_core/gardener/evidence.py` | read-only evidence queries |
| `collie_core/gardener/propose.py` | bounded proposal turn + strict validation |
| `collie_core/gardener/runner.py` | `run_gardener` / `apply_suggestion` |
| `collie_core/db.py` | schema V14 + `artifact_versions` methods |
| `collie_core/ipc/server.py` | `list_versions`, `rollback_artifact`, `run_dream`, `get_dream_history`, `run_gardener`, `apply_gardener_suggestion` |
| `collie_core/automations/scheduler.py` | `seed_gardener_automations` |
| `collie_core/runtime.py` | version store wiring, Dream/Gardener pipelines, manual triggers |
| `collie-ui/src/renderer/src/components/cards/DiffView.tsx` | unified-diff renderer |
| `collie-ui/src/renderer/src/components/cards/GardenerCard.tsx` | review cards |
| `collie-ui/src/renderer/src/components/settings/MemoryTab.tsx` | self-review buttons + History/Undo |
| `collie-ui/src/renderer/src/screens/AgentsScreen.tsx` | subagent History/Undo |
| `tests/collie/test_versions.py`, `test_dream_collie.py`, `test_gardener_mvp.py` | phase tests |

## Verification

- `pytest tests/collie -q --ignore=tests/collie/test_pet_status.py` — new
  phase tests pass; only the documented Windows-only baseline failures
  (DPAPI credentials, flaky IPC escape-path) remain.
- `ruff check nanobot collie_core tests` — clean.
- `npm run typecheck` + `npx vitest run` — clean.
- e2e phase gates 1–4 stay green.
- `tools/update_project_snapshot.py` + `--check` — regenerated for the new
  packages.
