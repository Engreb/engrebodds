# Engrebodds

Football predictions and tips for a Nigerian audience. See [CLAUDE.md](CLAUDE.md) for the product decisions
and [prototype/engrebodds.html](prototype/engrebodds.html) for the reference design.

Phase 1 (this repo so far): the API-Football data layer and the Supabase database.

| Folder | What it is |
|---|---|
| `supabase/migrations/` | Database schema, grading rules and access rules |
| `supabase/tests/` | pgTAP tests for grading, track-record integrity and access |
| `ingest/` | Python ingestion jobs (`python -m engrebodds_ingest <job>`) |
| `.github/workflows/` | Scheduled ingestion and tests |
| `reports/` | Coverage audit |

## Local setup

Needs Python 3.12+, Node (for `npx`) and Docker.

```bash
cp .env.example .env                 # then add your API_FOOTBALL_KEY
npx supabase@2.121.0 start           # local Postgres + Studio at http://127.0.0.1:54323
cd ingest && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # macOS/Linux: .venv/bin/pip
```

Tests:

```bash
npx supabase@2.121.0 test db
ingest/.venv/Scripts/python -m pytest -q ingest
```

## Jobs

| Job | Schedule (UTC) | What it does |
|---|---|---|
| `sync_reference` | Mondays 02:50 | Leagues, seasons, coverage; sets each league's tier (full / goals / off) |
| `sync_fixtures` | 03:20, 15:20 | Fixtures by date, yesterday to 3 days ahead |
| `sync_odds` | 05:13, 10:13, 14:13, 18:13 | Pre-match odds, today and tomorrow; changed prices only |
| `sync_results` | every 15 min (:07, :22, :37, :52) | Live and unfinished matches, grading, odds thinning |
| `sync_stats` | every 15 min (after results) | Statistics for finished full-tier matches still missing them |
| `sync_standings` | 06:40 | League tables for leagues playing this week |
| `backfill` | 04:31 | Past seasons of fixtures and results, one request per league-season; stops at the quota floor |
| `audit` | by hand | Coverage report into `reports/` |

These are the **Pro** schedules. The workflow reads the repository variable `API_PLAN`
(Settings → Secrets and variables → Actions → Variables): unless it is `pro`, the free profile runs —
results every 3 hours, fixtures for yesterday to tomorrow, odds once a day (2 pages per date), no league
tables, and the nightly backfill using the rest of the 100 daily requests while keeping 30 in reserve.
**When you upgrade API-Football to Pro, add the variable `API_PLAN` = `pro`.**

Options: `--days-back`, `--days-ahead`, `--max-pages`, `--limit` (see `python -m engrebodds_ingest --help`).
Every run is logged in the `ingestion_runs` table with requests used, rows written, notes and errors.

## Operating notes

**Fixtures with a suspect score.** When API-Football's score doesn't add up (e.g. "decided on penalties"
with no penalty score), the fixture gets a `score_issue` and its tips stay pending. To resolve one:
check the real result, correct the score columns, then set `score_issue = null` and `manual_score = true`
(so the next sync doesn't overwrite your correction). Grading picks it up on the next run.

```sql
select api_fixture_id, kickoff_at, score_issue from fixtures where score_issue is not null;
```

**Rules that protect the track record** (enforced in the database, see `supabase/migrations/`):
tips and slips are never deleted; nothing can be published inside the final hour before kick-off;
once locked, only a tip's result can change.

**Odds storage (lean policy).** Bet365 (the displayed price) is stored for every market; 1xBet and Pinnacle
for result, double chance, goals and GG/NG only. A price is written only when it changes. After a match,
only the full history of tipped selections and the main-market closing prices (all three bookmakers) are kept;
the price behind each tip is also stored on the tip itself (`ref_odds`).

**Failed runs** make the GitHub workflow fail, which emails the repo owner.
