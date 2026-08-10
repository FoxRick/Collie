# Collie "Your things" panel

**Status:** backend shipped (PR #39); desktop UI follow-up
**Date:** 2026-08-10

## Objective

Non-coders have no file explorer to find what Collie makes for them. When
Collie finishes a deliverable — a flyer, a document, a spreadsheet, a PDF, a
web page — it lands in a right-side **"Your things"** panel in the desktop
app: open it again anytime, save a copy, or show it in the folder. Chat
stays the timeline; the panel is the home for what Collie makes.

Normie vocabulary is a hard rule: the word "artifact" is internal-only.
Users see **things** ("it's in Your things"), never file paths, extensions,
or filenames. Paths travel as data to the Electron main process for
Open / Save a copy… / Show in folder — never rendered.

## UX rules (design sketch, 2026-08-10)

- Default: panel hidden, no toggle button in a fresh chat (calm default).
- First thing of a chat auto-opens the panel once; a manual close is
  respected for the rest of that chat.
- Toggle: top-right of the chat header; appears only when the chat has
  things; orange badge count when closed with unseen items.
- Panel is per-chat: "This chat | All chats" filter (All = later milestone
  with a sidebar "Things" tab).
- Card: thumbnail/icon · human title · type · size · time · new-dot until
  viewed · hover Open + ⋮ (Open, Save a copy… → Documents, Show in folder,
  Add to chat, Remove from your things — soft delete, recoverable in chat).
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
  upsert-by-id. No SQLite.
- Runtime: `_consume_outbound` routes ArtifactEvent to the desktop as an
  IPC `artifact` broadcast; messengers keep the text fallback.

## Frontend (follow-up PR)

ipc `artifact` event in the CollieEvent union, `lib/things.ts` fold,
`ThingsPanel` / `ThingCard` / `ThingMenu` / `ThingsToggle` /
`ThingInlineCard` / `ThingPreview` components, ChatScreen `case 'artifact'`,
main-process handlers (`thing:open`, `thing:showInFolder`,
`thing:saveCopy`), hydration via a `things.list` IPC read, all copy in
`lib/locales/en.ts`.

## Deferred

File tree · diffs · sidebar Things tab · version history · embedded
doc/sheet viewers · messenger file attachments (text fallback only).
