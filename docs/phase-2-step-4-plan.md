# Phase 2, step 4: tip publishing behind the switch (revised)

## Context
Step 3 is live: predictions are written twice a day, freeze at kick-off minus 1 hour, and are shadow-evaluated weekly. On the free plan they're stale (data quality 0, teams' latest data ~18 months old) until Pro. Emmanuel chose to build publishing now and stay on free.

Step 4 builds the **most-likely product** (the value product is off) end to end:
- 4 tips per match;
- bankers and slips;
- 3 free fixtures a day;
- the track record.

All of it sits **behind `PUBLISH_TIPS`, which stays off**. While off, the job writes a **preview**, which can be overwritten and never touches the permanent record. Tips can never be deleted, so nothing reaches `tips` until you switch it on.

Decisions (24 Oct 2026):
- **NPFL:** predictions only, **no NPFL tips yet**.
- **League eligibility:** **manual, by Emmanuel**, on shadow evidence about **what would be published** (see the league gate). The football-data backtest can't be used; it's private research. On free, closing odds are sparse, so few or no leagues will qualify until Pro.

## Publishing day and runs
- A **publishing day runs 06:45 UTC to 06:45 UTC the next day** (07:45–07:45 WAT). The window is fixed by the clock, not by when GitHub actually starts the run, so a late run still covers the same window.
- **06:45 run:** publishes tips for every eligible fixture in the window (kick-off at least 75 minutes away), then builds that day's **slips** from all the day's priced tips.
- **14:45 run:** fills only fixtures **in the same window** that still have no tips (e.g. fixtures added since the morning). It **never builds slips** and **never publishes beyond the current window**.
- Slips are built once per window, in the first run. If GitHub delays the 06:45 run, that delayed run is still the first, and fixtures it can no longer reach in time are simply skipped.

## Tip rules (most-likely product, per match)
**Eligibility:** the match must:
- be in a league with `tips_enabled`;
- have a prediction with **data quality ≥ 0.6** and **freshness ≤ 45 days**;
- kick off within the current window and at least **75 minutes** after the run;
- have no likely tips yet. It's published once and never edited.

**Probabilities (experiment 0):**
- result and O/U 2.5 use `p_final` (1xBet, power-method de-margined, where a snapshot exists; otherwise the model);
- everything else uses `p_model`;
- `p_source` is recorded.

**The 4 slots** (thresholds shared with the backtest, `backtest/products.py` `ProductConfig`):

| Slot | Choice | Price |
|---|---|---|
| Result | Most likely of 1 / X / 2 | 1xBet |
| Goals | Most likely over/under (0.5–4.5) priced at 1.30 or more (1xBet price where present, else fair odds) | 1xBet if priced |
| GG/NG | Whichever is more likely | 1xBet if priced |
| Builder | Most likely result-or-double-chance × over/under combo with fair price ≥ 1.50, from the model's score grid | none, always "no market price" |

**Recorded on each tip:** the latest **1xBet** snapshot for that exact selection, with `ref_bookmaker_id` and **`ref_captured_at`**. If it's missing, `ref_odds` is empty and the tip counts in hit rate only ("no market price"). Fair odds = 0.94 ÷ p. Confidence = clamp((p − 0.40) ÷ 0.40 × 10) × data quality.

**Per publishing day:**
- **Free tips:** all 4 tips on **3 fixtures**, chosen by the **league priority list first** (`leagues.display_order`, lowest first; leagues without a priority come last), then **earliest kick-off**. You set priorities with `league_priority <api_league_id> <n>`.
- **Banker of the day:** the highest-p priced result or goals tip at 1.30 or more.
- **Slips** (2, 5 and 10 odds, lowest-risk, bankers), built in the 06:45 run from the **whole day's priced single-leg tips**, one leg per match, greedily by p:
  - combined price = the product of the 1xBet prices, stored as `ref_odds`;
  - the chance all legs land is stored;
  - **capture time:** the slip stores `ref_captured_at` (the most recent of its legs' snapshot times), and each leg's own capture time is shown through its tip;
  - the slip locks at its earliest leg's lock.
- **Unchanged:** voids are excluded from hit rate, and a combo with a void leg is void.

## Access (RLS) changes
- **Tips and slips:** anonymous and signed-in non-members see **free tips at any time**. **Premium tips and slips appear only after the match has finished and been graded** (`result <> 'pending'`), no longer at lock. Members (`is_premium()`, Phase 5) see everything. `tip_legs` and `slip_legs` follow their parent.
- **Closing the same leak elsewhere:**
  - **`predictions`** (every market's probability, plus `p_final`) are visible to the public only for **finished** fixtures or fixtures with a **free tip** (or to members); no longer at lock. `prediction_markets` follows, since it's a security-invoker view.
  - **`model_runs`** (team ratings) becomes **service role only**, since ratings would let anyone compute the predictions.
  - The track record stays fully public after grading.

## League gate (on what is published)
The weekly shadow report gains a **per-league eligibility section**, and `league_tips` prints the same evidence. It evaluates the product **as it would be published**: tips are simulated with the same pure selection functions on the frozen predictions of finished matches. A league passes when it has:
- at least **150** evaluated matches;
- **1X2 log loss on `p_final`** within **0.01** of de-margined closing odds (power method; Pinnacle, else 1xBet, else Bet365, labelled), on matches with a closing price;
- **calibration of the selected tips per slot** (result, goals, GG/NG) within **3pp** each;
- and the report always shows the **model-fallback share**: the share of matches whose `p_final` came from the model because there was no 1xBet snapshot.

`league_tips <id> on` **refuses** NPFL, "World" competitions, and any league failing the gate; it prints the evidence either way.

## Schema (migration 0012)
- **`tips`:**
  - add `product` (`likely` | `value`, default `likely`), `p_source` and `ref_captured_at`;
  - the price check becomes "bookmaker, price and time all set, or all empty";
  - the new columns join the lock guard's protected fields.
- **`slips`:** add `ref_odds` and `ref_captured_at`.
- **`leagues`:** add `tips_enabled` (default false) and `tips_enabled_at`. `display_order` (already there) is the free-tip priority.
- **Policies:**
  - new select policies on `tips`, `slips` and `predictions` as described in Access;
  - `model_runs` public-read policy dropped.
- **Track record:**
  - `track_record` gets `product`, `p_source` and `ref_captured_at`; `profit_units` null without a market price; `odds_source` reads "bookmaker" or "no market price";
  - `track_record_summary`: hit rate on settled tips; units, ROI and average odds on priced tips only; `no_market_price` count; by product, slot, banker, free and league;
  - new `slip_record` / `slip_record_summary` (with capture time).
- **`tip_previews`** (dry run): run_id, created_at, window_start, kind (`tip` | `slip`), payload jsonb. Each preview run replaces the previous one. Service role only, and not part of the track record.

## Code
- `model/engrebodds_model/live/tips.py` (pure): `publishing_window(now)`, `select_tips`, `choose_free` (priority, then kick-off), `choose_banker`, `build_slips`. It reuses `markets` / `dixon_coles` helpers and the backtest thresholds.
- `model/engrebodds_model/live/publish.py`, the job:
  - window, eligible fixtures, latest predictions, 1xBet prices with capture times, then selection;
  - **preview mode** (default) replaces `tip_previews`; **on mode** inserts tips, legs and slips;
  - slips only in the window's first run;
  - logged in `ingestion_runs`.

  `--preview-all` ignores eligibility and quality filters (preview only) so you can see real output now.
- `model/engrebodds_model/live/shadow.py`: add the per-league product gate (simulated selected tips, per-slot calibration, `p_final` log loss vs closing, model-fallback share).
- CLI: `publish [--preview-all]`, `league_tips <api_league_id> on|off`, `league_priority <api_league_id> <n>`.
- Workflow: `publish` at **06:45 and 14:45 UTC**, with `PUBLISH_TIPS` read from the repo variable (unset = off). League commands run as manual inputs.

## Tests
- **Unit (pure):**
  - publishing window (06:45 boundaries, a late run stays in the same window, nothing beyond it);
  - slot rules (`p_final` vs `p_model`, goals ≥ 1.30, builder ≥ 1.50 with no price, confidence);
  - "no market price" path;
  - free picks by priority then kick-off (3 fixtures, 4 tips each);
  - banker;
  - slips from the whole day (one leg per match, combined price and chance, capture time = latest leg, lock = earliest leg).
- **DB (local, rolled back):**
  - preview mode writes only `tip_previews`; on mode writes tips, legs and slips; re-runs publish nothing new;
  - the 14:45-style run only fills gaps and builds no slips;
  - nothing beyond the window or inside the lock;
  - ineligible, low-quality and stale fixtures skipped;
  - NPFL and failing leagues refused by `league_tips`;
  - track-record maths (priced-only units and ROI, settled-only hit rate, voids out, slips the same);
  - grading end to end;
  - **RLS: anonymous users see free tips before kick-off; premium tips only after grading (hidden at lock and in play); predictions hidden until full time unless the fixture has a free tip; `model_runs` and previews hidden.**
- **Shadow:** the per-league gate on seeded data (pass and fail cases, fallback share reported).
- Existing suites keep passing (55 model, 39 ingest, 75 database; the existing pgTAP access test is updated for the new "after grading" rule).

## Verification (live)
1. Migration 0012 locally, then all tests; then the cloud (MCP, versions aligned).
2. Locally: enable one league, run `publish` in preview mode, then on mode, then grade, to check the whole path.
3. Cloud: `publish` runs in preview mode with no league enabled (previews nothing, correctly). Then a one-off `publish --preview-all` shows you real sample output (4 slots, prices with capture times, free picks, banker, slips) from today's fixtures, stale inputs and all, written only to `tip_previews`.

## Not in this step
Turning `PUBLISH_TIPS` on (after Pro, a clean shadow record and your approval of each league); the value product; NPFL tips; the website (Phase 4).
