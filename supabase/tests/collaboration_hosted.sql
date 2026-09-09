
-- Hosted collaboration validation. Everything below, including fixture users,
-- is rolled back by the final statement.
create temporary table hosted_validation_results(name text primary key, passed boolean not null default true) on commit drop;
create temporary table hosted_run_lease(result jsonb not null) on commit drop;
grant select,insert on hosted_validation_results to authenticated,service_role;
grant select,insert on hosted_run_lease to authenticated,service_role;

insert into auth.users(id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
values
 ('a1000000-0000-4000-8000-000000000001','authenticated','authenticated','collab-a-rollback@example.invalid','',now(),'{}','{}',now(),now()),
 ('a1000000-0000-4000-8000-000000000002','authenticated','authenticated','collab-b-rollback@example.invalid','',now(),'{}','{}',now(),now()),
 ('a1000000-0000-4000-8000-000000000003','authenticated','authenticated','collab-x-rollback@example.invalid','',now(),'{}','{}',now(),now());

update public.collaboration_config set shared_text_enabled=true,shared_execution_enabled=true,
 archive_enabled=true,slack_enabled=true,session_warn_bytes=300000,session_limit_bytes=400000,
 org_limit_bytes=1200000,default_response_reservation_bytes=70000;

set local role service_role;
do $$
declare capacity jsonb;
begin
 select public.collaboration_capacity_command(12000000,0,0) into capacity;
 if not coalesce((capacity->>'ok')::boolean,false) or capacity->>'measurement_id' is null
  then raise exception 'capacity fixture was not accepted: %',capacity; end if;
end $$;
insert into hosted_validation_results values('capacity_measurement_fixture',true);
select public.collaboration_rollout_command('set_user',jsonb_build_object('user_id','a1000000-0000-4000-8000-000000000001','enabled',true));
select public.collaboration_rollout_command('set_user',jsonb_build_object('user_id','a1000000-0000-4000-8000-000000000002','enabled',true));
select public.collaboration_rollout_command('set_user',jsonb_build_object('user_id','a1000000-0000-4000-8000-000000000003','enabled',true));
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_command('create_organization','{"org_id":"a2000000-0000-4000-8000-000000000001","name":"Rollback Org A","display_name":"Alice"}');
reset role;
set local role service_role;
select public.collaboration_rollout_command('set_organization','{"org_id":"a2000000-0000-4000-8000-000000000001","enabled":true}');
reset role;
insert into public.collaboration_org_members(org_id,user_id,display_name,role) values
 ('a2000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000002','Bob','member');

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_command('create_session','{"org_id":"a2000000-0000-4000-8000-000000000001","session_id":"a3000000-0000-4000-8000-000000000001","title":"rollback validation"}');
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('invite','{"session_id":"a3000000-0000-4000-8000-000000000001","invite_id":"a4000000-0000-4000-8000-000000000001","user_id":"a1000000-0000-4000-8000-000000000002"}');
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='AUDIENCE_POLICY_REQUIRED'; end;
 if not ok then raise exception 'audience policy was not required'; end if;
end $$;
select public.collaboration_command('invite',jsonb_build_object('session_id','a3000000-0000-4000-8000-000000000001',
 'invite_id','a4000000-0000-4000-8000-000000000001','user_id','a1000000-0000-4000-8000-000000000002',
 'audience_policy',jsonb_build_object('summary','Owners may invite additional participants who can read all published history.','includes_existing_history',true),
 'audience_policy_version',1));
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000002',true);
set local role authenticated;
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('accept_invite','{"invite_id":"a4000000-0000-4000-8000-000000000001"}');
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='AUDIENCE_CONSENT_REQUIRED'; end;
 if not ok then raise exception 'audience consent was not required'; end if;
end $$;
select public.collaboration_command('accept_invite','{"invite_id":"a4000000-0000-4000-8000-000000000001","audience_consent":true,"audience_policy_version":1}');
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_command('append_message',jsonb_build_object('session_id','a3000000-0000-4000-8000-000000000001',
 'event_id','a5000000-0000-4000-8000-000000000001','message_id','a6000000-0000-4000-8000-000000000001',
 'content','hello hosted rollback','role','user','author_id','a1000000-0000-4000-8000-000000000002',
 'mentioned_user_ids',jsonb_build_array('a1000000-0000-4000-8000-000000000002')));
do $$
declare stored uuid;
begin select author_id into stored from public.collaboration_events where event_id='a5000000-0000-4000-8000-000000000001';
 if stored<>'a1000000-0000-4000-8000-000000000001' then raise exception 'caller forged event author'; end if;
end $$;
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('append_message','{"session_id":"a3000000-0000-4000-8000-000000000001","event_id":"a5000000-0000-4000-8000-000000000001","message_id":"a6000000-0000-4000-8000-000000000001","content":"different","role":"user"}');
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='IDEMPOTENCY_CONFLICT'; end;
 if not ok then raise exception 'event payload mismatch was accepted'; end if;
end $$;
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('append_message',jsonb_build_object(
  'session_id','a3000000-0000-4000-8000-000000000001','event_id','a5000000-0000-4000-8000-000000000099',
  'message_id','a6000000-0000-4000-8000-000000000099','content',repeat('x',450000),'role','user'));
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='QUOTA_EXCEEDED'; end;
 if not ok then raise exception 'oversized message bypassed byte quota'; end if;
 if exists(select 1 from public.collaboration_events where event_id='a5000000-0000-4000-8000-000000000099')
  then raise exception 'failed quota command retained an event'; end if;
end $$;
insert into hosted_validation_results values('audience_consent_and_event_dedup',true);
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000003',true);
set local role authenticated;
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('read_events','{"session_id":"a3000000-0000-4000-8000-000000000001"}');
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='FORBIDDEN'; end;
 if not ok then raise exception 'cross-user read was not denied'; end if;
end $$;
do $$
declare n bigint;
begin select count(*) into n from public.collaboration_events where session_id='a3000000-0000-4000-8000-000000000001';
 if n<>0 then raise exception 'RLS exposed collaboration events'; end if;
end $$;
insert into hosted_validation_results values('cross_user_command_and_rls',true);
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000002',true);
set local role authenticated;
do $$
declare payload jsonb;
begin
 select public.collaboration_command('notifications','{"cursor":0,"limit":20}') into payload;
 if jsonb_array_length(payload->'notifications')<>2 then raise exception 'expected invite and mention notifications: %',payload; end if;
 perform public.collaboration_command('ack_notifications',jsonb_build_object('through',(payload->>'high_water')::bigint));
end $$;
insert into hosted_validation_results values('notification_fanout_and_ack',true);
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_command('append_message',jsonb_build_object('session_id','a3000000-0000-4000-8000-000000000001',
 'event_id','a5000000-0000-4000-8000-000000000002','message_id','a6000000-0000-4000-8000-000000000002',
 'content','run request','role','user','request_run',true,'run_id','a7000000-0000-4000-8000-000000000001',
 'reservation_id','a8000000-0000-4000-8000-000000000001','response_reservation_bytes',70000));
do $$
declare payload jsonb;
begin select public.collaboration_command('list_pending_runs','{}') into payload;
 if jsonb_array_length(payload->'runs')<>1 then raise exception 'queued requester run missing: %',payload; end if;
end $$;
select public.collaboration_command('enroll_device','{"device_id":"a9000000-0000-4000-8000-000000000001","label":"hosted rollback","public_key":"key-a"}');
insert into hosted_validation_results values('quota_reservation_and_requester_queue',true);
reset role;

select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000002',true);
set local role authenticated;
select public.collaboration_command('enroll_device','{"device_id":"a9000000-0000-4000-8000-000000000002","label":"hosted rollback b","public_key":"key-b"}');
do $$
declare ok boolean:=false; msg text;
begin
 begin perform public.collaboration_command('claim_run','{"run_id":"a7000000-0000-4000-8000-000000000001","device_id":"a9000000-0000-4000-8000-000000000002"}');
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='RUN_UNAVAILABLE'; end;
 if not ok then raise exception 'nonrequester claimed requester run'; end if;
end $$;
insert into hosted_validation_results values('requester_claim_binding',true);
reset role;

-- Closing freezes new submissions but an already accepted requester run must
-- still claim, renew, and publish its bounded completion.
select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
select public.collaboration_command('request_archive','{"session_id":"a3000000-0000-4000-8000-000000000001"}');
insert into hosted_run_lease
select public.collaboration_command('claim_run','{"run_id":"a7000000-0000-4000-8000-000000000001","device_id":"a9000000-0000-4000-8000-000000000001"}');
do $$
declare claim jsonb;
begin
 select result into claim from hosted_run_lease;
 if claim->>'session_id' is distinct from 'a3000000-0000-4000-8000-000000000001'
  or claim->>'requester_id' is distinct from 'a1000000-0000-4000-8000-000000000001'
  or claim->>'credential_owner_id' is distinct from 'a1000000-0000-4000-8000-000000000001'
  or claim->>'executor_device_id' is distinct from 'a9000000-0000-4000-8000-000000000001'
  or claim->>'run_id' is distinct from 'a7000000-0000-4000-8000-000000000001'
  or claim->>'audience_revision' is null or claim->>'context_cutoff' is null
  or claim->>'lease_token' is null or claim->>'lease_expires_at' is null
 then raise exception 'claim_run core DTO incomplete: %',claim; end if;
end $$;
select public.collaboration_command('renew_run',jsonb_build_object('run_id','a7000000-0000-4000-8000-000000000001',
 'device_id','a9000000-0000-4000-8000-000000000001','lease_token',(select result->>'lease_token' from hosted_run_lease)));
select public.collaboration_command('complete_run',jsonb_build_object('run_id','a7000000-0000-4000-8000-000000000001',
 'lease_token',(select result->>'lease_token' from hosted_run_lease),'event_id','a5000000-0000-4000-8000-000000000003',
 'message_id','a6000000-0000-4000-8000-000000000003','content','bounded completion while closing'));
insert into hosted_validation_results values('accepted_run_completes_while_closing',true);
reset role;
set local role service_role;
select public.collaboration_maintenance_command('advance_archives','{"limit":10}');
do $$
declare s record; m record; body jsonb; event_count bigint;
begin
 select final_seq,next_seq into s from public.collaboration_sessions
  where session_id='a3000000-0000-4000-8000-000000000001';
 select final_seq,manifest_json into m from public.collaboration_archive_manifests
  where session_id='a3000000-0000-4000-8000-000000000001' order by archive_revision desc limit 1;
 select count(*) into event_count from public.collaboration_events
  where session_id='a3000000-0000-4000-8000-000000000001';
 body:=m.manifest_json::jsonb;
 if s.final_seq<>s.next_seq-1 or s.final_seq<>3 or m.final_seq<>s.final_seq
  or (body->>'final_seq')::bigint<>s.final_seq or (body->>'final_sequence')::bigint<>s.final_seq
  or jsonb_array_length(body->'events')<>event_count or event_count<>3
  or not exists(select 1 from jsonb_array_elements(body->'events') e
    where e->>'event_id'='a5000000-0000-4000-8000-000000000003')
 then raise exception 'archive freeze omitted accepted completion: session %, manifest %, body %',s,m,body; end if;
end $$;
insert into hosted_validation_results values('archive_freeze_includes_closing_completion',true);
reset role;
select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000001',true);
set local role authenticated;
do $$
declare m record; ok boolean:=false; msg text;
begin
 select archive_revision,byte_length,final_seq into m from public.collaboration_archive_manifests
  where session_id='a3000000-0000-4000-8000-000000000001' order by archive_revision desc limit 1;
 begin perform public.collaboration_command('ack_archive',jsonb_build_object(
  'session_id','a3000000-0000-4000-8000-000000000001','archive_revision',m.archive_revision,
  'device_id','a9000000-0000-4000-8000-000000000001','digest',repeat('0',64),
  'byte_length',m.byte_length,'final_seq',m.final_seq));
 exception when sqlstate 'P0001' then get stacked diagnostics msg=message_text; ok:=msg='ARCHIVE_RECEIPT_INVALID'; end;
 if not ok then raise exception 'corrupt archive receipt was accepted'; end if;
end $$;
reset role;
set local role service_role;
do $$
declare claimed jsonb;
begin select public.collaboration_maintenance_command('claim_purge','{}') into claimed;
 if claimed is not null then raise exception 'purge claimed before all participant receipts'; end if;
end $$;
insert into hosted_validation_results values('zero_deletion_without_all_receipts',true);
reset role;

-- Verify real pgcrypto produced the canonical SHA-256 archive digest.
do $$
declare bad bigint;
begin select count(*) into bad from public.collaboration_archive_manifests
 where digest !~ '^[0-9a-f]{64}$' or digest<>encode(extensions.digest(convert_to(manifest_json,'UTF8'),'sha256'),'hex');
 if bad<>0 then raise exception 'archive digest mismatch'; end if;
end $$;
insert into hosted_validation_results values('real_pgcrypto_archive_digest',true);

select jsonb_build_object('ok',bool_and(passed),'checks',jsonb_agg(name order by name),
 'archive_fixture',(select jsonb_build_object('session_id',m.session_id,'archive_revision',m.archive_revision,
   'manifest_json',m.manifest_json,'digest',m.digest,'byte_length',m.byte_length,'final_seq',m.final_seq)
  from public.collaboration_archive_manifests m
  where m.session_id='a3000000-0000-4000-8000-000000000001'
  order by m.archive_revision desc limit 1)) as hosted_collaboration_validation
from hosted_validation_results;
