-- Supabase security advisor (22 Oct 2026): is_premium() was SECURITY DEFINER and callable by anon and
-- signed-in users through /rest/v1/rpc/is_premium. It needs no elevated rights: RLS policies call it with
-- the caller's rights, and in Phase 5 it will read the caller's own subscription row (visible to them by RLS).
create or replace function public.is_premium()
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
  select false;
$$;
