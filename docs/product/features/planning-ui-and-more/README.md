# Planning UI and more

**Status:** accepted feature specification; implemented
**Date:** 2026-08-01

## Decision

Collie will automatically show a small, live checklist for genuinely multi-step
work. It will ask the user to review a plan before starting only when the work
is broad enough that the user should be able to correct its direction first.

This is an execution aid, not a display of hidden reasoning and not an extra
permission system. Existing per-action approvals remain authoritative.

The feature has two deliberately different objects:

1. **Task checklist** — a mutable, durable progress list for normal multi-step
   work. It can evolve as new facts are discovered.
2. **Execution plan** — the existing immutable, versioned `plans`/`runs`
   contract used when the user must review scope before execution. Its plan
   hash, approval, and per-action permissions remain unchanged.

Do not overload the approval-plan data model to implement ordinary to-dos.
Those two lifecycles have incompatible rules: a checklist should be editable
while a reviewed plan must be immutable and versioned.

## Product behaviour

### When Collie creates a checklist

The model creates one before it starts work when any of these are true:

- the task has three or more dependent, meaningful outcomes;
- the user supplied several requests;
- the task needs research followed by a decision and an action;
- scope discovered during the first read-only inspection expands beyond a
  simple task; or
- the user explicitly asks for a plan, a checklist, or progress updates.

It does not create one for a direct answer, a single safe action, or a task
that can honestly be completed in fewer than three trivial actions. Steps are
short, user-verifiable outcomes (normally 3–7), not tool calls and not a trace
of the model's private reasoning.

Exactly one item may be `in_progress`. A step can be `pending`,
`in_progress`, `completed`, `blocked`, `skipped`, or `failed`. A completed
step may include a concise result; a blocked/failed step must include the
reason and the next user decision, if one is needed.

### When Collie asks first

Collie presents a reviewable execution plan and pauses before non-read work
when the proposed work has **seven or more** meaningful steps, crosses **two
or more services/workspaces**, has a material commitment such as spending,
destructive change, broad communication, or irreversible migration, or when
the model cannot state a stable success criterion without a user choice.

The policy is based on scope and reversibility, not a guessed duration. A
single email does not need a plan review; a seven-step supplier-change project
does. Existing safety approvals still apply to each external or sensitive
action after the plan is approved.

The review card states the goal, assumptions, steps, connected services,
expected approvals, and success criteria. It offers **Change plan** and
**Looks good, continue**. Approving starts the existing immutable-plan run.
If scope changes materially, Collie creates a new plan version and asks again.

### What the user sees

During a task, show a compact strip just above the composer:

```text
▸ Compare available hotels                         2/5
```

The strip expands into an accessible card with all steps, their state, the
current result/error, elapsed time, and Stop. Completed items retain a check
mark until the run finishes. The final assistant message retains a collapsed
summary so history remains useful without leaving a permanent dashboard in the
chat.

The active checklist must be restored after a window refresh or core restart.
It belongs to its conversation; background routines and other conversations
must never replace it.

## Implementation status

The durable checklist, reviewed-plan, scoped snapshot, and live progress UI
are implemented. V9 supplies the core persistence foundation: message task
state, review gates, checklists and steps, per-run task-state revisions, and
plan-change requests. V10 adds `plan_change_requests.terminal_message_id`, a
database-backed exactly-once claim for persisting the terminal summary when a
safe-boundary plan supersession completes.

## Architecture

### 1. Durable checklist store

V9 persists the checklist rows in `collie_core/db.py`:

```sql
task_checklists (
  id, conversation_id, goal, status, revision, current_step_key,
  review_plan_id NULL, review_plan_version NULL,
  created_at, updated_at, completed_at
)
task_checklist_steps (
  id, checklist_id, step_key, ordinal, title, status,
  summary NULL, error_message NULL, started_at, finished_at,
  UNIQUE(checklist_id, step_key)
)
```

Use one active checklist per conversation. Updates are compare-and-set on its
`revision` so late tool events cannot overwrite a newer step state. Validate
the state machine in the database method: at most one active step, no
completed-to-pending regression, and no new updates after terminal checklist
status.

Keep existing `plans`, `runs`, and `run_steps` unchanged for reviewed plans.
For an approved plan run, project its authoritative `run_steps` into the same
UI `task_state` shape instead of duplicating rows: `task.id` is the run ID,
`task.source` is `plan_run`, steps are ordered by `run_steps.ordinal`, and the
snapshot revision is a monotonically increasing run-step update sequence. The
serializer derives counts, current step, terminal state, and concise per-step
result/error from the run and its steps; it never infers progress from tool
cursor order.

### 2. Model-facing tools and policy

Add a Collie-owned `manage_task_checklist` tool, backed by the store, with two
operations:

- `create`: goal plus 3–7 stable step keys/titles; returns the initial
  snapshot.
- `update`: checklist ID, step key, state, and optional concise user-facing
  summary/error; returns the full new snapshot.

The initial prompt contract tells the agent to create the checklist before
multi-step work, update it immediately before changing steps, and finish or
block it before ending a turn. It may revise *pending* checklist items after
new evidence, but must preserve completed history.

The prompt contract must also tell the agent to call `present_plan` instead of
`manage_task_checklist.create` when the review policy applies. In Plan mode,
the evaluator denies material and domain mutations, with only two narrow local
write exceptions: `plan.present` and internal `task.progress`. `task.progress`
records user-visible orchestration state only; it remains subject to an
explicit deny and the read-only specialist posture, and grants no authority
over files, services, settings, or other domain state. In Execute mode, add a
planning gate before non-read tools: a plan marked
`requires_review` cannot progress into material work until the approved plan
run starts. This is host enforcement, not a best-effort prompt instruction.

Do not add a separate classifier model call. The working model has to form a
plan anyway; the structured tool makes its decision observable. The host
validates list size and applies the review threshold deterministically.

### 3. Event contract and rehydration

Introduce one renderer-facing event, always a full snapshot:

```ts
{ type: 'task_state', conversation_id, task: {
  id, source: 'checklist' | 'plan_run', status, revision,
  title, completed_count, total_count, current_step_key,
  steps: [{ key, title, status, summary?, error_message? }]
}}
```

Send it after every create/update, after every `run_step_updated`, and when a
client opens/reconnects to a conversation. Add `get_active_task` IPC as a
read-through fallback. It accepts a required `conversation_id`, authorizes
that conversation before reading, returns `{ task: null }` when there is no
active checklist or plan run, and must never substitute a task from another
conversation. Retain the existing run events for Routines and audit views,
but do not ask the chat UI to reconstruct state by replaying deltas.

For reviewed execution, add an explicit `step_key` to the execution context.
The agent moves the step via the task tool; the server then updates the matching
`run_steps` row. Tool start/end only add tool name and short result to that
already-selected step. A tool error fails its current step, never the next one.

Selecting **Change plan** during an active reviewed run pauses further
material work at the next safe boundary; it does not mutate the approved plan
or reinterpret completed work. Collie creates a new immutable plan version
that records the changed scope and completed outcomes as context, then returns
to review. At that safe boundary, the existing run is cancelled with a
`plan_superseded` reason and retains its completed steps. Only approval of the
new version may start a new run; ordinary pending checklist edits do not use
this flow.

### 4. Renderer

Create `components/tasks/TaskProgress.tsx` and a small reducer/hook such as
`useTaskProgress.ts`. `ChatScreen.tsx` owns the per-conversation snapshot map
and consumes `task_state`; it clears neither the active task nor its history
when unrelated routine events arrive.

Render the compact strip while active and the expandable card on click. Use
semantic `<ol>`, status text for screen readers, `aria-live="polite"` only for
the current-step summary, and no per-second state updates outside the elapsed
label. Reuse the existing approval sheet for reviewed plans. Update
`PlanCard.tsx` to switch from proposal controls to the same progress component
once its run starts.

On app ready, conversation switch, and socket reconnection, request the
current snapshot after messages load. Ignore snapshots whose
`conversation_id` does not match the selected chat and snapshots with an older
revision than the one already held.

## Acceptance criteria

- A three-step task automatically shows accurate current/completed/pending
  work without asking for confirmation.
- A seven-step or materially broad task pauses with a reviewable plan before
  material execution and resumes only after explicit approval.
- A completed check means that exact planned outcome completed, not merely
  that a tool returned.
- Progress survives refresh/reconnect, is isolated per conversation, and is
  recoverable from SQLite.
- Stopping, failing, or blocking work is visible and truthful; pending steps
  are never silently marked complete.
- Plan review never bypasses an existing action approval.

## Research incorporated

- [OpenCode's published todo guidance](https://github.com/anomalyco/opencode/issues/4063)
  uses to-dos for three or more distinct actions and keeps one active item.
  Collie adopts that lightweight threshold, but not its coding-agent language.
- [OpenClaw's local Control UI implementation](../../../../source%20code%20competitors/openclaw/ui/src/pages/chat/components/chat-plan-checklist.ts)
  uses a run-owned full plan snapshot, one active step, a compact count, and
  an expandable detailed card. Its task runtime also persists progress. This
  directly informs Collie's snapshot contract and UI.
- [OpenAI Codex's app-server item protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
  treats plans and tool calls as typed live items with authoritative terminal
  states. Its [base instruction](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md)
  requires short plans and exactly one in-progress item. Collie adopts the
  event discipline while retaining its consumer-oriented approval model.

## Non-goals for v1

- Showing chain-of-thought, raw tool payloads, or an engineering-only task
  debugger in the primary chat.
- Replacing Routines, subagent monitoring, or the existing safety approvals.
- Letting users manually edit a plan's immutable execution steps after
  approval; revision creates a new version instead.
