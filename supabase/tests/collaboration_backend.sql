begin;
create extension if not exists pgtap with schema extensions;
select plan(19);

select has_schema('collaboration_private','private authority schema exists');
select has_table('public','collaboration_organizations','organizations table exists');
select has_table('public','collaboration_sessions','sessions table exists');
select has_table('public','collaboration_events','canonical events table exists');
select has_table('public','collaboration_quota_reservations','quota reservations table exists');
select has_table('public','collaboration_archive_receipts','archive receipts table exists');
select has_table('public','collaboration_tombstones','archive tombstones table exists');
select has_column('public','collaboration_runs','provider_payer_id','runs record the requester as provider payer');
select has_function('public','collaboration_command',array['text','jsonb'],'single authenticated RPC exists');
select has_function('public','collaboration_slack_command',array['text','jsonb'],'service Slack RPC exists');

select ok((select relrowsecurity from pg_class where oid='public.collaboration_events'::regclass),'events have RLS');
select ok((select relrowsecurity from pg_class where oid='public.collaboration_sessions'::regclass),'sessions have RLS');
select ok(not has_table_privilege('anon','public.collaboration_events','SELECT'),'anonymous event reads are revoked');
select ok(not has_table_privilege('authenticated','public.collaboration_events','INSERT'),'clients cannot forge events directly');
select ok(has_function_privilege('authenticated','public.collaboration_command(text,jsonb)','EXECUTE'),'authenticated may call narrow RPC');
select ok(not has_function_privilege('anon','public.collaboration_command(text,jsonb)','EXECUTE'),'anonymous cannot call collaboration RPC');
select ok(not has_function_privilege('authenticated','public.collaboration_slack_command(text,jsonb)','EXECUTE'),'clients cannot call Slack worker RPC');
select ok(has_function_privilege('service_role','public.collaboration_slack_command(text,jsonb)','EXECUTE'),'service role may call Slack worker RPC');
select is((select shared_text_enabled or shared_execution_enabled or archive_enabled or slack_enabled from public.collaboration_config where singleton),false,'all collaboration gates default disabled');

select * from finish();
rollback;
