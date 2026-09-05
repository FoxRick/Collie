-- Bootstrap once in the existing Supabase project before deploying the Worker.
-- No client (including signed-in app users) can read or write this table.
begin;
create table public.app_feedback (
  id uuid primary key,
  message text not null check (char_length(btrim(message)) between 1 and 4000),
  created_at timestamptz not null default now()
);
alter table public.app_feedback enable row level security;
revoke all on public.app_feedback from public, anon, authenticated;
grant select, insert on public.app_feedback to service_role;
commit;
