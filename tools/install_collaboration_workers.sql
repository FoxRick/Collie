-- Run as the project administrator only after deploying the Edge functions.
-- Create Vault entries via the dashboard before running this script:
-- collaboration_project_url = the project's HTTPS origin
-- collaboration_worker_secret = the deployed COLLABORATION_WORKER_SECRET
-- No secret values are embedded in job definitions or returned by this script.
-- This does not enable feature flags or replace pilot capacity calibration.
begin;
create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;
do $$ begin
  if (select count(*) from vault.decrypted_secrets where name='collaboration_project_url'
      and decrypted_secret ~ '^https://[a-z0-9]+\.supabase\.co/?$')<>1
    or (select count(*) from vault.decrypted_secrets where name='collaboration_worker_secret'
      and length(decrypted_secret)>=32)<>1 then
    raise exception 'Configure unique collaboration project URL and worker secret in Vault first';
  end if;
end $$;

select cron.schedule('collaboration-archive-worker','* * * * *',$job$
  select net.http_post(
    url:=rtrim((select decrypted_secret from vault.decrypted_secrets where name='collaboration_project_url'),'/')||'/functions/v1/archive-worker',
    headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||(select decrypted_secret from vault.decrypted_secrets where name='collaboration_worker_secret')),
    body:='{}'::jsonb, timeout_milliseconds:=30000
  );
$job$);
select cron.schedule('collaboration-slack-worker','* * * * *',$job$
  select net.http_post(
    url:=rtrim((select decrypted_secret from vault.decrypted_secrets where name='collaboration_project_url'),'/')||'/functions/v1/slack-worker',
    headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||(select decrypted_secret from vault.decrypted_secrets where name='collaboration_worker_secret')),
    body:='{}'::jsonb, timeout_milliseconds:=30000
  );
$job$);
select cron.schedule('collaboration-capacity-snapshot','15 */6 * * *',$job$
  select public.collaboration_capacity_command(
    pg_database_size(current_database()),
    coalesce((select sum(pg_total_relation_size(format('%I.%I',schemaname,tablename)::regclass))::bigint
      from pg_tables where schemaname='public' and tablename like 'collaboration_%'),0),
    coalesce((select sum(case when metadata->>'size' ~ '^[0-9]+$' then (metadata->>'size')::bigint else 0 end)::bigint
      from storage.objects),0)
  );
$job$);
commit;
