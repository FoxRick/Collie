-- Durable archive orchestration. This migration deliberately exposes one
-- service-role-only maintenance RPC; clients continue to use collaboration_command.

alter table public.collaboration_sessions
  add column archive_warning_at timestamptz,
  add column archive_manifest_bytes bigint not null default 0 check (archive_manifest_bytes >= 0);

alter table public.collaboration_archive_recipients
  add column membership_revision bigint;
update public.collaboration_archive_recipients ar
set membership_revision = am.membership_revision
from public.collaboration_archive_manifests am
where am.session_id = ar.session_id and am.archive_revision = ar.archive_revision;
alter table public.collaboration_archive_recipients alter column membership_revision set not null;

alter table public.collaboration_purge_jobs
  drop constraint collaboration_purge_jobs_status_check;
alter table public.collaboration_purge_jobs
  add constraint collaboration_purge_jobs_status_check
  check (status in ('waiting','claimed','objects_done','complete','failed'));

-- Once an object-deletion lease is claimed the frozen recipient set must be
-- immutable until the lease is retried or purge finishes. This closes the
-- check -> Storage API delete race for every SQL mutation path, not only the
-- authenticated command implementation.
create function collaboration_private.prevent_purging_membership_change()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_session uuid;
begin
  if tg_op = 'DELETE' then v_session := old.session_id; else v_session := new.session_id; end if;
  if exists (select 1 from public.collaboration_sessions
    where session_id = v_session and status = 'purging')
  then perform collaboration_private.fail('PURGE_MEMBERSHIP_FROZEN'); end if;
  if tg_op = 'DELETE' then return old; else return new; end if;
end $$;
revoke all on function collaboration_private.prevent_purging_membership_change() from public, anon, authenticated, service_role;
drop trigger if exists collaboration_freeze_purging_membership on public.collaboration_session_members;
create trigger collaboration_freeze_purging_membership
before insert or update or delete on public.collaboration_session_members
for each row execute function collaboration_private.prevent_purging_membership_change();

-- Preserve evidence when the authenticated command invalidates an archive due
-- to a membership revocation, before the legacy command removes stale rows.
create function collaboration_private.audit_archive_recipient_invalidation()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if old.status <> 'revoked' and new.status = 'revoked' then
    insert into public.collaboration_audit(org_id, session_id, actor_id, action, detail)
    select new.org_id, new.session_id, (select auth.uid()), 'archive_recipients_invalidated',
      jsonb_build_object(
        'revoked_user_id', new.user_id,
        'old_membership_revision', s.membership_revision,
        'recipient_ledgers', coalesce((
          select jsonb_agg(jsonb_build_object(
            'archive_revision', ar.archive_revision,
            'membership_revision', ar.membership_revision,
            'user_id', ar.user_id,
            'eligible', ar.eligible,
            'receipt_required', ar.receipt_required,
            'received', exists (
              select 1 from public.collaboration_archive_receipts rr
              where rr.session_id = ar.session_id
                and rr.archive_revision = ar.archive_revision
                and rr.user_id = ar.user_id)))
          from public.collaboration_archive_recipients ar
          where ar.session_id = new.session_id), '[]'::jsonb))
    from public.collaboration_sessions s where s.session_id = new.session_id;
  end if;
  return new;
end $$;
revoke all on function collaboration_private.audit_archive_recipient_invalidation() from public, anon, authenticated, service_role;
drop trigger if exists collaboration_audit_archive_recipient_invalidation on public.collaboration_session_members;
create trigger collaboration_audit_archive_recipient_invalidation
before update of status on public.collaboration_session_members
for each row execute function collaboration_private.audit_archive_recipient_invalidation();

create or replace function collaboration_private.prepare_archive(p_session uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  s public.collaboration_sessions%rowtype;
  cfg public.collaboration_config%rowtype;
  v_rev bigint;
  v_text text;
  v_digest text;
  v_bytes bigint;
  v_event_count bigint;
  v_max_seq bigint;
begin
  select * into s from public.collaboration_sessions where session_id = p_session for update;
  if not found or s.status <> 'closing' then perform collaboration_private.fail('INVALID_STATE'); end if;
  select * into cfg from public.collaboration_config where singleton for share;
  if exists (select 1 from public.collaboration_runs where session_id = p_session and status in ('queued','leased','needs_reconciliation'))
     or exists (select 1 from public.collaboration_quota_reservations where session_id = p_session and status = 'active')
     or exists (select 1 from public.collaboration_outbox where session_id = p_session and status in ('pending','claimed','failed','uncertain','manual_review'))
  then perform collaboration_private.fail('ARCHIVE_WORK_ACTIVE'); end if;

  -- Closing stops new admissions, but already accepted work may still publish.
  -- Freeze only here, after all such work has settled, at the authoritative
  -- canonical high-water mark.
  s.final_seq := s.next_seq - 1;
  select count(*), coalesce(max(seq), 0) into v_event_count, v_max_seq
  from public.collaboration_events where session_id = p_session;
  if v_event_count <> s.final_seq or v_max_seq <> s.final_seq
  then perform collaboration_private.fail('ARCHIVE_HISTORY_INCOMPLETE'); end if;
  update public.collaboration_sessions
  set final_seq = s.final_seq, revision = revision + 1
  where session_id = p_session;
  v_rev := s.membership_revision;
  v_text := jsonb_build_object(
    'version', 1, 'session_id', s.session_id, 'org_id', s.org_id,
    'final_seq', s.final_seq, 'final_sequence', s.final_seq,
    'membership_revision', s.membership_revision,
    'events', coalesce((select jsonb_agg(jsonb_build_object(
      'event_id', e.event_id, 'session_id', e.session_id, 'seq', e.seq,
      'kind', e.kind, 'message_id', e.message_id, 'author_id', e.author_id,
      'role', e.role, 'content', e.content, 'revision', e.revision,
      'created_at', e.created_at) order by e.seq)
      from public.collaboration_events e
      where e.session_id = p_session and e.seq <= s.final_seq), '[]'::jsonb),
    'files', coalesce((select jsonb_agg(jsonb_build_object(
      'id', f.file_id, 'bucket', 'collaboration-files', 'path', f.object_path,
      'sha256', f.sha256, 'byte_length', f.expected_bytes) order by f.file_id)
      from public.collaboration_file_reservations f
      where f.session_id = p_session and f.status = 'uploaded'), '[]'::jsonb),
    'recipients', coalesce((select jsonb_agg(jsonb_build_object('user_id', sm.user_id) order by sm.user_id)
      from public.collaboration_session_members sm
      join public.collaboration_org_members om on om.org_id = sm.org_id and om.user_id = sm.user_id
      where sm.session_id = p_session and sm.status = 'active' and om.status = 'active'), '[]'::jsonb)
  )::text;
  v_bytes := octet_length(convert_to(v_text, 'UTF8'));
  v_digest := encode(extensions.digest(convert_to(v_text, 'UTF8'), 'sha256'), 'hex');

  -- Archive completion may consume the already reserved admission headroom.
  -- A physical-capacity measurement is still required before duplicating data.
  if not coalesce((select
      measured_at > now() - interval '36 hours'
      and database_bytes + cfg.admission_reserve_bytes < cfg.project_physical_stop_bytes
    from public.collaboration_capacity_measurements
    order by measured_at desc limit 1), false)
  then perform collaboration_private.fail('CAPACITY_UNMEASURED'); end if;

  insert into public.collaboration_archive_manifests(
    session_id, archive_revision, membership_revision, final_seq,
    manifest_json, digest, byte_length, status)
  values (p_session, v_rev, v_rev, s.final_seq, v_text, v_digest, v_bytes, 'pending');
  insert into public.collaboration_archive_recipients(
    session_id, archive_revision, user_id, eligible, receipt_required, membership_revision)
  select p_session, v_rev, sm.user_id, true, true, v_rev
  from public.collaboration_session_members sm
  join public.collaboration_org_members om on om.org_id = sm.org_id and om.user_id = sm.user_id
  where sm.session_id = p_session and sm.status = 'active' and om.status = 'active';

  -- The initial command migration charged conservative canonical-manifest
  -- headroom with each event before accepting it. Record the exact manifest
  -- size without charging the duplicated payload a second time.
  update public.collaboration_sessions
  set status = 'archive_pending', archive_manifest_bytes = v_bytes
  where session_id = p_session;
  return jsonb_build_object('session_id', p_session, 'archive_revision', v_rev,
    'manifest_digest', v_digest, 'byte_length', v_bytes, 'final_seq', s.final_seq);
end $$;
revoke all on function collaboration_private.prepare_archive(uuid) from public, anon, authenticated, service_role;

create function collaboration_private.archive_guard(
  p_session uuid, p_fence bigint, p_digest text, p_require_claim boolean default true)
returns public.collaboration_purge_jobs language plpgsql security definer set search_path = '' as $$
declare j public.collaboration_purge_jobs%rowtype; s public.collaboration_sessions%rowtype;
begin
  select * into j from public.collaboration_purge_jobs where session_id = p_session for update;
  if not found or (p_require_claim and (j.status <> 'claimed' or j.fence <> p_fence or j.lease_expires_at <= now()))
  then perform collaboration_private.fail('LEASE_LOST'); end if;
  select * into s from public.collaboration_sessions where session_id = p_session for update;
  if s.status <> 'purging'
    or not exists (select 1 from public.collaboration_archive_manifests am
      where am.session_id = p_session and am.archive_revision = j.archive_revision
        and am.membership_revision = s.membership_revision and am.digest = p_digest
        and am.final_seq = s.final_seq and s.final_seq = s.next_seq - 1
        and am.status in ('ready','purging'))
    or exists (select 1 from public.collaboration_events e
      where e.session_id = p_session and e.seq > s.final_seq)
    or (select count(*) from public.collaboration_events e where e.session_id = p_session) <> s.final_seq
    or (select coalesce(max(e.seq), 0) from public.collaboration_events e where e.session_id = p_session) <> s.final_seq
    or exists (select 1 from public.collaboration_runs where session_id = p_session and status in ('queued','leased','needs_reconciliation'))
    or exists (select 1 from public.collaboration_quota_reservations where session_id = p_session and status = 'active')
    or exists (select 1 from public.collaboration_outbox where session_id = p_session and status in ('pending','claimed','failed','uncertain','manual_review'))
    or exists (select 1 from public.collaboration_archive_recipients ar
      where ar.session_id = p_session and ar.archive_revision = j.archive_revision
        and ar.membership_revision = s.membership_revision and ar.eligible and ar.receipt_required
        and not exists (select 1 from public.collaboration_archive_receipts rr
          where rr.session_id = ar.session_id and rr.archive_revision = ar.archive_revision
            and rr.user_id = ar.user_id and rr.digest = p_digest))
  then perform collaboration_private.fail('PURGE_GUARD_FAILED'); end if;
  return j;
end $$;
revoke all on function collaboration_private.archive_guard(uuid,bigint,text,boolean) from public, anon, authenticated, service_role;

create function collaboration_private.maintenance_command(p_command text, p_payload jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  cfg public.collaboration_config%rowtype;
  s public.collaboration_sessions%rowtype;
  j public.collaboration_purge_jobs%rowtype;
  a public.collaboration_archive_manifests%rowtype;
  v_session uuid;
  v_fence bigint;
  v_digest text;
  v_object uuid;
  v_limit integer;
  v_count integer := 0;
  v_receipts jsonb;
  v_retained bigint;
  v_file_cleanup jsonb;
begin
  if p_payload is null or jsonb_typeof(p_payload) <> 'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
  select * into cfg from public.collaboration_config where singleton for share;

  if p_command = 'advance_archives' then
    v_limit := least(greatest(coalesce((p_payload->>'limit')::integer, 10), 1), 100);
    perform collaboration_private.release_expired_reservations(v_limit * 10);
    select public.collaboration_files_release_expired(v_limit * 10) into v_file_cleanup;

    if cfg.archive_enabled then
    with warned as (
      update public.collaboration_sessions x set archive_warning_at = now(), revision = revision + 1
      where x.session_id in (select session_id from public.collaboration_sessions
        where status = 'active' and archive_warning_at is null
          and last_published_at <= now() - interval '13 days'
        order by last_published_at for update skip locked limit v_limit)
      returning x.org_id, x.session_id, x.last_published_at
    )
    insert into public.collaboration_audit(org_id, session_id, action, detail)
    select org_id, session_id, 'archive_warning', jsonb_build_object('last_published_at', last_published_at) from warned;

    for s in
      select * from public.collaboration_sessions x
      where x.status = 'active'
        and (x.last_published_at <= now() - interval '14 days'
          or x.message_count >= cfg.max_messages_per_session
          or x.logical_bytes + x.reserved_bytes >= cfg.session_limit_bytes)
        and not exists (select 1 from public.collaboration_runs r where r.session_id = x.session_id and r.status in ('queued','leased','needs_reconciliation'))
        and not exists (select 1 from public.collaboration_quota_reservations q where q.session_id = x.session_id and q.status = 'active')
        and not exists (select 1 from public.collaboration_outbox o where o.session_id = x.session_id and o.status in ('pending','claimed','failed','uncertain','manual_review'))
      order by x.last_published_at for update skip locked limit v_limit
    loop
      update public.collaboration_invites set status = 'revoked'
        where session_id = s.session_id and status = 'pending';
      update public.collaboration_session_members set status = 'revoked', revoked_at = now()
        where session_id = s.session_id and status = 'invited';
      update public.collaboration_sessions set status = 'closing', final_seq = next_seq - 1,
        membership_revision = membership_revision + 1, revision = revision + 1
        where session_id = s.session_id;
      v_count := v_count + 1;
    end loop;
    end if;

    for s in select * from public.collaboration_sessions x where x.status = 'closing'
      order by x.last_published_at for update skip locked limit v_limit
    loop
      begin perform collaboration_private.prepare_archive(s.session_id); v_count := v_count + 1;
      exception when sqlstate 'P0001' then null; end;
    end loop;
    return jsonb_build_object('ok', true, 'advanced', v_count,
      'file_cleanup', coalesce(v_file_cleanup, jsonb_build_object('released', 0, 'objects', '[]'::jsonb)));

  elsif p_command = 'checkpoint_file_cleanup' then
    return public.collaboration_files_cleanup_complete((p_payload->>'file_id')::uuid);

  elsif p_command = 'claim_purge' then
    select * into j from public.collaboration_purge_jobs x
    where (x.status in ('waiting','failed') or (x.status = 'claimed' and x.lease_expires_at < now()))
      and exists (select 1 from public.collaboration_archive_manifests am
        join public.collaboration_sessions ss using(session_id)
        where am.session_id = x.session_id and am.archive_revision = x.archive_revision
          and (am.status = 'ready' or (x.status = 'claimed' and x.lease_expires_at < now() and am.status = 'purging'))
          and am.membership_revision = ss.membership_revision
          and am.final_seq = ss.final_seq and ss.final_seq = ss.next_seq - 1
          and ss.status in ('archive_pending','purging'))
      and not exists (select 1 from public.collaboration_events e
        join public.collaboration_sessions ss on ss.session_id = e.session_id
        where e.session_id = x.session_id and e.seq > ss.final_seq)
      and (select count(*) from public.collaboration_events e where e.session_id = x.session_id) =
        (select ss.final_seq from public.collaboration_sessions ss where ss.session_id = x.session_id)
      and (select coalesce(max(e.seq), 0) from public.collaboration_events e where e.session_id = x.session_id) =
        (select ss.final_seq from public.collaboration_sessions ss where ss.session_id = x.session_id)
      and not exists (select 1 from public.collaboration_archive_recipients ar
        where ar.session_id = x.session_id and ar.archive_revision = x.archive_revision
          and ar.eligible and ar.receipt_required and not exists (
            select 1 from public.collaboration_archive_receipts rr
            where rr.session_id = ar.session_id and rr.archive_revision = ar.archive_revision
              and rr.user_id = ar.user_id))
    order by x.updated_at for update skip locked limit 1;
    if not found then return null; end if;
    update public.collaboration_purge_jobs set status = 'claimed', fence = fence + 1,
      lease_token = gen_random_uuid(), lease_expires_at = now() + interval '2 minutes',
      attempts = attempts + 1, last_error_code = null, updated_at = now()
      where session_id = j.session_id returning * into j;
    update public.collaboration_sessions set status = 'purging' where session_id = j.session_id;
    update public.collaboration_archive_manifests set status = 'purging'
      where session_id = j.session_id and archive_revision = j.archive_revision returning * into a;
    return jsonb_build_object('session_id', j.session_id, 'fence', j.fence,
      'manifest_digest', a.digest, 'objects', coalesce((select jsonb_agg(jsonb_build_object(
        'id', f.file_id, 'bucket', 'collaboration-files', 'path', f.object_path) order by f.file_id)
        from public.collaboration_file_reservations f
        where f.session_id = j.session_id and f.status = 'uploaded'
          and not (j.object_checkpoint ? f.file_id::text)), '[]'::jsonb));

  elsif p_command in ('check_purge','checkpoint_purge','finish_purge') then
    v_session := (p_payload->>'session_id')::uuid;
    v_fence := (p_payload->>'fence')::bigint;
    v_digest := p_payload->>'manifest_digest';
    j := collaboration_private.archive_guard(v_session, v_fence, v_digest, true);

    if p_command = 'check_purge' then
      v_object := (p_payload->>'object_id')::uuid;
      perform 1 from public.collaboration_file_reservations f
      where f.file_id = v_object and f.session_id = v_session and f.status = 'uploaded'
        and not (j.object_checkpoint ? f.file_id::text)
        and not exists (select 1 from public.collaboration_file_reservations other
          where other.object_path = f.object_path and other.session_id <> v_session and other.status <> 'purged');
      if not found then perform collaboration_private.fail('OBJECT_NOT_EXCLUSIVE'); end if;
      update public.collaboration_purge_jobs set lease_expires_at = now() + interval '2 minutes', updated_at = now()
        where session_id = v_session;
      return jsonb_build_object('ok', true);
    elsif p_command = 'checkpoint_purge' then
      v_object := (p_payload->>'object_id')::uuid;
      perform 1 from public.collaboration_file_reservations
        where file_id = v_object and session_id = v_session and status = 'uploaded';
      if not found and not (j.object_checkpoint ? v_object::text) then perform collaboration_private.fail('OBJECT_NOT_FOUND'); end if;
      update public.collaboration_file_reservations set status = 'purged'
        where file_id = v_object and session_id = v_session;
      update public.collaboration_purge_jobs
      set object_checkpoint = object_checkpoint || jsonb_build_object(v_object::text, true),
        lease_expires_at = now() + interval '2 minutes', updated_at = now()
      where session_id = v_session;
      return jsonb_build_object('ok', true);
    else
      if exists (select 1 from public.collaboration_file_reservations
        where session_id = v_session and status = 'uploaded')
      then perform collaboration_private.fail('PURGE_INCOMPLETE'); end if;
      select * into s from public.collaboration_sessions where session_id = v_session for update;
      select * into a from public.collaboration_archive_manifests
        where session_id = v_session and archive_revision = j.archive_revision;
      select coalesce(jsonb_agg(jsonb_build_object('user_id', rr.user_id,
        'device_id', rr.device_id, 'verified_at', rr.verified_at)), '[]'::jsonb)
        into v_receipts from public.collaboration_archive_receipts rr
        where rr.session_id = v_session and rr.archive_revision = j.archive_revision;
      insert into public.collaboration_tombstones(session_id, org_id, final_seq, digest,
        archive_revision, eligible_user_ids, receipt_evidence, archived_at, purge_completed_at)
      values(v_session, s.org_id, a.final_seq, a.digest, a.archive_revision,
        array(select ar.user_id from public.collaboration_archive_recipients ar
          where ar.session_id = v_session and ar.archive_revision = a.archive_revision and ar.eligible),
        v_receipts, now(), now())
      on conflict(session_id) do update set final_seq = excluded.final_seq, digest = excluded.digest,
        archive_revision = excluded.archive_revision, eligible_user_ids = excluded.eligible_user_ids,
        receipt_evidence = excluded.receipt_evidence, archived_at = excluded.archived_at,
        purge_completed_at = excluded.purge_completed_at;

      -- This is the only command that erases database payload. Minimal receipt,
      -- recipient, tombstone, session, audit, and completed-job metadata remains.
      delete from public.collaboration_file_reservations where session_id = v_session;
      delete from public.collaboration_messages where session_id = v_session;
      delete from public.collaboration_runs where session_id = v_session;
      delete from public.collaboration_events where session_id = v_session;
      delete from public.collaboration_quota_reservations where session_id = v_session;
      delete from public.collaboration_outbox where session_id = v_session;
      delete from public.collaboration_slack_inbox si using public.collaboration_slack_threads st
        where st.session_id = v_session and si.installation_id = st.installation_id
          and (si.event->>'session_id' = v_session::text
            or si.event#>>'{event,session_id}' = v_session::text
            or ((si.event->>'channel_id' = st.channel_id or si.event#>>'{event,channel}' = st.channel_id)
              and (si.event->>'root_thread_ts' = st.root_thread_ts
                or coalesce(si.event#>>'{event,thread_ts}', si.event#>>'{event,ts}') = st.root_thread_ts)));
      update public.collaboration_archive_manifests set manifest_json = '{}', byte_length = 0, status = 'purged'
        where session_id = v_session;
      update public.collaboration_sessions set title = null, status = 'archived_local',
        reserved_bytes = 0, archive_manifest_bytes = 0,
        shared_file_bytes = 0, archived_at = now(), revision = revision + 1
        where session_id = v_session;
      update public.collaboration_purge_jobs set status = 'complete', lease_token = null,
        lease_expires_at = null, updated_at = now() where session_id = v_session;
      select coalesce(sum(retained_bytes), 0)::bigint into v_retained from (
        select pg_column_size(to_jsonb(x))::bigint retained_bytes from public.collaboration_sessions x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_session_members x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_archive_manifests x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_archive_recipients x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_archive_receipts x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_tombstones x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_purge_jobs x where x.session_id = v_session
        union all select pg_column_size(to_jsonb(x))::bigint from public.collaboration_audit x where x.session_id = v_session
      ) retained;
      update public.collaboration_sessions set logical_bytes = v_retained where session_id = v_session;
      update public.collaboration_quota_counters set
        logical_bytes = greatest(0, logical_bytes - s.logical_bytes + v_retained),
        reserved_bytes = greatest(0, reserved_bytes - s.reserved_bytes),
        shared_file_bytes = greatest(0, shared_file_bytes - s.shared_file_bytes), updated_at = now()
        where org_id = s.org_id;
      return jsonb_build_object('ok', true, 'session_id', v_session, 'status', 'archived_local');
    end if;

  elsif p_command = 'retry_purge' then
    v_session := (p_payload->>'session_id')::uuid;
    v_fence := (p_payload->>'fence')::bigint;
    update public.collaboration_purge_jobs set status = 'failed', lease_token = null,
      lease_expires_at = null, last_error_code = 'WORKER_RETRY', updated_at = now()
      where session_id = v_session and status = 'claimed' and fence = v_fence
        and exists (select 1 from public.collaboration_sessions ss
          join public.collaboration_archive_manifests am on am.session_id = ss.session_id
          where ss.session_id = v_session and ss.status = 'purging'
            and am.archive_revision = collaboration_purge_jobs.archive_revision
            and am.membership_revision = ss.membership_revision
            and am.status = 'purging');
    if not found then perform collaboration_private.fail('LEASE_LOST'); end if;
    update public.collaboration_archive_manifests am set status = 'ready'
      where am.session_id = v_session and am.archive_revision = (
        select archive_revision from public.collaboration_purge_jobs where session_id = v_session);
    update public.collaboration_sessions set status = 'archive_pending' where session_id = v_session;
    return jsonb_build_object('ok', true);
  else
    perform collaboration_private.fail('UNKNOWN_COMMAND');
  end if;
end $$;
revoke all on function collaboration_private.maintenance_command(text,jsonb) from public, anon, authenticated, service_role;

create function public.collaboration_maintenance_command(p_command text, p_payload jsonb)
returns jsonb language sql security definer set search_path = '' as $$
  select collaboration_private.maintenance_command(p_command, p_payload)
$$;
revoke all on function public.collaboration_maintenance_command(text,jsonb) from public, anon, authenticated;
grant execute on function public.collaboration_maintenance_command(text,jsonb) to service_role;

-- Retire the first migration's coarse maintenance entry points. They could
-- erase relational payload before object deletion and did not revalidate a fence.
revoke all on function collaboration_private.claim_purge(uuid) from public, anon, authenticated, service_role;
revoke all on function collaboration_private.finish_purge_step(uuid,uuid,text,jsonb) from public, anon, authenticated, service_role;
