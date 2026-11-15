# Runbook: Intraday earnings-gap backtest

Operator steps for the `backtest-intraday-gap` command (build plan:
[`../intraday-earnings-gap-plan.md`](../intraday-earnings-gap-plan.md), Step 6/7).
Research only. The strategy `earnings_gap_intraday` stays at `mode: backtest`;
no broker call, model call, scheduler entry or paper order is involved at any
step. Nothing here is financial advice. A passing run is **not** research
acceptance: the section 7 gate is evaluated by the operator on the single
out-of-sample run.

## Boundaries

- Paper/backtest only. The loader refuses `paper`, `shadow` and `live` modes
  for this strategy and the global `activation_stage` is not changed.
- `--fetch` is the only step that needs credentials (Alpaca market data, read
  from `.env` via the usual config) and the only step that uses the network.
  It is operator-only; automated workers and tests never run it.
- Without `--fetch` the command is fully offline and reads the on-disk cache.
  Missing cached symbol-days fail with `BACKTEST_ERROR` ("no cached intraday
  data ... run the explicit fetch step").

## Steps

1. **Export the earnings CSV** (columns: `symbol,fiscal_period,report_date,timing,release_time,eps_estimate,eps_actual,source,captured_at`; see
   `tests/fixtures/earnings_surprises_sample.csv`). `timing` is `am` or `pm`;
   `release_time` may be empty (see the release-time caveat below).
2. **Fetch bars** (operator, credentialed). Fetches every missing symbol-day
   (event history, the control window and the trend benchmark) for all events in
   range, for both splits, then stops without running a backtest:

   ```bash
   .venv/bin/python -m trading_research.cli backtest-intraday-gap \
     --earnings-csv data/earnings.csv --start 2024-01-01 --end 2026-10-31 \
     --variant orb --split in_sample --cache-dir data/intraday-cache --fetch
   ```

3. **Tune on the in-sample split only**, offline, over the fixed grid:
   variant {`orb`, `pmh_break`, `vwap_pullback`, `premarket_entry`,
   `post_report_overnight`} x opening range {5, 15, 30} minutes (where it
   applies) x `maximum_risk_percent` {5, 8, 12}:

   ```bash
   .venv/bin/python -m trading_research.cli backtest-intraday-gap \
     --earnings-csv data/earnings.csv --start 2024-01-01 --end 2026-10-31 \
     --variant orb --split in_sample --cache-dir data/intraday-cache \
     --opening-range-minutes 15 --maximum-risk-percent 8
   ```

   The split is chronological 60/40 by event (report) date; a date never
   straddles the boundary.
4. **Freeze the chosen parameters in a commit** before any out-of-sample run.
5. **Run out of sample once.** `--split out_of_sample` refuses to run unless
   `--opening-range-minutes` and `--maximum-risk-percent` are both given, and it
   uses only the later 40% of events:

   ```bash
   .venv/bin/python -m trading_research.cli backtest-intraday-gap \
     --earnings-csv data/earnings.csv --start 2024-01-01 --end 2026-10-31 \
     --variant orb --split out_of_sample --cache-dir data/intraday-cache \
     --opening-range-minutes 15 --maximum-risk-percent 8
   ```

6. **Record the result in the plan** (`docs/intraday-earnings-gap-plan.md`).

## Outputs

`reports/intraday-gap-<run_id>.json` and `.md` (override with `--report-dir`).
The run ID and every hash are deterministic: the same inputs give the same run
ID and byte-identical artifacts. Persistence is a JSON artifact only; there is
no database migration. Generated `reports/intraday-gap-*` files are git-ignored
and must not be committed.

## Reading the report (conservative readings)

- Baselines are compared with the strategy on mean net return % per trade
  (baselines have no R), run on all eligible candidates; a baseline with no
  trades fails its criterion.
- The ordinary-day control re-dates each event 10 sessions earlier, skips that
  symbol's event days, and fails the gate if it has no trades or its expectancy
  is above zero.
- Gap buckets are half-open: [5,10), [10,20), [20,inf).
- A profit factor with no losing trades counts as passing.
- The minimum-trades floor of 100 cannot be lowered.

## Data caveats

- **Early-close post-market end: UNCONFIRMED against vendor/broker - operator
  must confirm before merge.** It is the configured parameter
  `early_close_postmarket_end` (default 17:00 ET). Do not treat it as confirmed.
- Missing `release_time`: the conservative bound applies (am released at 09:30,
  pm released at 20:00). Excluded events get `release_time_unverified` and are
  counted in the report.
- Entries follow the plan's section 5.3 semantics: the order is submitted at
  signal-bar completion, risk guard and sizing use the signal-bar close, and the
  next open never cancels or resizes. A gap beyond the stop still fills and the
  stop exits at the worse of the stop and that open.
- Free-feed (IEX) volume understates consolidated volume; volume
  reconciliation flags differences but never repairs them.
- Survivorship, estimate-revision and fill-model caveats are printed at the top
  of every report.
