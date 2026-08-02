# Implementation brief: collie-bench evaluation laboratory repo

**Repo to create:** `FoxRick/collie-bench` (does not exist yet — create it)
**Parent plan:** `docs/engineering/evaluation/benchmarking-and-prompt-optimization.md`
**Status:** implementation spec — build it, then PR it (or push directly if it is the repo's first commit)

This brief is self-contained. You are creating a **new repository** that
benchmarks Collie against open-source agent harnesses using the Harbor
Framework. The Collie side (headless engine mode + prompt-hash telemetry)
is being built in parallel in the `FoxRick/Collie` repo by a different
agent — see `docs/engineering/evaluation/headless-mode-implementation.md`.
Coordinate only via the `FoxRick/Collie` branch it produces; do not wait
on it (see the interim IPC option in §5).

---

## Goal

A reproducible evaluation laboratory whose Phase-1 milestone is: **one
command reproduces a frozen 45-trial matrix** — 3 harnesses (Collie,
Hermes, Codex CLI) × 5 deterministic tasks × 3 repetitions — on one cheap
model (DeepSeek, OpenAI-compatible), with every trial in a clean isolated
environment, immutable run manifests, objective state-checking verifiers,
and a side-by-side report showing quality, tokens, cost, caching, calls,
latency, and failure categories.

## Key facts (verified)

- **Harbor Framework**: `harbor-framework/harbor` (from the Terminal-Bench
  team). Install: `uv tool install harbor` or `pip install harbor` (pin
  the version — check `harbor --version` after install and record it).
  `harbor run --dataset <dataset@version> --agent <agent> --model
  <model>` runs trials in isolated Docker containers; `--n-concurrent N`
  parallelizes; `harbor view` opens its built-in web viewer (job/trial
  lists, full trajectories, input/cached/output tokens, cost in USD via
  LiteLLM pricing, verifier results, per-job LLM analysis, run history).
  Docs: https://harborframework.com/docs — read "Core Concepts", "Tasks",
  "Datasets", "Agents", and the Agent Trajectory Interchange Format pages
  before building.
- **Harbor records per trial**: full trajectory (ATIF), token counts
  (input/cached/output), cost USD, verifier pass/fail, artifacts,
  latency, environment metadata. The lab never re-measures; it reads
  Harbor's stored records.
- **DeepSeek**: OpenAI-compatible API (`https://api.deepseek.com`); model
  id like `deepseek/deepseek-chat` (Harbor's model convention). The bench
  runs on a Linux VM with Docker Engine, Python 3.11+, `uv`, and git.
- **Security boundary (mandatory)**: every downloaded harness and
  generated command is untrusted — run as non-root container user, no
  host mounts, no Docker socket, no real provider credentials inside
  containers beyond the per-trial model key, egress only to the model API
  + simulated task services, fresh writable filesystem per trial, hidden
  verifiers outside the agent environment, destroy env after artifact
  collection.

---

## Repo structure

```text
collie-bench/
|-- agents/       Harbor adapters: Collie (headless), Hermes CLI, Codex CLI
|-- datasets/     collie-dev tasks (task dirs) + pinned external subsets
|-- scorers/      deterministic public verifiers (state-checking)
|-- configs/      frozen experiment matrices (cohort, model, reps, limits)
|-- manifests/    commit, image digest, model, prompt-hash, dataset identity
|-- reports/      generated results, comparisons, failure summaries
`-- scripts/      build, run, analyze, report entry points
```

Plus: `README.md` (what/why/how + one-command reproduce), `pyproject.toml`
or `requirements.txt` (stdlib + pinned `harbor`), `.gitignore` (runs/,
reports/, .venv, *.log), `LICENSE` (MIT, matching Collie).

## Phase-1 milestone checklist (build in this order)

### 1. Scaffold + Harbor smoke

1. Create the repo (public; private holdout tasks come later, in a
   separate private repo — never in this one).
2. Install pinned Harbor; run a stock dataset sample (e.g.
   `harbor run -d terminal-bench@2.0 -a codex-cli -m <cheap model> --n-concurrent 1`
   with a trial key) to prove Docker + Harbor work end-to-end. Record the
   harbor version + image digest.
3. `harbor view` — confirm the viewer shows the job and its trajectory.

### 2. Collie adapter (two options — pick per readiness)

- **Preferred:** wrapper that launches Collie headless:
  `python -m collie_core.headless --task "<task>" --home <trial home>
  --model deepseek/deepseek-chat --provider deepseek --api-key-env
  COLLIE_BENCH_KEY --timeout 300 --max-iterations 50 --approval-preset
  allow --json-out <file>` (contract: one JSON document on stdout per
  `headless-mode-implementation.md`). The adapter maps that JSON into a
  Harbor agent response + attaches the usage/cost data. The Collie trial
  container image must contain a pinned `collie-core` install
  (build from a pinned FoxRick/Collie commit; record commit + image
  digest in the manifest).
- **Interim (if headless is not merged yet):** drive the existing
  `collie_core.runtime` over its IPC WebSocket (localhost, port from
  `COLLIE_IPC_PORT` / `COLLIE_READY` handshake, token from
  `COLLIE_IPC_TOKEN`), modeled on `tests/collie/test_e2e_phase1..4.py` in
  the Collie repo. Zero Collie changes needed, but the adapter
  reimplements streaming + approvals; label these trials `driver=ipc` in
  the manifest.
- Mark every trial's manifest with `harness: collie`, `commit`, and
  `prompt_hash` (from the headless JSON) so results are always tied to an
  exact prompt version.

### 3. Hermes adapter

Hermes is CLI-scriptable (the Hermes agent runs on this VM; its CLI is
`hermes`). Adapter contract: launch hermes CLI with the task, pin the
model to the same DeepSeek model, capture the final output + usage
(tokens/cost from the CLI output where available; otherwise record
`usage: null` and note it), write the trial JSONL. Keep the adapter thin:
task in, output + usage out, no Collie-specific logic.

### 4. Codex CLI adapter

Harbor ships a built-in Codex adapter — use it. Pin the same model via
Harbor's `--model` (Codex is OpenAI-native; if protocol translation is
required, record and label that limitation per the parent plan).

### 5. The five deterministic tasks (`datasets/collie-dev/`)

Each task = a directory with `task.toml` (Harbor task format: instruction,
container env, test command), a `Dockerfile` (base image + pinned Collie
core for the Collie trials; shared base for Hermes/Codex), starting state
(seeded SQLite `~/.collie` DB and/or workspace files), and a **verifier
that checks state, not prose** (filesystem rows, DB rows, file contents).

Suggested Phase-1 set (deterministic, mirrors Collie's purpose, each with
a documented oracle + 100% oracle pass):

1. **reminder-set** — ask for a reminder; verifier checks the reminders
   table row (text, time) exists.
2. **calendar-conflict** — given two events in the seeded calendar,
   resolve the conflict; verifier checks the calendar rows changed only
   as specified (no unrelated edits).
3. **file-edit** — edit a permitted file in the workspace per spec;
   verifier diffs the file against the expected content; unrelated files
   unchanged.
4. **doc-extract** — extract structured facts from a provided document
   into the expected format; verifier validates the structured output.
5. **multi-step-plan** — complete a 3-step task in order; verifier checks
   all three artifacts/states exist.

Rules: every task's verifier must pass on the oracle solution before the
task ships; tasks that everyone passes or everyone fails are replaced
(no improvement signal); difficulty must separate harnesses.

### 6. Frozen experiment matrix (`configs/phase1.json`)

```json
{
  "model": "deepseek/deepseek-chat",
  "harnesses": ["collie", "hermes", "codex-cli"],
  "dataset": "collie-dev@1",
  "reps": 3,
  "n_concurrent": 4,
  "timeout_s": 600,
  "per_trial_budget": {"max_tokens": 100000, "max_calls": 200},
  "approved_before_run": ["model", "harnesses", "reps", "dataset"]
}
```

Freeze it; a run records the matrix identity in the manifest. Thresholds
for promotion (quality/cost gates) are declared **before** runs, per the
parent plan — never after viewing results.

### 7. Manifests (`manifests/`)

Every run writes a JSON manifest capturing: harbor version, image digests,
Collie commit, hermes/codex versions, dataset name+version+content digest,
exact model id + provider, generation/reasoning settings, prompt +
tool-schema + config hashes (from Collie headless JSON), adapter versions
+ launch commands, seed, start time, completion state, infra errors. Two
lanes: **regression cohort** (pinned harness versions) and **ecosystem
cohort** (refreshed + refrozen). Never overwrite an old run.

### 8. Report layer (`scripts/bench_report.py`)

~300–500 lines, stdlib-only, reads Harbor's stored trial records
(jobs/trials dirs — inspect what `harbor run` writes to disk and read
that; do not re-run anything). Outputs:

- side-by-side markdown/terminal table: harness × task — pass rate,
  tokens in/out/cache, cost, p50 latency, tool calls, failure cluster;
- Pareto frontier (pass rate × cost per solved task);
- **baseline-vs-candidate delta view** — the only view used for
  keep/revert decisions (compare two runs: quality delta, cost delta,
  per-task breakdown);
- failure-cluster histogram (tool selection, context loss, permissions,
  looping, verification, provider, infra);
- later: a small time-series HTML page over run history so improvement
  across weeks is visible (fed by the same records).

### 9. One-command reproduce (`scripts/run_phase1.sh`)

`./scripts/run_phase1.sh` = build images (pinned) → verify manifests →
`harbor run` the frozen matrix → collect → `bench_report.py` → write
`reports/phase1-<timestamp>/` with manifest + tables + trajectories.
A second operator reproduces the result from documented inputs (README).

---

## Acceptance criteria (Phase 1 done when)

1. One command reproduces the frozen 45-trial matrix (3×5×3) on the
   pinned model.
2. Every trial: clean environment, immutable run manifest, verifier
   scored without agent access, infra failures ≠ task failures.
3. Collie, Hermes, Codex receive the same task snapshot and enforced
   model; protocol-translation limitations labeled where they exist.
4. Report shows quality, tokens, cost, caching, calls, latency, failure
   categories (Harbor viewer for per-run detail, `bench_report.py` for
   the cross-run view).
5. Each custom task has 100% oracle pass + human-audited methodology.
6. No secrets in the repo, manifests, logs, or reports.

## Gates

- `harbor view` works; `bench_report.py` runs on a fresh clone with
  stored trial data (include one committed sample run under `reports/`
  as a fixture).
- `ruff`-clean Python (or project-equivalent lint), stdlib-only report
  script.

## Open decisions (record your choices in README)

- Benchmark VM provider/budget (parent plan: 4 vCPU/16 GB is enough for
  Phase 1; the VM that runs the Hermes Telegram gateway is off-limits —
  untrusted harness code never shares it).
- Which DeepSeek model id is cheapest while separating harnesses.
- Repo public vs private (recommended: public dev suite now, private
  holdouts later).
- Retention policy for full prompts/trajectories.

## Pitfalls

- Harbor's dataset/task format changes across versions — pin the harbor
  version and record it in every manifest.
- Trial images must NOT contain the real provider key; inject the
  per-trial key at run time via env and egress-limit to the model API.
- The Collie adapter must consume the headless JSON contract as-is
  (field names are stable — do not fork them).
- If Collie headless is delayed, ship the IPC-driven interim trials
  labeled `driver=ipc`; re-run the matrix with the headless driver before
  treating numbers as canonical.
- Cost-per-solve never stands alone (fail-fast cheating) — always publish
  pass rate + cost per attempt + mutually-solved-task cost next to it.
- Keep the public dataset free of anything resembling the private
  holdout material; the optimization loop (later phase) must never see
  holdout tasks or verifiers.
