begin;

-- Static contract checks that do not require feature flags or external Storage.
do $$
begin
  if has_function_privilege('anon', 'public.collaboration_maintenance_command(text,jsonb)', 'execute')
     or has_function_privilege('authenticated', 'public.collaboration_maintenance_command(text,jsonb)', 'execute') then
    raise exception 'maintenance RPC leaked to clients';
  end if;
  if not has_function_privilege('service_role', 'public.collaboration_maintenance_command(text,jsonb)', 'execute') then
    raise exception 'service role cannot invoke maintenance RPC';
  end if;
  if has_function_privilege('service_role', 'collaboration_private.claim_purge(uuid)', 'execute')
     or has_function_privilege('service_role', 'collaboration_private.finish_purge_step(uuid,uuid,text,jsonb)', 'execute') then
    raise exception 'legacy unsafe purge functions remain callable';
  end if;
end $$;

-- The worker DTO must stay stable: no job is represented as JSON null.
set local role service_role;
do $$
declare result jsonb;
begin
  result := public.collaboration_maintenance_command('claim_purge', '{}'::jsonb);
  if result is not null then raise exception 'unexpected purge job in empty fixture'; end if;
end $$;
reset role;

rollback;
