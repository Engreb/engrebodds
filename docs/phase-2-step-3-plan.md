# Phase 2, step 3: daily predictions (revised)

## Context
The Phase 2 gate is closed (Emmanuel, 24 Oct 2026):
- **value product off**;
- **most-likely tips use market prices where they exist** (experiment 0) and the improved model elsewhere;
- **model work stops for now**.

Step 3 puts real model output into the database every day, plus a **shadow evaluation** of how those predictions score after full time, so it can be watched before anything is published. **No tips are written in this step**; publishing is step 4, behind a switch. Standing rules:
- **nothing tuned on football-data goes into the live model**; live settings come from API-Football data via `tune_live.py`;
- shots need the Pro statistics backfill.

## What gets predicted
- **Domestic leagues** on the allowlist (tier full or goals, plus NPFL), kick-off in the **next 48 hours**. Fitted **per league** on that league's finished matches from the last ~3 years in our `fixtures` table.
- **Domestic cups:** a tie is predicted from a league's fit **only when both teams are rated in the same domestic league** (same country). Mixed-league ties are **skipped and logged** in the run notes (count and examples).
- **Continental and international competitions are excluded explicitly**: every league API-Football files under country "World" (CAF Champions League and Confederation Cup, UEFA competitions, AFCON and World Cup qualifiers). They're still ingested, but get no predictions in v1.
- **Not predicted:** teams with no matches in the window; leagues with fewer than 50 finished matches in the window. Both are counted in the notes.
- **Per fixture:**
  - expected goals (λ, μ) and ρ;
  - top-5 correct scores and the half-time score;
  - **data quality** = min(1.0, the less-documented team's matches in the last year ÷ 10), so **it never exceeds 1.0**;
  - **freshness** = days since the less-recent team's last match. On the free plan this season's results before 21 October are missing, so ratings lean on last season.
- **Per market**, both values are stored:
  - **`p_model`**, the model (calibrated if the active settings include maps);
  - **`p_final`**, what the most-likely product would use, plus **`p_source`** (`market` or `model`).

  For market-based values, the **bookmaker and snapshot time** are stored too.
  - **Market-based** (1X2 and O/U 2.5 only, the markets the backtest validated): from the **latest 1xBet snapshot** in `odds_latest`, with the margin removed by the **power method** (find k so that Σ (1/price)^k = 1). That corrects the favourite-longshot skew, which proportional removal leaves in. The backtest showed proportional-removed favourites landing 3–5 points more often than stated.
  - With **no 1xBet snapshot**, `p_final = p_model` and `p_source = model`.
  - **Stated difference:** experiment 0 was backtested on **Pinnacle early prices** (market average after July 2025) with **proportional** removal. Live uses **1xBet with power removal**. That's a different bookmaker, a larger margin (~5–6% vs ~2–3%) and a different method, so live `p_final` isn't exactly what was backtested. The shadow evaluation measures it.
- **Freezing:** predictions update until **kick-off minus 1 hour**, then freeze. A new database trigger enforces it: after lock, the only allowed change is `thin_predictions()` clearing old `probs`. The frozen row is what the shadow evaluation scores.
- **API cost: zero requests**, since everything comes from our database.

## Settings (`model_settings` table)
- **One active row** holds the model configuration (ξ, penalty, shots weight), the calibration maps, the validation seasons, the **experiment ladder**, the source and the date.
- **Until settings tuned on API-Football are activated**, the job uses **a-priori defaults, not football-data-derived:**
  - ξ = 0.0019/day (Dixon & Coles 1997);
  - penalty 1.0;
  - no shots;
  - no calibration.

  These are versioned **`dc-untuned`**.
- **`tune_live` writes its result as an *inactive* row** with the full ladder attached. **Activation is separate:** `python -m engrebodds_model activate_settings <id>`, also available as a manual GitHub Actions input, after you've reviewed the ladder. `tune_live` itself runs as a manual GitHub job (using the existing `DATABASE_URL` secret) once the backfill has at least 3 seasons for the main leagues (est. 1–2 weeks; 68 of ~1,160 league-seasons so far).

## Shadow evaluation (weekly)
- A `shadow_report` job runs **weekly** on GitHub (Mondays) and on demand, at zero API cost. It scores the **frozen predictions of matches that have finished** and writes a row to `shadow_reports` (week, settings version, results as jsonb), which you can read in Supabase. It also prints a short summary to the run log.
- **Reported overall, per league (where at least 50 matches) and per settings version:**
  - **1X2 log loss**: the model (`p_model`) and the product (`p_final`), vs **league base rates** (the league's home/draw/away frequencies over the previous 3 years);
  - **1X2 log loss vs de-margined closing odds** where a closing price exists (`odds_closing`; 1xBet, Bet365 or Pinnacle, labelled, power method);
  - **calibration:** 1X2 by probability decile, and **goals markets** (O/U 0.5–4.5, GG/NG, team totals), each with its calibration error;
  - **counts:** matches scored, how many had market-based `p_final`, and freshness and data-quality mix.
- **Caveat stated in every report:** on the free plan, few matches have odds snapshots, so market comparisons will be thin until Pro.

## Schema (migration 0011)
- **`model_settings`:** id, version, `is_active` (unique when true), source, config jsonb, calibrator jsonb, validated_on, ladder jsonb, created_at, activated_at. Public read; writes by the service role only.
- **`model_runs`:** one row per (league, model version), **replaced each run** so it doesn't grow: fitted_at, training window, match count, params jsonb (home advantage, ρ, goal level, team ratings). Public read.
- **`predictions`** gains:
  - `settings_id`, `fresh_days`, `computed_at`;
  - **`final` jsonb** for market-sourced keys only: {key: {p, bookmaker, captured_at}}. Model-sourced keys need no copy, since `p_final = p_model`, which keeps storage small;
  - `probs` keeps every market's `p_model`.

  A **view `prediction_markets`** expands this to one row per market, with p_model, p_final, p_source, bookmaker and snapshot time, so it's easy to read and to use in step 4.
- **Guard trigger** freezing predictions after kick-off minus 1 hour, except clearing `probs` via thinning.
- **`thin_predictions()`:** drops `probs` and `final` from fixtures finished more than 30 days ago that have no tips. The headline columns (λ, μ, 1X2) stay forever. The `predict` job calls it. Storage is about 0.75 MB a day before thinning, so the free tier lasts well over a year (now 23 MB).
- **`shadow_reports`:** week, settings_id, created_at, results jsonb. Service role only.
- **1xBet becomes the displayed bookmaker.** `sync_odds` reads `is_display`, so 1xBet gets every market from the next odds run, and Bet365 drops to main markets. This also puts the 1xBet snapshots that `p_final` needs in place.

## Code
- `model/engrebodds_model/live/data.py`:
  - load domestic training matches and upcoming fixtures from Supabase, flagging cups, mixed-league ties and "World" competitions;
  - load the latest 1xBet snapshots, and the closing odds for the shadow report;
  - reuse `tune_live.load`'s SQL style.
- `model/engrebodds_model/implied.py`: add `demargin_power()` (and keep proportional for the backtest's numbers), with a unit test.
- `model/engrebodds_model/live/settings.py`: the active settings, or the `dc-untuned` defaults; build the `Calibrator` from JSON; `activate(id)`.
- `model/engrebodds_model/live/predict.py`, the job:
  - fit per league (`dixon_coles.fit`), with the optional shots blend through a **shared helper** that the backtest also uses, so the two can't drift;
  - build matrices with `score_matrix` / `ht_matrix` and probabilities with `markets.all_markets`;
  - calibrate, add the market-based `p_final` for 1X2 and O/U 2.5;
  - upsert predictions outside the lock, and replace `model_runs`;
  - thin.

  It reuses `engrebodds_ingest.db.job_run`, so runs are logged in `ingestion_runs`.
- `model/engrebodds_model/live/shadow.py`: the weekly report. It reuses `backtest/metrics.py` (log loss, calibration) and `walkforward.base_rates`.
- `tune_live.py`: write an inactive `model_settings` row with the ladder (`--dry-run` prints instead).
- CLI: `predict`, `tune_live`, `activate_settings <id>`, `shadow_report`.
- Workflow `ingest.yml`:
  - install `./model` too;
  - **predict** twice daily on both plans (`57 5,17 * * *`);
  - **shadow_report** on Mondays;
  - `tune_live` and `activate_settings` manual only.

## Tests
- **Unit:**
  - power margin removal (sums to 1; moves probability towards favourites compared with proportional);
  - settings fallback and activation logic;
  - the shared shots helper matches the backtest path;
  - data quality capped at 1.0, and freshness;
  - cup tie same-league vs mixed (skipped and noted);
  - "World" competitions excluded;
  - `p_final` / `p_source` rules (market for 1X2 and O/U 2.5 with a 1xBet snapshot, model otherwise).
- **DB (local Supabase, rolled back):**
  - predictions written, with 1X2 summing to 1 and `probs` keys matching the catalogue;
  - re-run updates rather than duplicates;
  - the trigger blocks edits after lock but allows thinning;
  - `model_runs` stays one row per league;
  - `thin_predictions()` behaviour;
  - `prediction_markets` view correct;
  - `tune_live` row inactive until activated, with only one active row;
  - RLS: anonymous users can read settings, runs and predictions per the existing policy; they can't read shadow reports or write anything;
  - a shadow report on seeded finished fixtures has the expected structure.
- Existing suites keep passing (44 model, 39 ingest, 75 database).

## Verification (live)
1. Migration 0011 locally, then all tests; then the cloud project (MCP tools, versions aligned with the repo).
2. Run `predict` locally, then once on GitHub (manual run). Check the `ingestion_runs` row, prediction counts for the next 48 hours, cups skipped and "World" excluded, and data quality never above 1.
3. Spot-check 10 upcoming matches: `p_model` vs `p_final` (where 1xBet exists), freshness, data quality. Shown to you from the cloud database.
4. First shadow report after the weekend's matches have frozen and finished. You'll see what it says, including how thin the market comparisons are on the free plan.

## Not in this step
Tips, slips and the publish switch (step 4); the NPFL tips decision (step 4, a league setting); continental competitions (v1.1); running `tune_live` for real (once the backfill has 3 seasons); the website (Phase 4).
