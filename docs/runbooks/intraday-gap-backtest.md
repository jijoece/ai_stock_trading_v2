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
   `release_time` may be empty (see the release-time caveat below). Treat the
   file as a versioned snapshot: record its SHA-256 (`shasum -a 256 data/earnings.csv`)
   and commit or archive it. Use that exact file for fetching, tuning and the
   single out-of-sample run: the chronological split is recomputed from the
   rows, so a changed CSV moves the 60/40 boundary and invalidates the frozen
   parameters. Verify the snapshot by comparing the recorded CSV SHA-256 across
   runs. Do not compare `input_manifest_hash`: it covers only the selected
   split's earnings rows and the bars that run read, so it differs between the
   in-sample and out-of-sample runs even when the CSV is unchanged.
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
   `post_report_overnight`} x opening range {5, 15, 30} minutes (regular-session
   variants only; the extended-hours variants use the configured default) x
   `maximum_risk_percent` {5, 8, 12}, which is 33 runs:

   ```bash
   .venv/bin/python -m trading_research.cli tune-intraday-gap \
     --earnings-csv data/earnings.csv --start 2024-01-01 --end 2026-10-31 \
     --cache-dir data/intraday-cache
   ```

   It writes every run's report under `reports/tuning/`, a summary
   (`reports/intraday-gap-tuning.json` and `.md`), and a frozen-parameters
   template (`reports/intraday-gap-frozen.template.json`). The template is
   pre-filled with the highest in-sample expectancy per variant (ties go to the
   lower risk percent, then the longer opening range). That ranking is a
   suggestion, not a decision. The sweep stops at the first failing run and
   never summarizes a partial grid. Single in-sample runs still work with
   `backtest-intraday-gap --split in_sample --opening-range-minutes N
   --maximum-risk-percent R`.

   The split is chronological 60/40 by event (report) date; a date never
   straddles the boundary.
4. **Freeze the chosen parameters in a commit** before any out-of-sample run.
   Review the template, edit it if you choose differently, copy it to
   `config/intraday-gap-frozen.json`, and commit it. Values must be on the grid,
   and extended-hours variants must have `"opening_range_minutes": null`. The
   file records the earnings CSV's SHA-256, the `in_sample_fraction`, and
   `strategy_config_sha256`: a hash of every `earnings_gap_intraday` setting
   except the two grid parameters, plus the equity, adjustment, and feed the
   sweep ran with. Do not edit that hash; re-tune if the configuration changes.
5. **Run out of sample once per variant.** `--split out_of_sample` takes its
   parameters only from `--frozen-params` (passing `--opening-range-minutes` or
   `--maximum-risk-percent` too is refused). It refuses if the file is untracked
   or has uncommitted changes, if the CSV's SHA-256, `in_sample_fraction`, or
   strategy configuration (any non-grid setting, or `--equity`) differs from the
   frozen values, or if the variant has no frozen
   entry. It uses
   only the later 40% of events:

   ```bash
   .venv/bin/python -m trading_research.cli backtest-intraday-gap \
     --earnings-csv data/earnings.csv --start 2024-01-01 --end 2026-10-31 \
     --variant orb --split out_of_sample --cache-dir data/intraday-cache \
     --frozen-params config/intraday-gap-frozen.json
   ```

   The run ID includes the frozen file's SHA-256 and commit. Each run is
   recorded in `config/intraday-gap-frozen.oos-ledger.json`. Before any report
   is written, the run reserves its variant (`"status": "pending"`, with its run
   ID) under an exclusive lock (`config/intraday-gap-frozen.oos-ledger.lock`, an
   empty file you need not commit). After the reports are written, the entry
   becomes `"status": "committed"`. Re-running with identical inputs reproduces
   the same run ID and is allowed; that is also how an interrupted run (left
   `pending`) is completed. A run that would produce a different run ID for a
   variant already in the ledger, whether pending or committed, including a
   concurrent run, fails with `OUT_OF_SAMPLE_ALREADY_RUN` and writes no report.
   Commit the ledger with the results. It guards against accidental and
   concurrent re-runs; it does not stop an operator who deliberately edits it.
6. **Record the result in the plan** (`docs/intraday-earnings-gap-plan.md`,
   "Step 7 results"): one row per variant, with the run ID, the frozen commit,
   the verdict, and the section 7 metrics.

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
- Truncated data fails closed with `incomplete_session_data` and never shrinks the
  sample. The run is invalid in any of these cases: a candidate's regular
  session ends before the entry cutoff with no signal; a reaction-day regular
  session is missing entirely; an extended-hours limit order goes unfilled
  because the bars stop inside its order window; a baseline's session lacks the
  09:30 opening bar or ends more than one bar before the close; a filled
  position's bars end before its exit. Isolated missing bars inside a session
  are tolerated. Refetch complete sessions and rerun.
- Missing `release_time`: the conservative bound applies (am released at 09:30,
  pm released at 20:00). Excluded events get `release_time_unverified` and are
  counted in the report.
- Entries follow the plan's section 5.3 semantics: the order is submitted at
  signal-bar completion, risk guard and sizing use the signal-bar close, and the
  next open never cancels or resizes. A gap beyond the stop still fills and the
  stop exits at the worse of the stop and that open.
- Free-feed (IEX) volume understates consolidated volume. The helper
  `reconcile_daily_volume` (flags differences, never repairs them) exists in
  `backtesting/intraday_history.py` but is **not yet wired into the fetch,
  backtest or report**, so the run does not flag symbol-days whose intraday and
  daily volumes disagree. Liquidity filters, relative volume and participation
  caps use the one consistent intraday source; check volume sanity manually
  until reconciliation is wired in.
- Survivorship, estimate-revision and fill-model caveats are printed at the top
  of every report.
