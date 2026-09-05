-- Presence is separate from account backups. No account/content/name is stored.
create table public.install_heartbeats (
  install_id uuid primary key,
  first_seen timestamptz not null default now(),
  last_seen timestamptz not null default now(),
  version text not null check (length(version) between 1 and 64),
  platform text not null check (platform in ('win32', 'darwin', 'linux'))
);
create index install_heartbeats_last_seen_idx on public.install_heartbeats (last_seen);
alter table public.install_heartbeats enable row level security;
revoke all on public.install_heartbeats from public, anon, authenticated;
grant select on public.install_heartbeats to service_role;

-- The intentionally anonymous write API cannot read rows, set timestamps, or
-- touch backups. Keep elevated execution in an unexposed schema.
create schema install_presence_private;
grant usage on schema install_presence_private to anon, authenticated;
create function install_presence_private.record_install_heartbeat(p_install_id uuid, p_version text, p_platform text)
returns void language sql security definer set search_path = '' as $$
  insert into public.install_heartbeats (install_id, version, platform)
  values (p_install_id, p_version, p_platform)
  on conflict (install_id) do update
    set last_seen = now(), version = excluded.version, platform = excluded.platform;
$$;
revoke all on function install_presence_private.record_install_heartbeat(uuid, text, text) from public;
grant execute on function install_presence_private.record_install_heartbeat(uuid, text, text) to anon, authenticated;

create function public.record_install_heartbeat(p_install_id uuid, p_version text, p_platform text)
returns void language sql security invoker set search_path = '' as $$
  select install_presence_private.record_install_heartbeat(p_install_id, p_version, p_platform);
$$;
revoke all on function public.record_install_heartbeat(uuid, text, text) from public;
grant execute on function public.record_install_heartbeat(uuid, text, text) to anon, authenticated;
