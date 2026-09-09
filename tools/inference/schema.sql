-- Deployment schema. Apply only to an approved existing free project; never auto-run.
-- No automatic user grants, purchases, recurring charges, or paid overage.
create schema if not exists collie_inference;
revoke all on schema collie_inference from public, anon, authenticated;

create table collie_inference.allowances (
  user_id uuid primary key references auth.users(id) on delete cascade,
  plan text not null default 'free' check (plan in ('free','plus')),
  units bigint not null default 0 check (units >= 0),
  spent bigint not null default 0 check (spent >= 0 and spent <= units),
  expires_at timestamptz not null,
  enabled boolean not null default false
);
create table collie_inference.pool (
  id boolean primary key default true check (id),
  enabled boolean not null default false,
  daily_limit bigint not null default 100000 check (daily_limit between 0 and 100000),
  daily_spent bigint not null default 0,
  day date not null default (now() at time zone 'UTC')::date,
  minute_start timestamptz not null default date_trunc('minute',now()),
  minute_spent bigint not null default 0
);
insert into collie_inference.pool(id) values(true);
create table collie_inference.requests (
  user_id uuid not null references auth.users(id) on delete cascade,
  request_id text not null,
  units bigint not null,
  created_at timestamptz not null default now(),
  primary key(user_id,request_id)
);
alter table collie_inference.allowances enable row level security;
alter table collie_inference.pool enable row level security;
alter table collie_inference.requests enable row level security;

-- Only the backend service role can invoke these functions. No user-supplied JWT
-- can select another identity or allocate funds via the public Data API.
create or replace function public.collie_inference_allowance(p_user uuid)
returns jsonb language sql security definer
set search_path = pg_catalog, collie_inference
as $$
 select coalesce((select jsonb_build_object(
   'available', a.enabled and a.expires_at > now() and a.spent < a.units and p.enabled
      and (p.day <> (now() at time zone 'UTC')::date or p.daily_spent < p.daily_limit),
   'remaining', case when a.enabled and a.expires_at > now() then a.units-a.spent else 0 end,
   'limit', a.units, 'resetsAt', null, 'expiresAt', a.expires_at, 'plan', a.plan)
 from collie_inference.allowances a cross join collie_inference.pool p
 where a.user_id=p_user),
 '{"available":false,"remaining":0,"limit":0,"resetsAt":null}'::jsonb);
$$;
revoke all on function public.collie_inference_allowance(uuid) from public,anon,authenticated;
grant execute on function public.collie_inference_allowance(uuid) to service_role;

create or replace function public.collie_inference_reserve(
 p_user uuid,p_request text,p_units bigint)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, collie_inference
as $$
declare
 p collie_inference.pool%rowtype;
 a collie_inference.allowances%rowtype;
begin
 if p_user is null or p_units is null or p_request is null or
    p_units < 1 or p_units > 6000 or p_request !~ '^[a-zA-Z0-9-]{16,80}$' then
   return '{"allowed":false,"reason":"invalid"}'::jsonb;
 end if;
 -- One lock order, including duplicate checks; never hold across inference.
 select * into p from collie_inference.pool where id=true for update;
 if not found or not p.enabled then
   return '{"allowed":false,"reason":"pool"}'::jsonb;
 end if;
 if exists(select 1 from collie_inference.requests where user_id=p_user and request_id=p_request) then
   return '{"allowed":false,"reason":"duplicate"}'::jsonb;
 end if;
 select * into a from collie_inference.allowances where user_id=p_user for update;
 if not found or not a.enabled or a.expires_at <= now() or a.units-a.spent < p_units or not p.enabled then
   return '{"allowed":false,"reason":"allowance"}'::jsonb;
 end if;
 if p.day <> (now() at time zone 'UTC')::date then p.daily_spent:=0; end if;
 if p.minute_start <> date_trunc('minute',now()) then p.minute_spent:=0; end if;
 if p.daily_spent+p_units > p.daily_limit or p.minute_spent+p_units > 6000 then
   return '{"allowed":false,"reason":"pool"}'::jsonb;
 end if;
 insert into collie_inference.requests(user_id,request_id,units) values(p_user,p_request,p_units);
 update collie_inference.allowances set spent=spent+p_units where user_id=p_user;
 update collie_inference.pool set
   day=(now() at time zone 'UTC')::date, daily_spent=p.daily_spent+p_units,
   minute_start=date_trunc('minute',now()), minute_spent=p.minute_spent+p_units where id=true;
 return '{"allowed":true}'::jsonb;
end;
$$;
revoke all on function public.collie_inference_reserve(uuid,text,bigint) from public,anon,authenticated;
grant execute on function public.collie_inference_reserve(uuid,text,bigint) to service_role;
