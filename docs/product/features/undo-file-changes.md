# Undo file changes

**Status:** shipped (alpha, 2026-08-10) — one-tap undo for local file changes
**Date:** 2026-08-10
**Applies to:** Chat file work (LocalFilesTool writes)

## Outcome

When Collie changes a local file for you, you can take it back with one tap.
Every write in a conversation is journaled first: a safety copy of the
pre-write state is stored under the Collie home directory, and the assistant
message for that turn carries a **files changed** card with a "Take it back"
button. Pressing it restores the previous bytes — or removes a file Collie
created. Strictly scoped to files Collie itself changed; nothing external
(emails, calendar invites, posts) is ever presented as one-tap undoable.

This extends the approval story: *plan → approve → act → review → undo*.
The undo surface supersedes the earlier parked "undo pill" idea (user
re-requested 2026-08-10, scoped to file changes only).

## How it works

1. **Journal before write** — `LocalFilesTool` snapshots the target file
   (copy-before-write) via `collie_core/undo/journal.py` before every
   `create` / `overwrite` / `edit` / `save`. Created files record a
   no-bytes entry (`existed=False`); undo removes them.
2. **Card per turn** — tool results carrying `undo_entry_id` are collected
   in `CollieIPCServer._run_chat_turn`; at turn end a `files_changed` card
   (files + `conversation_id`) is attached to the assistant message. It takes
   precedence over info cards when the turn changed files.
3. **One-tap restore** — `FilesChangedCard` renders the list with a "Take it
   back" button → IPC `undo_file_changes` → `undo_entries()` restores the
   shadow bytes (atomic replace) or unlinks created files, and consumes the
   entries.

## Storage

```
~/.collie/undo/<conversation_id>/manifest.json   # entries, newest first
~/.collie/undo/<conversation_id>/<id>.orig       # pre-write snapshot bytes
```

- Entries expire after 7 days (lazy sweep on the next write).
- Files over 1 MB are not journaled (writes still work; no undo entry).
- No conversation id in scope (non-chat contexts) → writes work, no journal.

## Scope boundaries

- **Undoable:** local text artifacts changed via `LocalFilesTool`.
- **Not undoable (by design):** anything with external side effects (sent
  messages, calendar events, connector writes) — no fake undo buttons.
- UI-side workspace writes (`_cmd_write_file`, the user's own editor) are the
  user's edits, not journaled.

## Design decisions

- **Files only** (user direction 2026-08-10): the one-tap undo promise must
  be 100% honest; external actions can't be reliably taken back.
- **No confirmation dialog:** undo is the safe action — instant, reversible
  (the restored state is itself journaled as the new "current" state going
  forward only if the user edits again).
- **Approval-free IPC:** restoring files Collie itself changed is
  reversible + low blast radius, matching the approval philosophy
  (gate by blast radius + reversibility, not category).
