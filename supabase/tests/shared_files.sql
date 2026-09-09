begin;

do $$
begin
  if has_function_privilege('anon','public.collaboration_files_command(text,jsonb)','execute') then
    raise exception 'anonymous file command access leaked';
  end if;
  if has_function_privilege('authenticated','public.collaboration_files_verify(uuid,bigint,text)','execute') then
    raise exception 'trusted verification leaked to clients';
  end if;
  if not has_function_privilege('service_role','public.collaboration_files_verify(uuid,bigint,text)','execute') then
    raise exception 'service verification unavailable';
  end if;
end $$;

insert into auth.users(id) values
  ('10000000-0000-4000-8000-000000000001'),
  ('10000000-0000-4000-8000-000000000002');
insert into public.collaboration_organizations(org_id,name,owner_id)
values('20000000-0000-4000-8000-000000000001','Files test','10000000-0000-4000-8000-000000000001');
insert into public.collaboration_org_members(org_id,user_id,display_name,role) values
  ('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','Owner','owner'),
  ('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','Reader','member');
insert into public.collaboration_quota_counters(org_id) values('20000000-0000-4000-8000-000000000001');
insert into public.collaboration_sessions(session_id,org_id,created_by) values
  ('30000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001');
insert into public.collaboration_session_members(org_id,session_id,user_id,role,status,joined_at) values
  ('20000000-0000-4000-8000-000000000001','30000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','owner','active',now()),
  ('20000000-0000-4000-8000-000000000001','30000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','member','active',now());

select set_config('request.jwt.claim.sub','10000000-0000-4000-8000-000000000001',true);
set local role authenticated;
do $$ begin
  begin
    perform public.collaboration_files_command('reserve',jsonb_build_object(
      'session_id','30000000-0000-4000-8000-000000000001','file_id','40000000-0000-4000-8000-000000000099',
      'name','missing-revision.txt','content_type','text/plain','byte_length',1,'sha256',repeat('c',64)));
    raise exception 'missing revisions unexpectedly accepted';
  exception when sqlstate 'P0001' then
    if sqlerrm <> 'REVISION_CONFLICT' then raise; end if;
  end;
end $$;
select public.collaboration_files_command('reserve',jsonb_build_object(
  'session_id','30000000-0000-4000-8000-000000000001','file_id','40000000-0000-4000-8000-000000000001',
  'name','notes.txt','content_type','text/plain','byte_length',5,'sha256',repeat('a',64),
  'expected_session_revision',1,'membership_revision',1));
-- Lost acknowledgements retry the same immutable reservation.
select public.collaboration_files_command('reserve',jsonb_build_object(
  'session_id','30000000-0000-4000-8000-000000000001','file_id','40000000-0000-4000-8000-000000000001',
  'name','notes.txt','content_type','text/plain','byte_length',5,'sha256',repeat('a',64),
  'expected_session_revision',1,'membership_revision',1));
reset role;

do $$ begin
  assert (select file_reserved_bytes=5242880 and shared_file_bytes=0 from public.collaboration_sessions where session_id='30000000-0000-4000-8000-000000000001');
  assert (select count(*)=1 from public.collaboration_file_reservations where file_id='40000000-0000-4000-8000-000000000001');
end $$;

set local role service_role;
select public.collaboration_files_verify('40000000-0000-4000-8000-000000000001',5,repeat('a',64));
reset role;
do $$ begin
  assert (select file_reserved_bytes=0 and shared_file_bytes=5 from public.collaboration_sessions where session_id='30000000-0000-4000-8000-000000000001');
  assert (select status='uploaded' and verified_bytes=5 from public.collaboration_file_reservations where file_id='40000000-0000-4000-8000-000000000001');
end $$;

-- Every accepted member may list and authorize a verified download.
select set_config('request.jwt.claim.sub','10000000-0000-4000-8000-000000000002',true);
set local role authenticated;
select public.collaboration_files_command('list','{"session_id":"30000000-0000-4000-8000-000000000001"}'::jsonb);
select public.collaboration_files_command('authorize_download','{"file_id":"40000000-0000-4000-8000-000000000001"}'::jsonb);
reset role;

-- A second reservation demonstrates recovery of an abandoned upload.
select set_config('request.jwt.claim.sub','10000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_files_command('reserve',jsonb_build_object(
  'session_id','30000000-0000-4000-8000-000000000001','file_id','40000000-0000-4000-8000-000000000002',
  'name','abandoned.bin','content_type','application/octet-stream','byte_length',1,'sha256',repeat('b',64),
  'expected_session_revision',1,'membership_revision',1));
reset role;
update public.collaboration_quota_reservations set expires_at=now()-interval '1 second'
where reservation_id=(select reservation_id from public.collaboration_file_reservations where file_id='40000000-0000-4000-8000-000000000002');
set local role service_role;
select public.collaboration_files_release_expired(10);
reset role;
do $$ begin
  assert (select status='cleanup_pending' and failure_code='RESERVATION_EXPIRED' from public.collaboration_file_reservations where file_id='40000000-0000-4000-8000-000000000002');
  assert (select file_reserved_bytes=0 and shared_file_bytes=5 from public.collaboration_sessions where session_id='30000000-0000-4000-8000-000000000001');
end $$;
set local role service_role;
select public.collaboration_files_cleanup_complete('40000000-0000-4000-8000-000000000002');
reset role;
do $$ begin
  assert (select status='failed' from public.collaboration_file_reservations where file_id='40000000-0000-4000-8000-000000000002');
end $$;

rollback;
