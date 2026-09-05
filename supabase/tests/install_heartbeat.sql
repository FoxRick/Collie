begin;
set local role anon;
select public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', 'test-1', 'win32');
reset role;
update public.install_heartbeats set first_seen = now() - interval '1 day', last_seen = now() - interval '1 day'
  where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076';
set local role anon;
select public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', 'test-2', 'linux');
do $$ begin
  begin
    perform * from public.install_heartbeats;
    raise exception 'Anonymous read unexpectedly allowed';
  exception when insufficient_privilege then null; end;
  begin
    delete from public.install_heartbeats;
    raise exception 'Anonymous delete unexpectedly allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.install_heartbeats set version = 'forged';
    raise exception 'Anonymous update unexpectedly allowed';
  exception when insufficient_privilege then null; end;
  begin
    perform public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', repeat('x', 65), 'win32');
    raise exception 'Unbounded version unexpectedly accepted';
  exception when check_violation then null; end;
  begin
    perform public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', 'test', 'invalid');
    raise exception 'Invalid platform unexpectedly accepted';
  exception when check_violation then null; end;
end $$;
reset role;
do $$ begin
  assert (select count(*) = 1 from public.install_heartbeats where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076');
  assert (select version = 'test-2' and platform = 'linux' and first_seen = now() - interval '1 day' and last_seen = now()
    from public.install_heartbeats where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076');
end $$;
set local role service_role;
select count(*) from public.install_heartbeats;
rollback;
