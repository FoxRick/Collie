-- Daily presence survives last_seen updates. No historical presence is inferred.
create table public.install_activity_daily (
  day date not null,
  install_id uuid not null,
  primary key (day, install_id)
);
create table public.install_metrics_daily (
  day date not null,
  install_id uuid not null,
  source_id uuid not null,
  runs bigint not null check (runs between 0 and 10000000),
  interactive_runs bigint not null check (interactive_runs between 0 and runs),
  tool_calls bigint not null check (tool_calls between 0 and 10000000),
  primary key (day, install_id, source_id)
);
create table public.product_metrics_rollout (
  singleton boolean primary key default true check (singleton),
  started_at timestamptz not null default now()
);
insert into public.product_metrics_rollout default values;
alter table public.install_activity_daily enable row level security;
alter table public.install_metrics_daily enable row level security;
alter table public.product_metrics_rollout enable row level security;
revoke all on public.install_activity_daily, public.install_metrics_daily,
  public.product_metrics_rollout from public, anon, authenticated;
grant select on public.install_activity_daily, public.install_metrics_daily,
  public.product_metrics_rollout to service_role;

-- Existing desktop builds gain daily presence without a new payload.
create or replace function install_presence_private.record_install_heartbeat(
  p_install_id uuid, p_version text, p_platform text
) returns void language sql security definer set search_path = '' as $$
  insert into public.install_heartbeats (install_id, version, platform)
  values (p_install_id, p_version, p_platform)
  on conflict (install_id) do update
    set last_seen = now(), version = excluded.version, platform = excluded.platform;
  insert into public.install_activity_daily (day, install_id)
  values ((now() at time zone 'UTC')::date, p_install_id)
  on conflict do nothing;
$$;

-- Deliberately anonymous, write-only API, following the existing presence boundary.
-- A source is a random local counter generation, independent of accounts/backups.
create function install_presence_private.record_install_metrics(
  p_install_id uuid, p_source_id uuid, p_days jsonb
) returns void language plpgsql security definer set search_path = '' as $$
declare
  item jsonb;
  metric_day date;
  today date := (now() at time zone 'UTC')::date;
begin
  if p_install_id is null or p_source_id is null or p_days is null
     or jsonb_typeof(p_days) <> 'array' then
    raise exception 'Invalid metrics envelope' using errcode = '22023';
  end if;
  if jsonb_array_length(p_days) > 35 then
    raise exception 'Too many metric days' using errcode = '22023';
  end if;
  for item in select value from jsonb_array_elements(p_days) loop
    if jsonb_typeof(item) <> 'object'
       or not (item ?& array['day', 'runs', 'interactive_runs', 'tool_calls'])
       or (item - array['day', 'runs', 'interactive_runs', 'tool_calls']) <> '{}'::jsonb
       or jsonb_typeof(item->'day') <> 'string'
       or (item->>'day') !~ '^\d{4}-\d{2}-\d{2}$'
       or jsonb_typeof(item->'runs') <> 'number'
       or jsonb_typeof(item->'interactive_runs') <> 'number'
       or jsonb_typeof(item->'tool_calls') <> 'number'
       or (item->>'runs') !~ '^\d{1,8}$'
       or (item->>'interactive_runs') !~ '^\d{1,8}$'
       or (item->>'tool_calls') !~ '^\d{1,8}$' then
      raise exception 'Invalid daily counters' using errcode = '22023';
    end if;
    metric_day := (item->>'day')::date;
    if metric_day < today - 34 or metric_day > today then
      raise exception 'Metric day outside retry window' using errcode = '22023';
    end if;
    insert into public.install_metrics_daily
      (day, install_id, source_id, runs, interactive_runs, tool_calls)
    values (metric_day, p_install_id, p_source_id, (item->>'runs')::bigint,
      (item->>'interactive_runs')::bigint, (item->>'tool_calls')::bigint)
    on conflict (day, install_id, source_id) do update set
      runs = greatest(install_metrics_daily.runs, excluded.runs),
      interactive_runs = greatest(install_metrics_daily.interactive_runs, excluded.interactive_runs),
      tool_calls = greatest(install_metrics_daily.tool_calls, excluded.tool_calls);
  end loop;
end;
$$;
revoke all on function install_presence_private.record_install_metrics(uuid, uuid, jsonb) from public;
grant execute on function install_presence_private.record_install_metrics(uuid, uuid, jsonb) to anon, authenticated;
create function public.record_install_metrics(p_install_id uuid, p_source_id uuid, p_days jsonb)
returns void language sql security invoker set search_path = '' as $$
  select install_presence_private.record_install_metrics(p_install_id, p_source_id, p_days);
$$;
revoke all on function public.record_install_metrics(uuid, uuid, jsonb) from public;
grant execute on function public.record_install_metrics(uuid, uuid, jsonb) to anon, authenticated;

-- Owner-only report: aggregate in the database, without client pagination limits.
create function public.weekly_product_metrics(p_latest_week_start date)
returns table (
  week_start date, active_installs bigint, runs bigint, interactive_runs bigint,
  tool_calls bigint, collection_started_at timestamptz
) language plpgsql security invoker set search_path = '' as $$
begin
  if p_latest_week_start is null or extract(dow from p_latest_week_start) <> 0
     or p_latest_week_start + 7 > (now() at time zone 'UTC')::date then
    raise exception 'Expected a complete Sunday-Saturday UTC week' using errcode = '22023';
  end if;
  return query
    select w.start,
      (select count(distinct a.install_id) from public.install_activity_daily a
        where a.day >= w.start and a.day < w.start + 7),
      coalesce(m.runs, 0)::bigint, coalesce(m.interactive_runs, 0)::bigint,
      coalesce(m.tool_calls, 0)::bigint, r.started_at
    from (values (p_latest_week_start), (p_latest_week_start - 7)) w(start)
    cross join public.product_metrics_rollout r
    cross join lateral (
      select sum(d.runs) as runs, sum(d.interactive_runs) as interactive_runs,
        sum(d.tool_calls) as tool_calls
      from public.install_metrics_daily d
      where d.day >= w.start and d.day < w.start + 7
    ) m order by w.start desc;
end;
$$;
revoke all on function public.weekly_product_metrics(date) from public, anon, authenticated;
grant execute on function public.weekly_product_metrics(date) to service_role;
