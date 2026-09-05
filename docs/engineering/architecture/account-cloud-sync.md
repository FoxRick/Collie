# Account Cloud Sync — per-device snapshots, opt-in

> Status: **spec** (2026-08-22) · Owner: Rick · Scope: Supabase + Electron app
> Amends `account-system-spec.md` §1: the v1 non-goal "no cross-device sync of
> conversations/settings" is narrowed — **identity-level data** (memory,
> About Me, personality, settings mirror) may sync, strictly opt-in. The
> local-first story is unchanged: nothing uploads unless the user flips the
> toggle, and turning it off stops all uploads (local data is never deleted).

## 1. Product rules (the promises we make users)

1. **Opt-in only.** Sync is OFF by default. The toggle lives in
   Settings → Account, next to the sign-in state, in plain language.
2. **Per-device snapshots.** Each computer uploads its own snapshot under its
   own device name. There is always exactly one "latest version" per computer
   online. Restoring is a **choice**, never automatic: the user sees their
   devices' snapshots and picks which one to take.
3. **Local is never clobbered silently.** A restore writes through the same
   versioned paths the app already uses (artifact versioning, undo journal) —
   a restore is undoable like any other change.
4. **Nothing sensitive leaves.** API keys, connector tokens, conversations,
   plans, approvals never sync. Only the items in §3.
5. **Sign out = sync stops.** Local data stays. Online snapshots stay until
   account deletion removes them.

## 2. Why per-device snapshots (not live row sync)

- **Understandable to non-coders.** "This computer's version from yesterday"
  beats conflict resolution UI. No merge dialogs, no last-write-wins surprises.
- **Bounded scope.** No change-detection plumbing, no background daemon, no
  battery/CPU cost. Upload happens only on explicit "Back up now" and on
  sign-in (if enabled). Restore happens only on explicit "Restore".
- **Matches the 2-computer story.** The user's mental model: each Collie
  keeps its own copy online; a new computer borrows one.

## 3. What syncs

| Item | Source (local) | Online shape |
|---|---|---|
| Profile memory (facts) | `profile` table | JSON map |
| People | `people` table | JSON array |
| Important dates | `important_dates` table | JSON array |
| About Me (AGENTS.md) | workspace file | text |
| Personality (VISION.md) | workspace file | text |

**Settings mirror: deliberately cut from v1.** The core intentionally blocks
shell writes to most settings keys (`_IPC_SETTABLE_SETTINGS` defense-in-depth),
and a synced provider/model without its OS-keychain API key is useless on a
fresh machine anyway. Provider setup stays per-machine; revisit in v2 if the
core exposes safe setters.

Conversations, plans, approvals, automations, connectors, messengers:
**never sync** (v2 question at the earliest).

## 4. Data model (Supabase, existing project)

```sql
create table if not exists public.user_sync_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null,            -- stable random id, generated per install
  device_name text not null,          -- user-readable, e.g. "Rick's laptop"
  payload jsonb not null,             -- §3 shape
  created_at timestamptz not null default now(),
  -- Maker liveness columns (see §4b): PATCHed by the heartbeat, never in the
  -- snapshot payload. `payload` stays NOT NULL with no default — the heartbeat
  -- therefore PATCHes, it does NOT merge-upsert (a partial upsert would 400).
  last_seen timestamptz,              -- last heartbeat ("live" = within ~10m)
  version text,                       -- app version at heartbeat
  platform text                       -- process.platform (win32/darwin/linux)
);
create index if not exists user_sync_snapshots_user_idx
  on public.user_sync_snapshots (user_id, created_at desc);

alter table public.user_sync_snapshots enable row level security;

create policy "own rows only"
  on public.user_sync_snapshots
  for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
```

### §4b. Maker liveness view (owner-only, not client-facing)

Install presence is now independent of cloud sync and accounts. See
[install heartbeat](install-heartbeat.md) for the anonymous write API,
count definitions, privacy disclosure, and deployment order. Older clients
PATCH the legacy presence columns on their existing snapshot; that only
covers signed-in users with sync enabled and is not an installation count.

- One row per (user, device): the app **upserts** on `(user_id, device_id)`.
- RLS: every operation scoped to `auth.uid()`; anon key only, no service role
  on any client. A second user's rows are invisible, full stop.
- Account deletion (`delete_my_account()`) cascades here via the FK.

## 5. App flow (Electron main process, `cloud-sync.ts`)

**Upload ("Back up now" + auto on sign-in when enabled):**
1. Gather §3 via the existing core IPC (`get_profile`, people/dates lists,
   `read_file` for the two MDs).
2. `POST /rest/v1/user_sync_snapshots` with `Prefer: resolution=merge-duplicates`
   on the conflict target `(user_id, device_id)` — the device's single latest
   snapshot, per the product rule.
3. Show plain-language result. Failures never block sign-in or app usage.

**Restore (explicit pick, never automatic):**
1. `GET` the user's snapshots (RLS scopes them), show device name + time.
2. On pick: write through existing versioned paths — `write_file` for the MDs
   (versioned + undoable), core IPC for profile/people/dates.
3. Confirm dialog states what will be replaced, in plain language.

**Toggle:** stored locally (`settings` key `account.sync_enabled`, default
off). Turning it on uploads the first snapshot before the enabled state is
stored, so a failed baseline leaves sync off. Toggle transitions are ordered,
and a newer request supersedes an in-flight older one before it can persist
stale state. Turning it off stops uploads; online copies remain until account
deletion.

**Device identity:** `device_id` = random UUID persisted in userData on first
run; `device_name` = `<username>'s <os>` at first upload, editable later (v2).

## 6. Security checklist

- [x] RLS own-rows-only policy on `user_sync_snapshots` (verified: anon
      insert/select/update/delete all rejected; unauthenticated select empty)
- [x] Anon key only in the app; no service role anywhere client-side
- [x] No secrets in payload (keychain items excluded by allowlist)
- [x] Restore writes are versioned + undoable (same rails as manual edits)
- [x] Sync traffic uses the user's own access token over HTTPS; tokens at
      rest already safeStorage-encrypted (account-auth.ts)
- [x] Account deletion cascades snapshots (FK `on delete cascade`)

## 7. Verification matrix

- Toggle on with no account → toggle disabled, plain-language hint
- Baseline upload fails → toggle remains off and a later toggle still works
- Toggle off during the baseline upload → the completed upload cannot persist
  a stale enabled state
- Sign in → (if enabled) snapshot appears in Supabase with device name
- Back up twice → still one row per device (upsert, not accumulate)
- Second device sign-in → sees first device's snapshot, restores, content
  matches (MDs, memory, settings), undo works after restore
- Sign out → no further uploads; toggle persists for next sign-in
- Anon/cross-user access → blocked by RLS (tested via REST)
