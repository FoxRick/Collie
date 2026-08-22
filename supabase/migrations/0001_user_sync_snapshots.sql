-- Collie account cloud sync — per-device snapshots (account-cloud-sync.md §4).
-- One row per (user, device). RLS scopes every operation to auth.uid().
-- Applied via the Supabase Management API; kept in-repo for review/replay.

create table if not exists public.user_sync_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null,
  device_name text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  -- One latest snapshot per device: the app upserts on this target.
  constraint user_sync_snapshots_user_device_key unique (user_id, device_id)
);

create index if not exists user_sync_snapshots_user_idx
  on public.user_sync_snapshots (user_id, created_at desc);

alter table public.user_sync_snapshots enable row level security;

drop policy if exists "own rows only" on public.user_sync_snapshots;
create policy "own rows only"
  on public.user_sync_snapshots
  for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
