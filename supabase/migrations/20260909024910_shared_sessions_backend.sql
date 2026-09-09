-- Shared-session coordination authority. Feature gates default off; applying this
-- migration does not enable collaboration or perform any production writes.
create extension if not exists pgcrypto;
create schema if not exists collaboration_private;
revoke all on schema collaboration_private from public, anon, authenticated;
grant usage on schema collaboration_private to authenticated, service_role;

create table public.collaboration_config (
  singleton boolean primary key default true check (singleton),
  shared_text_enabled boolean not null default false,
  shared_execution_enabled boolean not null default false,
  archive_enabled boolean not null default false,
  slack_enabled boolean not null default false,
  max_participants integer not null default 5 check (max_participants between 1 and 50),
  max_sessions_per_org integer not null default 5 check (max_sessions_per_org > 0),
  max_messages_per_session integer not null default 200 check (max_messages_per_session > 0),
  session_warn_bytes bigint not null default 1572864 check (session_warn_bytes > 0),
  session_limit_bytes bigint not null default 2097152 check (session_limit_bytes >= session_warn_bytes),
  org_limit_bytes bigint not null default 10485760 check (org_limit_bytes > 0),
  project_logical_limit_bytes bigint not null default 104857600 check (project_logical_limit_bytes > 0),
  project_physical_stop_bytes bigint not null default 419430400 check (project_physical_stop_bytes > 0),
  admission_reserve_bytes bigint not null default 52428800 check (admission_reserve_bytes >= 0),
  max_file_bytes bigint not null default 5242880,
  max_session_file_bytes bigint not null default 20971520,
  max_project_file_bytes bigint not null default 104857600,
  default_response_reservation_bytes bigint not null default 262144,
  updated_at timestamptz not null default now()
);
insert into public.collaboration_config default values;

create table public.collaboration_organizations (
  org_id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) between 1 and 120),
  owner_id uuid not null references auth.users(id),
  revision bigint not null default 1 check (revision > 0),
  created_at timestamptz not null default now()
);
create table public.collaboration_org_members (
  org_id uuid not null references public.collaboration_organizations(org_id) on delete cascade,
  user_id uuid not null references auth.users(id),
  display_name text not null check (length(btrim(display_name)) between 1 and 120),
  role text not null check (role in ('owner','admin','member')),
  status text not null default 'active' check (status in ('active','revoked')),
  revision bigint not null default 1,
  joined_at timestamptz not null default now(), revoked_at timestamptz,
  primary key (org_id,user_id)
);
create table public.collaboration_org_invites (
  invite_id uuid primary key, org_id uuid not null references public.collaboration_organizations(org_id) on delete cascade,
  invited_user_id uuid not null references auth.users(id), invited_by uuid not null references auth.users(id),
  display_name text not null check(length(btrim(display_name)) between 1 and 120),
  status text not null default 'pending' check(status in ('pending','accepted','revoked')),
  created_at timestamptz not null default now(), accepted_at timestamptz, unique(org_id,invited_user_id)
);
create table public.collaboration_capacity_measurements (
  measurement_id bigint generated always as identity primary key,
  database_bytes bigint not null check(database_bytes>=0), collaboration_bytes bigint not null check(collaboration_bytes>=0),
  storage_bytes bigint not null check(storage_bytes>=0), measured_at timestamptz not null default now()
);
create table public.collaboration_sessions (
  session_id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.collaboration_organizations(org_id) on delete cascade,
  created_by uuid not null references auth.users(id),
  continuation_of uuid references public.collaboration_sessions(session_id),
  title text check(title is null or length(title)<=200),
  status text not null default 'active' check (status in ('active','closing','archive_pending','purging','archived_local')),
  revision bigint not null default 1,
  membership_revision bigint not null default 1,
  next_seq bigint not null default 1,
  final_seq bigint,
  message_count integer not null default 0,
  logical_bytes bigint not null default 0,
  reserved_bytes bigint not null default 0,
  shared_file_bytes bigint not null default 0,
  last_published_at timestamptz not null default now(),
  created_at timestamptz not null default now(), archived_at timestamptz,
  unique (org_id,session_id)
);
alter table public.collaboration_sessions add constraint collaboration_continuation_not_self check (continuation_of is null or continuation_of <> session_id);
alter table public.collaboration_sessions add constraint collaboration_continuation_scope_fk foreign key(org_id,continuation_of) references public.collaboration_sessions(org_id,session_id);
create table public.collaboration_session_members (
  org_id uuid not null,
  session_id uuid not null,
  user_id uuid not null references auth.users(id),
  role text not null check (role in ('owner','member')),
  status text not null default 'active' check (status in ('invited','active','revoked')),
  invite_id uuid,
  joined_at timestamptz, revoked_at timestamptz,
  primary key(session_id,user_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade,
  foreign key(org_id,user_id) references public.collaboration_org_members(org_id,user_id)
);
create table public.collaboration_invites (
  invite_id uuid primary key,
  org_id uuid not null,
  session_id uuid not null,
  invited_user_id uuid not null references auth.users(id),
  invited_by uuid not null references auth.users(id),
  status text not null default 'pending' check(status in ('pending','accepted','revoked')),
  created_at timestamptz not null default now(), accepted_at timestamptz,
  unique(session_id,invited_user_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade,
  foreign key(org_id,invited_user_id) references public.collaboration_org_members(org_id,user_id)
);
create table public.collaboration_events (
  event_id uuid primary key,
  org_id uuid not null,
  session_id uuid not null,
  seq bigint not null,
  kind text not null check(kind in ('message','edit','delete')),
  message_id uuid not null,
  author_id uuid not null references auth.users(id),
  role text not null check(role in ('user','assistant','system')),
  content text,
  revision integer not null check(revision > 0),
  logical_bytes bigint not null check(logical_bytes >= 0),
  request_hash text not null,
  created_at timestamptz not null default now(),
  unique(session_id,seq), unique(session_id,event_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade
);
create table public.collaboration_messages (
  org_id uuid not null, session_id uuid not null, message_id uuid not null,
  author_id uuid not null references auth.users(id), role text not null,
  content text, revision integer not null, deleted boolean not null default false,
  latest_event_id uuid not null, created_at timestamptz not null, updated_at timestamptz not null,
  primary key(session_id,message_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade,
  foreign key(session_id,latest_event_id) references public.collaboration_events(session_id,event_id)
);
create table public.collaboration_routine_publications (
  event_id uuid primary key, org_id uuid not null, session_id uuid not null,
  routine_id uuid not null, creator_id uuid not null references auth.users(id),
  audience_revision bigint not null, created_at timestamptz not null default now(),
  foreign key(session_id,event_id) references public.collaboration_events(session_id,event_id) on delete cascade,
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade
);
create table public.collaboration_devices (
  device_id uuid primary key, user_id uuid not null references auth.users(id),
  label text not null check(length(btrim(label)) between 1 and 120),
  public_key text not null, fence bigint not null default 1,
  status text not null default 'active' check(status in ('active','revoked')),
  enrolled_at timestamptz not null default now(), last_seen_at timestamptz not null default now(),
  unique(user_id,device_id)
);
create table public.collaboration_runs (
  run_id uuid primary key, org_id uuid not null, session_id uuid not null,
  request_event_id uuid not null, requester_id uuid not null references auth.users(id),
  provider_payer_id uuid not null references auth.users(id),
  audience_revision bigint not null,
  device_id uuid references public.collaboration_devices(device_id),
  status text not null default 'queued' check(status in ('queued','leased','completed','cancelled','needs_reconciliation')),
  lease_token uuid, lease_fence bigint, lease_expires_at timestamptz,
  response_reservation_id uuid, result_event_id uuid, created_at timestamptz not null default now(), completed_at timestamptz,
  unique(session_id,request_event_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade
);
create unique index collaboration_one_started_run on public.collaboration_runs(session_id) where status='leased';
create table public.collaboration_quota_counters (
  org_id uuid primary key references public.collaboration_organizations(org_id) on delete cascade,
  logical_bytes bigint not null default 0, reserved_bytes bigint not null default 0,
  shared_file_bytes bigint not null default 0, updated_at timestamptz not null default now()
);
create table public.collaboration_quota_reservations (
  reservation_id uuid primary key, org_id uuid not null, session_id uuid not null,
  run_id uuid, kind text not null check(kind in ('response','file')),
  reserved_bytes bigint not null check(reserved_bytes > 0), used_bytes bigint not null default 0,
  status text not null default 'active' check(status in ('active','settled','released','expired')),
  expires_at timestamptz not null, created_at timestamptz not null default now(), settled_at timestamptz,
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade
);
alter table public.collaboration_quota_reservations add constraint collaboration_reservation_scope_uq unique(org_id,session_id,reservation_id);
alter table public.collaboration_runs add constraint collaboration_run_reservation_fk foreign key(response_reservation_id) references public.collaboration_quota_reservations(reservation_id);
alter table public.collaboration_quota_reservations add constraint collaboration_reservation_run_fk foreign key(run_id) references public.collaboration_runs(run_id) deferrable initially deferred;
alter table public.collaboration_runs add constraint collaboration_run_request_event_fk foreign key(session_id,request_event_id) references public.collaboration_events(session_id,event_id);
alter table public.collaboration_runs add constraint collaboration_run_result_event_fk foreign key(session_id,result_event_id) references public.collaboration_events(session_id,event_id);
alter table public.collaboration_runs add constraint collaboration_run_device_fk foreign key(requester_id,device_id) references public.collaboration_devices(user_id,device_id);
alter table public.collaboration_runs add constraint collaboration_run_reservation_scope_fk foreign key(org_id,session_id,response_reservation_id) references public.collaboration_quota_reservations(org_id,session_id,reservation_id) deferrable initially deferred;
create table public.collaboration_file_reservations (
  file_id uuid primary key, reservation_id uuid not null unique references public.collaboration_quota_reservations(reservation_id),
  org_id uuid not null, session_id uuid not null, uploader_id uuid not null references auth.users(id),
  object_path text not null unique, expected_bytes bigint not null, sha256 text,
  status text not null default 'reserved' check(status in ('reserved','uploaded','purged')),
  created_at timestamptz not null default now(),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id) on delete cascade
);
create table public.collaboration_archive_manifests (
  session_id uuid not null references public.collaboration_sessions(session_id) on delete cascade,
  archive_revision bigint not null, membership_revision bigint not null, final_seq bigint not null,
  manifest_json text not null, digest text not null check(digest ~ '^[0-9a-f]{64}$'),
  byte_length bigint not null, status text not null default 'pending' check(status in ('pending','ready','purging','purged')),
  created_at timestamptz not null default now(), primary key(session_id,archive_revision),
  unique(session_id,digest)
);
create table public.collaboration_archive_recipients (
  session_id uuid not null, archive_revision bigint not null, user_id uuid not null references auth.users(id),
  eligible boolean not null default true, receipt_required boolean not null default true,
  primary key(session_id,archive_revision,user_id),
  foreign key(session_id,archive_revision) references public.collaboration_archive_manifests(session_id,archive_revision) on delete cascade
);
create table public.collaboration_archive_receipts (
  session_id uuid not null, archive_revision bigint not null, user_id uuid not null,
  device_id uuid not null references public.collaboration_devices(device_id), digest text not null,
  byte_length bigint not null, final_seq bigint not null, verified_at timestamptz not null default now(),
  primary key(session_id,archive_revision,user_id,device_id),
  foreign key(session_id,archive_revision,user_id) references public.collaboration_archive_recipients(session_id,archive_revision,user_id)
);
create table public.collaboration_purge_jobs (
  session_id uuid primary key references public.collaboration_sessions(session_id) on delete cascade,
  archive_revision bigint not null, status text not null default 'waiting' check(status in ('waiting','claimed','database_done','objects_done','complete','failed')),
  lease_token uuid, fence bigint not null default 0, lease_expires_at timestamptz, attempts integer not null default 0,
  db_checkpoint jsonb not null default '{}'::jsonb, object_checkpoint jsonb not null default '{}'::jsonb,
  last_error_code text, updated_at timestamptz not null default now()
);
create table public.collaboration_tombstones (
  session_id uuid primary key, org_id uuid not null, final_seq bigint not null, digest text not null,
  archive_revision bigint not null, eligible_user_ids uuid[] not null, receipt_evidence jsonb not null,
  archived_at timestamptz not null, purge_completed_at timestamptz not null
);
create table public.collaboration_audit (
  audit_id bigint generated always as identity primary key, org_id uuid, session_id uuid,
  actor_id uuid, action text not null, detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create table public.collaboration_outbox (
  outbox_id uuid primary key, org_id uuid not null, session_id uuid,
  kind text not null, payload jsonb not null, dedup_key text not null unique,
  status text not null default 'pending' check(status in ('pending','claimed','sent','failed','uncertain','manual_review')),
  attempts integer not null default 0, fence bigint not null default 0, lease_token uuid, lease_expires_at timestamptz,
  retry_after timestamptz, slack_ts text, reconcile_cursor text, created_at timestamptz not null default now(), completed_at timestamptz
);

-- Slack authority. Raw signing verification and encryption happen in the edge function.
create table public.collaboration_slack_oauth_states (
  state_hash text primary key, actor_id uuid not null references auth.users(id), org_id uuid not null references public.collaboration_organizations(org_id),
  expires_at timestamptz not null, consumed_at timestamptz, created_at timestamptz not null default now()
);
create table public.collaboration_slack_installations (
  installation_id uuid primary key default gen_random_uuid(), org_id uuid not null references public.collaboration_organizations(org_id),
  team_id text not null unique, bot_user_id text not null, token_ciphertext text not null,
  status text not null default 'active' check(status in ('active','revoked')),
  installed_at timestamptz not null default now(), revoked_at timestamptz
);
alter table public.collaboration_slack_installations add constraint collaboration_slack_install_scope_uq unique(org_id,installation_id);
create table public.collaboration_slack_sender_links (
  installation_id uuid not null references public.collaboration_slack_installations(installation_id),
  slack_user_id text not null, user_id uuid not null references auth.users(id), linked_at timestamptz not null default now(),
  primary key(installation_id,slack_user_id), unique(installation_id,user_id)
);
create table public.collaboration_slack_link_challenges (
  code_hash text primary key, installation_id uuid not null references public.collaboration_slack_installations(installation_id),
  user_id uuid not null references auth.users(id), expires_at timestamptz not null,
  consumed_at timestamptz, created_at timestamptz not null default now()
);
create table public.collaboration_slack_channels (
  installation_id uuid not null references public.collaboration_slack_installations(installation_id),
  channel_id text not null, org_id uuid not null references public.collaboration_organizations(org_id), selected_by uuid not null references auth.users(id),
  selected_at timestamptz not null default now(), primary key(installation_id,channel_id),
  unique(org_id,installation_id,channel_id), foreign key(org_id,installation_id) references public.collaboration_slack_installations(org_id,installation_id)
);
create table public.collaboration_slack_threads (
  org_id uuid not null, installation_id uuid not null, channel_id text not null, root_thread_ts text not null,
  session_id uuid not null, created_at timestamptz not null default now(),
  primary key(installation_id,channel_id,root_thread_ts), unique(session_id),
  foreign key(org_id,installation_id,channel_id) references public.collaboration_slack_channels(org_id,installation_id,channel_id),
  foreign key(org_id,session_id) references public.collaboration_sessions(org_id,session_id)
);
create table public.collaboration_slack_inbox (
  inbox_id uuid not null default gen_random_uuid() unique, installation_id uuid not null references public.collaboration_slack_installations(installation_id), event_id text not null,
  event jsonb not null, status text not null default 'pending' check(status in ('pending','claimed','processed','failed')),
  fence bigint not null default 0, lease_token uuid, lease_expires_at timestamptz, retry_after timestamptz, attempts integer not null default 0, last_error_code text,
  received_at timestamptz not null default now(), completed_at timestamptz, primary key(installation_id,event_id)
);

create index collaboration_events_cursor_idx on public.collaboration_events(session_id,seq);
create index collaboration_members_user_idx on public.collaboration_session_members(user_id,session_id) where status='active';
create index collaboration_runs_pickup_idx on public.collaboration_runs(requester_id,status,created_at);
create index collaboration_reservations_expiry_idx on public.collaboration_quota_reservations(status,expires_at);
create index collaboration_outbox_pickup_idx on public.collaboration_outbox(status,created_at);

-- No table is a direct client mutation surface. Selected membership-scoped rows
-- are readable; all writes pass through guarded commands.
do $enable_rls$
declare r record;
begin
  for r in select tablename from pg_tables where schemaname='public' and tablename like 'collaboration_%' loop
    execute format('alter table public.%I enable row level security',r.tablename);
    execute format('revoke all on table public.%I from public, anon, authenticated',r.tablename);
  end loop;
end $enable_rls$;
grant select on public.collaboration_config to authenticated;
grant select on public.collaboration_organizations, public.collaboration_org_members,
  public.collaboration_sessions, public.collaboration_session_members,
  public.collaboration_events, public.collaboration_messages, public.collaboration_routine_publications,
  public.collaboration_runs, public.collaboration_archive_manifests,
  public.collaboration_archive_recipients, public.collaboration_archive_receipts,
  public.collaboration_tombstones to authenticated;

create function collaboration_private.is_org_member(p_org_id uuid,p_user_id uuid)
returns boolean language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.collaboration_org_members m where m.org_id=p_org_id and m.user_id=p_user_id and m.status='active')
$$;
create function collaboration_private.is_session_member(p_session_id uuid,p_user_id uuid)
returns boolean language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.collaboration_session_members m join public.collaboration_org_members om on om.org_id=m.org_id and om.user_id=m.user_id and om.status='active' where m.session_id=p_session_id and m.user_id=p_user_id and m.status='active')
$$;
create function collaboration_private.can_access_file(p_name text,p_write boolean) returns boolean language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.collaboration_file_reservations f join public.collaboration_quota_reservations q using(reservation_id) join public.collaboration_sessions s on s.session_id=f.session_id where f.object_path=p_name and collaboration_private.is_session_member(f.session_id,(select auth.uid())) and (not p_write or (f.uploader_id=(select auth.uid()) and f.status='reserved' and q.status='active' and q.expires_at>now() and s.status='active')))
$$;
revoke all on function collaboration_private.is_org_member(uuid,uuid), collaboration_private.is_session_member(uuid,uuid), collaboration_private.can_access_file(text,boolean) from public;
grant execute on function collaboration_private.is_org_member(uuid,uuid), collaboration_private.is_session_member(uuid,uuid), collaboration_private.can_access_file(text,boolean) to authenticated;

create policy collaboration_org_read on public.collaboration_organizations for select to authenticated
 using ((select collaboration_private.is_org_member(org_id,(select auth.uid()))));
create policy collaboration_config_read on public.collaboration_config for select to authenticated using (true);
create policy collaboration_org_member_read on public.collaboration_org_members for select to authenticated
 using ((select collaboration_private.is_org_member(org_id,(select auth.uid()))));
create policy collaboration_session_read on public.collaboration_sessions for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_session_member_read on public.collaboration_session_members for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_event_read on public.collaboration_events for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_message_read on public.collaboration_messages for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_routine_publication_read on public.collaboration_routine_publications for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_run_read on public.collaboration_runs for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_manifest_read on public.collaboration_archive_manifests for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_recipient_read on public.collaboration_archive_recipients for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_receipt_read on public.collaboration_archive_receipts for select to authenticated
 using ((select collaboration_private.is_session_member(session_id,(select auth.uid()))));
create policy collaboration_tombstone_read on public.collaboration_tombstones for select to authenticated
 using ((select auth.uid())=any(eligible_user_ids));

-- Private shared-file bucket. Object path is exactly org/session/file UUIDs;
-- inserts require an active reservation, and reads require session membership.
insert into storage.buckets(id,name,public,file_size_limit)
values('collaboration-files','collaboration-files',false,5242880)
on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit;
create policy collaboration_files_read on storage.objects for select to authenticated using (
 bucket_id='collaboration-files' and (select collaboration_private.can_access_file(name,false)));
create policy collaboration_files_insert on storage.objects for insert to authenticated with check (
 bucket_id='collaboration-files' and (select collaboration_private.can_access_file(name,true)));

-- Realtime is only a wake-up hint. Topics include the current membership
-- revision, so revocation rotates the authorized topic immediately.
do $realtime_policy$
begin
 if to_regclass('realtime.messages') is not null then
   execute $policy$create policy collaboration_realtime_receive on realtime.messages
     for select to authenticated using (
       realtime.topic() ~ '^collaboration:[0-9a-f-]{36}:[0-9]+$'
       and exists (
         select 1 from public.collaboration_sessions s
         where s.session_id=split_part(realtime.topic(),':',2)::uuid
           and s.membership_revision=split_part(realtime.topic(),':',3)::bigint
           and collaboration_private.is_session_member(s.session_id,(select auth.uid()))
       )
     )$policy$;
 end if;
end $realtime_policy$;

create function collaboration_private.fail(p_code text,p_detail text default null) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$ begin
 raise exception '%',p_code using errcode='P0001',detail=coalesce(p_detail,''); end $$;

create function collaboration_private.command(p_actor uuid,p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare
 cfg public.collaboration_config%rowtype; s public.collaboration_sessions%rowtype; m public.collaboration_messages%rowtype;
 v_org uuid; v_session uuid; v_event uuid; v_message uuid; v_run uuid; v_invite uuid; v_device uuid; v_res uuid;
 v_expected bigint; v_seq bigint; v_bytes bigint; v_cursor bigint; v_limit int; v_count int; v_hash text; v_role text; v_content text;
 v_manifest text; v_digest text; v_archive_rev bigint; v_result jsonb; v_fence bigint; v_expiry timestamptz;
begin
 if p_actor is null or p_actor is distinct from (select auth.uid()) then perform collaboration_private.fail('UNAUTHENTICATED'); end if;
 if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
 select * into cfg from public.collaboration_config where singleton for share;
 if p_command='bootstrap' then
   return jsonb_build_object('ok',true,'gates',jsonb_build_object('shared_text',cfg.shared_text_enabled,'shared_execution',cfg.shared_execution_enabled,'archive',cfg.archive_enabled,'slack',cfg.slack_enabled),
    'organizations',coalesce((select jsonb_agg(jsonb_build_object('org_id',o.org_id,'name',o.name,'revision',o.revision)) from public.collaboration_organizations o join public.collaboration_org_members om using(org_id) where om.user_id=p_actor and om.status='active'),'[]'::jsonb),
    'sessions',coalesce((select jsonb_agg(jsonb_build_object('session_id',bs.session_id,'org_id',bs.org_id,'title',bs.title,'status',bs.status,'revision',bs.revision,'membership_revision',bs.membership_revision,'high_water',bs.next_seq-1,'message_count',bs.message_count,'logical_bytes',bs.logical_bytes,'reserved_bytes',bs.reserved_bytes,'warning',bs.logical_bytes+bs.reserved_bytes>=cfg.session_warn_bytes,'run_state',(select jsonb_build_object('run_id',r.run_id,'status',case when r.status='leased' then 'running' else r.status end,'requested_by',r.requester_id,'created_at',r.created_at) from public.collaboration_runs r where r.session_id=bs.session_id and r.status in('queued','leased','needs_reconciliation') order by r.created_at limit 1),'members',coalesce((select jsonb_agg(jsonb_build_object('user_id',sm2.user_id,'display_name',om.display_name,'role',sm2.role,'status',sm2.status)) from public.collaboration_session_members sm2 join public.collaboration_org_members om on om.org_id=sm2.org_id and om.user_id=sm2.user_id where sm2.session_id=bs.session_id),'[]'::jsonb))) from public.collaboration_sessions bs join public.collaboration_session_members sm using(session_id) where sm.user_id=p_actor and sm.status='active'),'[]'::jsonb),
    'invites',coalesce((select jsonb_agg(x) from (select jsonb_build_object('kind','session','invite_id',i.invite_id,'org_id',i.org_id,'session_id',i.session_id,'invited_by',i.invited_by) x from public.collaboration_invites i where i.invited_user_id=p_actor and i.status='pending' union all select jsonb_build_object('kind','organization','invite_id',oi.invite_id,'org_id',oi.org_id,'invited_by',oi.invited_by,'display_name',oi.display_name) from public.collaboration_org_invites oi where oi.invited_user_id=p_actor and oi.status='pending') q),'[]'::jsonb),
    'queued_runs',coalesce((select jsonb_agg(jsonb_build_object('run_id',r.run_id,'session_id',r.session_id,'provider_payer_id',r.provider_payer_id,'created_at',r.created_at)) from public.collaboration_runs r where r.requester_id=p_actor and r.status='queued' and collaboration_private.is_session_member(r.session_id,p_actor)),'[]'::jsonb));
 end if;
 if not cfg.shared_text_enabled and p_command in ('create_organization','invite_organization','accept_organization_invite','create_session','invite','accept_invite','append_message','edit_message','delete_message','publish_routine','claim_run','renew_run','complete_run','continue_session','begin_slack_link','select_slack_channel') then perform collaboration_private.fail('FEATURE_DISABLED'); end if;
 if p_command='create_organization' then
   v_org:=coalesce((p_payload->>'org_id')::uuid,gen_random_uuid());
   insert into public.collaboration_organizations(org_id,name,owner_id) values(v_org,p_payload->>'name',p_actor);
   insert into public.collaboration_org_members(org_id,user_id,display_name,role) values(v_org,p_actor,coalesce(nullif(p_payload->>'display_name',''),p_payload->>'name'),'owner');
   insert into public.collaboration_quota_counters(org_id) values(v_org); return jsonb_build_object('ok',true,'org_id',v_org,'revision',1);
 elsif p_command='invite_organization' then
   v_org:=(p_payload->>'org_id')::uuid; if not exists(select 1 from public.collaboration_org_members where org_id=v_org and user_id=p_actor and status='active' and role in('owner','admin')) then perform collaboration_private.fail('FORBIDDEN'); end if; v_invite:=(p_payload->>'invite_id')::uuid; insert into public.collaboration_org_invites values(v_invite,v_org,(p_payload->>'user_id')::uuid,p_actor,p_payload->>'display_name','pending',now(),null); return jsonb_build_object('ok',true,'invite_id',v_invite);
 elsif p_command='accept_organization_invite' then
   v_invite:=(p_payload->>'invite_id')::uuid; update public.collaboration_org_invites set status='accepted',accepted_at=now() where invite_id=v_invite and invited_user_id=p_actor and status='pending' returning org_id,display_name into v_org,v_content; if not found then perform collaboration_private.fail('NOT_FOUND'); end if; insert into public.collaboration_org_members(org_id,user_id,display_name,role) values(v_org,p_actor,v_content,'member'); return jsonb_build_object('ok',true,'org_id',v_org);
 elsif p_command='directory' then
   v_org:=(p_payload->>'org_id')::uuid; if not collaboration_private.is_org_member(v_org,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   return jsonb_build_object('ok',true,'members',coalesce((select jsonb_agg(jsonb_build_object('user_id',user_id,'display_name',display_name,'role',role,'revision',revision)) from public.collaboration_org_members where org_id=v_org and status='active'),'[]'::jsonb));
 elsif p_command='slack_settings' then
   v_org:=(p_payload->>'org_id')::uuid; if not collaboration_private.is_org_member(v_org,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if; return jsonb_build_object('ok',true,'installations',coalesce((select jsonb_agg(jsonb_build_object('installation_id',i.installation_id,'team_id',i.team_id,'status',i.status,'linked_current_user',exists(select 1 from public.collaboration_slack_sender_links l where l.installation_id=i.installation_id and l.user_id=p_actor),'selected_channels',coalesce((select jsonb_agg(c.channel_id) from public.collaboration_slack_channels c where c.installation_id=i.installation_id),'[]'::jsonb))) from public.collaboration_slack_installations i where i.org_id=v_org),'[]'::jsonb));
 elsif p_command='create_session' then
   v_org:=(p_payload->>'org_id')::uuid; if not collaboration_private.is_org_member(v_org,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   perform 1 from public.collaboration_quota_counters where org_id=v_org for update;
   if not exists(select 1 from (select * from public.collaboration_capacity_measurements order by measured_at desc limit 1) latest where latest.measured_at>now()-interval '36 hours' and latest.database_bytes+cfg.admission_reserve_bytes<cfg.project_physical_stop_bytes and latest.collaboration_bytes<262144000) then perform collaboration_private.fail('CAPACITY_UNMEASURED'); end if;
   select count(*) into v_count from public.collaboration_sessions where org_id=v_org and status<>'archived_local'; if v_count>=cfg.max_sessions_per_org then perform collaboration_private.fail('QUOTA_EXCEEDED','session_count'); end if;
   v_session:=coalesce((p_payload->>'session_id')::uuid,gen_random_uuid()); insert into public.collaboration_sessions(session_id,org_id,created_by,title) values(v_session,v_org,p_actor,nullif(p_payload->>'title',''));
   insert into public.collaboration_session_members(org_id,session_id,user_id,role,status,joined_at) values(v_org,v_session,p_actor,'owner','active',now()); return jsonb_build_object('ok',true,'session_id',v_session,'org_id',v_org,'revision',1);
 elsif p_command='invite' then
   v_session:=(p_payload->>'session_id')::uuid; select * into s from public.collaboration_sessions where session_id=v_session for update; if not found then perform collaboration_private.fail('NOT_FOUND'); end if; if s.status<>'active' then perform collaboration_private.fail('SESSION_CLOSED'); end if;
   if not exists(select 1 from public.collaboration_session_members where session_id=v_session and user_id=p_actor and status='active' and role='owner') then perform collaboration_private.fail('FORBIDDEN'); end if;
   select count(*) into v_count from public.collaboration_session_members where session_id=v_session and status in ('active','invited'); if v_count>=cfg.max_participants then perform collaboration_private.fail('QUOTA_EXCEEDED','participants'); end if;
   v_invite:=(p_payload->>'invite_id')::uuid; insert into public.collaboration_invites(invite_id,org_id,session_id,invited_user_id,invited_by) values(v_invite,s.org_id,v_session,(p_payload->>'user_id')::uuid,p_actor);
   insert into public.collaboration_session_members(org_id,session_id,user_id,role,status,invite_id) values(s.org_id,v_session,(p_payload->>'user_id')::uuid,'member','invited',v_invite); update public.collaboration_sessions set membership_revision=membership_revision+1,revision=revision+1 where session_id=v_session;
   return jsonb_build_object('ok',true,'invite_id',v_invite);
 elsif p_command='accept_invite' then
   v_invite:=(p_payload->>'invite_id')::uuid; update public.collaboration_invites i set status='accepted',accepted_at=now() from public.collaboration_sessions target_session where i.invite_id=v_invite and i.invited_user_id=p_actor and i.status='pending' and target_session.session_id=i.session_id and target_session.status='active' returning i.session_id into v_session; if not found then perform collaboration_private.fail('NOT_FOUND'); end if;
   update public.collaboration_session_members set status='active',joined_at=now() where invite_id=v_invite and user_id=p_actor; update public.collaboration_sessions set membership_revision=membership_revision+1,revision=revision+1 where session_id=v_session; return jsonb_build_object('ok',true,'session_id',v_session);
 elsif p_command='read_events' then
   v_session:=(p_payload->>'session_id')::uuid; if not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   v_cursor:=coalesce((p_payload->>'cursor')::bigint,0); v_limit:=least(greatest(coalesce((p_payload->>'limit')::int,100),1),500);
   select next_seq-1 into v_seq from public.collaboration_sessions where session_id=v_session;
   return jsonb_build_object('ok',true,'events',coalesce((select jsonb_agg(x.obj order by x.seq) from (select e.seq,jsonb_build_object('event_id',e.event_id,'session_id',e.session_id,'seq',e.seq,'kind',e.kind,'message_id',e.message_id,'author_id',e.author_id,'role',e.role,'content',e.content,'revision',e.revision,'created_at',e.created_at) obj from public.collaboration_events e where e.session_id=v_session and e.seq>v_cursor and e.seq<=v_seq order by e.seq limit v_limit)x),'[]'::jsonb),'next_cursor',coalesce((select max(seq) from (select seq from public.collaboration_events where session_id=v_session and seq>v_cursor and seq<=v_seq order by seq limit v_limit)q),v_cursor),'high_water',v_seq,'run_state',(select jsonb_build_object('run_id',r.run_id,'status',case when r.status='leased' then 'running' else r.status end,'requested_by',r.requester_id,'created_at',r.created_at) from public.collaboration_runs r where r.session_id=v_session and r.status in('queued','leased','needs_reconciliation') order by r.created_at limit 1));
 elsif p_command='list_pending_runs' then
   return jsonb_build_object('ok',true,'runs',coalesce((select jsonb_agg(jsonb_build_object('run_id',r.run_id,'session_id',r.session_id,'request_event_id',r.request_event_id,'provider_payer_id',r.provider_payer_id,'created_at',r.created_at) order by r.created_at) from public.collaboration_runs r where r.requester_id=p_actor and r.status='queued' and collaboration_private.is_session_member(r.session_id,p_actor)),'[]'::jsonb));
 elsif p_command in ('append_message','edit_message','delete_message') then
   v_session:=(p_payload->>'session_id')::uuid; v_event:=(p_payload->>'event_id')::uuid; v_message:=(p_payload->>'message_id')::uuid; v_content:=p_payload->>'content'; v_hash:=encode(extensions.digest(convert_to(p_actor::text||':'||p_command||':'||p_payload::text,'UTF8'),'sha256'),'hex');
   select * into s from public.collaboration_sessions where session_id=v_session for update; if not found or not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   if exists(select 1 from public.collaboration_events where event_id=v_event) then if exists(select 1 from public.collaboration_events where event_id=v_event and request_hash=v_hash) then return (select jsonb_build_object('ok',true,'deduplicated',true,'event_id',e.event_id,'session_id',e.session_id,'seq',e.seq,'message_id',e.message_id,'revision',e.revision,'run_id',r.run_id,'reservation_id',r.response_reservation_id) from public.collaboration_events e left join public.collaboration_runs r on r.session_id=e.session_id and r.request_event_id=e.event_id where e.event_id=v_event); else perform collaboration_private.fail('IDEMPOTENCY_CONFLICT'); end if; end if;
   if s.status<>'active' then perform collaboration_private.fail('SESSION_CLOSED'); end if;
   v_role:=coalesce(p_payload->>'role','user'); if v_role<>'user' then perform collaboration_private.fail('FORBIDDEN','client_author_role'); end if;
   if p_command='append_message' then
     if s.message_count+1+(select count(*) from public.collaboration_runs where session_id=v_session and status in('queued','leased'))+(case when coalesce((p_payload->>'request_run')::boolean,false) then 1 else 0 end)>cfg.max_messages_per_session then perform collaboration_private.fail('QUOTA_EXCEEDED','reserved_message_slot'); end if; v_expected:=1;
   else
     select * into m from public.collaboration_messages where session_id=v_session and message_id=v_message for update; if not found then perform collaboration_private.fail('NOT_FOUND'); end if; if m.author_id<>p_actor then perform collaboration_private.fail('FORBIDDEN'); end if;
     if m.revision is distinct from (p_payload->>'expected_revision')::bigint then perform collaboration_private.fail('REVISION_CONFLICT'); end if; v_expected:=m.revision+1;
   end if;
   -- Includes conservative event/index plus canonical-manifest headroom.
   v_bytes:=octet_length(convert_to(coalesce(v_content,''),'UTF8'))+640;
   perform 1 from public.collaboration_quota_counters where org_id=s.org_id for update;
   if s.logical_bytes+s.reserved_bytes+v_bytes>cfg.session_limit_bytes or (select logical_bytes+reserved_bytes from public.collaboration_quota_counters where org_id=s.org_id)+v_bytes>cfg.org_limit_bytes or (select coalesce(sum(logical_bytes+reserved_bytes),0) from public.collaboration_quota_counters)+v_bytes>cfg.project_logical_limit_bytes then perform collaboration_private.fail('QUOTA_EXCEEDED','bytes'); end if;
   v_seq:=s.next_seq; insert into public.collaboration_events(event_id,org_id,session_id,seq,kind,message_id,author_id,role,content,revision,logical_bytes,request_hash) values(v_event,s.org_id,v_session,v_seq,case p_command when 'append_message' then 'message' when 'edit_message' then 'edit' else 'delete' end,v_message,p_actor,v_role,case when p_command='delete_message' then null else v_content end,v_expected,v_bytes,v_hash);
   if p_command='append_message' then insert into public.collaboration_messages values(s.org_id,v_session,v_message,p_actor,v_role,v_content,1,false,v_event,now(),now()); else update public.collaboration_messages set content=case when p_command='delete_message' then null else v_content end,deleted=(p_command='delete_message'),revision=v_expected,latest_event_id=v_event,updated_at=now() where session_id=v_session and message_id=v_message; end if;
   update public.collaboration_sessions set next_seq=next_seq+1,message_count=message_count+(case when p_command='append_message' then 1 else 0 end),logical_bytes=logical_bytes+v_bytes,last_published_at=now(),revision=revision+1 where session_id=v_session;
   update public.collaboration_quota_counters set logical_bytes=logical_bytes+v_bytes,updated_at=now() where org_id=s.org_id;
   if p_command='append_message' and coalesce((p_payload->>'request_run')::boolean,false) then
     if not cfg.shared_execution_enabled then perform collaboration_private.fail('FEATURE_DISABLED','shared_execution'); end if;
     v_run:=coalesce((p_payload->>'run_id')::uuid,gen_random_uuid()); v_res:=coalesce((p_payload->>'reservation_id')::uuid,gen_random_uuid()); v_bytes:=least(coalesce((p_payload->>'response_reservation_bytes')::bigint,cfg.default_response_reservation_bytes),cfg.session_limit_bytes);
     if s.logical_bytes+s.reserved_bytes+(octet_length(convert_to(coalesce(v_content,''),'UTF8'))+640)+v_bytes>cfg.session_limit_bytes
        or (select logical_bytes+reserved_bytes from public.collaboration_quota_counters where org_id=s.org_id)+v_bytes+(octet_length(convert_to(coalesce(v_content,''),'UTF8'))+640)>cfg.org_limit_bytes
        or (select coalesce(sum(logical_bytes+reserved_bytes),0) from public.collaboration_quota_counters)+v_bytes>cfg.project_logical_limit_bytes then perform collaboration_private.fail('QUOTA_EXCEEDED','response_reservation'); end if;
     insert into public.collaboration_quota_reservations(reservation_id,org_id,session_id,run_id,kind,reserved_bytes,expires_at) values(v_res,s.org_id,v_session,v_run,'response',v_bytes,now()+interval '2 hours');
     insert into public.collaboration_runs(run_id,org_id,session_id,request_event_id,requester_id,provider_payer_id,audience_revision,response_reservation_id) values(v_run,s.org_id,v_session,v_event,p_actor,p_actor,s.membership_revision,v_res);
     update public.collaboration_sessions set reserved_bytes=reserved_bytes+v_bytes where session_id=v_session; update public.collaboration_quota_counters set reserved_bytes=reserved_bytes+v_bytes where org_id=s.org_id;
   end if;
   perform pg_catalog.pg_notify('collaboration_hint',jsonb_build_object('session_id',v_session,'high_water',v_seq,'membership_revision',s.membership_revision)::text);
   return jsonb_build_object('ok',true,'event_id',v_event,'session_id',v_session,'seq',v_seq,'message_id',v_message,'revision',v_expected,'run_id',v_run,'reservation_id',v_res,'warning',case when s.logical_bytes+v_bytes>=cfg.session_warn_bytes then 'SESSION_STORAGE_WARNING' else null end);
 elsif p_command='publish_routine' then
   v_session:=(p_payload->>'session_id')::uuid; v_event:=(p_payload->>'event_id')::uuid; v_message:=(p_payload->>'message_id')::uuid; v_content:=p_payload->>'content'; v_hash:=encode(extensions.digest(convert_to(p_actor::text||':publish_routine:'||p_payload::text,'UTF8'),'sha256'),'hex'); select * into s from public.collaboration_sessions where session_id=v_session for update; if not found or not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   if exists(select 1 from public.collaboration_events where event_id=v_event) then if exists(select 1 from public.collaboration_events where event_id=v_event and request_hash=v_hash and author_id=p_actor) then return (select jsonb_build_object('ok',true,'deduplicated',true,'event',jsonb_build_object('event_id',event_id,'session_id',session_id,'seq',seq,'kind',kind,'message_id',message_id,'author_id',author_id,'role',role,'content',content,'revision',revision,'created_at',created_at)) from public.collaboration_events where event_id=v_event); else perform collaboration_private.fail('IDEMPOTENCY_CONFLICT'); end if; end if;
   if s.status<>'active' then perform collaboration_private.fail('SESSION_CLOSED'); end if; if s.membership_revision is distinct from (p_payload->>'audience_revision')::bigint then perform collaboration_private.fail('STALE_MEMBERSHIP'); end if;
   if s.message_count+1+(select count(*) from public.collaboration_runs where session_id=v_session and status in('queued','leased'))>cfg.max_messages_per_session then perform collaboration_private.fail('QUOTA_EXCEEDED','reserved_message_slot'); end if; v_bytes:=octet_length(convert_to(coalesce(v_content,''),'UTF8'))+640; perform 1 from public.collaboration_quota_counters where org_id=s.org_id for update; if s.logical_bytes+s.reserved_bytes+v_bytes>cfg.session_limit_bytes or (select logical_bytes+reserved_bytes from public.collaboration_quota_counters where org_id=s.org_id)+v_bytes>cfg.org_limit_bytes or (select coalesce(sum(logical_bytes+reserved_bytes),0) from public.collaboration_quota_counters)+v_bytes>cfg.project_logical_limit_bytes then perform collaboration_private.fail('QUOTA_EXCEEDED','bytes'); end if;
   v_seq:=s.next_seq; insert into public.collaboration_events values(v_event,s.org_id,v_session,v_seq,'message',v_message,p_actor,'assistant',v_content,1,v_bytes,v_hash,now()); insert into public.collaboration_messages values(s.org_id,v_session,v_message,p_actor,'assistant',v_content,1,false,v_event,now(),now()); insert into public.collaboration_routine_publications(event_id,org_id,session_id,routine_id,creator_id,audience_revision) values(v_event,s.org_id,v_session,(p_payload->>'routine_id')::uuid,p_actor,s.membership_revision); update public.collaboration_sessions set next_seq=next_seq+1,message_count=message_count+1,logical_bytes=logical_bytes+v_bytes,last_published_at=now(),revision=revision+1 where session_id=v_session; update public.collaboration_quota_counters set logical_bytes=logical_bytes+v_bytes,updated_at=now() where org_id=s.org_id; if exists(select 1 from public.collaboration_slack_threads where session_id=v_session) then insert into public.collaboration_outbox(outbox_id,org_id,session_id,kind,payload,dedup_key) values(gen_random_uuid(),s.org_id,v_session,'slack_message',jsonb_build_object('content',v_content,'event_id',v_event,'routine_id',p_payload->>'routine_id'),v_event::text) on conflict(dedup_key) do nothing; end if; perform pg_catalog.pg_notify('collaboration_hint',jsonb_build_object('session_id',v_session,'high_water',v_seq,'membership_revision',s.membership_revision)::text); return jsonb_build_object('ok',true,'event',jsonb_build_object('event_id',v_event,'session_id',v_session,'seq',v_seq,'kind','message','message_id',v_message,'author_id',p_actor,'role','assistant','content',v_content,'revision',1,'created_at',now()));
 elsif p_command='enroll_device' then
   v_device:=(p_payload->>'device_id')::uuid; insert into public.collaboration_devices(device_id,user_id,label,public_key) values(v_device,p_actor,p_payload->>'label',p_payload->>'public_key') on conflict(device_id) do update set label=excluded.label,last_seen_at=now() where collaboration_devices.user_id=p_actor and collaboration_devices.public_key=excluded.public_key returning fence into v_fence; if not found then perform collaboration_private.fail('DEVICE_KEY_MISMATCH'); end if; return jsonb_build_object('ok',true,'device_id',v_device,'fence',v_fence);
 elsif p_command='claim_run' then
   if not cfg.shared_execution_enabled then perform collaboration_private.fail('FEATURE_DISABLED'); end if; v_run:=(p_payload->>'run_id')::uuid; v_device:=(p_payload->>'device_id')::uuid;
   select fence into v_fence from public.collaboration_devices where device_id=v_device and user_id=p_actor and status='active' for update; if not found then perform collaboration_private.fail('DEVICE_FENCED'); end if;
   update public.collaboration_devices set last_seen_at=now() where device_id=v_device returning fence into v_fence;
   update public.collaboration_runs r set status='leased',device_id=v_device,lease_token=gen_random_uuid(),lease_fence=v_fence,lease_expires_at=now()+interval '2 minutes' where r.run_id=v_run and r.requester_id=p_actor and (r.status='queued' or (r.status='leased' and r.lease_expires_at<now())) and exists(select 1 from public.collaboration_sessions ss where ss.session_id=r.session_id and ss.status in('active','closing') and ss.membership_revision=r.audience_revision) and collaboration_private.is_session_member(r.session_id,p_actor) and not exists(select 1 from public.collaboration_runs other where other.session_id=r.session_id and other.run_id<>r.run_id and other.status='leased' and other.lease_expires_at>now()) returning r.lease_token,r.session_id into v_res,v_session; if not found then perform collaboration_private.fail('RUN_UNAVAILABLE'); end if;
   return jsonb_build_object('ok',true,'run_id',v_run,'lease_token',v_res,'fence',v_fence,'lease_expires_at',(select lease_expires_at from public.collaboration_runs where run_id=v_run),'session_id',v_session,'requester_id',(select requester_id from public.collaboration_runs where run_id=v_run),'credential_owner_id',(select provider_payer_id from public.collaboration_runs where run_id=v_run),'provider_payer_id',(select provider_payer_id from public.collaboration_runs where run_id=v_run),'executor_device_id',v_device,'audience_revision',(select audience_revision from public.collaboration_runs where run_id=v_run),'request_event_id',(select request_event_id from public.collaboration_runs where run_id=v_run),'request_content',(select e.content from public.collaboration_runs r join public.collaboration_events e on e.session_id=r.session_id and e.event_id=r.request_event_id where r.run_id=v_run),'context_cutoff',(select next_seq-1 from public.collaboration_sessions where session_id=v_session),'events',coalesce((select jsonb_agg(jsonb_build_object('event_id',e.event_id,'session_id',e.session_id,'seq',e.seq,'kind',e.kind,'message_id',e.message_id,'author_id',e.author_id,'role',e.role,'content',e.content,'revision',e.revision,'created_at',e.created_at) order by e.seq) from public.collaboration_events e where e.session_id=v_session),'[]'::jsonb));
 elsif p_command='renew_run' then
   v_run:=(p_payload->>'run_id')::uuid; v_device:=(p_payload->>'device_id')::uuid; update public.collaboration_runs r set lease_expires_at=now()+interval '2 minutes' from public.collaboration_devices d,public.collaboration_sessions renew_session where r.run_id=v_run and r.requester_id=p_actor and r.device_id=v_device and r.lease_token=(p_payload->>'lease_token')::uuid and r.status='leased' and r.lease_expires_at>now() and d.device_id=v_device and d.user_id=p_actor and d.status='active' and d.fence=r.lease_fence and renew_session.session_id=r.session_id and renew_session.status in('active','closing') and renew_session.membership_revision=r.audience_revision and collaboration_private.is_session_member(r.session_id,p_actor) returning r.lease_expires_at into v_expiry; if not found then perform collaboration_private.fail('DEVICE_FENCED'); end if; return jsonb_build_object('ok',true,'lease_expires_at',v_expiry);
 elsif p_command='cancel_run' then
   v_run:=(p_payload->>'run_id')::uuid; update public.collaboration_runs set status=case when status='queued' then 'cancelled' else 'needs_reconciliation' end,completed_at=now(),lease_token=null,lease_expires_at=null where run_id=v_run and requester_id=p_actor and status in('queued','leased') and (status='queued' or lease_token=(p_payload->>'lease_token')::uuid) returning response_reservation_id into v_res; if not found then perform collaboration_private.fail('RUN_UNAVAILABLE'); end if; select reserved_bytes,session_id,org_id into v_bytes,v_session,v_org from public.collaboration_quota_reservations where reservation_id=v_res and status='active' for update; if found then update public.collaboration_sessions set reserved_bytes=greatest(0,reserved_bytes-v_bytes) where session_id=v_session; update public.collaboration_quota_counters set reserved_bytes=greatest(0,reserved_bytes-v_bytes) where org_id=v_org; update public.collaboration_quota_reservations set status='released',settled_at=now() where reservation_id=v_res; end if; return jsonb_build_object('ok',true,'run_id',v_run,'needs_reconciliation',(select status='needs_reconciliation' from public.collaboration_runs where run_id=v_run));
 elsif p_command='stop_session_run' then
   v_session:=(p_payload->>'session_id')::uuid; if not exists(select 1 from public.collaboration_session_members where session_id=v_session and user_id=p_actor and status='active') then perform collaboration_private.fail('FORBIDDEN'); end if; update public.collaboration_runs r set status=case when r.status='leased' then 'needs_reconciliation' else 'cancelled' end,completed_at=now(),lease_token=null,lease_expires_at=null where r.run_id=(select rr.run_id from public.collaboration_runs rr where rr.session_id=v_session and rr.status in('queued','leased') and (rr.requester_id=p_actor or exists(select 1 from public.collaboration_session_members sm where sm.session_id=v_session and sm.user_id=p_actor and sm.role='owner' and sm.status='active')) order by rr.created_at limit 1 for update skip locked) returning r.run_id,r.response_reservation_id into v_run,v_res; if not found then return jsonb_build_object('ok',true,'stopped',false); end if; select reserved_bytes,org_id into v_bytes,v_org from public.collaboration_quota_reservations where reservation_id=v_res and status='active' for update; if found then update public.collaboration_sessions set reserved_bytes=greatest(0,reserved_bytes-v_bytes) where session_id=v_session; update public.collaboration_quota_counters set reserved_bytes=greatest(0,reserved_bytes-v_bytes) where org_id=v_org; update public.collaboration_quota_reservations set status='released',settled_at=now() where reservation_id=v_res; end if; return jsonb_build_object('ok',true,'stopped',true,'run_id',v_run,'needs_reconciliation',(select status='needs_reconciliation' from public.collaboration_runs where run_id=v_run));
 elsif p_command='resolve_reconciliation' then
   v_run:=(p_payload->>'run_id')::uuid; update public.collaboration_runs set status='cancelled',completed_at=now() where run_id=v_run and requester_id=p_actor and status='needs_reconciliation'; if not found then perform collaboration_private.fail('RUN_UNAVAILABLE'); end if; return jsonb_build_object('ok',true,'run_id',v_run,'status','cancelled');
 elsif p_command='revoke_device' then
   v_device:=(p_payload->>'device_id')::uuid; update public.collaboration_devices set status='revoked',fence=fence+1 where device_id=v_device and user_id=p_actor; if not found then perform collaboration_private.fail('NOT_FOUND'); end if; update public.collaboration_runs set status='needs_reconciliation',lease_token=null,lease_expires_at=null where device_id=v_device and status='leased'; return jsonb_build_object('ok',true,'device_id',v_device);
 elsif p_command='complete_run' then
   v_run:=(p_payload->>'run_id')::uuid; select * into s from public.collaboration_sessions where session_id=(select session_id from public.collaboration_runs where run_id=v_run) for update;
   if exists(select 1 from public.collaboration_runs where run_id=v_run and requester_id=p_actor and status='completed') then return jsonb_build_object('ok',true,'deduplicated',true,'event_id',(select result_event_id from public.collaboration_runs where run_id=v_run)); end if;
   perform 1 from public.collaboration_runs r join public.collaboration_devices d on d.device_id=r.device_id join public.collaboration_sessions ss on ss.session_id=r.session_id where r.run_id=v_run and r.requester_id=p_actor and r.status='leased' and r.lease_token=(p_payload->>'lease_token')::uuid and r.lease_expires_at>now() and d.fence=r.lease_fence and d.status='active' and ss.status in('active','closing') and ss.membership_revision=r.audience_revision and collaboration_private.is_session_member(r.session_id,p_actor); if not found then perform collaboration_private.fail('DEVICE_FENCED'); end if;
   v_event:=(p_payload->>'event_id')::uuid; v_message:=(p_payload->>'message_id')::uuid; v_content:=p_payload->>'content'; v_bytes:=octet_length(convert_to(v_content,'UTF8'))+640;
   select response_reservation_id into v_res from public.collaboration_runs where run_id=v_run; if v_bytes>(select reserved_bytes from public.collaboration_quota_reservations where reservation_id=v_res and status='active') then perform collaboration_private.fail('QUOTA_EXCEEDED','reserved_response'); end if;
   v_seq:=s.next_seq; insert into public.collaboration_events values(v_event,s.org_id,s.session_id,v_seq,'message',v_message,p_actor,'assistant',v_content,1,v_bytes,encode(extensions.digest(convert_to(p_payload::text,'UTF8'),'sha256'),'hex'),now()); insert into public.collaboration_messages values(s.org_id,s.session_id,v_message,p_actor,'assistant',v_content,1,false,v_event,now(),now());
   update public.collaboration_quota_reservations set status='settled',used_bytes=v_bytes,settled_at=now() where reservation_id=v_res; update public.collaboration_runs set status='completed',result_event_id=v_event,completed_at=now(),lease_token=null,lease_expires_at=null where run_id=v_run;
   update public.collaboration_sessions set next_seq=next_seq+1,message_count=message_count+1,logical_bytes=logical_bytes+v_bytes,reserved_bytes=reserved_bytes-(select reserved_bytes from public.collaboration_quota_reservations where reservation_id=v_res),last_published_at=now(),revision=revision+1,status=case when message_count+1>=cfg.max_messages_per_session or logical_bytes+v_bytes>=cfg.session_limit_bytes then 'closing' else status end,final_seq=case when message_count+1>=cfg.max_messages_per_session or logical_bytes+v_bytes>=cfg.session_limit_bytes then next_seq else final_seq end where session_id=s.session_id;
   update public.collaboration_quota_counters set logical_bytes=logical_bytes+v_bytes,reserved_bytes=reserved_bytes-(select reserved_bytes from public.collaboration_quota_reservations where reservation_id=v_res) where org_id=s.org_id;
   if exists(select 1 from public.collaboration_slack_threads where session_id=s.session_id) then insert into public.collaboration_outbox(outbox_id,org_id,session_id,kind,payload,dedup_key) values(gen_random_uuid(),s.org_id,s.session_id,'slack_message',jsonb_build_object('content',v_content,'event_id',v_event),v_event::text) on conflict(dedup_key) do nothing; end if;
   perform pg_catalog.pg_notify('collaboration_hint',jsonb_build_object('session_id',s.session_id,'high_water',v_seq,'membership_revision',s.membership_revision)::text);
   return jsonb_build_object('ok',true,'event_id',v_event,'seq',v_seq);
 elsif p_command='request_archive' then
   if not cfg.archive_enabled then perform collaboration_private.fail('FEATURE_DISABLED'); end if; v_session:=(p_payload->>'session_id')::uuid; select * into s from public.collaboration_sessions where session_id=v_session for update;
   if not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if; if s.status not in ('active','closing') then perform collaboration_private.fail('INVALID_STATE'); end if; if exists(select 1 from public.collaboration_runs where session_id=v_session and status='needs_reconciliation') then perform collaboration_private.fail('RUN_ACTIVE'); end if;
   update public.collaboration_sessions set status='closing',final_seq=next_seq-1,revision=revision+1 where session_id=v_session; return jsonb_build_object('ok',true,'session_id',v_session,'status','closing','final_seq',s.next_seq-1);
 elsif p_command='archive_manifest' then
   v_session:=(p_payload->>'session_id')::uuid; if not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   select manifest_json,digest,archive_revision,final_seq into v_manifest,v_digest,v_archive_rev,v_seq from public.collaboration_archive_manifests where session_id=v_session order by archive_revision desc limit 1; if not found then perform collaboration_private.fail('ARCHIVE_PENDING'); end if;
   return jsonb_build_object('ok',true,'session_id',v_session,'archive_revision',v_archive_rev,'manifest_json',v_manifest,'digest',v_digest,'byte_length',octet_length(convert_to(v_manifest,'UTF8')),'final_seq',v_seq);
 elsif p_command='ack_archive' then
   v_session:=(p_payload->>'session_id')::uuid; v_archive_rev:=(p_payload->>'archive_revision')::bigint; v_device:=(p_payload->>'device_id')::uuid; v_digest:=p_payload->>'digest';
   if exists(select 1 from public.collaboration_archive_receipts where session_id=v_session and archive_revision=v_archive_rev and user_id=p_actor and device_id=v_device and digest=v_digest and byte_length=(p_payload->>'byte_length')::bigint and final_seq=(p_payload->>'final_seq')::bigint) then return jsonb_build_object('ok',true,'deduplicated',true); end if;
   perform 1 from public.collaboration_sessions where session_id=v_session and status='archive_pending' for update; if not found then perform collaboration_private.fail('INVALID_STATE'); end if;
   perform 1 from public.collaboration_archive_recipients ar join public.collaboration_archive_manifests am using(session_id,archive_revision) join public.collaboration_devices d on d.device_id=v_device where ar.session_id=v_session and ar.archive_revision=v_archive_rev and ar.user_id=p_actor and ar.eligible and d.user_id=p_actor and d.status='active' and am.status='pending' and am.membership_revision=(select membership_revision from public.collaboration_sessions where session_id=v_session) and am.digest=v_digest and am.byte_length=(p_payload->>'byte_length')::bigint and am.final_seq=(p_payload->>'final_seq')::bigint; if not found then perform collaboration_private.fail('ARCHIVE_RECEIPT_INVALID'); end if;
   insert into public.collaboration_archive_receipts values(v_session,v_archive_rev,p_actor,v_device,v_digest,(p_payload->>'byte_length')::bigint,(p_payload->>'final_seq')::bigint,now()) on conflict(session_id,archive_revision,user_id,device_id) do update set verified_at=excluded.verified_at,digest=excluded.digest,byte_length=excluded.byte_length,final_seq=excluded.final_seq;
   if not exists(select 1 from public.collaboration_archive_recipients ar where ar.session_id=v_session and ar.archive_revision=v_archive_rev and ar.eligible and ar.receipt_required and not exists(select 1 from public.collaboration_archive_receipts rr where rr.session_id=ar.session_id and rr.archive_revision=ar.archive_revision and rr.user_id=ar.user_id)) then update public.collaboration_archive_manifests set status='ready' where session_id=v_session and archive_revision=v_archive_rev; update public.collaboration_sessions set status='archive_pending' where session_id=v_session; insert into public.collaboration_purge_jobs(session_id,archive_revision) values(v_session,v_archive_rev) on conflict do nothing; end if; return jsonb_build_object('ok',true);
 elsif p_command='archive_status' then
   v_session:=(p_payload->>'session_id')::uuid; if not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
   return jsonb_build_object('ok',true,'session_id',v_session,'session_status',(select status from public.collaboration_sessions where session_id=v_session),'recipients',coalesce((select jsonb_agg(jsonb_build_object('user_id',ar.user_id,'received',exists(select 1 from public.collaboration_archive_receipts rr where rr.session_id=ar.session_id and rr.archive_revision=ar.archive_revision and rr.user_id=ar.user_id))) from public.collaboration_archive_recipients ar where ar.session_id=v_session and ar.archive_revision=(select max(archive_revision) from public.collaboration_archive_manifests where session_id=v_session)),'[]'::jsonb));
 elsif p_command='continue_session' then
   v_session:=(p_payload->>'session_id')::uuid; select * into s from public.collaboration_sessions where session_id=v_session; if not collaboration_private.is_session_member(v_session,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if; if s.status='active' then perform collaboration_private.fail('INVALID_STATE'); end if;
   v_event:=coalesce((p_payload->>'new_session_id')::uuid,gen_random_uuid()); perform 1 from public.collaboration_quota_counters where org_id=s.org_id for update; select count(*) into v_count from public.collaboration_sessions where org_id=s.org_id and status<>'archived_local'; if v_count>=cfg.max_sessions_per_org then perform collaboration_private.fail('QUOTA_EXCEEDED','session_count'); end if;
   insert into public.collaboration_sessions(session_id,org_id,created_by,continuation_of,title) values(v_event,s.org_id,p_actor,v_session,nullif(p_payload->>'title','')); insert into public.collaboration_session_members(org_id,session_id,user_id,role,status,joined_at) select s.org_id,v_event,user_id,case when user_id=p_actor then 'owner' else 'member' end,'active',now() from public.collaboration_session_members where session_id=v_session and status='active'; return jsonb_build_object('ok',true,'session_id',v_event,'continuation_of',v_session);
 elsif p_command='revoke_member' then
   v_session:=(p_payload->>'session_id')::uuid; select * into s from public.collaboration_sessions where session_id=v_session for update; if not exists(select 1 from public.collaboration_session_members where session_id=v_session and user_id=p_actor and role='owner' and status='active') then perform collaboration_private.fail('FORBIDDEN'); end if;
   update public.collaboration_session_members set status='revoked',revoked_at=now() where session_id=v_session and user_id=(p_payload->>'user_id')::uuid and role<>'owner' and status<>'revoked'; if not found then perform collaboration_private.fail('NOT_FOUND'); end if; update public.collaboration_sessions set membership_revision=membership_revision+1,revision=revision+1 where session_id=v_session;
   update public.collaboration_invites set status='revoked' where session_id=v_session and invited_user_id=(p_payload->>'user_id')::uuid and status='pending';
   if s.status='purging' then perform collaboration_private.fail('INVALID_STATE','purge_in_progress'); end if;
   if s.status in ('closing','archive_pending') then delete from public.collaboration_purge_jobs where session_id=v_session; delete from public.collaboration_archive_receipts where session_id=v_session; delete from public.collaboration_archive_recipients where session_id=v_session; delete from public.collaboration_archive_manifests where session_id=v_session; update public.collaboration_sessions set status='closing' where session_id=v_session; end if; return jsonb_build_object('ok',true);
 elsif p_command='begin_slack_link' then
   v_content:=replace(replace(encode(extensions.gen_random_bytes(18),'base64'),'/','_'),'+','-'); v_content:=replace(v_content,'=',''); v_hash:=encode(extensions.digest(convert_to(v_content,'UTF8'),'sha256'),'hex'); insert into public.collaboration_slack_link_challenges(code_hash,installation_id,user_id,expires_at) select v_hash,(p_payload->>'installation_id')::uuid,p_actor,now()+interval '10 minutes' from public.collaboration_slack_installations i where i.installation_id=(p_payload->>'installation_id')::uuid and i.status='active' and collaboration_private.is_org_member(i.org_id,p_actor); if not found then perform collaboration_private.fail('FORBIDDEN'); end if; return jsonb_build_object('ok',true,'code',v_content,'expires_in',600);
 elsif p_command='select_slack_channel' then
   v_org:=(p_payload->>'org_id')::uuid; if not exists(select 1 from public.collaboration_slack_installations i join public.collaboration_org_members om on om.org_id=i.org_id where i.installation_id=(p_payload->>'installation_id')::uuid and i.org_id=v_org and i.status='active' and om.user_id=p_actor and om.status='active' and om.role in('owner','admin')) then perform collaboration_private.fail('FORBIDDEN'); end if; insert into public.collaboration_slack_channels values((p_payload->>'installation_id')::uuid,p_payload->>'channel_id',v_org,p_actor,now()) on conflict(installation_id,channel_id) do update set selected_by=excluded.selected_by,selected_at=now() where collaboration_slack_channels.org_id=excluded.org_id; return jsonb_build_object('ok',true);
 else perform collaboration_private.fail('UNKNOWN_COMMAND'); end if;
 return '{}'::jsonb;
end $$;
revoke all on function collaboration_private.command(uuid,text,jsonb) from public;
grant execute on function collaboration_private.command(uuid,text,jsonb) to authenticated;
create function public.collaboration_command(p_command text,p_payload jsonb) returns jsonb language sql security invoker set search_path='' as $$ select collaboration_private.command((select auth.uid()),p_command,p_payload) $$;
revoke all on function public.collaboration_command(text,jsonb) from public,anon;
grant execute on function public.collaboration_command(text,jsonb) to authenticated;

-- Archive worker creates the exact canonical UTF-8 payload once and persists
-- that same string. Clients hash those exact bytes rather than reserializing JSON.
create function collaboration_private.prepare_archive(p_session uuid) returns jsonb language plpgsql security definer set search_path='' as $$
declare s public.collaboration_sessions%rowtype; v_rev bigint; v_text text; v_digest text;
begin
 select * into s from public.collaboration_sessions where session_id=p_session for update; if not found or s.status<>'closing' then perform collaboration_private.fail('INVALID_STATE'); end if;
 if exists(select 1 from public.collaboration_runs where session_id=p_session and status in ('queued','leased')) then perform collaboration_private.fail('RUN_ACTIVE'); end if;
 v_rev:=s.membership_revision; v_text:=jsonb_build_object('version',1,'session_id',s.session_id,'org_id',s.org_id,'final_seq',s.final_seq,'membership_revision',s.membership_revision,'events',coalesce((select jsonb_agg(jsonb_build_object('event_id',e.event_id,'session_id',e.session_id,'seq',e.seq,'kind',e.kind,'message_id',e.message_id,'author_id',e.author_id,'role',e.role,'content',e.content,'revision',e.revision,'created_at',e.created_at) order by e.seq) from public.collaboration_events e where e.session_id=p_session and e.seq<=s.final_seq),'[]'::jsonb),'files',coalesce((select jsonb_agg(jsonb_build_object('file_id',f.file_id,'sha256',f.sha256,'byte_length',f.expected_bytes)) from public.collaboration_file_reservations f where f.session_id=p_session and f.status='uploaded'),'[]'::jsonb))::text;
 v_digest:=encode(extensions.digest(convert_to(v_text,'UTF8'),'sha256'),'hex'); insert into public.collaboration_archive_manifests(session_id,archive_revision,membership_revision,final_seq,manifest_json,digest,byte_length) values(p_session,v_rev,s.membership_revision,s.final_seq,v_text,v_digest,octet_length(convert_to(v_text,'UTF8')));
 insert into public.collaboration_archive_recipients(session_id,archive_revision,user_id) select p_session,v_rev,user_id from public.collaboration_session_members where session_id=p_session and status='active'; update public.collaboration_sessions set status='archive_pending' where session_id=p_session; return jsonb_build_object('session_id',p_session,'archive_revision',v_rev,'digest',v_digest);
end $$;
revoke all on function collaboration_private.prepare_archive(uuid) from public,anon,authenticated;
grant execute on function collaboration_private.prepare_archive(uuid) to service_role;

create function collaboration_private.release_expired_reservations(p_limit integer default 100) returns integer language plpgsql security definer set search_path='' as $$
declare r record; n int:=0; begin for r in select * from public.collaboration_quota_reservations where kind='response' and status='active' and expires_at<now() order by expires_at for update skip locked limit least(p_limit,1000) loop update public.collaboration_quota_reservations set status='expired',settled_at=now() where reservation_id=r.reservation_id; update public.collaboration_sessions set reserved_bytes=greatest(0,reserved_bytes-r.reserved_bytes) where session_id=r.session_id; update public.collaboration_quota_counters set reserved_bytes=greatest(0,reserved_bytes-r.reserved_bytes) where org_id=r.org_id; update public.collaboration_runs set status=case when status='leased' then 'needs_reconciliation' else 'cancelled' end,completed_at=now(),lease_token=null,lease_expires_at=null where run_id=r.run_id and status in('queued','leased'); n:=n+1; end loop; return n; end $$;
revoke all on function collaboration_private.release_expired_reservations(integer) from public,anon,authenticated; grant execute on function collaboration_private.release_expired_reservations(integer) to service_role;

create function collaboration_private.claim_purge(p_worker uuid) returns jsonb language plpgsql security definer set search_path='' as $$
declare j public.collaboration_purge_jobs%rowtype; begin select * into j from public.collaboration_purge_jobs where status in('waiting','failed','database_done','objects_done') and (lease_expires_at is null or lease_expires_at<now()) and not exists(select 1 from public.collaboration_archive_recipients ar where ar.session_id=collaboration_purge_jobs.session_id and ar.archive_revision=collaboration_purge_jobs.archive_revision and ar.eligible and ar.receipt_required and not exists(select 1 from public.collaboration_archive_receipts rr where rr.session_id=ar.session_id and rr.archive_revision=ar.archive_revision and rr.user_id=ar.user_id)) order by updated_at for update skip locked limit 1; if not found then return null; end if; update public.collaboration_purge_jobs set status='claimed',lease_token=p_worker,lease_expires_at=now()+interval '2 minutes',attempts=attempts+1,updated_at=now() where session_id=j.session_id; update public.collaboration_sessions set status='purging' where session_id=j.session_id; return jsonb_build_object('session_id',j.session_id,'archive_revision',j.archive_revision,'db_checkpoint',j.db_checkpoint,'object_checkpoint',j.object_checkpoint); end $$;
create function collaboration_private.finish_purge_step(p_worker uuid,p_session uuid,p_step text,p_checkpoint jsonb default '{}'::jsonb) returns jsonb language plpgsql security definer set search_path='' as $$
declare j public.collaboration_purge_jobs%rowtype; a public.collaboration_archive_manifests%rowtype; s public.collaboration_sessions%rowtype; begin select * into j from public.collaboration_purge_jobs where session_id=p_session and lease_token=p_worker and lease_expires_at>now() for update; if not found then perform collaboration_private.fail('LEASE_LOST'); end if; if p_step='database' then delete from public.collaboration_messages where session_id=p_session; delete from public.collaboration_events where session_id=p_session; update public.collaboration_purge_jobs set status='database_done',db_checkpoint=p_checkpoint,lease_expires_at=now()+interval '2 minutes',updated_at=now() where session_id=p_session; elsif p_step='objects' then update public.collaboration_file_reservations set status='purged' where session_id=p_session; update public.collaboration_purge_jobs set status='objects_done',object_checkpoint=p_checkpoint,lease_expires_at=now()+interval '2 minutes',updated_at=now() where session_id=p_session; elsif p_step='complete' then select * into a from public.collaboration_archive_manifests where session_id=p_session and archive_revision=j.archive_revision; select * into s from public.collaboration_sessions where session_id=p_session; if j.status not in('database_done','objects_done') or exists(select 1 from public.collaboration_events where session_id=p_session) or exists(select 1 from public.collaboration_file_reservations where session_id=p_session and status='uploaded') then perform collaboration_private.fail('PURGE_INCOMPLETE'); end if; insert into public.collaboration_tombstones values(p_session,s.org_id,a.final_seq,a.digest,a.archive_revision,array(select user_id from public.collaboration_archive_recipients where session_id=p_session and archive_revision=a.archive_revision and eligible),coalesce((select jsonb_agg(jsonb_build_object('user_id',user_id,'device_id',device_id,'verified_at',verified_at)) from public.collaboration_archive_receipts where session_id=p_session and archive_revision=a.archive_revision),'[]'::jsonb),now(),now()); update public.collaboration_archive_manifests set manifest_json='{}',status='purged' where session_id=p_session and archive_revision=a.archive_revision; update public.collaboration_sessions set title=null,status='archived_local',logical_bytes=0,reserved_bytes=0,shared_file_bytes=0,archived_at=now() where session_id=p_session; update public.collaboration_quota_counters set logical_bytes=greatest(0,logical_bytes-s.logical_bytes),reserved_bytes=greatest(0,reserved_bytes-s.reserved_bytes),shared_file_bytes=greatest(0,shared_file_bytes-s.shared_file_bytes) where org_id=s.org_id; update public.collaboration_purge_jobs set status='complete',lease_token=null,lease_expires_at=null,updated_at=now() where session_id=p_session; else perform collaboration_private.fail('INVALID_STEP'); end if; return jsonb_build_object('ok',true,'step',p_step); end $$;
revoke all on function collaboration_private.claim_purge(uuid), collaboration_private.finish_purge_step(uuid,uuid,text,jsonb) from public,anon,authenticated; grant execute on function collaboration_private.claim_purge(uuid), collaboration_private.finish_purge_step(uuid,uuid,text,jsonb) to service_role;

create function collaboration_private.slack_command(p_command text,p_payload jsonb) returns jsonb language plpgsql security definer set search_path='' as $$
declare i public.collaboration_slack_installations%rowtype; s public.collaboration_sessions%rowtype; cfg public.collaboration_config%rowtype; v_id uuid; v_token uuid; v_row record; v_hash text; v_session uuid; v_event uuid; v_message uuid; v_run uuid; v_res uuid; v_content text; v_bytes bigint; v_response_bytes bigint; v_fence bigint; v_count integer; begin
 if p_command='begin_install' then insert into public.collaboration_slack_oauth_states(state_hash,actor_id,org_id,expires_at) values(p_payload->>'state_hash',(p_payload->>'actor_id')::uuid,(p_payload->>'org_id')::uuid,(p_payload->>'expires_at')::timestamptz); return jsonb_build_object('ok',true);
 elsif p_command='consume_install' then update public.collaboration_slack_oauth_states set consumed_at=now() where state_hash=p_payload->>'state_hash' and consumed_at is null and expires_at>now() returning actor_id,org_id into v_row; if not found then perform collaboration_private.fail('OAUTH_STATE_INVALID'); end if; return jsonb_build_object('ok',true,'actor_id',v_row.actor_id,'org_id',v_row.org_id);
 elsif p_command='save_install' then
   perform 1 from public.collaboration_slack_oauth_states where state_hash=p_payload->>'state_hash' and actor_id=(p_payload->>'actor_id')::uuid and org_id=(p_payload->>'org_id')::uuid and consumed_at is not null; if not found then perform collaboration_private.fail('OAUTH_STATE_INVALID'); end if;
   insert into public.collaboration_slack_installations(org_id,team_id,bot_user_id,token_ciphertext) values((p_payload->>'org_id')::uuid,p_payload->>'team_id',p_payload->>'bot_user_id',p_payload->>'token_ciphertext') on conflict(team_id) do update set org_id=excluded.org_id,bot_user_id=excluded.bot_user_id,token_ciphertext=excluded.token_ciphertext,status='active',revoked_at=null returning installation_id into v_id;
   if nullif(p_payload->>'authed_slack_user_id','') is not null then insert into public.collaboration_slack_sender_links values(v_id,p_payload->>'authed_slack_user_id',(p_payload->>'actor_id')::uuid,now()) on conflict(installation_id,slack_user_id) do update set user_id=excluded.user_id,linked_at=now(); end if; delete from public.collaboration_slack_oauth_states where state_hash=p_payload->>'state_hash'; return jsonb_build_object('ok',true,'installation_id',v_id);
 elsif p_command='redeem_link' then
   if coalesce(p_payload->>'code','') !~ '^[A-Za-z0-9_-]{20,128}$' then perform collaboration_private.fail('LINK_CODE_INVALID'); end if; v_hash:=encode(extensions.digest(convert_to(p_payload->>'code','UTF8'),'sha256'),'hex');
   update public.collaboration_slack_link_challenges c set consumed_at=now() from public.collaboration_slack_installations i where c.code_hash=v_hash and c.installation_id=i.installation_id and i.team_id=p_payload->>'team_id' and c.consumed_at is null and c.expires_at>now() returning c.installation_id,c.user_id into v_row; if not found then perform collaboration_private.fail('LINK_CODE_INVALID'); end if; insert into public.collaboration_slack_sender_links values(v_row.installation_id,p_payload->>'slack_user_id',v_row.user_id,now()) on conflict(installation_id,slack_user_id) do update set user_id=excluded.user_id,linked_at=now(); return jsonb_build_object('ok',true,'user_id',v_row.user_id);
 elsif p_command='enqueue_event' then select * into i from public.collaboration_slack_installations where team_id=p_payload->>'team_id' and status='active'; if not found then perform collaboration_private.fail('INSTALLATION_NOT_FOUND'); end if; if octet_length(convert_to((p_payload->'event')::text,'UTF8'))>65536 then perform collaboration_private.fail('PAYLOAD_TOO_LARGE'); end if; insert into public.collaboration_slack_inbox(installation_id,event_id,event) values(i.installation_id,p_payload->>'event_id',p_payload->'event') on conflict do nothing; return jsonb_build_object('ok',true,'accepted',true);
 elsif p_command='ingest_message' then
   select * into cfg from public.collaboration_config where singleton for share; if not cfg.shared_text_enabled or not cfg.shared_execution_enabled or not cfg.slack_enabled then perform collaboration_private.fail('FEATURE_DISABLED'); end if;
   select i.* into i from public.collaboration_slack_installations i where i.installation_id=(p_payload->>'installation_id')::uuid and i.status='active'; if not found then perform collaboration_private.fail('INSTALLATION_NOT_FOUND'); end if;
   select l.user_id into v_row from public.collaboration_slack_sender_links l join public.collaboration_org_members om on om.org_id=i.org_id and om.user_id=l.user_id and om.status='active' where l.installation_id=i.installation_id and l.slack_user_id=p_payload->>'slack_user_id'; if not found then return jsonb_build_object('ok',true,'ignored',true,'needs_link',true); end if;
   if not exists(select 1 from public.collaboration_devices where user_id=v_row.user_id and status='active') then return jsonb_build_object('ok',true,'waiting_for_executor',true); end if;
   select t.session_id into v_session from public.collaboration_slack_threads t where t.installation_id=i.installation_id and t.channel_id=p_payload->>'channel_id' and t.root_thread_ts=p_payload->>'thread_ts';
   if not found then
     if not coalesce((p_payload->>'is_mention')::boolean,false) or not exists(select 1 from public.collaboration_slack_channels where installation_id=i.installation_id and channel_id=p_payload->>'channel_id' and org_id=i.org_id) then return jsonb_build_object('ok',true,'ignored',true); end if;
     perform 1 from public.collaboration_quota_counters where org_id=i.org_id for update;
     if not exists(select 1 from (select * from public.collaboration_capacity_measurements order by measured_at desc limit 1) latest where latest.measured_at>now()-interval '36 hours' and latest.database_bytes+cfg.admission_reserve_bytes<cfg.project_physical_stop_bytes and latest.collaboration_bytes<262144000) then perform collaboration_private.fail('CAPACITY_UNMEASURED'); end if;
     select count(*) into v_count from public.collaboration_sessions where org_id=i.org_id and status<>'archived_local'; if v_count>=cfg.max_sessions_per_org then perform collaboration_private.fail('QUOTA_EXCEEDED','session_count'); end if;
     v_session:=gen_random_uuid(); insert into public.collaboration_sessions(session_id,org_id,created_by,title) values(v_session,i.org_id,v_row.user_id,'Slack conversation'); insert into public.collaboration_session_members values(i.org_id,v_session,v_row.user_id,'owner','active',null,now(),null); insert into public.collaboration_slack_threads values(i.org_id,i.installation_id,p_payload->>'channel_id',p_payload->>'thread_ts',v_session,now());
   elsif not collaboration_private.is_session_member(v_session,v_row.user_id) then perform collaboration_private.fail('FORBIDDEN'); end if;
   v_event:=(p_payload->>'event_id')::uuid; v_message:=coalesce((p_payload->>'message_id')::uuid,v_event); v_run:=coalesce((p_payload->>'run_id')::uuid,gen_random_uuid()); v_res:=coalesce((p_payload->>'reservation_id')::uuid,gen_random_uuid()); v_content:=p_payload->>'content'; v_bytes:=octet_length(convert_to(v_content,'UTF8'))+640; v_response_bytes:=least(coalesce((p_payload->>'response_reservation_bytes')::bigint,cfg.default_response_reservation_bytes),cfg.session_limit_bytes); v_hash:=encode(extensions.digest(convert_to(v_row.user_id::text||':slack:'||p_payload::text,'UTF8'),'sha256'),'hex');
   select * into s from public.collaboration_sessions where session_id=v_session for update; if s.status<>'active' then perform collaboration_private.fail('SESSION_CLOSED'); end if;
   if exists(select 1 from public.collaboration_events where event_id=v_event) then if exists(select 1 from public.collaboration_events where event_id=v_event and request_hash=v_hash and author_id=v_row.user_id) then return (select jsonb_build_object('ok',true,'deduplicated',true,'session_id',e.session_id,'event_id',e.event_id,'seq',e.seq,'message_id',e.message_id,'run_id',r.run_id,'reservation_id',r.response_reservation_id) from public.collaboration_events e left join public.collaboration_runs r on r.session_id=e.session_id and r.request_event_id=e.event_id where e.event_id=v_event); else perform collaboration_private.fail('IDEMPOTENCY_CONFLICT'); end if; end if;
   perform 1 from public.collaboration_quota_counters where org_id=i.org_id for update;
   if s.message_count+2+(select count(*) from public.collaboration_runs where session_id=v_session and status in('queued','leased'))>cfg.max_messages_per_session then perform collaboration_private.fail('QUOTA_EXCEEDED','reserved_message_slot'); end if;
   if s.logical_bytes+s.reserved_bytes+v_bytes+v_response_bytes>cfg.session_limit_bytes or (select logical_bytes+reserved_bytes from public.collaboration_quota_counters where org_id=i.org_id)+v_bytes+v_response_bytes>cfg.org_limit_bytes or (select coalesce(sum(logical_bytes+reserved_bytes),0) from public.collaboration_quota_counters)+v_bytes+v_response_bytes>cfg.project_logical_limit_bytes then perform collaboration_private.fail('QUOTA_EXCEEDED','bytes'); end if;
   insert into public.collaboration_events values(v_event,i.org_id,v_session,s.next_seq,'message',v_message,v_row.user_id,'user',v_content,1,v_bytes,v_hash,now()); insert into public.collaboration_messages values(i.org_id,v_session,v_message,v_row.user_id,'user',v_content,1,false,v_event,now(),now());
   insert into public.collaboration_quota_reservations(reservation_id,org_id,session_id,run_id,kind,reserved_bytes,expires_at) values(v_res,i.org_id,v_session,v_run,'response',v_response_bytes,now()+interval '2 hours'); insert into public.collaboration_runs(run_id,org_id,session_id,request_event_id,requester_id,provider_payer_id,audience_revision,response_reservation_id) values(v_run,i.org_id,v_session,v_event,v_row.user_id,v_row.user_id,s.membership_revision,v_res);
   update public.collaboration_sessions set next_seq=next_seq+1,message_count=message_count+1,logical_bytes=logical_bytes+v_bytes,reserved_bytes=reserved_bytes+v_response_bytes,last_published_at=now(),revision=revision+1 where session_id=v_session; update public.collaboration_quota_counters set logical_bytes=logical_bytes+v_bytes,reserved_bytes=reserved_bytes+v_response_bytes where org_id=i.org_id;
   perform pg_catalog.pg_notify('collaboration_hint',jsonb_build_object('session_id',v_session,'high_water',s.next_seq,'membership_revision',s.membership_revision)::text);
   return jsonb_build_object('ok',true,'session_id',v_session,'event_id',v_event,'seq',s.next_seq,'message_id',v_message,'run_id',v_run,'reservation_id',v_res);
 elsif p_command='claim_inbox' then
   select x.*,i.team_id,i.token_ciphertext into v_row from public.collaboration_slack_inbox x join public.collaboration_slack_installations i using(installation_id) where i.status='active' and (x.status='pending' or (x.status='failed' and coalesce(x.retry_after,now())<=now()) or (x.status='claimed' and x.lease_expires_at<now())) order by x.received_at for update of x skip locked limit 1; if not found then return null; end if; v_token:=gen_random_uuid(); update public.collaboration_slack_inbox set status='claimed',lease_token=v_token,lease_expires_at=now()+interval '2 minutes',attempts=attempts+1,fence=fence+1 where inbox_id=v_row.inbox_id returning fence into v_fence; return jsonb_build_object('id',v_row.inbox_id,'fence',v_fence,'event',v_row.event,'team_id',v_row.team_id,'installation_id',v_row.installation_id,'token_ciphertext',v_row.token_ciphertext,'event_id',v_row.event_id);
 elsif p_command='finish_inbox' then
   update public.collaboration_slack_inbox set status=case p_payload->>'status' when 'complete' then 'processed' when 'retry' then 'failed' else status end,retry_after=case when p_payload->>'status'='retry' then coalesce((p_payload->>'retry_after')::timestamptz,now()+interval '1 minute') else null end,last_error_code=p_payload->>'error_code',completed_at=case when p_payload->>'status'='complete' then now() else null end,lease_token=null,lease_expires_at=null where inbox_id=(p_payload->>'id')::uuid and fence=(p_payload->>'fence')::bigint and status='claimed'; if not found then perform collaboration_private.fail('LEASE_LOST'); end if; return jsonb_build_object('ok',true);
 elsif p_command='claim_outbox' then
   update public.collaboration_outbox set status='uncertain',lease_token=null,lease_expires_at=null where status='claimed' and lease_expires_at<now();
   select o.*,i.team_id,i.token_ciphertext,t.channel_id,t.root_thread_ts into v_row from public.collaboration_outbox o join public.collaboration_slack_threads t on t.session_id=o.session_id join public.collaboration_slack_installations i on i.installation_id=t.installation_id where i.status='active' and (o.status in('pending','uncertain') or (o.status='failed' and coalesce(o.retry_after,now())<=now())) order by o.created_at for update of o skip locked limit 1; if not found then return null; end if; v_token:=gen_random_uuid(); update public.collaboration_outbox set status=case when v_row.status='uncertain' then 'uncertain' else 'claimed' end,lease_token=v_token,lease_expires_at=now()+interval '2 minutes',attempts=attempts+1,fence=fence+1 where outbox_id=v_row.outbox_id returning fence into v_fence; return jsonb_build_object('id',v_row.outbox_id,'fence',v_fence,'team_id',v_row.team_id,'token_ciphertext',v_row.token_ciphertext,'channel_id',v_row.channel_id,'thread_ts',v_row.root_thread_ts,'content',v_row.payload->>'content','client_msg_id',v_row.dedup_key,'status',v_row.status,'reconcile_cursor',v_row.reconcile_cursor);
 elsif p_command='complete_outbox' then
   if p_payload->>'status' not in('complete','retry','uncertain','manual_review') then perform collaboration_private.fail('INVALID_STATUS'); end if; update public.collaboration_outbox set status=case p_payload->>'status' when 'complete' then 'sent' when 'retry' then 'failed' when 'uncertain' then 'uncertain' else 'manual_review' end,retry_after=case when p_payload->>'status'='retry' then coalesce((p_payload->>'retry_after')::timestamptz,now()+interval '1 minute') else null end,slack_ts=coalesce(p_payload->>'slack_ts',slack_ts),reconcile_cursor=coalesce(p_payload->>'reconcile_cursor',reconcile_cursor),completed_at=case when p_payload->>'status'='complete' then now() else null end,lease_token=null,lease_expires_at=null where outbox_id=(p_payload->>'id')::uuid and fence=(p_payload->>'fence')::bigint and status in('claimed','uncertain'); if not found then perform collaboration_private.fail('LEASE_LOST'); end if; return jsonb_build_object('ok',true);
 elsif p_command='uninstall' then update public.collaboration_slack_installations set status='revoked',token_ciphertext='',revoked_at=now() where team_id=p_payload->>'team_id'; return jsonb_build_object('ok',true);
 else perform collaboration_private.fail('UNKNOWN_COMMAND'); end if; return '{}'::jsonb; end $$;
revoke all on function collaboration_private.slack_command(text,jsonb) from public,anon,authenticated; grant execute on function collaboration_private.slack_command(text,jsonb) to service_role;
create function public.collaboration_slack_command(p_command text,p_payload jsonb) returns jsonb language sql security invoker set search_path='' as $$ select collaboration_private.slack_command(p_command,p_payload) $$;
revoke all on function public.collaboration_slack_command(text,jsonb) from public,anon,authenticated; grant execute on function public.collaboration_slack_command(text,jsonb) to service_role;

create function public.collaboration_capacity_command(p_database_bytes bigint,p_collaboration_bytes bigint,p_storage_bytes bigint)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare v_id bigint; begin if p_database_bytes<0 or p_collaboration_bytes<0 or p_storage_bytes<0 then perform collaboration_private.fail('INVALID_MEASUREMENT'); end if; insert into public.collaboration_capacity_measurements(database_bytes,collaboration_bytes,storage_bytes) values(p_database_bytes,p_collaboration_bytes,p_storage_bytes) returning measurement_id into v_id; return jsonb_build_object('ok',true,'measurement_id',v_id,'measured_at',now()); end $$;
revoke all on function public.collaboration_capacity_command(bigint,bigint,bigint) from public,anon,authenticated; grant execute on function public.collaboration_capacity_command(bigint,bigint,bigint) to service_role;

revoke all on all functions in schema collaboration_private from public,anon,authenticated;
grant execute on function collaboration_private.command(uuid,text,jsonb), collaboration_private.is_org_member(uuid,uuid), collaboration_private.is_session_member(uuid,uuid) to authenticated;
grant execute on all functions in schema collaboration_private to service_role;
