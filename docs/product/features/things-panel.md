# Collie "Your things" panel

**Status:** shipped (backend PR #39; desktop UI PR #40)
**Date:** 2026-08-11

## Objective

Non-coders have no file explorer to find what Collie makes for them. When
Collie finishes a deliverable — a flyer, a document, a spreadsheet, a PDF, a
web page — it lands in a right-side **"Your things"** panel in the desktop
app: open it again anytime, save a copy, or show it in the folder. Chat
stays the timeline; the panel is the home for what Collie makes.

Normie vocabulary is a hard rule: the word "artifact" is internal-only.
Users see **things** ("it's in Your things"), never file paths, extensions,
or filenames. The renderer never handles filesystem paths: it sends
`(conversation_id, thing_id)` over IPC and the Electron main process resolves
the path from the trusted on-disk index (see Frontend below).

## UX rules (design sketch, 2026-08-10)

- Default: panel hidden, no toggle button in a fresh chat (calm default).
- First thing of a chat auto-opens the panel once; a manual close is
  respected for the rest of that chat.
- Toggle: top-right of the chat header; appears only when the chat has
  things; orange badge count when closed with unseen items.
- Panel is per-chat: "This chat | All chats" filter (All = later milestone
  with a sidebar "Things" tab).
- Card: thumbnail/icon · human title · type · size · time · new-dot until
  viewed · hover Open + ⋮ (Open, Save a copy… → Documents, Show in folder).
- Deliverables only: the model declares "this is a deliverable" by calling
  the `save_thing` tool. Temporary working files never appear.
- Revisions replace the card; version history = later milestone.

## Backend (shipped, PR #39)

- `ArtifactEvent` (nanobot/bus/outbound_events.py) — typed outbound event
  (id, title, kind, file_path, size_bytes, created_at, status, version)
  with a normie text fallback (`📎 Made: <title> · Open`) so messenger
  channels render it with zero channel-specific code.
- `save_thing` tool (collie_core/tools/artifacts.py) — model registers a
  finished deliverable. Validates title (≤120 chars), existing file,
  extension↔kind, workspace scope (local_files resolver) + carve-out for
  assistant-generated media. approval_free (metadata-only, reversible,
  nothing leaves the device).
- `ThingStore` (collie_core/things/store.py) — JSON-per-conversation index
  under `~/.collie/things/<conv>.json`; atomic writes, newest-first,
  upsert-by-id, per-conversation `delete` (metadata only — user files stay).
  No SQLite.
- Runtime: `_consume_outbound` routes ArtifactEvent to the desktop as an
  IPC `artifact` broadcast; messengers keep the text fallback.

## Frontend (shipped, PR #40)

- `ThingPanel` / `ThingCard` / `ThingPreview` / `ThingsToggle` /
  `thingMeta` components, ChatScreen `case 'artifact'`, hydration via the
  `list_things` IPC read, all copy in `lib/locales/en.ts`.
- Main-process handlers (`collie:thing-open`, `collie:thing-show-in-folder`,
  `collie:thing-save-copy`, `collie:thing-read`) in
  `src/main/things-files.ts` — **trusted-ID boundary**: the renderer sends
  only `(conversation_id, thing_id)`; the main process resolves the path from
  the ThingStore index under `COLLIE_HOME/things/` and rejects unknown
  conversations/things. Preview is authorized by the registered record, so
  deliverables saved from user-approved project folders preview like
  workspace-made ones. Deleting a conversation removes its index; the
  deliverables stay on disk.

## Deferred

File tree · diffs · sidebar Things tab · version history · embedded
doc/sheet viewers · messenger file attachments (text fallback only) ·
"Add to chat" · recoverable "Remove from your things" (soft delete).
