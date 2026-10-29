# Runbook: Historical strategy backtesting

Replays a deterministic strategy scanner over daily-bar history and backtests the
resulting signals with the existing engine. Research only: no broker, no paper
order, no model call, no scheduler. Nothing here is financial advice. Design and
decisions: [`../historical-backtesting-plan.md`](../historical-backtesting-plan.md).

## Run it

```bash
# Offline, synthetic data (pipeline check; says nothing about any real market)
python -m trading_research.cli backtest-strategy --strategy momentum_breakout \
  --symbols AAA,BBB,CCC --start 2023-01-03 --end 2024-12-31 --initial-cash 100000 \
  --data-source fixture --db /tmp/backtest.sqlite3

# Real Alpaca daily bars. The first run fetches and writes the snapshot; later runs
# read only the snapshot, offline.
python -m trading_research.cli backtest-strategy --strategy momentum_breakout \
  --symbols SYM1,SYM2 --start 2022-01-03 --end 2025-12-31 --initial-cash 100000 \
  --data-source alpaca --snapshot data/backtests/mb-2022-2025.snapshot.json \
  --split-date 2024-12-31 --cost-grid 0,10,25
```

Fetching needs `ALPACA_MARKET_DATA_API_KEY` and `ALPACA_MARKET_DATA_API_SECRET`.
They are read only when the snapshot file does not exist yet; with an existing
snapshot no credential is read. Only run a fetch when you mean to: it calls the
market-data API (the paper broker is never touched).

| Option | Meaning |
|---|---|
| `--strategy` | `momentum_breakout` or `mean_reversion` (see "Strategy limits"). `event_catalyst` is refused. |
| `--symbols` | Your explicit universe. The benchmark is added automatically. |
| `--snapshot` | Snapshot file: read if present, otherwise fetched once and written. Required for `alpaca`. |
| `--benchmark` | Relative-strength and benchmark-return reference. Default `SPY`. |
| `--slippage-bps`, `--fee-per-order` | Cost model for the primary run. |
| `--cost-grid 0,10,25` | Re-run at each slippage level and report them side by side. |
| `--split-date` | Report the window up to the date and the window after it separately. |
| `--min-trades` | Round trips below this are flagged `INSUFFICIENT_SAMPLE`. Default 30. |
| `--price-adjustment` | `split` (default), `raw` or `all`. Recorded in the snapshot and run identity. |
| `--screening-config` | Alternative screening thresholds. Default `config/screening.yaml`. |
| `--db` | SQLite file for the `backtest_*` tables. Default: the configured research database. |

The command prints one JSON report (sorted keys) and exits 0, or prints
`{"error": {...}}` and exits 2. Error text is sanitized: it never contains
credentials or raw provider output.

## Reading the report

- `run` / `data`: the run ID, strategy version and config hash, and the snapshot
  SHA-256. The same inputs give the same run ID, metrics and report bytes. A
  different snapshot gives a different run ID even when the signals are identical.
- `screening_profile`: which gates were `EVALUATED` and which were `NOT_EVALUATED`.
  Market cap, fundamentals, halts, spread, earnings blackout and similar gates have
  no point-in-time history here, so they are not applied. A symbol that passes the
  backtest screen is not thereby production-eligible.
- `signals`: evaluations and `status_counts` (these sum to `evaluations`), plus
  `reason_code_counts`. One evaluation carries several reason codes, so reason-code
  counts do not sum to the evaluation total.
- `summary`: strategy return next to `benchmark_return`, drawdown, exposure and round
  trips. `sample.status` is `INSUFFICIENT_SAMPLE` until `--min-trades` is met.
- `split`: use only `in_sample` for configuration tuning; judge on `out_of_sample`.
- `cost_sensitivity`: the same signals at each slippage level.
- `caveats`: always read them. They include survivorship bias, daily-bar limits and
  the stop-first assumption.

## Strategy limits

- **Share-price cap.** `config/screening.yaml` caps `max_share_price` at 25.0. With the
  default thresholds, symbols trading above that are screened out on every session and
  produce no signals. Pass `--screening-config` to use different thresholds; the
  thresholds used are written to `screening_profile`.
- **`mean_reversion`.** It requires a point-in-time catalyst/SEC-risk snapshot that no
  backtest input supplies, so every evaluation is `INCOMPLETE`
  (`missing_catalyst_risk_data`) and no signal is produced. This is deliberate: treating
  missing risk data as "no risk" is what the scanner forbids.
- **Relative strength.** The symbol's 20-session return minus the benchmark's, over the
  same sessions, using only bars known at that session's close.

## Rules for using results

- A single run is not evidence for changing `activation_stage` in
  `config/strategies.yaml`. Moving to `STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST` is an
  operator decision based on out-of-sample results with an adequate sample.
- Never tune thresholds on the out-of-sample window.
- Fixture data is synthetic. Do not quote its numbers as performance.

## Snapshot files

A snapshot holds the symbols, date range (including warm-up history before `--start`),
adjustment, bars and a content hash. Reading it recomputes the hash and refuses a file
that was altered. The file is never overwritten; to refetch, move it aside. A snapshot
whose provider, adjustment, symbols or date coverage does not match the request is
refused rather than silently reused.
