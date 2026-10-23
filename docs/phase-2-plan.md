# Phase 2 plan: Dixon-Coles prediction model, two tip products and backtest (final revision)

## Context
Phase 1 is built and running: API-Football ingestion into Supabase, a schema that grades tips automatically and never deletes them, and a nightly backfill of 2022–2024 seasons (free API key; Pro deferred). Phase 2 turns that data into what the site sells. CLAUDE.md requires a **backtest before anything is published**, so the backtest is the centre of this phase and the gate before going live.

## Decisions (Emmanuel, 23 Oct 2026)
1. **Two products, each with its own backtest section and its own track record:**
   - **Value tips** are the **paid product**.
   - **Most-likely tips** are 4 per match (result, goals, GG/NG, builder), plus bankers and the 2/5/10-odds and lowest-risk slips.

   Access: all value tips are for members; for most-likely tips and slips, 3 fixtures a day are free and the rest are for members.
2. **The value product covers only the markets the backtest validated: 1X2 and O/U 2.5.** Any other market needs its own backtest before joining.
3. **Reference price: 1xBet.** It operates in Nigeria and appeared in every odds sample. SportyBet isn't carried by API-Football, and its unofficial endpoints are ruled out (CLAUDE.md). Every priced tip stores the **bookmaker and snapshot time**.
4. **League gate** (needed for any tips):
   - 1X2 log loss no more than **0.01 worse than Pinnacle closing with the margin removed**;
   - calibration within **3 percentage points** on the holdout.
5. **Value eligibility per league:**
   - the lower bound of the **95% CLV interval is above 0**, and
   - there are **at least 150 value tips** in the holdout.

   The **2025/26 final holdout can only remove leagues**: any league without positive mean CLV there is dropped. Every ROI is reported with bootstrap 95% intervals, and marked **"inconclusive"** when the interval includes 0.
6. **Tips without a market price** (builder tips, and any tip with no 1xBet snapshot) count in **hit rate only, never in units or ROI**, and are labelled **"no market price"**.
7. **Postponed, abandoned and cancelled matches** are void. Void tips are **excluded from hit rate**, units and ROI, though still listed. The Phase 1 summary already does this; tests will pin it down.
8. **Slips are tracked in the record:** the result, plus the **1xBet accumulator price** (the product of the legs' 1xBet prices) when every leg has one. Otherwise the slip is "no market price": its result counts, its units don't.
9. **v1 scope:** domestic leagues, plus cups only for **same-league ties**. Everything else is v1.1.
10. **Fair odds** carry a 6% flat margin: 0.94 ÷ probability.
11. **Open, for Emmanuel to decide (NPFL):**
    - **Current setting:** no NPFL or CAF tips, since there's no Pinnacle benchmark.
    - **Alternative:** NPFL **most-likely tips only**, under their own gate (holdout calibration within 3pp, and log loss better than base rates), labelled **"no market benchmark"** and tracked separately; still **no NPFL value tips**.

    The code will support either, switched by a league setting, so this can be decided at the backtest gate.

## Honest expectations
- **Most-likely tips** will show a high hit rate and a negative ROI, about −5% (the margin). That's shown, not hidden.
- **Value tips** exist only where CLV is proven positive. They may end up in few leagues, or none. That's the point of the gate.
- **The free key limits going live:** odds run at 2 pages per date, so most tips would have no 1xBet price, and current-season ratings miss August–October. Build, backtest and dry run on free; **the public record starts only after Pro**. Publishing stays behind a switch that's off by default.
- **Corners, cards, shots and fouls** need the Pro statistics backfill. v1 covers goals markets only.

## Two tips pointing different ways on one match
The two products answer different questions, so they can disagree. For example, most likely is "Home win (52%)" while the value pick is "Away win at 4.60 (our 25% vs 1xBet's 21%)". Neither is ever hidden, merged or edited to agree:
- the match page shows them in **two separate labelled blocks**, **"Most likely"** and **"Value pick"**, each with its probability, price and confidence or edge;
- one line explains the difference: "Most likely is the outcome we rate highest. A value pick is an outcome whose price is higher than our probability justifies; it will lose more often than it wins";
- if both products land on the same selection, it shows once with both labels, but it's still **two records**, one per product;
- each product's record is graded on its own tip. Bankers and slips use most-likely tips only.

## Phase 1 changes (migration 0011)
- **1xBet becomes the displayed bookmaker** (`is_display`). `sync_odds.load_policy` (`ingest/engrebodds_ingest/jobs/sync_odds.py`) reads `is_display`, so 1xBet automatically gets every market under the lean policy, and Bet365 drops to the main markets.
- **`tips`, new columns:**
  - `product` (`likely` | `value`);
  - `ref_captured_at`, with a check that the bookmaker, price and time are all set or all empty;
  - for the closing price: `close_price`, `close_bookmaker_id`, `close_captured_at`, `close_source` (`snapshot` | `fallback`) and `clv`.

  `product` and `ref_captured_at` join the lock guard's protected fields. The closing fields are written after lock, as grading fields are.
- **`slips`:** `ref_odds` (the 1xBet accumulator price, or null) and `ref_captured_at`.
- **Track-record views:**
  - `track_record` gets `product`; `profit_units` is null when there's no market price; `odds_source` reads "no market price"; `clv` is included.
  - `track_record_summary` is broken down by product and adds mean CLV. Hit rate counts settled tips (won + lost); units, ROI and average odds count priced tips only.
  - New `slip_record` and `slip_record_summary` views on the same rules.
- **`model_runs`:** version, league, fit time, training window, match count, and params as jsonb (home advantage, ρ, ξ, half-time share, team ratings). Public read.
- **League settings** for the gate results: `leagues.tips_enabled`, `leagues.value_enabled` and `leagues.benchmark` (`pinnacle` | `none`), all set from the backtest, never by hand-editing code.

## The model (`model/engrebodds_model/dixon_coles.py`)
- **Dixon-Coles per league:** attack and defence ratings, home advantage, ρ. Matches are weighted by time decay exp(−ξ·days ago) over about 3 seasons. A ridge penalty pulls ratings towards the league mean, and promoted or new teams start at last season's bottom-three average. Fitted by scipy maximum likelihood (L-BFGS).
- **Data quality** (0–1) comes from each team's recent match count. No tips below 0.6, or in leagues with less than one full season of history.
- **Half-time:** λ_HT = λ × first-half goal share. Whether to use one shared share or one per league is **tuned in the backtest**.
- **Blend variant:** p = w·p_model + (1−w)·p_Pinnacle, using **early** Pinnacle prices with the margin removed (closing prices would be look-ahead). w is tuned per market. It goes live only if it beats the model alone on the holdout, and only where Pinnacle prices exist.

## Markets (`markets.py`)
From a 0–10 × 0–10 score matrix: 1X2, double chance, over/under 0.5–4.5, team totals, GG/NG, clean sheets, the top 5 correct scores, and the half-time markets. Combos come exactly from the joint matrix. Every market code matches the Phase 1 catalogue.

## Products (`tips.py`)
**Most-likely:**
- **Result:** the most likely of 1 / X / 2.
- **Goals:** the most likely over/under selection priced at 1.30 or more.
- **GG/NG:** whichever is more likely.
- **Builder:** the most likely two-leg combo with a combined price of at least 1.50; always "no market price".
- **Confidence** = clamp((p − 0.40) ÷ 0.40 × 10) × data quality.
- **Banker:** the top tip priced at 1.30 or more.
- **Slips:** single-leg tips, one per match, chosen greedily until the target is reached. Each stores the chance every leg lands, and the 1xBet accumulator price when every leg has a price.
- **Free tips:** all 4 tips on 3 fixtures a day.

**Value:**
- **1X2 and O/U 2.5 selections only**, with a 1xBet price, where p × price ≥ 1 + edge.
- At most one per match: the highest edge.
- The edge is **tuned in the backtest**.
- Only in leagues with `value_enabled`.

**Price:** the latest 1xBet snapshot from `odds_latest`, with the bookmaker and time stored on the tip. With no snapshot, the price is fair odds, labelled "no market price".

## Live CLV
- A new job, **`close_value`**, runs in every live slot (:07, :22, :37, :52). For each published value tip kicking off in the next 5–25 minutes, it fetches `/odds?fixture=` and records the **closing price from Pinnacle if API-Football carries the match, otherwise from 1xBet**. The snapshot is also stored in `odds_snapshots`.
- CLV = price taken ÷ closing fair price − 1, where the closing fair price is that bookmaker's closing price with the margin removed. CLV is stored on the tip and shown in the value track record.
- **If GitHub skips the slot**, the job falls back to the last snapshot before kick-off from `odds_closing` and marks `close_source = 'fallback'`. The record shows how many fallbacks there were.
- **Quota:** at most one value tip per match, in eligible leagues only, so about 100 requests a day at most. That's against 7,500 on Pro, alongside about 3,000 on peak days for the existing jobs, so it fits. On the free plan, `close_value` is off along with publishing.

## Jobs and schedule
- `predict`: daily after the odds run; writes predictions for fixtures in the next 48 hours.
- `publish`: at 06:45 and 14:45 UTC; writes both products and slips for fixtures 75+ minutes away that have no tips yet. It never edits a published tip, and runs only when **`PUBLISH_TIPS=on`**.
- `close_value`: in the live slot, when publishing is on.
- `backtest`: run by hand, locally; reports go to `reports/`.

## Backtest (`backtest/`)
- **First task:** confirm that football-data.co.uk's **2025/26** files still have Pinnacle closing columns (`PSCH`/`PSCD`/`PSCA`, `PC>2.5`/`PC<2.5`). The notes list them, but say some bookmakers have been dropped. If they're gone, the fallback benchmark for 2025/26 is Betfair Exchange closing (`BFECH`…), then market-average closing (`AvgCH`…), documented in the report. I'll also check their terms of use, and which of the ~38 leagues carry 1xBet columns (`1XBH`…). Where 1xBet is missing, Bet365 stands in, and the report says so.
- **Walk-forward, no look-ahead:** refit weekly on matches before the cutoff, predict the next 7 days.
  - **Tuning:** 2019/20–2022/23, for ξ, the penalty, the half-time share, the blend weight w, the value edge, and the 1.30/1.50 thresholds.
  - **Holdout:** 2023/24–2024/25, evaluated once; decides the league gate and value eligibility.
  - **Final holdout:** 2025/26 to date, run once with everything frozen; can only remove leagues.
- **Metrics** for the model and for the blend: 1X2 log loss, Brier and ranked probability score; O/U 2.5 log loss; calibration by decile; compared with de-margined Pinnacle closing and with base rates.
- **Simulation**, reported **separately per product**, with slips reported as well:
  - ROI at **Bet365 early and Bet365 closing prices**, plus 1xBet where present;
  - hit rate **with** average odds, units, maximum drawdown, and **CLV against Pinnacle closing**;
  - bootstrap 95% intervals and an "inconclusive" flag.

  Tips without a market price appear in hit rate only, and void matches are excluded.
- **Outputs:** `reports/backtest-<date>.md` with:
  - calibration charts and cumulative-units charts per product;
  - a per-league table: gate pass or fail, value eligibility, the number of value tips and the CLV interval;
  - the chosen parameters;
  - the final-holdout outcome.

## Code layout
New package `model/` (numpy, scipy, pandas) that depends on `ingest`, reusing `engrebodds_ingest.db.job_run`, the settings and the connection handling:
`model/engrebodds_model/{dixon_coles, markets, ratings, tips, clv}.py`, `jobs/{predict, publish, close_value}.py`, `backtest/{football_data, walkforward, metrics, blend, report}.py`, tests in `model/tests/`. CLI: `python -m engrebodds_model <job>`.

## Build order (Emmanuel reviews after each step)
1. **Model core and markets**, with tests.
2. **Backtest** (data checks first, then model, blend, both products and slips). **Gate:** agree:
   - which leagues pass and which get the value product;
   - the tuned edge and thresholds;
   - whether the blend goes live;
   - **the NPFL decision**.
3. **Migration 0011** and the `predict` job on schedule (predictions only).
4. **Publishing** both products and slips, plus `close_value`, behind `PUBLISH_TIPS` (off), tested against a local database only.
5. **Go-live checklist:**
   - upgrade to Pro and set `API_PLAN=pro`;
   - **backfill the 2025 season** and refit;
   - confirm API-Football carries Pinnacle for the eligible leagues;
   - a week of clean predictions;
   - then `PUBLISH_TIPS=on`.

## Verification
- **Unit tests:**
  - parameter recovery, market sums, combos;
  - slot rules; value rule (1X2 and O/U 2.5 only, one per match, tuned edge);
  - CLV maths, including removing the margin;
  - a slip's accumulator price only when every leg is priced;
  - the conflicting-tips case (both kept, separately).
- **DB tests:**
  - the record maths per product: priced tips only in units and ROI, voids out of hit rate, slips on the same rules;
  - publish never edits a published tip;
  - closing fields writable after lock while everything else stays locked;
  - bookmaker and snapshot time on every priced tip.
- **No-look-ahead tests:** fits never see matches after the cutoff, and the blend never uses closing prices.
- **The reports** read together at the gate. The final holdout runs only after parameters are frozen.

## Not in Phase 2
Cross-league and mixed-league competitions (v1.1); CAF tips; value tips in markets other than 1X2 and O/U 2.5; corners/cards/shots/fouls; the website and written analysis (Phase 4); payments (Phase 5).
