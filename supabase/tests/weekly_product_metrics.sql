begin;
set local timezone = 'Pacific/Honolulu';
set local role anon;
select public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', 'test', 'win32');
select public.record_install_heartbeat('b9d4991a-47cb-4eb4-9556-cf5b324aa076', 'test', 'win32');
do $$
declare
  install uuid := 'b9d4991a-47cb-4eb4-9556-cf5b324aa076';
  source uuid := 'fbdf518d-cdb3-4a6e-8fe3-260ebfccd565';
  today date := (now() at time zone 'UTC')::date;
  payload jsonb;
begin
  payload := jsonb_build_array(jsonb_build_object(
    'day', today, 'runs', 5, 'interactive_runs', 3, 'tool_calls', 9));
  perform public.record_install_metrics(install, source, payload);
  perform public.record_install_metrics(install, source, payload);
  -- Out-of-order lower counters must never reduce counts.
  perform public.record_install_metrics(install, source, jsonb_build_array(jsonb_build_object(
    'day', today, 'runs', 2, 'interactive_runs', 1, 'tool_calls', 4)));
  -- Validate every row and roll back the entire call if any row is invalid.
  begin
    perform public.record_install_metrics(install, source, jsonb_build_array(
      jsonb_build_object('day', today, 'runs', 8, 'interactive_runs', 3, 'tool_calls', 12),
      jsonb_build_object('day', today + 1, 'runs', 1, 'interactive_runs', 1, 'tool_calls', 0)));
    raise exception 'Future day accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.record_install_metrics(install, source, jsonb_build_array(jsonb_build_object(
      'day', today - 35, 'runs', 1, 'interactive_runs', 1, 'tool_calls', 0)));
    raise exception 'Expired day accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.record_install_metrics(install, source, payload ||
      jsonb_build_array(jsonb_build_object('day', today, 'runs', 1.5,
        'interactive_runs', 1, 'tool_calls', 0)));
    raise exception 'Fractional counter accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.record_install_metrics(install, source,
      jsonb_build_array(jsonb_build_object('day', today, 'runs', 1,
        'interactive_runs', 2, 'tool_calls', 0)));
    raise exception 'Invalid subset accepted';
  exception when check_violation then null; end;
  begin
    perform public.record_install_metrics(install, source,
      jsonb_build_array(jsonb_build_object('day', today, 'runs', 10000001,
        'interactive_runs', 0, 'tool_calls', 0)));
    raise exception 'Unbounded counter accepted';
  exception when check_violation then null; end;
  begin
    perform public.record_install_metrics(install, source,
      jsonb_build_array((payload->0) || '{"tool_name":"secret"}'::jsonb));
    raise exception 'Unrecognized fields accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.record_install_metrics(install, source, '[null]'::jsonb);
    raise exception 'Null row accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.record_install_metrics(install, source,
      (select jsonb_agg(payload->0) from generate_series(1, 36)));
    raise exception 'Oversized batch accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform * from public.install_metrics_daily;
    raise exception 'Anonymous read accepted';
  exception when insufficient_privilege then null; end;
  begin
    perform * from public.weekly_product_metrics(today);
    raise exception 'Anonymous report accepted';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
do $$ begin
  assert (select count(*) = 1 from public.install_activity_daily
    where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076');
  assert (select day = (now() at time zone 'UTC')::date from public.install_activity_daily
    where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076');
  assert (select runs = 5 and interactive_runs = 3 and tool_calls = 9
    from public.install_metrics_daily where install_id = 'b9d4991a-47cb-4eb4-9556-cf5b324aa076');
end $$;
set local role authenticated;
do $$ begin
  begin
    delete from public.install_activity_daily;
    raise exception 'Client deletion accepted';
  exception when insufficient_privilege then null; end;
  begin
    update public.install_metrics_daily set runs = 100;
    raise exception 'Client update accepted';
  exception when insufficient_privilege then null; end;
  begin
    perform * from public.install_activity_daily;
    raise exception 'Client presence read accepted';
  exception when insufficient_privilege then null; end;
  begin
    perform * from public.weekly_product_metrics('2026-08-30');
    raise exception 'Client report accepted';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

-- Isolate report fixtures within the rolled-back test transaction.
delete from public.install_activity_daily;
delete from public.install_metrics_daily;
do $$
declare
  today date := (now() at time zone 'UTC')::date;
  week date := today - extract(dow from today)::integer - 7;
begin
  insert into public.install_activity_daily values
    (week, 'b9d4991a-47cb-4eb4-9556-cf5b324aa076'),
    (week + 6, 'b9d4991a-47cb-4eb4-9556-cf5b324aa076'),
    (week + 1, 'fbdf518d-cdb3-4a6e-8fe3-260ebfccd565'),
    (week - 1, 'b9d4991a-47cb-4eb4-9556-cf5b324aa076'),
    (week + 7, 'f34e58a3-03e6-4d9f-b5b1-f8fd9103777c');
  perform public.record_install_metrics('b9d4991a-47cb-4eb4-9556-cf5b324aa076',
    'fbdf518d-cdb3-4a6e-8fe3-260ebfccd565', jsonb_build_array(
      jsonb_build_object('day', week, 'runs', 5, 'interactive_runs', 3, 'tool_calls', 9),
      jsonb_build_object('day', week - 1, 'runs', 2, 'interactive_runs', 1, 'tool_calls', 4)));
  -- Rotating a cleared local database source adds new runs, not new installs.
  perform public.record_install_metrics('b9d4991a-47cb-4eb4-9556-cf5b324aa076',
    'f34e58a3-03e6-4d9f-b5b1-f8fd9103777c', jsonb_build_array(
      jsonb_build_object('day', week, 'runs', 1, 'interactive_runs', 1, 'tool_calls', 2)));
end $$;
set local role service_role;
do $$
declare
  today date := (now() at time zone 'UTC')::date;
  week date := today - extract(dow from today)::integer - 7;
  row record;
begin
  select * into row from public.weekly_product_metrics(week) where week_start = week;
  assert row.active_installs = 2 and row.runs = 6 and row.interactive_runs = 4 and row.tool_calls = 11;
  select * into row from public.weekly_product_metrics(week) where week_start = week - 7;
  assert row.active_installs = 1 and row.runs = 2 and row.tool_calls = 4;
  begin
    perform public.weekly_product_metrics(week + 1);
    raise exception 'Non-Sunday week accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.weekly_product_metrics(week + 7);
    raise exception 'Incomplete week accepted';
  exception when invalid_parameter_value then null; end;
end $$;
rollback;
