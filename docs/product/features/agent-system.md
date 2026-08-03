# Collie, Agent Update

**Status:** active product direction
**Date:** 2026-08-01

## Direction

Collie should be a chat-first app for nontechnical users. Nearly every safe action available in menus should also be possible through chat, including:

- Adding or changing models.
- Connecting and managing connectors.
- Changing safe settings.
- Creating and managing routines.
- Creating and managing agents and skills.

These actions do not need a separate Setup agent. They should be approved chat tools using the same underlying functions as the graphical interface.

## Hidden Collie capabilities

### Collie Guide

Answers questions about Collie, knows which features are currently available, and routes users to the correct action without requiring them to search menus.

Guide answers must be driven by one verified feature-availability registry,
not static prompt claims. It may open the relevant safe UI or invoke the same
approved chat tool as that UI; it must say when a feature is unavailable,
requires owner setup, or needs a separate approval.

### Collie Doctor

Provides offline-capable diagnostics and safe recovery where possible. It can check the core process, internet access, model configuration, credentials, connector status, and local data health.

Doctor ships in two explicitly separate capabilities:

1. **Read-only diagnosis** reports checks, evidence, severity, and a plain-language next action. It must redact secrets and distinguish a local failure from an unavailable external provider.
2. **Safe repair** is introduced only after diagnosis. Each repair is a narrow allowlisted operation with a preview, approval when it changes state, result logging, and a recovery path. It may restart a local component or rebuild a derived local index when designed and tested for that operation. It must not silently alter credentials, permissions, personal memory, provider settings, or repeat external sends, payments, deletion, or publication.

### Collie Gardener

Improves Collie gradually using evidence from real use. It can:

- Consolidate duplicate or outdated memory.
- Maintain project and folder maps as files change.
- Extract decisions, terminology, and unfinished work from conversations.
- Suggest shorter and clearer agent instructions.
- Identify skills that repeatedly fail or need better steps.
- Recommend project-specific skills after repeated workflows are observed.

The improvement process should be:

1. Observe several real interactions.
2. Identify a specific improvement.
3. Show a before-and-after diff.
4. Replay the proposed change in a sandbox against representative previous
   tasks, without live connectors, credentials, external writes, or mutation
   of the user's active memory/instructions.
5. Apply meaningful behavioural changes only after approval.
6. Keep the previous version for rollback.

Automatic maintenance may update indexes, compact sessions, remove duplicate context, and record project changes. Additive memory writes (new facts, people, and dates) may also be automatic when they are logged and reversible, with a visible recent-activity trail the owner can inspect. Rewriting agents or skills, changing or deleting personal memory, or changing settings and permissions should require approval.

The Gardener should improve Collie in response to observed problems and repeated patterns, not rewrite things merely because a timer fired.

Every approved Gardener change keeps a versioned before-state, diff, sandbox
replay evidence, and a one-action rollback. Rollback restores the previous
artifact and does not itself re-run historical actions or overwrite newer
owner edits.

## Acceptance criteria

- Guide gives availability and setup answers that agree with the verified
  registry, and never claims a catalog entry is connected without live proof.
- Doctor can complete an offline read-only diagnosis without exposing secrets
  or making state changes, and clearly identifies checks it could not run.
- Each repair is rejected unless it is allowlisted; state-changing repair
  shows its scope and receives the required approval before it starts.
- Doctor never retries a consequential external action automatically.
- Gardener produces a reviewable diff and sandbox replay result before a
  behavioural, memory, instruction, setting, or permission change.
- An owner can roll back an approved Gardener change in one action without
  overwriting subsequent owner changes or replaying external work.
