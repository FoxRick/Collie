-- Per-tenant rollout, explicit audience consent, and durable mention/invite notifications.
-- Global collaboration_config switches remain authoritative in the base command.

create table public.collaboration_user_rollouts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default false,
  updated_at timestamptz not null default now()
);
create table public.collaboration_org_rollouts (
  org_id uuid primary key references public.collaboration_organizations(org_id) on delete cascade,
  enabled boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.collaboration_invites
  add column audience_policy jsonb,
  add column audience_policy_version integer,
  add column consent_required boolean not null default true,
  add column audience_consented_at timestamptz,
  add column audience_consented_version integer;
alter table public.collaboration_invites add constraint collaboration_invite_audience_policy_check
  check (audience_policy is null or (jsonb_typeof(audience_policy)='object'
    and jsonb_typeof(audience_policy->'summary')='string'
    and jsonb_typeof(audience_policy->'includes_existing_history')='boolean'));

create table public.collaboration_session_audience_consents (
  session_id uuid not null,
  user_id uuid not null,
  policy_version integer not null check(policy_version=1),
  consented_at timestamptz not null default now(),
  primary key(session_id,user_id),
  foreign key(session_id,user_id) references public.collaboration_session_members(session_id,user_id) on delete cascade
);

create table public.collaboration_notifications (
  notification_id bigint generated always as identity primary key,
  recipient_id uuid not null references auth.users(id) on delete cascade,
  org_id uuid not null references public.collaboration_organizations(org_id) on delete cascade,
  session_id uuid references public.collaboration_sessions(session_id) on delete cascade,
  kind text not null check(kind in ('session_invite','mention')),
  actor_id uuid not null references auth.users(id),
  event_id uuid references public.collaboration_events(event_id) on delete cascade,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  read_at timestamptz,
  unique(recipient_id,kind,event_id),
  check(kind='mention' or event_id is null)
);
create index collaboration_notifications_recipient_cursor_idx
  on public.collaboration_notifications(recipient_id,notification_id);

alter table public.collaboration_user_rollouts enable row level security;
alter table public.collaboration_org_rollouts enable row level security;
alter table public.collaboration_notifications enable row level security;
alter table public.collaboration_session_audience_consents enable row level security;
revoke all on public.collaboration_user_rollouts,public.collaboration_org_rollouts,public.collaboration_notifications
  from public,anon,authenticated;
revoke all on public.collaboration_session_audience_consents from public,anon,authenticated;
grant select on public.collaboration_user_rollouts,public.collaboration_org_rollouts,public.collaboration_notifications to authenticated;
grant select on public.collaboration_session_audience_consents to authenticated;
create policy collaboration_user_rollout_read on public.collaboration_user_rollouts for select to authenticated
  using(user_id=(select auth.uid()));
create policy collaboration_org_rollout_read on public.collaboration_org_rollouts for select to authenticated
  using(collaboration_private.is_org_member(org_id,(select auth.uid())));
create policy collaboration_notification_read on public.collaboration_notifications for select to authenticated
  using(recipient_id=(select auth.uid()));
create policy collaboration_audience_consent_read on public.collaboration_session_audience_consents for select to authenticated
  using(collaboration_private.is_session_member(session_id,(select auth.uid())));

create function collaboration_private.rollout_enabled(p_actor uuid,p_org uuid default null)
returns boolean language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.collaboration_user_rollouts u where u.user_id=p_actor and u.enabled)
    and (p_org is null or exists(select 1 from public.collaboration_org_rollouts o where o.org_id=p_org and o.enabled))
$$;
revoke all on function collaboration_private.rollout_enabled(uuid,uuid) from public,anon,authenticated,service_role;

create function collaboration_private.command_with_rollout(p_actor uuid,p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_org uuid; v_session uuid; v_result jsonb; v_event uuid; v_invite uuid;
 v_cursor bigint; v_limit integer; v_high bigint; v_policy jsonb; v_policy_version integer;
begin
  if p_actor is null or p_actor is distinct from (select auth.uid()) then perform collaboration_private.fail('UNAUTHENTICATED'); end if;
  if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;

  if p_command='notifications' then
    if not collaboration_private.rollout_enabled(p_actor,null) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
    v_cursor:=coalesce((p_payload->>'cursor')::bigint,0);
    v_limit:=least(greatest(coalesce((p_payload->>'limit')::integer,100),1),500);
    select coalesce(max(notification_id),v_cursor) into v_high from public.collaboration_notifications where recipient_id=p_actor;
    return jsonb_build_object('ok',true,'notifications',coalesce((select jsonb_agg(jsonb_build_object(
      'notification_id',n.notification_id,'org_id',n.org_id,'session_id',n.session_id,'kind',n.kind,
      'actor_id',n.actor_id,'event_id',n.event_id,'payload',n.payload,'created_at',n.created_at,'read_at',n.read_at)
      order by n.notification_id) from (select * from public.collaboration_notifications
        where recipient_id=p_actor and notification_id>v_cursor order by notification_id limit v_limit)n),'[]'::jsonb),
      'next_cursor',coalesce((select max(notification_id) from (select notification_id from public.collaboration_notifications
        where recipient_id=p_actor and notification_id>v_cursor order by notification_id limit v_limit)x),v_cursor),'high_water',v_high);
  elsif p_command='ack_notifications' then
    if not collaboration_private.rollout_enabled(p_actor,null) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
    v_high:=(p_payload->>'through')::bigint;
    update public.collaboration_notifications set read_at=coalesce(read_at,now())
      where recipient_id=p_actor and notification_id<=v_high;
    return jsonb_build_object('ok',true,'through',v_high);
  end if;

  if p_command='create_organization' then
    if not collaboration_private.rollout_enabled(p_actor,null) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
  elsif p_command in ('bootstrap','directory','read_events','list_pending_runs','archive_manifest','archive_status',
    'ack_archive','stop_session_run','resolve_reconciliation','cancel_run','revoke_member','request_archive') then
    null; -- Preserve read, recovery, revocation, and archive access during rollback.
  else
    if p_command in ('claim_run','renew_run','complete_run') then
      select org_id into v_org from public.collaboration_runs where run_id=(p_payload->>'run_id')::uuid;
    elsif p_command='accept_invite' then
      select org_id into v_org from public.collaboration_invites where invite_id=(p_payload->>'invite_id')::uuid;
    elsif p_command='accept_organization_invite' then
      select org_id into v_org from public.collaboration_org_invites where invite_id=(p_payload->>'invite_id')::uuid;
    elsif p_command in ('begin_slack_link','select_slack_channel') then
      select org_id into v_org from public.collaboration_slack_installations where installation_id=(p_payload->>'installation_id')::uuid;
    elsif p_command in ('invite','append_message','edit_message','delete_message','continue_session','publish_routine') then
      v_session:=(p_payload->>'session_id')::uuid;
      select org_id into v_org from public.collaboration_sessions where session_id=v_session;
    elsif p_command in ('create_session','invite_organization') then v_org:=(p_payload->>'org_id')::uuid;
    end if;
    if not collaboration_private.rollout_enabled(p_actor,v_org) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
  end if;

  if p_command='invite' then
    v_policy:=p_payload->'audience_policy';
    v_policy_version:=coalesce((p_payload->>'audience_policy_version')::integer,1);
    if jsonb_typeof(v_policy) is distinct from 'object'
      or v_policy->>'summary' is distinct from 'Owners may invite additional participants who can read all published history.'
      or coalesce((v_policy->>'includes_existing_history')::boolean,false) is not true
      or v_policy_version<>1
    then perform collaboration_private.fail('AUDIENCE_POLICY_REQUIRED'); end if;
    if exists(select 1 from public.collaboration_session_members sm where sm.session_id=(p_payload->>'session_id')::uuid
      and sm.status='active' and not exists(select 1 from public.collaboration_session_audience_consents c
        where c.session_id=sm.session_id and c.user_id=sm.user_id and c.policy_version=1))
    then perform collaboration_private.fail('AUDIENCE_CONSENT_REQUIRED','existing_participant'); end if;
    v_result:=collaboration_private.command(p_actor,p_command,p_payload);
    v_invite:=(v_result->>'invite_id')::uuid;
    update public.collaboration_invites set audience_policy=v_policy,audience_policy_version=v_policy_version,
      consent_required=true where invite_id=v_invite;
    insert into public.collaboration_notifications(recipient_id,org_id,session_id,kind,actor_id,payload)
      select invited_user_id,org_id,session_id,'session_invite',p_actor,jsonb_build_object(
        'invite_id',invite_id,'audience_policy',v_policy,'audience_policy_version',v_policy_version,'consent_required',true)
      from public.collaboration_invites where invite_id=v_invite;
    return v_result || jsonb_build_object('audience_policy',v_policy,'audience_policy_version',v_policy_version,'consent_required',true);
  elsif p_command='accept_invite' then
    v_invite:=(p_payload->>'invite_id')::uuid;
    select audience_policy_version into v_policy_version from public.collaboration_invites
      where invite_id=v_invite and invited_user_id=p_actor and status='pending';
    if not found then perform collaboration_private.fail('NOT_FOUND'); end if;
    if not coalesce((p_payload->>'audience_consent')::boolean,false)
      or (p_payload->>'audience_policy_version')::integer is distinct from v_policy_version
      or v_policy_version is distinct from 1
    then perform collaboration_private.fail('AUDIENCE_CONSENT_REQUIRED'); end if;
    v_result:=collaboration_private.command(p_actor,p_command,p_payload);
    update public.collaboration_invites set audience_consented_at=now(),audience_consented_version=v_policy_version
      where invite_id=v_invite;
    insert into public.collaboration_session_audience_consents(session_id,user_id,policy_version)
      values((v_result->>'session_id')::uuid,p_actor,v_policy_version)
      on conflict(session_id,user_id) do update set policy_version=excluded.policy_version,consented_at=now();
    return v_result || jsonb_build_object('audience_consented_version',v_policy_version);
  end if;

  v_result:=collaboration_private.command(p_actor,p_command,p_payload);
  if p_command='bootstrap' then
    return jsonb_set(v_result,'{invites}',coalesce((select jsonb_agg(x) from (
      select jsonb_build_object('kind','session','invite_id',i.invite_id,'org_id',i.org_id,
        'session_id',i.session_id,'invited_by',i.invited_by,'audience_policy',i.audience_policy,
        'audience_policy_version',i.audience_policy_version,'consent_required',i.consent_required) x
      from public.collaboration_invites i where i.invited_user_id=p_actor and i.status='pending'
      union all
      select jsonb_build_object('kind','organization','invite_id',oi.invite_id,'org_id',oi.org_id,
        'invited_by',oi.invited_by,'display_name',oi.display_name)
      from public.collaboration_org_invites oi where oi.invited_user_id=p_actor and oi.status='pending')q),'[]'::jsonb),true)
      || jsonb_build_object('rollout',jsonb_build_object('user_enabled',collaboration_private.rollout_enabled(p_actor,null)));
  elsif p_command='create_organization' then
    insert into public.collaboration_org_rollouts(org_id,enabled) values((v_result->>'org_id')::uuid,false)
      on conflict(org_id) do nothing;
  elsif p_command='create_session' then
    insert into public.collaboration_session_audience_consents(session_id,user_id,policy_version)
      values((v_result->>'session_id')::uuid,p_actor,1) on conflict do nothing;
  elsif p_command='append_message' and p_payload ? 'mentioned_user_ids' then
    v_event:=(v_result->>'event_id')::uuid; v_session=(v_result->>'session_id')::uuid;
    if jsonb_typeof(p_payload->'mentioned_user_ids')<>'array' then perform collaboration_private.fail('INVALID_MENTIONS'); end if;
    insert into public.collaboration_notifications(recipient_id,org_id,session_id,kind,actor_id,event_id,payload)
      select sm.user_id,sm.org_id,sm.session_id,'mention',p_actor,v_event,
        jsonb_build_object('message_id',v_result->>'message_id','seq',(v_result->>'seq')::bigint)
      from public.collaboration_session_members sm
      join (select distinct value::uuid user_id from jsonb_array_elements_text(p_payload->'mentioned_user_ids')) requested using(user_id)
      where sm.session_id=v_session and sm.status='active' and sm.user_id<>p_actor
      on conflict(recipient_id,kind,event_id) do nothing;
  end if;
  return v_result;
end $$;
revoke all on function collaboration_private.command_with_rollout(uuid,text,jsonb) from public,anon,authenticated,service_role;
grant execute on function collaboration_private.command_with_rollout(uuid,text,jsonb) to authenticated;
revoke execute on function collaboration_private.command(uuid,text,jsonb) from authenticated;

create or replace function public.collaboration_command(p_command text,p_payload jsonb)
returns jsonb language sql security invoker set search_path='' as $$
  select collaboration_private.command_with_rollout((select auth.uid()),p_command,p_payload)
$$;
revoke all on function public.collaboration_command(text,jsonb) from public,anon;
grant execute on function public.collaboration_command(text,jsonb) to authenticated;

create function public.collaboration_rollout_command(p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
  if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
  if p_command='set_user' then
    insert into public.collaboration_user_rollouts(user_id,enabled) values((p_payload->>'user_id')::uuid,(p_payload->>'enabled')::boolean)
      on conflict(user_id) do update set enabled=excluded.enabled,updated_at=now();
  elsif p_command='set_organization' then
    insert into public.collaboration_org_rollouts(org_id,enabled) values((p_payload->>'org_id')::uuid,(p_payload->>'enabled')::boolean)
      on conflict(org_id) do update set enabled=excluded.enabled,updated_at=now();
  else perform collaboration_private.fail('UNKNOWN_COMMAND'); end if;
  return jsonb_build_object('ok',true);
end $$;
revoke all on function public.collaboration_rollout_command(text,jsonb) from public,anon,authenticated;
grant execute on function public.collaboration_rollout_command(text,jsonb) to service_role;

create function collaboration_private.file_command_with_rollout(p_actor uuid,p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_org uuid; v_session uuid;
begin
  if p_actor is null or p_actor is distinct from (select auth.uid()) then perform collaboration_private.fail('UNAUTHENTICATED'); end if;
  if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
  if p_command not in ('authorize_download','list','cancel') then
    if p_command='reserve' then v_session=(p_payload->>'session_id')::uuid;
    else select session_id into v_session from public.collaboration_file_reservations where file_id=(p_payload->>'file_id')::uuid;
    end if;
    select org_id into v_org from public.collaboration_sessions where session_id=v_session;
    if not collaboration_private.rollout_enabled(p_actor,v_org) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
  end if;
  return collaboration_private.file_command(p_actor,p_command,p_payload);
end $$;
revoke all on function collaboration_private.file_command_with_rollout(uuid,text,jsonb) from public,anon,authenticated,service_role;
grant execute on function collaboration_private.file_command_with_rollout(uuid,text,jsonb) to authenticated;
revoke execute on function collaboration_private.file_command(uuid,text,jsonb) from authenticated;
create or replace function public.collaboration_files_command(p_command text,p_payload jsonb)
returns jsonb language sql security invoker set search_path='' as $$
  select collaboration_private.file_command_with_rollout((select auth.uid()),p_command,p_payload)
$$;
revoke all on function public.collaboration_files_command(text,jsonb) from public,anon;
grant execute on function public.collaboration_files_command(text,jsonb) to authenticated;

create function collaboration_private.slack_command_with_rollout(p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_org uuid; v_actor uuid; v_result jsonb;
begin
  if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
  if p_command='ingest_message' then
    select i.org_id,l.user_id into v_org,v_actor from public.collaboration_slack_installations i
      join public.collaboration_slack_sender_links l on l.installation_id=i.installation_id
      where i.installation_id=(p_payload->>'installation_id')::uuid and i.status='active'
        and l.slack_user_id=p_payload->>'slack_user_id';
    if found and not collaboration_private.rollout_enabled(v_actor,v_org) then perform collaboration_private.fail('ROLLOUT_DISABLED'); end if;
  end if;
  v_result:=collaboration_private.slack_command(p_command,p_payload);
  if p_command='ingest_message' and v_actor is not null and v_result ? 'session_id' then
    insert into public.collaboration_session_audience_consents(session_id,user_id,policy_version)
      values((v_result->>'session_id')::uuid,v_actor,1) on conflict do nothing;
  end if;
  return v_result;
end $$;
revoke all on function collaboration_private.slack_command_with_rollout(text,jsonb) from public,anon,authenticated,service_role;
grant execute on function collaboration_private.slack_command_with_rollout(text,jsonb) to service_role;
revoke execute on function collaboration_private.slack_command(text,jsonb) from service_role;
create or replace function public.collaboration_slack_command(p_command text,p_payload jsonb)
returns jsonb language sql security invoker set search_path='' as $$
  select collaboration_private.slack_command_with_rollout(p_command,p_payload)
$$;
revoke all on function public.collaboration_slack_command(text,jsonb) from public,anon,authenticated;
grant execute on function public.collaboration_slack_command(text,jsonb) to service_role;
