# Collie MVP Agent and Skill Runtime

**Status:** Accepted implementation direction
**Date:** 2026-07-28
**Applies to:** Alpha and early beta

## Decision

Collie will keep nanobot's simple execution model:

> One primary Collie agent loop, optional focused subagents, progressively
> loaded skills, deterministic permissions, and ordinary tools.

We will not build a supervisor hierarchy, agent graph, debate system, or a
second orchestration framework for the MVP.

The normal user request must require only one model-driven loop. Extra model
calls are allowed only when the task benefits from a specialist, explicit
review, or background context maintenance.

## Product model

The user interacts with **Collie**. Collie can complete simple work directly
or ask one of four built-in specialists for help:

| Agent | Use it for | Alpha permission posture |
|---|---|---|
| Researcher | Web and connected-source research with citations | Read-only |
| Analyst | Calculations, comparisons, tables, spreadsheets, and findings | Read-only inputs; returns analysis in chat |
| Reviewer | Independent checks for accuracy, omissions, tone, and requirement coverage | Read-only |
| Operator | Multi-step actions in connected services | Inherits Execute mode; every mutation still passes through approvals |

These are capability presets on the existing subagent runner. They are not
separate services and do not talk freely to one another.

For alpha:

- Collie decides whether one or more specialists are useful.
- Users can also invoke a specialist by name.
- Run at most three independent specialists at a time.
- Researcher, Analyst, and Reviewer may run in parallel.
- Operator is exclusive: do not run multiple Operators or run an Operator
  alongside another mutating specialist.
- Do not automatically run Researcher, then Analyst, then Reviewer.
- A specialist returns a focused result to Collie; Collie remains responsible
  for the user-facing answer.
- Researcher, Analyst, and Reviewer must never gain write access merely
  because the parent conversation is in Execute mode.
- Operator may request actions but can never approve its own action.

The four bundled specialists are editable Markdown files seeded once on first
initialization. User edits and deletions win: upgrades never overwrite an
edited file or silently restore a deleted one. User-created agents default to
read-only.

User-created agents remain supported as Markdown prompt presets. Advanced
per-agent models, arbitrary permission editors, and agent-to-agent graphs are
deferred.

## What is always on

The always-on layer should be deterministic code wherever possible.

### Keep and finish for alpha

1. **Plan/Execute enforcement**
   - Plan is read-only at the permission layer.
   - New conversations default to Execute; Execute permits tool calls subject
     to approval rules.
   - Plan and Execute are modes, not extra LLM agents.

2. **Central permission authorizer**
   - Every local tool, MCP tool, routine, and subagent action uses the same
     authorizer.
   - Child permissions are never broader than the parent run.
   - Hard-approval actions always require a fresh approval.
   - Automatic approval is an explicit per-operation property, never an
     inference from a coarse local-write risk. Prompt-free operations must be
     reversible local work; `Approve for me` and task-wide approval require a
     separate explicit eligibility check at both grant and use time.
   - Financial, external-write/send/publish, destructive, sensitive,
     connector/credential, reusable-capability, provider/settings,
     schedule/routine, and unknown MCP operations cannot gain automatic or
     task-wide authority.

3. **Local-file scope is distinct from authority**
   - A turn may name the selected project folder, chosen local folders, or
     confirmed full local-file access for the bounded `local_files` tool.
   - This is not generic workspace or network access: `access_mode` stays
     restricted, and a file scope grants no external, connector, send, payment,
     publish, account, destructive, or routine authority.
   - Validate and canonicalize roots at the IPC boundary and in the file tool;
     subagents inherit, but cannot broaden, the parent scope. Full local-file
     access is session-only.

4. **Approval broker**
   - Pause before mutation.
   - Handle allow, reject, timeout, cancellation, shutdown, and connection
     loss safely.
   - An approved action executes once, not twice.

5. **Context governance**
   - Preserve nanobot's history limits, consolidation, tool-result offloading,
     and in-flight compaction.
   - Keep only compact, universal instructions in the always-loaded prompt.

6. **Untrusted-content guard**
   - Continue labeling web and retrieved content as data rather than
     instructions.
   - Retrieved content can never alter permissions or system policy.

7. **Run record and usage telemetry**
   - Record tools, approvals, results, failures, latency, and token use.
   - Keep technical token controls out of the normal consumer UI.

8. **Simple recovery limits**
   - Stop repeated identical actions.
   - Stop when the tool or model budget is exhausted.
   - Report a concrete partial result or blocker.

9. **In-chat model switching (`/model` + `set_model`)**
   - `/model` shows the current model and configured providers; `/model <id>`
     switches. The agent can also switch via the `set_model` tool.
   - The change persists `provider.model` and applies live through the loop's
     runtime resolver (`select_model`) — future turns use the new model, no
     loop rebuild, no interrupted turn.
   - Model changes are reversible local writes but stay approval-gated: they
     mutate provider settings, so they never gain automatic or task-wide
     authority.

### Do not turn these into hidden LLM agents

- Permission checking
- Approval decisions
- Plan-mode enforcement
- Tool-call classification
- Audit logging
- File or external-object existence checks
- Timeout and cancellation handling
- Loop detection
- Token, iteration, and time budgets

Safety must not depend on a model deciding to follow a safety prompt.

## Verification policy

There will not be an always-on LLM Reviewer.

Verification happens in this order:

1. **Deterministic checks first**
   - Tool call returned success.
   - Expected file or external object exists.
   - Required fields are present.
   - A calculation or schema validates.
   - No plan step is still pending.

2. **Reviewer only when useful**
   - The user asks for review.
   - The output will be published or sent externally.
   - The task contains important calculations or many factual claims.
   - The artifact is long or has several explicit requirements.
   - Execution recovered from a partial failure.
   - The domain is consequential enough to justify a second pass.

3. **No review for routine chat**
   - Rewriting a sentence, answering a basic question, or creating a reminder
     should not trigger another model call.

Reviewer feedback should be returned to Collie as a short list of actionable
issues. Alpha does not need recursive review/rewrite loops. At most one review
pass is sufficient.

## Skills for alpha

Skills are procedures, not characters. Full skill instructions are loaded only
when relevant.

Ship a small, reliable starter set:

1. **Research brief**
   - Search more than one source when available.
   - Separate evidence from inference.
   - Include source links.

2. **Summarize and extract**
   - Summarize documents, pages, transcripts, and threads.
   - Extract decisions, dates, risks, and action items.

3. **Write and rewrite**
   - Draft, shorten, clarify, proofread, and change tone while preserving the
     user's meaning.

4. **Compare and decide**
   - Produce a compact comparison, state assumptions and trade-offs, and give
     a recommendation when requested.

5. **Meeting notes**
   - Produce a summary, decisions, owners, action items, and follow-up draft.

Use existing document, spreadsheet, presentation, email, and calendar tools
when they are connected and working. Do not advertise a skill as ready when
its required connector is unavailable.

Startup-specific procedures such as competitor research, customer interview
synthesis, positioning, PRDs, pitch decks, and unit economics can be added as
one later **Startup Pack**. They do not require new agent types.

## Token and latency rules

The MVP should have understandable cost behavior:

- Simple request: one Collie loop, no subagent, no Reviewer.
- Specialist request: Collie plus one focused subagent.
- Reviewed request: Collie plus one Reviewer pass.
- Do not run multiple candidate agents and vote.
- Do not run an LLM router before every request.
- Do not inject every skill body into every prompt.
- Do not expose disconnected tools.
- Keep specialist prompts short and pass only the task context they require.
- Return summaries from subagents, not raw tool traces.

Initial runtime limits:

- Three concurrent read-only subagents for alpha.
- One Operator at a time.
- A conservative per-turn tool-iteration limit, substantially below the
  upstream emergency ceiling of 200.
- Separate wall-time and token budgets for the main turn and subagent.
- One semantic review pass maximum.

Choose final numeric limits from measured alpha traces. Start conservatively,
then raise a limit only when real tasks demonstrate the need.

The current skill-name-and-description catalog is adequate while the catalog
is small. Add semantic skill retrieval only when the installed catalog becomes
large enough to create measurable prompt cost or routing errors.

Likewise, add dynamic MCP tool selection only when connected services expose
enough tool schemas to cause measurable prompt bloat. Do not build a vector
retrieval service preemptively.

## What to implement now

1. Replace the consumer-oriented starter-agent catalog with:
   - Researcher
   - Analyst
   - Reviewer
   - Operator

2. Give built-in agent definitions a minimal execution posture:
   - `read_only` for Researcher, Analyst, and Reviewer.
   - `inherit` for Operator.
   - Enforce this in the authorizer context, not only in the prompt.

3. Reuse the existing `SubagentManager`, `call_subagent` tool, result
   injection, activity UI, and approval broker.

4. Limit alpha to three running specialists per conversation and make
   Operator exclusive.

5. Add the five alpha skills above using the existing `SKILL.md` loader and
   progressive-disclosure behavior.

6. Add small deterministic completion checks around artifact-producing and
   external mutation tools.

7. Add a selective Reviewer invocation rule. Keep manual invocation available.

8. Reduce the practical iteration ceiling and add repeated-action, wall-time,
   and token-budget termination.

9. Test the following end-to-end:
   - Direct chat completes without a subagent.
   - Researcher returns cited research without writes.
   - Analyst returns a correct comparison or calculation without mutation.
   - Reviewer finds a seeded omission without modifying the artifact.
   - Operator requests approval and performs exactly one approved mutation.
   - Rejected actions perform no mutation.
- A subagent cannot bypass Plan mode or parent permissions.
- Three read-only specialists can complete independent work in parallel.
- A second Operator or parallel mutating specialist is rejected.
   - A failed or timed-out specialist returns control to Collie cleanly.

## What not to implement for alpha

- A separate LLM intent-router call
- A supervisor or orchestrator agent
- Automatic multi-agent chains
- Agent debate, voting, or candidate swarms
- An always-on Reviewer
- Recursive self-reflection loops
- A visual agent graph builder
- Agent-to-agent free conversation
- Per-agent model selection UI
- Arbitrary user-authored permission DSL
- Vector search for a small skill catalog
- Vector search for a small tool catalog
- An agent or skill marketplace
- Remote skill installation
- Automatic self-editing skills
- Automatic memory of everything
- Detailed consumer-facing token controls

These can be reconsidered only after observed alpha failures identify a
specific problem that the simpler design cannot solve.

## Alpha success criteria

The architecture is ready for alpha when:

- Ordinary chat still feels as fast and direct as the current nanobot loop.
- The four specialists are understandable and invoke reliably.
- Most requests use no specialist.
- Read-only specialists cannot mutate state.
- Operator cannot bypass approval.
- Skills load on demand and do not bloat every prompt.
- Cancellation, rejection, timeout, and restart leave consistent state.
- Activity history explains what happened without exposing internal
  chain-of-thought.
- Token and latency traces are recorded so beta decisions use evidence.

## Simplicity test for future proposals

Before adding another agent, hidden model call, middleware layer, or retrieval
system, answer:

1. What observed user failure does it solve?
2. Can the existing Collie loop plus a skill solve it?
3. Can deterministic code solve it more safely?
4. Does it add a model call to ordinary requests?
5. Can it bypass or complicate the central authorizer?
6. How will we test that it improves completion rate enough to justify its
   token, latency, and failure cost?

If these questions do not have concrete answers, do not add it.
