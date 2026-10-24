# Model improvement plan (Phase 2, between step 2 and the gate), revised

## Context
The first backtest (22 leagues, private football-data research) showed the Dixon-Coles model trailing Pinnacle's closing prices by **0.0215 in 1X2 log loss** (0.0178 against early prices). Only 1 of 22 leagues passed the gate, and value tips had negative CLV everywhere. Emmanuel asked to try improving the model before deciding the product. The original Phase 2 plan is in `docs/phase-2-plan.md`.

## What the diagnostics showed (holdout 2023/24–2024/25, read-only)
- **1X2 calibration is fine.** A fitted recalibration gained nothing (1.0169 → 1.0168). Draws are slightly under-predicted (25.4% vs 26.2%).
- **The core gap is information:** 0.0185 even in January–June without promoted teams.
- **Promoted and relegated teams early in the season** are the worst segment (gap 0.047), and most of those matches are excluded by the data-quality filter.
- **Goals-market tips are overconfident:** Under 2.5 at 60.2% landed 53.3%; GG/NG at 58.9% landed 54.7%, and at 67.8% landed 61.7%.

## Data licensing (checked 24 Oct 2026)
football-data's **fair data usage policy** (contact page): the data is "intended for private individuals only for personal betting and/or academic interest and research". Requests for commercial permission "will not receive a response… assume no data usage permission is granted". Commercial users should buy **TheStatsAPI**. Automated downloads should be light, "twice per week maximum". Consequences:
- **Nothing derived from football-data goes into the live commercial model**, including ξ, penalties, season reset, shots weight and calibration maps.
- **Production parameters are tuned on our own API-Football results** (licensed). Log-loss tuning needs only results, not odds; the nightly backfill is loading 2022–2024. Calibration maps for goals markets are fitted the same way.
- football-data stays **private research for market comparisons only** (gaps to the market, CLV, gate). Before commercial launch those comparisons get re-run on TheStatsAPI or another licensed odds source (already on the go-live checklist).
- **Download etiquette:** the loader fetches only missing files. The only new download is the 22 current-season (2026/27) files, once. My first download (220 files, one a second) was heavier than "light"; no more bulk downloads.

## Validation design: nothing accepted in-sample
- **Rolling season-by-season validation.** For each validation season V in 2020/21, 2021/22 and 2022/23:
  1. tune an experiment's settings on seasons before V;
  2. evaluate on V.

  An experiment is **accepted only if it improves log loss on average across the three validation seasons, and in at least 2 of the 3**. This applies to every experiment, especially calibration, the draw shift and per-league tuning, whose maps and shifts are always fitted on earlier seasons and applied to the later one.
- Rolling, rather than a single split, because 2019/20–2020/21 were the no-crowd COVID seasons (home advantage collapsed). Tuning only on them would bias home advantage.
- **Holdouts:** the accepted model runs once on 2023/24–2024/25 (the gate, as before: Pinnacle closing) and on 2025/26 (market-average closing). Both have been looked at once already, which is noted in the report.
- **2026/27 to date** is used **only to compare the baseline and improved models**, never for gate decisions. Its benchmark is the **market-average closing price, not Pinnacle** (Pinnacle is unreliable since July 2025).

## Experiments
**0. Market-based probabilities for the most-likely product only.**
- Where market prices exist (1X2 and O/U 2.5; in the backtest, *early* prices: Pinnacle early before July 2025, market-average early after), the most-likely tips use **de-margined market probabilities**.
- Unpriced markets keep the model: builder, correct score, half-time, GG/NG and other O/U lines, and leagues without odds such as NPFL.
- Reported next to model-only: gate results (log loss gap) and tip calibration per slot.
- A variant is reported alongside for consistency: **market-implied goals rates**. λ and μ are solved so the score grid reproduces the market's 1X2 and O/U 2.5, then every market is derived from them. That stops a market-based result tip and a model-based builder tip from contradicting each other.
- **The value product stays model-only.**

**1. Linked divisions (promoted-team ratings).**
- Fit each country's divisions together, with a goal level per division: England (E0–EC), Scotland (SC0–SC3), and the two divisions each of Germany, Italy, Spain and France. Teams moving between divisions link the scales.
- Countries with one division in the data: a newly arrived team is pulled towards last season's bottom-three average instead of 0.
- Data quality counts matches in the linked fit, which recovers early-season coverage.

**2. Season-boundary reset.** At each new season, older matches' weight is multiplied by a factor k ≤ 1, tuned (k = 1 means no change).

**3. Goals-market calibration and draw shift.** Per market family (O/U per line, GG/NG, team totals), a two-parameter logistic calibration fitted on earlier seasons only. A single 1X2 draw shift is tested the same way. Kept only if validation accepts it, and checked against the selected-tip overconfidence.

**4. Per-league tuning** of ξ and the penalty. Kept only if validation accepts it by more than 0.001.

**5. Shots on target (run last).**
- **Data check first:** compare football-data's `HST`/`AST` with API-Football's "Shots on Goal" on about 20 Premier League 2024/25 matches. The fixture list is already saved from the audit, the free plan allows that season, and it costs about 20 requests. **If they don't agree closely, the experiment stops**, since live data would differ from what it was tuned on.
- **If they agree:** fit a plain-Poisson rating model on shots on target, converted to expected goals by the league's conversion rate, and blend: log λ = (1−s)·log λ_goals + s·log λ_shots, with s validated.
- Live, it needs the Pro statistics backfill; until then s = 0.

## Code changes
- `model/engrebodds_model/dixon_coles.py`: per-division `base`, ridge towards per-team prior means, a shots variant (ρ fixed at 0), with the exact gradient extended and tested (gradient check, recovery with two linked divisions).
- `model/engrebodds_model/calibration.py` (new): logistic maps per market family (fit, apply, JSON), and the draw shift.
- `model/engrebodds_model/implied.py` (new): de-margining helpers, and solving λ and μ from market 1X2 and O/U 2.5 (experiment 0 variant).
- `backtest/walkforward.py`: pyramid fits, the season factor, the shots blend, and a rolling-validation driver. `Config` gains `link_divisions`, `season_reset`, `shots_weight` and `draw_shift`.
- `backtest/football_data.py`: load `HST`/`AST` and season `2627`, missing files only.
- `backtest/shots_check.py` (new): the football-data vs API-Football shots comparison.
- `backtest/run.py` and `report.py`:
  - an **experiment ladder** (each experiment's validation result per season, accepted or rejected, never hidden);
  - experiment 0 alongside model-only;
  - baseline vs improved on the holdouts and on 2026/27 (comparison only);
  - goals-tip calibration tables;
  - gate and value results as before.
- **Production parameter tuning** (new script `model/engrebodds_model/tune_live.py`): runs the same rolling validation on **API-Football results from Supabase** to produce the live settings file. It's built now and run once the backfill has finished.

## Verification
- All existing tests pass, plus new ones:
  - gradient and linked-division recovery;
  - calibration maps (monotone, identity when already calibrated, fitted only on earlier seasons);
  - implied λ and μ reproducing the market's 1X2 and O/U 2.5;
  - no look-ahead extended to pyramid fits, the season factor and the shots blend.
- The experiment ladder shows every experiment's per-season validation result.
- The final report (local, private) compares baseline, improved model, and experiment 0 on:
  - log loss gap;
  - leagues passing the gate;
  - goals-tip calibration;
  - value CLV;
  - early-season coverage.
- **Decision point with Emmanuel:** keep improving, adopt experiment 0 for the most-likely product, or settle the product question.

## Not in scope
Lineups, injuries, xG; NPFL and CAF backtests (no odds); live-pipeline changes (after the decision); any use of football-data-derived parameters in production.
