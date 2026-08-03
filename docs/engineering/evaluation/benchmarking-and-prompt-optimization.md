# Benchmarking and prompt-optimization laboratory

**Status:** in progress — Phase 1 landed (2026-08-02/03), Milestone A building
**Audience:** product, engineering, evaluation, and release owners

## Outcome

Make Collie measurably better on every iteration by running it against
open-source agent harnesses under controlled conditions. The laboratory
answers one question per change: *did this Collie prompt, instruction, or
engine tweak improve task completion — and at what token and cost cost?*

The end state is an evidence-gated loop:

```text
bench (Harbor, fixed cohort + model)
  -> cluster failures (tool choice, context loss, looping, permissions, ...)
  -> one hypothesis -> patch a prompt template or engine behavior
  -> re-bench candidate vs baseline side-by-side
  -> keep or revert (predeclared gates, human approves the merge)
```

## Locked decisions (from the 2026-08-02 evaluation discussion)

These were agreed and verified in the earlier session; they are the
foundation and should not be re-litigated:

| Decision | Choice | Why |
|---|---|---|
| Trial runner | **Harbor Framework** (`harbor-framework/harbor`, from the Terminal-Bench team) | Isolated Docker environments per trial, agent adapters, versioned datasets, trajectory/artifact/usage collection, RL-rollout support for the endgame |
| Recording + visualization | **Harbor's built-in web viewer** (`harbor view`) — not a custom tool | Verified in Harbor source: job/trial lists, full trajectory inspection (input/output text per step), input/cached/output token counts, **cost in USD** via LiteLLM pricing, verifier results, per-job LLM analysis, run history, and launching runs from the UI. We only add a thin cross-run regression layer on top (below) |
| Lab location | **Separate `collie-bench` repo**, Linux-native on the VM | Keeps untrusted harness code and generated commands away from the Windows-maintained Collie workflow; the benchmark VM is not a production Collie machine |
| Phase 1 scope | 45 trials = 3 harnesses × 5 deterministic tasks × 3 reps | Proves measurement before building anything bigger |
| First model | One cheap model (DeepSeek-class, OpenAI-compatible) | Cost discipline is a feature; cheap models amplify harness-quality differences |
| Metered gateway | **Deferred** | Per-trial API keys + usage parsed from API responses give ~80% of the value at ~10% of the cost |
| Normalized-tool mode | Deferred to Phase 2 | Native-harness mode alone measures the product users actually run |
| SWE-bench / heavy anchor suites | Deferred | Image storage and runtime are a monster; deterministic product tasks first |
| Cohort | Collie, Hermes, OpenCode CLI | Codex RETIRED 2026-08-02 (CLI 0.146+ is Responses-API-only; DeepSeek speaks Chat Completions → 401, verified live). OpenCode added; Hermes + OpenCode wrapped by custom adapter subclasses (stock adapters can't run this cohort) |
| Scoring | Hidden deterministic verifiers check **state**, not prose | Kills self-serving scoring; LLM judging only where objective verification is impossible |
| Security boundary | Non-root container, no host mounts, egress only to the gateway + simulated services, fresh filesystem per trial | Every downloaded harness and generated command is untrusted |

## The Collie-side gap: no headless mode

**RESOLVED 2026-08-02 — PR #12 landed `collie_core/headless.py`.** The
one-shot JSON contract below shipped as designed; the bench adapter defaults
to the headless driver (`collie-bench` `ff90d99`, `build_job_config.py`
`driver: headless`). An IPC driver remains as fallback. Historical context
preserved below.

`collie-core` stated in `pyproject.toml`: *"Collie is launched by the
Electron shell as a managed subprocess. No CLI entry points."* The agent
loop, providers, tools, permissions, and telemetry all exist and are tested
(`collie_core.runtime` boots them); they are simply only ever started by the
UI.

Benchmarking needs a process that runs **one task and exits with evidence**.
Proposed engineering capability (not a user-facing product CLI — the
"no dev shell" non-goal in `VISION.md` is preserved):

```text
python -m collie_core.headless --task "..." --model deepseek/... \
    --provider-id <id> --api-key-env COLLIE_BENCH_KEY \
    --workspace <tmp> --home <tmp> [--session-key bench-<run>] \
    [--max-iterations N] [--timeout S]
```

Behavior:

- Boots `CollieRuntime`-equivalent composition against an isolated
  `COLLIE_HOME`/workspace (no real user data touched).
- Runs one prompt through `AgentLoop.process_direct` (the same path the UI
  uses), streams nothing to a UI.
- Prints a single JSON document on exit: final message, per-turn usage
  (input/output/reasoning/cache tokens from provider responses), tool calls,
  wall-clock time, and exit state (`ok` / `timeout` / `error`).
- Accepts an API key via environment variable only — never on the command
  line, never in logs.

This is the critical path for the entire laboratory; nothing can be
benchmarked until it exists. It is a ~1–2 day change, mostly wiring
existing composition into a new entry point plus a focused test.

**Why an entry point instead of driving the live IPC protocol?** The
Electron shell is a WebSocket client, so a bench adapter could drive the
existing `collie_core.runtime` directly — zero Collie changes, maximum
fidelity. That path is viable (the e2e phase gates already exercise it),
but the adapter would have to reimplement the streaming, approval, and
readiness protocol, chase internal protocol changes, and boot background
schedulers per trial. The headless entry point trades ~1–2 days of Collie
work for a stable one-shot JSON contract, deterministic startup (no
scheduler/reminder/messenger noise), and a trivial adapter — and doubles
as the CI regression unit and the future optimization loop's task runner.
No user-facing CLI is added; this is an internal capability. Interim
option: the first smoke matrix can drive the IPC protocol with zero Collie
changes while the entry point is built.

## Parity: the headless engine IS the live app

Headless mode is **not a second implementation** — it is the same engine
without a window. Collie's architecture already guarantees this:

- The Electron shell is only a client: renderer → preload → main →
  localhost WebSocket → `collie_core.runtime`. All agent behavior (loop,
  prompts, tools, permissions, memory, telemetry) lives in collie-core;
  the UI contains no engine logic.
- Headless mode reuses the exact same composition: `CollieRuntime._build_loop()`
  (same `build_config` from settings, same `ToolLoader(collie_tools)`
  discovery, same Jinja2 templates) and the same `AgentLoop.process_direct`
  call the IPC server uses for every chat message. Only the entry point
  differs; the engine does not.

Anti-drift guarantees (so parity is enforced, not hoped for):

1. **One-entry-point rule** — headless code may only reuse
   `CollieRuntime` methods; it never re-composes the loop, tools, prompts,
   or permissions. Enforced in review and by a consistency test asserting
   both paths produce identical tool registries and rendered system
   prompts from the same settings.
2. **Phase-gate e2e tests run through the headless entry point too** —
   the existing fake-OpenAI e2e gates (`tests/collie/test_e2e_phase1..4.py`)
   already guard the live chat path; the same assertions run against
   `collie_core.headless`, so any drift fails CI.
3. **Prompt/config hashes in every trial** — the prompt-hash telemetry
   makes parity visible in the data: each run records which rendered
   system prompt and config it measured. When prompts change, the next
   run's hash changes, and comparisons always know exactly what they
   compared.
4. **Pinned commit per run** — Harbor trials launch a pinned Collie
   checkout (commit + image digest in the manifest), so a benchmark
   measures the exact code the app runs at that commit. Prompts and tools
   are data files in that checkout; nothing is copied into the lab.

What "equal" does not cover: the UI layer itself (renderer, tray, pet,
notifications) and Windows packaging/OS integrations stay out of the
headless lane by design — the Windows acceptance lane covers the shell.
The agent brain — loop, prompts, tools, permissions, settings, memory,
telemetry — is identical.

**The "no redoing" answer:** changes flow one way. Edit a prompt template,
tool, or engine behavior in collie-core once; both the app and the next
bench run read the same files at build time. The lab never copies Collie's
prompts or tools — it only launches the pinned engine. The only thing that
changes between runs is the recorded commit hash.

## Collie-side enabler: prompt-hash telemetry

Reproducibility requires knowing *which* system prompt and tool schema a
run used. Collie's prompts are Jinja2 templates under
`nanobot/templates/agent/` (plus the workspace bootstrap files
`VISION.md`, `AGENTS.md`, `MEMORY.md`), so they are already data files that
can be hashed.

Extend the existing run-record telemetry (`collie_core/telemetry/recorder.py`)
with a small, additive field set per turn:

- `prompt_hash` — stable hash of the rendered system prompt (template set
  + version + rendered content);
- `tool_schema_hash` — hash of the tool schemas presented to the model;
- `config_hash` — hash of model id, provider, generation settings, limits.

These are one-line-per-record writes through the existing fire-and-forget
writer; no schema migration beyond new columns with `None` defaults.

## The laboratory (`collie-bench` repo)

```text
collie-bench/
|-- agents/       Harbor adapters for Collie (headless mode), Hermes, Codex
|-- datasets/     Public dev tasks + pinned external benchmark subsets
|-- scorers/      Deterministic public verifiers (state-checking)
|-- configs/      Frozen experiment matrices (cohort, model, reps)
|-- manifests/    Commit, image digest, model, prompt-hash, dataset identity
|-- reports/      Generated comparisons and failure summaries
`-- scripts/      build, run, analyze, report entry points
```

Private holdout tasks and hidden verifier material live in a separate
private repo/service; the optimization loop never reads them.

## Recording and visualization (Harbor first)

**Harbor already records and visualizes the per-run detail the user wants
to see.** Verified against Harbor source (`src/harbor/viewer/server.py`,
`src/harbor/cli/view.py`):

- **Per trial, recorded automatically:** the full trajectory (every
  input/output/tool-call step in the standard Agent Trajectory Interchange
  Format), input / cached-input / output token counts, **cost in USD**
  (LiteLLM pricing table), verifier pass/fail, artifacts, latency, and
  environment metadata.
- **Built-in web viewer** (`harbor view`): a FastAPI + React app that
  lists jobs and trials, shows each trial's trajectory (input text, output
  text, tool calls), usage and cost stats, per-job LLM analysis
  (`/api/jobs/{job}/analysis`), run history, and can even start runs from
  the browser.

So the "something visual/recording" requirement is Harbor's job, not a
custom dashboard. What Harbor does **not** provide out of the box is the
*cross-run* view: "benchmark from last week vs today, per harness and per
prompt version, side by side — and did prompt v3 beat prompt v2?" That is
the one thin layer we add in `collie-bench`:

- `collie-bench/scripts/bench_report.py` (~300–500 lines, stdlib-only):
  reads Harbor's stored job/trial records, renders side-by-side tables
  (harness × task: pass rate, tokens in/out/cache, cost, p50 latency, tool
  calls, failure cluster), a Pareto frontier, and the **baseline-vs-candidate
  delta view** used for keep/revert decisions.
- Later: a small time-series page (HTML) on top of the same records so
  re-runs over weeks visibly show improvement — fed entirely by Harbor's
  data, never re-measured.

Harbor runs the trials and records everything; the report layer only
aggregates and compares what Harbor already stored.

## Iteration loop and the "1% per day" reality check

- With 5–10 tasks × 3 reps, the measurement noise floor is ~10–15%. A
  single-point delta below ~5% is not measurable; do not chase it.
- Iterate daily if desired, but measure **failure-cluster shrink** (did
  this task type's failures drop?) rather than a single percentage.
- Every candidate re-bench runs the **baseline side-by-side** in the same
  session; the second benchmark is meaningless without the first.
- Promote only when quality improves without disproportionate cost growth,
  or cost-per-solve falls materially with non-inferior quality — thresholds
  declared *before* the run, not after viewing it.

## Phase 1 status (verified 2026-08-02/03)

Landed and reproducible:

- **Headless Collie mode** — `collie_core/headless.py` (PR #12), driver
  `headless` default in collie-bench (`ff90d99`), IPC driver fallback.
- **Prompt-hash telemetry** — `prompt_hash` / `tool_schema_hash` /
  `config_hash` recorded per trial (verified in `result.json` metadata).
- **`collie-bench` repo** (PRIVATE, FoxRick/collie-bench) — 5 deterministic
  tasks with state-checking verifiers, 100% oracle pass; adapters for
  Collie (headless), Hermes, OpenCode; frozen matrix in `configs/phase1.json`.
- **First live benchmark** (`bench1`, 2026-08-02): collie + hermes + opencode
  × reminder-set × 1 rep — **3/3 reward-1**. Full cohort proven end-to-end
  (collie ~6 min, hermes ~1–2 min, opencode ~1–2 min per trial, ~12 min wall).
- **Viewer** (`harbor view`) works on the VM; desktop launcher
  `~/.local/bin/collie-bench-viewer.sh` (icon `Collie-Bench`) opens the
  latest report. Two viewer pitfalls fixed: flat-layout nesting (stage a
  real copy under `<job>/`) and symlinked job dirs rejected by the viewer's
  path validation (400 "Invalid job name" — copy, don't symlink).

Known data gaps (Milestone A fixes): input **prompt text** is not stored
per trial (only `prompt_hash`); hermes adapter records no usage tokens
(input/output/cost all `None`).

## Milestone A — prompt variants, full capture, compare view (building)

The prompt-optimization loop (the "1% per day" engine) needs three
mechanical pieces, all in `collie-bench` — **zero product-code changes**:

1. **Variant registry + injection** — `collie-bench/prompts/<variant>.md`
   holds candidate system prompts. Collie's system prompt is template data
   (`collie-core/nanobot/templates/agent/*.md`), pinned into the trial image
   at build time, so a variant is a `COPY` of the candidate template over
   the pinned checkout in `images/base/Dockerfile` — no engine change, no
   fork. `build_job_config.py` picks the variant per trial; `prompt_hash`
   (already recorded) proves which variant ran.
2. **Full input capture** — the collie driver dumps the rendered
   system-prompt + tool schema + task text per trial into `agent/`
   (`prompt.txt`), alongside the existing `collie-result.json`. Fix the
   hermes adapter to parse usage from its session output so all three
   harnesses report input/output/cache tokens + cost.
3. **Prompt-compare report** — extend `bench_report.py` (or a sibling)
   with the cross-run view: harness × prompt-variant × task matrix (pass
   rate, tokens in/out, cost, latency) plus a text diff between variants.
   This is the "what's driving the numbers" view — two dimensions:
   cross-harness (what does the winning harness tell the model?) and
   cross-variant (did our change help?).

## Phased delivery

### Phase 1 — measurement proof ✅ (landed 2026-08-02/03)
- Headless Collie mode + prompt-hash telemetry (Collie repo PR #12 + follow-ups).
- `collie-bench` repo: 5 deterministic product tasks (reminders, calendar
  conflict, file edit, doc extraction, multi-step plan) with pytest-style
  state verifiers, 100% oracle pass.
- Adapters for Collie headless, Hermes CLI, OpenCode CLI; one DeepSeek model
  (`deepseek/deepseek-v4-flash`).
- 45-trial matrix config frozen (`configs/phase1.json`); bench1 (3-trial
  full-cohort smoke) green 3/3; report shows quality, tokens, cost, caching,
  calls, latency, failure categories.

### Phase 2 — regression laboratory (next, after Milestone A)
- 20 deterministic product tasks; add OpenCode, Aider (+ NanoBot family
  harnesses as they become CLI-benchable).
- Nightly runs (cron on the bench VM), paired statistics, frozen regression
  cohort + refreshed ecosystem cohort, public reports.
- Milestone B: scheduler + auto-report (the nightly loop is the autonomy
  the product loop needs).

### Phase 3 — guarded optimization
- Private holdout scorer; failure analysis + candidate-patch generation.
- Predeclared promotion gates, holdout query limits, human approval.
- This is where "adapt Collie's system prompts automatically" becomes safe
  reality — and only after Phases 1–2 prove the measurements are trustworthy.

### Phase 4 — platform coverage
- Windows Collie acceptance lane; model families; native/normalized
  protocol experiments; reproducible methodology publication.

## Acceptance criteria (Phase 1 done when)

- One command reproduces the frozen 45-trial matrix.
- Every trial uses a clean environment + immutable run manifest.
- Collie, Hermes, and Codex receive the same task snapshot and enforced model.
- Verifiers score artifacts without agent access; infrastructure failures
  are not counted as task failures.
- The report shows quality, tokens, cost, caching, calls, latency, and
  failure categories — Harbor viewer for per-run detail, `bench_report.py`
  for the cross-run side-by-side.
- A second operator can reproduce the result from documented inputs.

## Open decisions

- ~~Benchmark VM~~ **RESOLVED**: live trials run on the Hermes VM (docker
  group + buildx installed 2026-08-02; `sg docker` wrapper for commands).
  If a bigger matrix strains the box, revisit a dedicated Linux host later.
- ~~First exact model id + provider~~ **RESOLVED**: `deepseek/deepseek-v4-flash`
  (OpenAI-compatible), key via `COLLIE_BENCH_KEY` env, never in configs.
- ~~`collie-bench` repo public vs private~~ **RESOLVED**: PRIVATE
  (decision 2026-08-02) — dev suite + verifiers stay out of public view.
- Retention policy for full prompts and trajectories (Milestone A's prompt
  capture makes this concrete; default: keep per-report dirs, prune jobs/
  after report generation).

## Follow-up PR roadmap (Collie repo)

1. **This plan** ✅ (PR #10 merged, direction approved).
2. **Headless mode** ✅ (`collie_core/headless.py` landed in PR #12 —
   engineering decision: internal capability, not a product CLI).
3. **Prompt-hash telemetry** ✅ (additive run-record fields + tests).
4. (Lab repo, done 2026-08-02) adapters, tasks, verifiers, cross-run report
   layer on top of Harbor's viewer data.
5. **Milestone A** (lab repo): variant registry + image COPY injection +
   full prompt capture + hermes usage fix + prompt-compare report.
6. **Milestone B** (lab repo): nightly cron matrix + auto-report.
7. **Milestone C** (product repo PR): ship a winning prompt variant into
   `nanobot/templates/agent/` — the first evidence-gated product change.
