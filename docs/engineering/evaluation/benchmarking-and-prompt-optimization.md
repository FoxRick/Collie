# Benchmarking and prompt-optimization laboratory

**Status:** proposed
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
| Lab location | **Separate `collie-bench` repo**, Linux-native on the VM | Keeps untrusted harness code and generated commands away from the Windows-maintained Collie workflow; the benchmark VM is not a production Collie machine |
| Phase 1 scope | 45 trials = 3 harnesses × 5 deterministic tasks × 3 reps | Proves measurement before building anything bigger |
| First model | One cheap model (DeepSeek-class, OpenAI-compatible) | Cost discipline is a feature; cheap models amplify harness-quality differences |
| Metered gateway | **Deferred** | Per-trial API keys + usage parsed from API responses give ~80% of the value at ~10% of the cost |
| Normalized-tool mode | Deferred to Phase 2 | Native-harness mode alone measures the product users actually run |
| SWE-bench / heavy anchor suites | Deferred | Image storage and runtime are a monster; deterministic product tasks first |
| Cohort | Collie, Hermes, Codex CLI first | Hermes is CLI-scriptable, Codex has a built-in Harbor adapter. CommandCode v0.1.1 has no CLI (`bin: None`) — revisit when a headless build exists |
| Scoring | Hidden deterministic verifiers check **state**, not prose | Kills self-serving scoring; LLM judging only where objective verification is impossible |
| Security boundary | Non-root container, no host mounts, egress only to the gateway + simulated services, fresh filesystem per trial | Every downloaded harness and generated command is untrusted |

## The Collie-side gap: no headless mode

`collie-core` states in `pyproject.toml`: *"Collie is launched by the
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

## The small comparison tool

A deliberately tiny (~300–500 lines, stdlib-only) reporter that turns a
directory of trial manifests into the side-by-side overview. It is *not* a
framework — Harbor is the runner, this is the dashboard.

**Input:** one JSON-lines file per run (or a directory of them). Every
trial record carries:

```json
{
  "run_id": "2026-08-03T10:00:00Z-collie-reminder-1",
  "harness": "collie", "version": "0.2.2+abc123",
  "model": "deepseek/deepseek-chat", "task": "reminder-set",
  "prompt_hash": "sha256:...", "tool_schema_hash": "sha256:...",
  "passed": true, "exit_state": "ok",
  "usage": {"input": 1200, "output": 310, "cache_read": 0, "cache_write": 0},
  "calls": {"model": 4, "tool": 7, "retries": 0},
  "latency_ms": 18400, "cost_usd": 0.0042,
  "failure_cluster": null, "trajectory_path": "runs/.../trajectory.jsonl"
}
```

**Output:** markdown + terminal tables:

| harness | task | pass | tokens in | tokens out | cost | p50 lat | tool calls | failure cluster |
|---|---|---|---|---|---|---|---|---|
| collie | reminder-set | ✅ 3/3 | 3.4k | 1.1k | $0.013 | 18s | 7 | — |
| hermes | reminder-set | ✅ 2/3 | 4.1k | 1.3k | $0.016 | 21s | 9 | context-loss |
| codex | reminder-set | ❌ 1/3 | 5.2k | 0.9k | $0.019 | 29s | 12 | looping |

Plus a Pareto frontier (pass rate × cost per solve), a paired baseline-vs-
candidate delta view (the *only* view used for promotion decisions), and a
failure-cluster histogram. One command: `python bench_report.py runs/`.

Where it lives: `collie-bench/scripts/bench_report.py` (it is a lab tool,
not a product feature). If a future UI wants it, the markdown output is
already embeddable.

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

## Phased delivery

### Phase 1 — measurement proof (the milestone this plan enables)
- Headless Collie mode + prompt-hash telemetry (Collie repo PRs).
- `collie-bench` repo: 5 deterministic product tasks (reminders, calendar
  conflict, file edit, doc extraction, multi-step plan) with pytest-style
  state verifiers, 100% oracle pass.
- Adapters for Collie headless, Hermes CLI, Codex CLI; one DeepSeek model.
- 45-trial matrix; one command reproduces it; report shows quality, tokens,
  cost, caching, calls, latency, failure categories.

### Phase 2 — regression laboratory
- 20 deterministic product tasks; add OpenCode, Aider (+ NanoBot family
  harnesses as they become CLI-benchable).
- Nightly runs, paired statistics, frozen regression cohort + refreshed
  ecosystem cohort, public reports.

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
  failure categories — the small tool's output.
- A second operator can reproduce the result from documented inputs.

## Open decisions

- Benchmark VM: dedicated Linux box (Hetzner/OVH-class, 4 vCPU/16 GB is
  enough for Phase 1); the current Hermes VM is off-limits (it runs the
  Telegram gateway — untrusted harness code stays away).
- First exact model id + provider (DeepSeek v4 flash, OpenAI-compatible).
- `collie-bench` repo public vs private at Phase 1 (recommend public dev
  suite, private holdouts).
- Retention policy for full prompts and trajectories.

## Follow-up PR roadmap (Collie repo)

1. **This plan** (current PR) — review and merge the direction.
2. **Headless mode** — `collie_core/headless.py` entry point + test + docs
   (engineering decision: internal capability, not a product CLI).
3. **Prompt-hash telemetry** — additive run-record fields + migration test.
4. (Lab repo, after 2–3 merge) adapters, tasks, verifiers, `bench_report.py`.
