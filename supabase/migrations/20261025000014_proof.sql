-- Phase 4 step 5: public proof of the record (commit and reveal; docs: Phase 4 plan, addition 8).
-- Each live publish run stores the canonical JSON of what it published and its SHA-256. The hash is posted
-- publicly before the first kick-off; the JSON itself only after grading. Rows are never deleted, and the
-- payload and hash can never change. Service role only: the payload would reveal premium tips early.

create table public.proof_runs (
  id                   bigint generated always as identity primary key,
  published_at         timestamptz not null unique,   -- the publish transaction's time, shared by its tips and slips
  window_date          date not null,
  tips                 integer not null,
  slips                integer not null,
  first_kickoff        timestamptz not null,
  last_kickoff         timestamptz not null,
  payload              text not null,                 -- canonical JSON, exactly the bytes that were hashed
  sha256               text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  telegram_message_id  bigint,
  telegram_posted_at   timestamptz,
  repo_hash_url        text,
  repo_committed_at    timestamptz,
  revealed_at          timestamptz,
  reveal_url           text,
  missed               text,                          -- why the hash wasn't posted before kick-off, if it wasn't
  created_at           timestamptz not null default now()
);

create function public.proof_runs_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (new.published_at, new.window_date, new.tips, new.slips, new.first_kickoff, new.last_kickoff, new.payload, new.sha256)
     is distinct from
     (old.published_at, old.window_date, old.tips, old.slips, old.first_kickoff, old.last_kickoff, old.payload, old.sha256) then
    raise exception 'Proof run % is fixed once written; only posting details can be added', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;
create trigger proof_runs_guard before update on public.proof_runs for each row execute function public.proof_runs_guard();
create trigger proof_runs_no_delete before delete on public.proof_runs for each row execute function public.forbid_delete();
create trigger proof_runs_no_truncate before truncate on public.proof_runs for each statement execute function public.forbid_delete();

alter table public.proof_runs enable row level security;           -- no policy: service role only
revoke all on public.proof_runs from anon, authenticated;
revoke delete, truncate on public.proof_runs from service_role;
