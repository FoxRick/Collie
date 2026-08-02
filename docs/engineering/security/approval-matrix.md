# Collie approval matrix

**Implementation review:** 2026-08-01
**Scope:** agent tool calls, approval settings, local-file access, and their renderer-to-runtime transport.

This is the current implementation overview. Code and focused tests remain the source of truth for a particular operation.

## Product policy now implemented

Collie starts new chats in **Execute** mode. **Plan** remains strictly read-only. In Execute, the permission system makes an explicit per-operation decision immediately before a tool runs:

- Read-only operations are allowed, subject to project and network safety checks.
- Explicitly classified, reversible local personal actions are prompt-free. This includes ordinary notes, one-time reminders, non-destructive shopping-list work, image creation, local calendar/contact updates, one-time goals, and starting a focused subagent.
- Other explicitly eligible local work can use the user's **Approve for me** setting or an approved-for-this-task rule. **Ask me** is the conservative default and presents a normal approval card for that work.
- A broad setting or task rule is never inferred just from `LOCAL_WRITE`. Each operation must opt in, and a task-wide rule is checked again when it is used.
- Financial transactions, sends, publishing/external writes, destructive actions, sensitive work, credentials/connectors, reusable capabilities, schedules/routines, provider/settings changes, and unknown MCP work still require their own approval path. No saved rule or task-wide approval bypasses a hard action.

`Approve for me` does not grant external, recurring, account, payment, publication, send, or destructive authority. It is a convenience setting for the narrow local operations that explicitly declare themselves eligible.

## Approval decision order

The evaluator in [`collie-core/collie_core/permissions/evaluator.py`](../../../collie-core/collie_core/permissions/evaluator.py) applies these protections in order:

1. A parent denial and the `read_only` execution posture deny any escalation.
2. Explicit deny rules deny the operation.
3. A review gate still protects consequential work, but does not block an explicitly safe ordinary action or an operation eligible for the user's local approval preference. A plan can still be presented to clear the gate.
4. Plan mode denies non-read work (apart from the narrowly required plan and progress handling).
5. Hard, sensitive, and destructive operations require a fresh approval.
6. A saved or run-wide rule only applies when the current operation is explicitly eligible for it.
7. Read-only work is allowed; then `Approve for me` applies only to eligible local work; then an explicitly `approval_free` reversible local operation is allowed. Everything else asks.

An approval request waits for an explicit one-time or task-wide decision; dismissal, timeout, cancellation, or denial prevents execution. Sensitive tool parameters are redacted before display and storage.

## Everyday actions and hard boundaries

| Area | Current behavior |
|---|---|
| Notes | Ordinary local note actions are prompt-free. Connected-service actions remain separately classified. |
| Reminders | Create, list, complete, and snooze a non-recurring reminder are prompt-free. Recurring work stays protected; `delete` is classified as `delete.destructive`. |
| Shopping | Add, list, check, and uncheck are prompt-free. `remove` and `clear_checked` are destructive and require fresh approval. |
| Image creation | Prompt-free as an explicit reversible local artifact action. Provider/data-handling constraints remain separate from approval convenience. |
| Calendar and contacts | Explicit ordinary local operations are prompt-free. Connected external service mutations retain connector/MCP approval. |
| Goals and focused subagents | One-time goals and `call_subagent` are ordinary eligible local operations. Reusable agents and skills remain hard capability changes. |
| Routines and cron | They are ineligible for automatic or task-wide approval and retain their existing explicit lifecycle and plan gates because they create recurring authority. |
| Sending messages/email, publishing, money, deletion | Always fresh approval. Email list/read/search remain read operations, and an email draft is an ordinary local action that asks rather than sends. |

The classifier inspects multi-action wrapper parameters as well as tool names, so reminder deletion and shopping removal/clear operations cannot be mistaken for ordinary housekeeping.

## Local text-file work

`local_files` is the agent-facing tool for everyday document work. It can list folders; read small UTF-8 text files; and create, save, overwrite, or make one exact edit to supported text artifacts. It does not offer shell access, file deletion, network paths, device paths, symlink/junction hops, Word/PDF binaries, or unrestricted binary handling. Replacements are atomic; read hashes can protect a subsequent overwrite from a stale read.

Reading text may send its contents to the configured model provider to answer the request; the tool says so in its permission metadata. Writing remains local-only. Folder choice is a boundary, not automatic permission to write: in **Ask me** mode an eligible bounded write prompts, while **Approve for me** can continue an explicitly eligible local write.

The chat composer exposes one compact **Files** selector that combines the
conversation project folder with the local-file boundary:

| Choice | Local-file boundary |
|---|---|
| **Project folder only** | The conversation project folder only. This is the default. Choosing General Chat clears the previous conversation project scope. |
| **Choose other folders…** | One to sixteen user-selected, canonical local folders. |
| **Full file access** | Local text files anywhere on the machine except network/device paths, drive roots, and reparse-point paths. It requires an explicit confirmation and is not persisted across app restarts. |

This combined control does not merge the underlying security concepts. Product
file access keeps the generic workspace `access_mode` restricted, including
Full file access. It therefore does not relax loopback/network protections or
grant external authority. The selected scope is carried with the current turn,
validated again in the core, and inherited by subagents; a child cannot broaden
it.

## Opening files with the default app

`open_file` opens an existing local file or folder with the operating system's
default handler (a document/image/audio/video viewer, the browser, or the file
explorer). It exists so Collie can "show" the user the artifacts it creates,
without shell access or arbitrary app launching.

- It shares `local_files`' exact scope and path-safety boundary: the same
  canonical resolution (workspace/project roots, symlink/junction refusal,
  UNC/device refusal) is reused, so the two tools can never disagree about what
  a safe local target is.
- Only an allowlisted set of harmless types can be opened — documents and data
  (`.md .txt .pdf .docx .xlsx .pptx .csv .rtf .html .json .xml .yaml .log …`),
  images, audio, and video. A separate denylist (`.exe .bat .cmd .ps1 .vbs
  .msi .lnk .url .reg .scr .jar …`) is defense in depth so a future allowlist
  edit can never hand an executable or shortcut to a default handler.
- It is classified `Risk.READ` with no `data_leaving_device`: the file opens in
  a local app and nothing is sent to any provider. It is reversible (close the
  window), never writes, and can only target files that already exist. Within
  the approved folders, a read-only open is allowed without an approval card;
  anything outside the allowed folders is refused outright by the tool, and the
  launch is revalidated at execution time, not just at card time.

## Interface and ownership map

| Responsibility | Primary implementation |
|---|---|
| Request fields and hard-action defaults | [`permissions/models.py`](../../../collie-core/collie_core/permissions/models.py), [`permissions/defaults.py`](../../../collie-core/collie_core/permissions/defaults.py) |
| Classification and evaluation | [`permissions/classifier.py`](../../../collie-core/collie_core/permissions/classifier.py), [`permissions/evaluator.py`](../../../collie-core/collie_core/permissions/evaluator.py) |
| Approval cards and task-wide rule validation | [`permissions/broker.py`](../../../collie-core/collie_core/permissions/broker.py), [`ApprovalSheet.tsx`](../../../collie-ui/src/renderer/src/components/approvals/ApprovalSheet.tsx) |
| Local text-file tool | [`tools/local_files.py`](../../../collie-core/collie_core/tools/local_files.py) |
| Default-app opener tool | [`tools/open_file.py`](../../../collie-core/collie_core/tools/open_file.py) |
| File-access scope validation and inheritance | [`nanobot/security/workspace_access.py`](../../../collie-core/nanobot/security/workspace_access.py) |
| Chat transport and composer controls | [`ipc/server.py`](../../../collie-core/collie_core/ipc/server.py), [`ChatScreen.tsx`](../../../collie-ui/src/renderer/src/screens/ChatScreen.tsx), [`ChatInput.tsx`](../../../collie-ui/src/renderer/src/components/ChatInput.tsx) |

Electron settings and system dialogs have their own explicitly invoked UI flows; they do not become agent authority merely because an approval preset or file scope is selected. Direct UI operations should continue to be audited at the narrow IPC boundary when their behavior changes.
