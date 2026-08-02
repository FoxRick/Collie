# Implementation brief: Collie headless engine mode + prompt-hash telemetry

**Repo:** `FoxRick/Collie` (collie-main)
**Parent plan:** `docs/engineering/evaluation/benchmarking-and-prompt-optimization.md`
**Status:** implementation spec — build it, then PR it

This brief is self-contained: read the referenced files, implement the two
changes below, add the tests, run the gates, open a PR. Do NOT create the
`collie-bench` repo — that is a separate task in a separate chat (see
`docs/engineering/evaluation/collie-bench-implementation.md`).

---

## Goal

Make Collie benchmarkable from the outside without changing the product:

1. **Headless engine mode** (`python -m collie_core.headless`): run one
   task through the real agent loop, print one JSON result document
   (answer, token usage, calls, latency, prompt hashes), exit. Internal
   engineering capability only — **no user-facing CLI, no
   `[project.scripts]` entry points** (preserves the VISION "no dev shell"
   non-goal and the pyproject "No CLI entry points" stance).
2. **Prompt-hash telemetry**: additive columns on the existing run-record
   telemetry so every turn knows which rendered system prompt, tool
   schema, and config it used.

## Architecture facts (verified — read these files first)

- `collie_core/runtime.py` — `CollieRuntime` composes everything:
  - `__init__(port, db, ipc_token)` → DB, workspace, profile, tools
    bindings, permission evaluator, `CollieIPCServer`, `ApprovalBroker`,
    scheduler, messengers.
  - `_build_loop()` → `collie_settings.build_config(db, mcp_servers=...)`
    → `AgentLoop.from_config(...)` with `hook_factories=[create_telemetry_hook_factory(db)]`,
    registers Collie tools via `ToolLoader(collie_tools).load(ctx, loop.tools)`,
    sets `loop.subagents.hook_factories`, registers a runtime-context
    provider, `loop.context.command_guidance = True`.
  - `_configure()` / `_configure_locked()` → builds loop, starts
    `loop.run()` task, outbound consumer, messengers. Returns
    `{"configured": True, "model": ...}`.
  - `_configure_provider_candidate(candidate)` → transactional provider
    setup: `_validated_provider_candidate()` normalizes (fields:
    `provider_id, name, auth_type="api-key", model, runtime_name,
    secret_name, protocol, api_base, api_key`), `collie_settings.set_api_key(secret_name, key)`
    for the transient in-memory key, `db.configure_provider_candidate_record(**normalized)`
    persists, then rebuilds the loop. **Reuse this — it is the same path
    the UI uses.**
  - `_chat(...)` → calls `self.loop.process_direct(content, session_key,
    channel, chat_id, on_stream=..., on_superseded_response=...,
    on_progress=..., media=..., message_metadata=...,
    permission_context={execution_mode, conversation_id, run_id, plan_id,
    plan_version, origin, model_provider}, workspace_scope=...)`. After
    the call: `usage = getattr(self.loop, "_last_usage", None) or {}`,
    then `db.record_usage(provider_id, messages=1,
    tokens=int(usage.get("total_tokens") or 0))`.
  - `run()` → starts IPC server + scheduler + reminder checker, prints
    `COLLIE_READY {"port": ...}`, blocks. Shutdown order matters:
    `ipc.stop()` → scheduler/reminder cancel → `_shutdown_loop()` →
    `approvals.cancel_all()` → `RunRecorder.active_for(db).shutdown()` →
    `db.close()`.
  - `main(argv)` → argparse `--port`; `_env_port()` honors
    `COLLIE_IPC_PORT`.
- `collie_core/settings.py`:
  - `build_config(db, mcp_servers=None)` reads SQLite settings keys:
    `provider.name` (default "openai"), `provider.secret_name`,
    `provider.model`, `provider.api_base`, `agent.timezone`,
    `agent.bot_name`, `agent.max_tool_iterations` (clamped 1..2000).
  - API key resolution: transient `set_api_key()` first, then env
    `COLLIE_<PROVIDER>_API_KEY`, then `COLLIE_PROVIDER_API_KEY`. **So a
    bench key can be passed purely via environment — no IPC needed.**
  - `ensure_workspace()` creates `~/.collie/workspace` with default
    VISION.md / AGENTS.md. `collie_home()` lives in `collie_core/db.py`
    and honors `COLLIE_HOME` (verified in AGENTS.md).
  - Permission preset setting: `permissions.local_write_preset`
    (default "ask") is read in `CollieRuntime.__init__` and applied to
    `PermissionEvaluator`; `set_local_write_preset` is the existing
    setter used by the IPC path.
- `collie_core/telemetry/recorder.py` — `RunRecorder` (fire-and-forget
  writer thread, bounded queue `MAX_QUEUED_WRITES=500`, `suspend()`,
  `flush()`, `shutdown()`, `sanitize_text()`, `summarize()`). Turn
  lifecycle: `start_turn(turn_id, session_key, conversation_id,
  turn_kind)`. Registry: `RunRecorder.for_db(db)` /
  `active_for(db)`. Hook factory: `collie_core/telemetry/hook.py`
  `create_telemetry_hook_factory(db)`.
- `nanobot/agent/context.py` — `ContextBuilder` renders the system
  prompt: `BOOTSTRAP_FILES = ["VISION.md", "AGENTS.md", "MEMORY.md"]`
  from the workspace, plus Jinja2 templates under
  `nanobot/templates/agent/*.md` rendered via
  `nanobot/utils/prompt_templates.py::render_template`.
- `nanobot/agent/loop.py` — `AgentLoop.from_config(config, bus,
  session_manager, hook_factories=..., provider=...)`,
  `process_direct(...)`, `_last_usage`.
- Tests to mirror: `tests/collie/test_e2e_phase1..4.py` (full IPC → loop
  → fake OpenAI endpoint → SQLite round trips; patch the provider, never
  hit a real API). `tests/collie/test_pet_status.py` needs tkinter —
  ignore it in gates.

---

## Part A — Headless engine mode

### A.1 Contract

New module: `collie_core/headless.py` (run via `python -m
collie_core.headless`). It boots a `CollieRuntime`-equivalent composition
against an **isolated `COLLIE_HOME`** (never `~/.collie` by default), runs
exactly one task, prints ONE JSON document to stdout, and exits.

Arguments (argparse):

| Flag | Meaning | Default |
|---|---|---|
| `--task` (required) | The task/user prompt to run | — |
| `--home` | `COLLIE_HOME` dir (created if missing) | fresh `tempfile.mkdtemp()` |
| `--model` | model id (e.g. `deepseek/deepseek-chat`) | from provider record / settings |
| `--provider` | provider name (e.g. `deepseek`) | `deepseek` |
| `--api-base` | custom OpenAI-compatible base URL | provider default |
| `--api-key-env` | env var name holding the key (e.g. `COLLIE_BENCH_KEY`) | `COLLIE_PROVIDER_API_KEY` |
| `--timeout` | wall-clock seconds for the whole turn | `300` |
| `--max-iterations` | maps to `agent.max_tool_iterations` (clamped 1..2000) | `50` |
| `--approval-preset` | `allow` \| `ask` \| `deny` — wired through the SAME `set_local_write_preset` path, never a bypass | `allow` (bench sandbox is disposable) |
| `--session-key` | engine session key (defaults to `bench-<uuid>`) | auto |
| `--json-out` | also write the result document to this file | stdout only |

Environment: the API key MUST come from env (see `--api-key-env`);
never accept a key via argv, never log it. `build_config` already reads
`COLLIE_<PROVIDER>_API_KEY` / `COLLIE_PROVIDER_API_KEY` directly.

Exit codes: `0` ok, `1` task/engine error, `2` timeout, `3` usage/config
error (bad args, missing key, provider config failed).

### A.2 Implementation steps

1. In `collie_core/headless.py`:
   - `main(argv)` parses args, resolves `--home` (+ `COLLIE_HOME` env),
     sets `os.environ["COLLIE_HOME"]` **before** importing/building
     `CollieDB`, then `asyncio.run(run_one(...))`.
   - Build `CollieRuntime` (reuse it — do not re-compose). Skip the
     long-running parts: do NOT call `runtime.run()` (which starts the
     IPC server/scheduler/reminders). Instead drive the lifecycle
     manually:
     a. Provider: build the candidate dict (provider_id, name,
        auth_type="api-key", model, runtime_name, secret_name, protocol,
        api_base, api_key from env) and call
        `await runtime._configure_provider_candidate(candidate)`. If
        `configured` is False → print error JSON, exit 3. (This reuses
        validation + transactional rebuild — the exact UI path.)
     b. Approval preset: `runtime.db.set_setting("permissions.local_write_preset", preset)`
        BEFORE `_configure_provider_candidate` (the evaluator reads it at
        init) — or call the evaluator's `set_local_write_preset` after.
     c. Session: `session_key = args.session_key or f"bench-{uuid4().hex}"`;
        create a conversation via `runtime.db.create_conversation(title="Bench")`.
     d. Run: `await asyncio.wait_for(runtime._chat(...), timeout=args.timeout)`
        with `conversation_id=...`, `execution_mode="execute"`, and a
        `permission_context` shaped exactly like `_chat`'s (origin
        "bench" is acceptable; keep model_provider from the provider
        record). Catch `asyncio.TimeoutError` → exit_state "timeout",
        exit 2.
     e. Usage: `loop._last_usage` (same read as `_chat`); also read the
        run record from telemetry for prompt/config hashes (Part B) or
        compute them in-process.
     f. Flush telemetry: `RunRecorder.active_for(runtime.db)` → `flush()`
        then `shutdown()`; then `runtime.db.close()`. Mirror the shutdown
        ordering in `runtime.run()` (cancel in-flight work first).
   - Assemble the JSON result (schema below), print as a single line to
     stdout, return exit code.
2. Do NOT touch `pyproject.toml` entry points. `python -m
   collie_core.headless` works without a `[project.scripts]` entry.

### A.3 Result document (stable contract — do not change field names)

```json
{
  "schema_version": 1,
  "run_id": "<uuid>",
  "harness": "collie",
  "commit": "<git sha, or 'unknown'>",
  "model": "<model id>",
  "provider": "<provider id>",
  "task": "<the task text>",
  "session_key": "...",
  "conversation_id": "...",
  "prompt_hash": "sha256:...",
  "tool_schema_hash": "sha256:...",
  "config_hash": "sha256:...",
  "final_text": "<sanitized final assistant text>",
  "usage": {"input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0},
  "calls": {"model_calls": 0, "tool_calls": 0,
            "retries": 0, "no_action_turns": 0},
  "latency_ms": 0,
  "exit_state": "ok|timeout|error",
  "error": null
}
```

- `final_text` and any error text MUST pass through the telemetry
  `sanitize_text()` (secrets never leave the process).
- Usage fields come from `loop._last_usage` where present; map what the
  provider returned (input/output/cache keys may vary by provider —
  normalize to the schema keys, zero-fill missing).

### A.4 Parity rules (non-negotiable)

1. **One-entry-point rule**: headless code only *calls* `CollieRuntime`
   methods (`_build_loop`, `_configure_provider_candidate`, `_chat`,
   `_conversation_target`). It never re-composes the loop, tools,
   prompts, permissions, or config. Review must enforce this.
2. **Consistency test**: assert both paths produce identical tool
   registries and rendered system prompts from the same settings.
3. **Phase-gate e2e through headless**: the existing
   `tests/collie/test_e2e_phase1..4.py` fake-OpenAI pattern must also run
   against the headless entry (same assertions) so drift fails CI.

### A.5 Tests (new file `tests/collie/test_headless.py`)

- `test_headless_runs_task_and_outputs_contract` — fake OpenAI endpoint
  (reuse the e2e patching pattern), assert: exit 0, JSON parses, all
  schema keys present, `exit_state == "ok"`, `final_text` non-empty,
  usage/calls are ints ≥ 0, prompt_hash/tool_schema_hash/config_hash are
  non-empty sha256 strings.
- `test_headless_timeout` — fake endpoint that sleeps past `--timeout`;
  assert exit 2, `exit_state == "timeout"`.
- `test_headless_missing_key` — no key in env; assert exit 3 and error
  JSON; assert the key never appears in stdout/stderr.
- `test_headless_parity_tool_registry` — build loop via runtime
  `_build_loop()` and via the headless path; assert identical registered
  tool names/schemas.
- `test_headless_isolated_home` — assert `collie_home()` points inside
  `--home` and the real user home is untouched (no rows in the default
  DB).
- Run the e2e phase gates through headless (see A.4.3).

---

## Part B — Prompt-hash telemetry

### B.1 DB changes

Run-record table (find it via `collie_core/telemetry/recorder.py` +
`collie_core/db.py` — the turn/tool record tables) gains three columns,
all **nullable with `None` default at the signature** (review lesson: any
non-None default on an UPDATE clobbers existing values via COALESCE):

- `prompt_hash TEXT NULL` — sha256 of the rendered system prompt(s) for
  the turn
- `tool_schema_hash TEXT NULL` — sha256 of the tool schemas presented to
  the model
- `config_hash TEXT NULL` — sha256 of model id + provider + generation
  settings + limits

Migration: additive `ALTER TABLE ... ADD COLUMN ... NULL` in the existing
migration path; old rows stay NULL. Add a migration test asserting old
rows read back with NULLs and existing upsert behavior is unchanged.

### B.2 Hash computation — new module `collie_core/telemetry/prompt_hashes.py`

Small pure functions (stdlib only, `hashlib.sha256` over stable JSON):

- `hash_system_prompt(system_messages: list[str]) -> str` — sha256 of the
  joined rendered system prompt text (the assembled system messages for
  the turn).
- `hash_tool_schema(schemas: list[dict]) -> str` — sha256 of
  `json.dumps(schemas, sort_keys=True, separators=(",", ":"))`.
- `hash_config(model: str, provider: str, generation: dict, limits: dict)
  -> str` — same stable-JSON approach.

**Where to capture the rendered system prompt:** the turn hook factory
(`collie_core/telemetry/hook.py`) sees turn events; find where the built
context/messages are available (pointers: `nanobot/agent/context.py`
`ContextBuilder` — `BOOTSTRAP_FILES`, `render_template`; the loop's turn
hooks receive the assembled context). Hash what the model actually
received, not a reconstruction. If the exact hook point is ambiguous,
prefer hashing at the point where system messages are final before the
provider call.

Tool schema hash: hash `loop.tools` schemas after `_build_loop()`
registration (the same schemas the provider receives).

Config hash: from the values in `build_config` output (model, provider,
maxToolIterations, generation settings, limits) — not raw SQLite rows.

### B.3 Hook integration

- Extend `RunRecorder.start_turn(...)` with the three hash kwargs
  (default `None`), stored in the turn record row.
- In the telemetry hook, compute/attach the hashes when available and
  enqueue the write through the existing fire-and-forget writer. Do NOT
  add a new writer or an awaited write.
- Hashes are not secrets and need no redaction, but the values fed to
  them (rendered prompts) must never be logged.

### B.4 Tests

- `test_prompt_hash_changes_with_template` — render `identity.md` +
  bootstrap files, hash; mutate a template copy; assert hash differs.
- `test_tool_schema_hash_stable` — same schema dict → same hash
  (sort_keys), different schema → different hash.
- `test_config_hash_stable` — same config → same hash.
- `test_run_record_has_hash_columns` — run a turn via the fake-OpenAI
  e2e path, assert the run record row has non-NULL prompt_hash /
  tool_schema_hash / config_hash.
- `test_old_rows_null_hashes` — pre-migration row reads back with NULLs.
- Run the full telemetry review checklist (see
  `collie-branch-review` skill → `references/telemetry-review-lessons.md`):
  None defaults, event-time timestamps, no awaited writes, sanitizer
  coverage for any new stored text.

---

## Acceptance criteria

1. `python -m collie_core.headless --task "..." --home <tmp> --model
   deepseek/deepseek-chat --api-key-env COLLIE_BENCH_KEY` runs a real
   turn against a fake endpoint and prints the full JSON contract with
   exit 0; timeout and missing-key paths behave per contract.
2. The headless path and the live IPC path register the same tools and
   render the same system prompt (consistency test green).
3. Run records contain non-NULL prompt/tool-schema/config hashes after a
   turn; pre-existing rows remain NULL; migration test green.
4. No user-facing CLI: `pyproject.toml` gains no `[project.scripts]`.
5. Secrets never appear in headless stdout/stderr/logs (sanitizer +
   env-only key).

## Gates (from `collie-core/`, venv at `collie-core/.venv`)

```bash
.venv/bin/python -m pytest tests/collie -q --ignore=tests/collie/test_pet_status.py
.venv/bin/python -m ruff check nanobot collie_core tests
.venv/bin/python -m pytest tests/collie --cov=nanobot --cov=collie_core --cov-report=term-missing -q --ignore=tests/collie/test_pet_status.py 2>&1 | tail -5
```

Coverage `fail_under = 70`. Healthy Linux baseline ≈ 475 passed / ~5
expected Windows-only failures / ~1 skipped — report the delta vs your
run.

## Documentation-impact check (docs/WORKFLOW.md)

- New interface (headless entry) → update `docs/PROJECT_MAP.md` runtime
  ownership (one line: headless engine mode under `collie_core/`) and
  `collie-core/AGENTS.md` (how to run headless + the parity rule).
- No VISION change (internal capability, not a product surface).
- Regenerate the snapshot:
  `collie-core/.venv/bin/python tools/update_project_snapshot.py` then
  `--check`.

## Pitfalls

- Never call `runtime.run()` in headless (starts IPC + schedulers).
- Set `COLLIE_HOME` before first `CollieDB()` import.
- `agent.max_tool_iterations` is clamped 1..2000 by `build_config` — a
  0/negative flag value must not reach the engine.
- The approval preset must go through `set_local_write_preset` /
  `PermissionEvaluator` — a bypass violates the product's central-gating
  invariant and will fail review.
- `loop._last_usage` is a dict and may be empty — zero-fill.
- Do not add `[project.scripts]`; `python -m` is the entry.
- Merge conflicts on `docs/generated/REPOSITORY_SNAPSHOT.md` with other
  open docs PRs are expected — resolve by re-running the snapshot tool.
