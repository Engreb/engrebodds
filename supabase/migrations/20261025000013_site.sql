-- Phase 4: what the website may say about locked content (docs: Phase 4 plan, "How locking works").
-- RLS hides pending premium tips and slips completely. The site still needs to know that a match has
-- premium tips (to draw the "Premium" chip), that we cover it at all (it has a prediction), and that a
-- slip exists (leg count, combined odds, chance all legs land), without seeing selections, probabilities
-- or prices. These two functions run as their owner and return only those facts.

create function public.fixture_tip_status(p_from timestamptz, p_to timestamptz)
returns table (fixture_id bigint, predicted boolean, tips integer, has_free boolean, has_banker boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select f.id,
         exists (select 1 from public.predictions p where p.fixture_id = f.id),
         count(t.id)::integer,
         coalesce(bool_or(t.is_free), false),
         coalesce(bool_or(t.is_banker), false)
    from public.fixtures f
    left join public.tips t on t.fixture_id = f.id and t.product = 'likely'
   where f.kickoff_at >= p_from and f.kickoff_at < p_to
     and p_to - p_from <= interval '8 days'          -- a page's worth, not a dump of the whole table
     and (t.id is not null or exists (select 1 from public.predictions p where p.fixture_id = f.id))
   group by f.id;
$$;

create function public.slip_status(p_date date)
returns table (kind text, legs integer, combined_odds numeric, ref_odds numeric, ref_captured_at timestamptz,
               combined_prob numeric, is_free boolean, locks_at timestamptz, result text)
language sql
stable
security definer
set search_path = ''
as $$
  select s.kind, (select count(*)::integer from public.slip_legs sl where sl.slip_id = s.id),
         s.combined_odds, s.ref_odds, s.ref_captured_at, s.combined_prob, s.is_free, s.locks_at, s.result
    from public.slips s
   where s.slip_date = p_date;
$$;

revoke execute on function public.fixture_tip_status(timestamptz, timestamptz) from public;
revoke execute on function public.slip_status(date) from public;
grant execute on function public.fixture_tip_status(timestamptz, timestamptz) to anon, authenticated;
grant execute on function public.slip_status(date) to anon, authenticated;
