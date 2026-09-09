-- Complete shared-file lifecycle. File capacity is held at the configured
-- maximum until trusted verification observes the stored object.
alter table public.collaboration_sessions
  add column file_reserved_bytes bigint not null default 0 check (file_reserved_bytes >= 0);
alter table public.collaboration_quota_counters
  add column file_reserved_bytes bigint not null default 0 check (file_reserved_bytes >= 0);
alter table public.collaboration_file_reservations
  add column display_name text,
  add column content_type text,
  add column verified_bytes bigint,
  add column version integer not null default 1 check (version = 1),
  add column reserved_session_revision bigint,
  add column reserved_membership_revision bigint,
  add column uploaded_at timestamptz,
  add column failed_at timestamptz,
  add column failure_code text;
alter table public.collaboration_file_reservations
  drop constraint collaboration_file_reservations_status_check;
alter table public.collaboration_file_reservations
  add constraint collaboration_file_reservations_status_check
  check (status in ('reserved','uploaded','cleanup_pending','failed','cancelled','purged'));
alter table public.collaboration_file_reservations
  add constraint collaboration_file_expected_bytes_check check (expected_bytes between 1 and 5242880),
  add constraint collaboration_file_verified_bytes_check check (verified_bytes is null or verified_bytes between 1 and 5242880),
  add constraint collaboration_file_sha256_check check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  add constraint collaboration_file_display_name_check check (display_name is null or (length(display_name) between 1 and 255 and display_name !~ '[[:cntrl:]]'));

create index collaboration_files_session_status_idx
  on public.collaboration_file_reservations(session_id,status,created_at);
-- Uploads pass through the trusted Edge handler so claimed metadata can never
-- mark an unchecked object complete.
drop policy if exists collaboration_files_insert on storage.objects;
grant select on public.collaboration_file_reservations to authenticated;
alter table public.collaboration_file_reservations enable row level security;
create policy collaboration_file_metadata_read on public.collaboration_file_reservations
  for select to authenticated
  using (status = 'uploaded' and (select collaboration_private.is_session_member(session_id,(select auth.uid()))));

create function collaboration_private.release_file_reservation(p_file_id uuid,p_status text,p_failure text default null)
returns void language plpgsql security definer set search_path='' as $$
declare f public.collaboration_file_reservations%rowtype;
begin
  select * into f from public.collaboration_file_reservations where file_id=p_file_id for update;
  if not found or f.status <> 'reserved' then return; end if;
  update public.collaboration_file_reservations set status=p_status, failed_at=case when p_status='failed' then now() end,
    failure_code=p_failure where file_id=p_file_id;
  update public.collaboration_quota_reservations set status='released',settled_at=now() where reservation_id=f.reservation_id and status='active';
  update public.collaboration_sessions set file_reserved_bytes=greatest(0,file_reserved_bytes-(select max_file_bytes from public.collaboration_config where singleton))
    where session_id=f.session_id;
  update public.collaboration_quota_counters set file_reserved_bytes=greatest(0,file_reserved_bytes-(select max_file_bytes from public.collaboration_config where singleton)),updated_at=now()
    where org_id=f.org_id;
end $$;
revoke all on function collaboration_private.release_file_reservation(uuid,text,text) from public,anon,authenticated,service_role;

create function collaboration_private.file_command(p_actor uuid,p_command text,p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare cfg public.collaboration_config%rowtype; s public.collaboration_sessions%rowtype;
 f public.collaboration_file_reservations%rowtype; v_file uuid; v_res uuid; v_bytes bigint;
 v_name text; v_type text; v_hash text; v_project bigint;
begin
  if p_actor is null then perform collaboration_private.fail('AUTH_REQUIRED'); end if;
  if p_payload is null or jsonb_typeof(p_payload)<>'object' then perform collaboration_private.fail('INVALID_PAYLOAD'); end if;
  select * into cfg from public.collaboration_config where singleton for update;
  if p_command='reserve' then
    v_file=(p_payload->>'file_id')::uuid; v_bytes=(p_payload->>'byte_length')::bigint;
    v_name=nullif(btrim(p_payload->>'name'),''); v_type=left(coalesce(nullif(btrim(p_payload->>'content_type'),''),'application/octet-stream'),255);
    v_hash=lower(p_payload->>'sha256');
    if v_name is null or length(v_name)>255 or v_name ~ '[[:cntrl:]]' or v_type ~ '[[:cntrl:]]'
      or v_bytes<1 or v_bytes>least(cfg.max_file_bytes,5242880) or v_hash !~ '^[0-9a-f]{64}$' then
      perform collaboration_private.fail('INVALID_FILE');
    end if;
    select * into s from public.collaboration_sessions where session_id=(p_payload->>'session_id')::uuid for update;
    if not found or s.status<>'active' then perform collaboration_private.fail('SESSION_NOT_ACTIVE'); end if;
    if s.revision is distinct from (p_payload->>'expected_session_revision')::bigint
      or s.membership_revision is distinct from (p_payload->>'membership_revision')::bigint then perform collaboration_private.fail('REVISION_CONFLICT'); end if;
    if not collaboration_private.is_session_member(s.session_id,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
    -- Serialize all project-wide file admission beneath the singleton config lock.
    perform 1 from public.collaboration_quota_counters order by org_id for update;
    select coalesce(sum(shared_file_bytes+file_reserved_bytes),0) into v_project from public.collaboration_quota_counters;
    if s.shared_file_bytes+s.file_reserved_bytes+cfg.max_file_bytes>cfg.max_session_file_bytes
      or v_project+cfg.max_file_bytes>cfg.max_project_file_bytes then perform collaboration_private.fail('FILE_QUOTA_EXCEEDED'); end if;
    select * into f from public.collaboration_file_reservations where file_id=v_file;
    if found then
      if f.session_id=s.session_id and f.uploader_id=p_actor and f.expected_bytes=v_bytes and f.sha256=v_hash and f.display_name=v_name then
        return jsonb_build_object('ok',true,'file_id',f.file_id,'object_path',f.object_path,'status',f.status,'version',f.version);
      end if;
      perform collaboration_private.fail('IDEMPOTENCY_CONFLICT');
    end if;
    v_res=gen_random_uuid();
    insert into public.collaboration_quota_reservations(reservation_id,org_id,session_id,kind,reserved_bytes,expires_at)
      values(v_res,s.org_id,s.session_id,'file',cfg.max_file_bytes,now()+interval '30 minutes');
    insert into public.collaboration_file_reservations(file_id,reservation_id,org_id,session_id,uploader_id,object_path,expected_bytes,sha256,display_name,content_type,reserved_session_revision,reserved_membership_revision)
      values(v_file,v_res,s.org_id,s.session_id,p_actor,s.org_id::text||'/'||s.session_id::text||'/'||p_actor::text||'/'||v_file::text||'/v1',v_bytes,v_hash,v_name,v_type,s.revision,s.membership_revision)
      returning * into f;
    update public.collaboration_sessions set file_reserved_bytes=file_reserved_bytes+cfg.max_file_bytes where session_id=s.session_id;
    update public.collaboration_quota_counters set file_reserved_bytes=file_reserved_bytes+cfg.max_file_bytes,updated_at=now() where org_id=s.org_id;
    return jsonb_build_object('ok',true,'file_id',f.file_id,'object_path',f.object_path,'status','reserved','version',1,'expires_at',now()+interval '30 minutes');
  elsif p_command in ('authorize_upload','authorize_download') then
    select * into f from public.collaboration_file_reservations where file_id=(p_payload->>'file_id')::uuid for share;
    if not found then perform collaboration_private.fail('FILE_NOT_FOUND'); end if;
    select * into s from public.collaboration_sessions where session_id=f.session_id for share;
    if p_command='authorize_upload' then
      if f.uploader_id<>p_actor or f.status<>'reserved' or s.status<>'active'
        or s.membership_revision<>f.reserved_membership_revision
        or not collaboration_private.is_session_member(f.session_id,p_actor)
        or not exists(select 1 from public.collaboration_quota_reservations q where q.reservation_id=f.reservation_id and q.status='active' and q.expires_at>now())
      then perform collaboration_private.fail('UPLOAD_NOT_AUTHORIZED'); end if;
    else
      if f.status<>'uploaded' or not collaboration_private.is_session_member(f.session_id,p_actor) then perform collaboration_private.fail('DOWNLOAD_NOT_AUTHORIZED'); end if;
    end if;
    return jsonb_build_object('ok',true,'file_id',f.file_id,'object_path',f.object_path,'name',f.display_name,'content_type',f.content_type,
      'byte_length',coalesce(f.verified_bytes,f.expected_bytes),'sha256',f.sha256,'version',f.version);
  elsif p_command='cancel' then
    v_file=(p_payload->>'file_id')::uuid;
    select * into f from public.collaboration_file_reservations where file_id=v_file for update;
    if not found or f.uploader_id<>p_actor then perform collaboration_private.fail('FILE_NOT_FOUND'); end if;
    perform collaboration_private.release_file_reservation(v_file,'cleanup_pending');
    return jsonb_build_object('ok',true);
  elsif p_command='list' then
    if not collaboration_private.is_session_member((p_payload->>'session_id')::uuid,p_actor) then perform collaboration_private.fail('FORBIDDEN'); end if;
    return jsonb_build_object('ok',true,'files',coalesce((select jsonb_agg(jsonb_build_object('file_id',x.file_id,'name',x.display_name,'content_type',x.content_type,'byte_length',x.verified_bytes,'sha256',x.sha256,'version',x.version,'uploader_id',x.uploader_id,'uploaded_at',x.uploaded_at) order by x.uploaded_at,x.file_id) from public.collaboration_file_reservations x where x.session_id=(p_payload->>'session_id')::uuid and x.status='uploaded'),'[]'::jsonb));
  else perform collaboration_private.fail('UNKNOWN_COMMAND'); end if;
end $$;
revoke all on function collaboration_private.file_command(uuid,text,jsonb) from public,anon,authenticated,service_role;

create function public.collaboration_files_command(p_command text,p_payload jsonb)
returns jsonb language sql security definer set search_path='' as $$ select collaboration_private.file_command((select auth.uid()),p_command,p_payload) $$;
revoke all on function public.collaboration_files_command(text,jsonb) from public,anon;
grant execute on function public.collaboration_files_command(text,jsonb) to authenticated;

create function public.collaboration_files_verify(p_file_id uuid,p_byte_length bigint,p_sha256 text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare f public.collaboration_file_reservations%rowtype; cfg public.collaboration_config%rowtype;
begin
  select * into cfg from public.collaboration_config where singleton for update;
  select * into f from public.collaboration_file_reservations where file_id=p_file_id for update;
  if not found then perform collaboration_private.fail('FILE_NOT_FOUND'); end if;
  if f.status='uploaded' and f.verified_bytes=p_byte_length and f.sha256=lower(p_sha256) then return jsonb_build_object('ok',true,'status','uploaded'); end if;
  if f.status<>'reserved' then perform collaboration_private.fail('FILE_NOT_RESERVED'); end if;
  if not exists(select 1 from public.collaboration_quota_reservations q where q.reservation_id=f.reservation_id and q.status='active' and q.expires_at>now()) then
    perform collaboration_private.release_file_reservation(p_file_id,'cleanup_pending','RESERVATION_EXPIRED'); return jsonb_build_object('ok',false,'status','cleanup_pending');
  end if;
  if p_byte_length<>f.expected_bytes or p_byte_length>cfg.max_file_bytes or lower(p_sha256)<>f.sha256 then
    perform collaboration_private.release_file_reservation(p_file_id,'cleanup_pending','CONTENT_MISMATCH'); return jsonb_build_object('ok',false,'status','cleanup_pending');
  end if;
  update public.collaboration_file_reservations set status='uploaded',verified_bytes=p_byte_length,uploaded_at=now() where file_id=p_file_id;
  update public.collaboration_quota_reservations set status='settled',used_bytes=p_byte_length,settled_at=now() where reservation_id=f.reservation_id;
  update public.collaboration_sessions set file_reserved_bytes=greatest(0,file_reserved_bytes-cfg.max_file_bytes),shared_file_bytes=shared_file_bytes+p_byte_length where session_id=f.session_id;
  update public.collaboration_quota_counters set file_reserved_bytes=greatest(0,file_reserved_bytes-cfg.max_file_bytes),shared_file_bytes=shared_file_bytes+p_byte_length,updated_at=now() where org_id=f.org_id;
  return jsonb_build_object('ok',true,'status','uploaded','file_id',f.file_id,'byte_length',p_byte_length,'sha256',lower(p_sha256));
end $$;
revoke all on function public.collaboration_files_verify(uuid,bigint,text) from public,anon,authenticated;
grant execute on function public.collaboration_files_verify(uuid,bigint,text) to service_role;

create function public.collaboration_files_release_expired(p_limit integer default 100)
returns jsonb language plpgsql security definer set search_path='' as $$
declare x record; n integer:=0; v_limit integer:=least(greatest(p_limit,1),500); v_objects jsonb;
begin
  for x in select f.file_id from public.collaboration_file_reservations f join public.collaboration_quota_reservations q using(reservation_id)
    where f.status='reserved' and q.status='active' and q.expires_at<=now() order by q.expires_at for update skip locked limit v_limit
  loop perform collaboration_private.release_file_reservation(x.file_id,'cleanup_pending','RESERVATION_EXPIRED'); n:=n+1; end loop;
  select coalesce(jsonb_agg(jsonb_build_object('file_id',f.file_id,'bucket','collaboration-files','path',f.object_path) order by f.created_at,f.file_id),'[]'::jsonb)
    into v_objects from (select * from public.collaboration_file_reservations where status='cleanup_pending' order by created_at,file_id limit v_limit) f;
  return jsonb_build_object('released',n,'objects',v_objects);
end $$;
revoke all on function public.collaboration_files_release_expired(integer) from public,anon,authenticated;
grant execute on function public.collaboration_files_release_expired(integer) to service_role;

create function public.collaboration_files_cleanup_complete(p_file_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
  update public.collaboration_file_reservations set status=case when failure_code is null then 'cancelled' else 'failed' end
    where file_id=p_file_id and status='cleanup_pending';
  if not found and not exists(select 1 from public.collaboration_file_reservations where file_id=p_file_id and status in ('failed','cancelled','purged'))
    then perform collaboration_private.fail('CLEANUP_NOT_PENDING'); end if;
  return jsonb_build_object('ok',true);
end $$;
revoke all on function public.collaboration_files_cleanup_complete(uuid) from public,anon,authenticated;
grant execute on function public.collaboration_files_cleanup_complete(uuid) to service_role;
