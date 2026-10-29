# Historical Strategy Backtesting — Build Plan

**Status:** Steps 1-4 implemented (2026-10-24); Step 5 is operator-initiated and
not run. See section 6 for where the implementation departs from this plan.
**Stage:** `config/strategies.yaml` is at `STAGE_2_HISTORICAL_BACKTEST`. This
plan delivers what that stage needs.
**Scope:** Research only. No broker path, no model calls, no scheduler changes.
Nothing here is financial advice.

## 1. Goal

Run the deterministic strategy scanners (`momentum_breakout`, then
`mean_reversion`) over real daily price history. The run must:

- reuse the existing engine and strategy adapter;
- let no strategy see a bar before it was knowable;
- produce the same run ID and metrics from the same inputs;
- run offline after a one-time data fetch.

## 2. Current state (as of `main` at `6f59e21`)

| Capability | Where | State |
|---|---|---|
| Daily-bar engine: next-session entry, limit fills, ATR stop/target, stop-first ambiguity, costs, loss/drawdown breakers, benchmark return, persistence | `src/trading_research/backtesting/engine.py` | Done. Kept as the authoritative engine by `library-migration/pr8/DECISION.md` |
| Strategy signal → engine entry, plus per-strategy metrics | `src/trading_research/strategies/backtest_adapter.py`, `strategy_metrics.py` | Done. Called only from tests |
| Scanners that take `HistoricalBar` tuples and reject future bars via `available_at` | `strategies/momentum_breakout.py`, `mean_reversion.py`, `timestamps.py` | Done |
| Alpaca daily bars, cached, with explicit `raw`/`split`/`all` adjustment | `evidence_providers/market_data_provider.py` (`AlpacaMarketDataClient`) | Done. Returns `PriceBar`, not `HistoricalBar` |
| Trading calendar with early closes | `evaluation/market_calendar.py` (`regular_session_close`, `next_trading_session`) | Done |
| Persistence tables | `backtest_runs`, `backtest_daily_states`, `backtest_orders`, `backtest_fills`, `backtest_metrics` | Done |

### Gaps

1. **No real-data provider.** The only `HistoricalDataProvider` is
   `FixtureHistoricalDataProvider`.
2. **No historical signal replay.** Nothing replays the scanners over past
   sessions to produce the signal set that `run_strategy_backtest` consumes.
3. **No point-in-time relative strength.** `momentum_breakout` returns
   `INCOMPLETE` (`missing_relative_strength`) without a
   `TechnicalFactorInput.relative_strength` that has a `freshness.as_of`.
   No production code computes one.
4. **Screening blocks historical runs.** `analysis/screener.py` fails closed.
   Its fundamental and microstructure gates (market cap, cash runway,
   dilution, going concern, shell/OTC, bid-ask spread, halts, reverse
   splits, operating history, earnings blackout) have no point-in-time
   history here, so every signal would be `NOT_ELIGIBLE`.
5. **Engine availability check is per run, not per session.** The engine
   checks bar availability only once, against the end of the range
   (PR 8 §4.3). The replay must enforce per-session knowability itself.
6. **No entry point.** There is no CLI command or runbook.

## 3. Decisions required before step 1

| # | Decision | Recommendation |
|---|---|---|
| D1 | Screening during a backtest | Add an explicit **backtest screening profile**. It evaluates only the gates computable from bars (`max_share_price`, `min_avg_daily_dollar_volume`, `max_realized_volatility`, `max_data_staleness_seconds`). Every other gate is recorded as `NOT_EVALUATED` in the run identity and the report, never silently passed. The production screener stays unchanged. |
| D2 | Price adjustment | `split` for signals and fills, recorded in the run identity and data hash. `raw` stays selectable. |
| D3 | Universe | An explicit, operator-supplied symbol list. Every report states the survivorship-bias caveat. |
| D4 | First strategies | `momentum_breakout`, then `mean_reversion`; both need bars only. `event_catalyst` waits for point-in-time events (step 6). **Correction:** `mean_reversion` also needs a point-in-time `CatalystRiskFlags` snapshot (see section 6). |
| D5 | Relative strength definition | A versioned, deterministic formula: the symbol's N-session return minus the benchmark's N-session return over the same sessions (default N = 20, benchmark `SPY`). `freshness.as_of` = the latest `available_at` of the bars used. The formula must match the scale that `minimum_relative_strength: 0.0` assumes. |

## 4. Steps

Build each step test-first and ship it as its own PR. Before opening each PR,
run `nox -s ci`, and run `nox -s safety_typecheck` if a safety-critical module
is touched. List the exact Nox sessions and their results in the PR summary.

### Step 1 — Historical bar provider and data snapshot

New module: `src/trading_research/backtesting/alpaca_history.py`.

- **Provider:** `AlpacaHistoricalDataProvider` implements
  `HistoricalDataProvider.bars(symbol, start, end, as_of)` by wrapping
  `AlpacaMarketDataClient.get_price_history`.
- **Bar mapping:** each `PriceBar` maps to a `HistoricalBar` with:
  - `available_at = regular_session_close(session_date)` (aware, early
    closes included);
  - `source_id = "alpaca:<adjustment>"`;
  - `point_in_time_safe = True` only when `available_at <= as_of`.
- **Snapshot file:** the first fetch writes a local JSON snapshot containing
  symbols, the date range, adjustment, the bars and a SHA-256 content hash.
  Later runs read only the snapshot, so a run is offline and reproducible.
  The content hash becomes part of the run identity.
- **Warm-up history:** fetching starts early enough before `start` to cover
  the longest lookback (200 sessions for `mean_reversion`'s
  `long_trend_sma_days`, plus the ATR period).
- **Credentials:** the provider reads `ALPACA_MARKET_DATA_API_KEY` and
  `ALPACA_MARKET_DATA_API_SECRET` only when it fetches. When they are
  missing, it fails closed with a sanitized error.

Tests use the existing `FixtureMarketDataClient`, with no network. They
cover:

- field mapping and `available_at` on a normal day and an early-close day;
- rejection of bars after `as_of`;
- a snapshot round trip with hash stability;
- rejection of a tampered snapshot;
- failure when credentials are missing;
- presence of the warm-up range.

### Step 2 — Point-in-time signal replay

New module: `src/trading_research/backtesting/signal_replay.py`.

For each trading session `s` in `[start, end]`:

1. Set `now = regular_session_close(s)`.
2. For each symbol, take only the bars with `available_at <= now`.
3. Compute relative strength (D5) from those bars and the benchmark's bars,
   both cut off at `now`.
4. Build the backtest screening result from the D1 profile.
5. Call `strategy.evaluate(symbol, StrategyMarketData(...),
   StrategyContext(now=now, ...))` and keep every resulting `StrategySignal`.
6. Pass only the `ELIGIBLE` signals to `run_strategy_backtest`. Count all the
   others by reason code for the report.

Look-ahead protection:

- **Explicit guard:** assert that no bar with `available_at > now` reaches a
  strategy (the PR 8 §4.3 gap is closed at the replay layer).
  `backtesting/engine.py` is not modified unless a failing test proves it
  must be.
- **Truncation test:** cutting the dataset after session N leaves every
  signal on or before N unchanged, for the same strategy, data and config.

Other tests:

- an injected future bar or future freshness value is rejected;
- results are identical on a rerun;
- one run refuses mixed strategy versions;
- reason-code counts add up to the total number of evaluations.

### Step 3 — CLI command and runbook

New command, `backtest-strategy`, in `src/trading_research/cli.py`:

```text
backtest-strategy --strategy momentum_breakout --symbols AAPL,MSFT,...
                  --start 2022-01-03 --end 2025-12-31 --initial-cash 100000
                  --data-source fixture|alpaca --snapshot PATH
                  [--benchmark SPY] [--slippage-bps 10] [--fee-per-order 0]
                  [--split-date 2024-12-31] [--db PATH]
```

- **Output:** a JSON report containing:
  - the run ID and the data snapshot hash;
  - the screening profile, with each gate marked `NOT_EVALUATED` or
    evaluated;
  - the strategy metrics and the engine report, including the benchmark
    return;
  - signal reason-code counts;
  - fixed caveats: survivorship bias, daily bars only, stop-first ambiguity,
    gates not evaluated, and "not financial advice".
- **Persistence:** results go to the existing `backtest_*` tables.
- **Safety:**
  - errors are sanitized through the existing CLI error path;
  - nothing imports a broker or runtime;
  - credentials are read only when `--data-source alpaca` runs without an
    existing snapshot.
- **Docs:** add `docs/runbooks/strategy-backtesting.md` and link it from
  `docs/INDEX.md`, then run `scripts/check_links.sh`.

Tests cover an end-to-end fixture run (`--data-source fixture`) and a
golden-file check of the JSON report shape. A further test confirms that no
`paper_books`, `runtime`, or broker module is imported, using the existing
AST import-boundary test pattern.

### Step 4 — Result integrity checks

- **Reproducibility:** identical inputs give an identical run ID, metrics
  and report bytes.
- **Benchmark:** the engine's existing `benchmark_return` is shown next to
  strategy return, drawdown and exposure.
- **Cost sensitivity:** a `--cost-grid 0,10,25` option reruns at several
  slippage levels and reports them side by side.
- **Out-of-sample split:** with `--split-date`, metrics are reported before
  and after the split separately. Config tuning should use only the earlier
  window.
- **Minimum sample:** the report flags results with fewer than a configured
  number of round trips (default 30) as `INSUFFICIENT_SAMPLE`.
- **Optional cross-check:** a `vector_research.run_parameter_sweep`
  comparison on the same closes, labelled exploratory.

### Step 5 — First real-data run (operator-initiated)

Requires an explicit operator request and market-data credentials.

1. Confirm the Alpaca data feed (IEX or SIP) and how much history the account
   can access.
2. Fetch the snapshot once, then rerun offline from it.
3. Review the report and its caveats with the operator. Do not change
   `activation_stage` based on a single run.

### Step 6 — Later work (separate plans)

- Point-in-time SEC fundamentals, to evaluate the full screening gate set.
- Point-in-time event history, for `event_catalyst`.
- A survivorship-free universe that includes delisted symbols.
- Moving to `STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST`. This is an operator
  decision, based on out-of-sample results.

## 5. Out of scope

- Any order, broker, LumiBot or Alpaca paper-account action.
- Model or LLM calls.
- Scheduler activation.
- Changes to production screening.
- Intraday bars.
- Replacing `backtesting/engine.py`.

## 6. Implementation notes (departures from the plan above)

- **`mean_reversion` cannot run on bars alone.** The scanner returns `INCOMPLETE`
  (`missing_catalyst_risk_data`) without a fresh `CatalystRiskFlags` snapshot, because
  treating missing SEC-risk data as "no flags" is forbidden. The replay supplies none, so
  `mean_reversion` currently produces no signals. Supplying one needs point-in-time
  filings data (Step 6) or an explicit operator decision to relax that rule.
- **Production share-price cap applies.** The backtest screening profile reuses the
  production thresholds, including `max_share_price: 25.0`, so higher-priced symbols
  never pass. `--screening-config` overrides the thresholds, and the report records them.
- **Engine change.** `run_backtest` and `run_strategy_backtest` gained an optional
  `run_context` argument that is hashed into the run identity and stored in the report.
  Without it, the identity is unchanged. This was needed because the engine's run ID
  covered only configuration, signals and code version, so two different data snapshots
  with identical signals would have shared one run ID and one persisted row.
- **Reason-code counts.** One evaluation carries several reason codes, so the counts per
  code cannot sum to the number of evaluations. `status_counts` is the additive total;
  `reason_code_counts` counts evaluations carrying each code.
- **Provider and snapshot.** `AlpacaHistoricalDataProvider` serves from a snapshot only;
  the live fetch is a separate function that wraps `AlpacaMarketDataClient`. Snapshots
  record their provider (`alpaca-data` or `fixture`) and a snapshot of one provider is
  refused for the other. Tests use a local stub client because
  `FixtureMarketDataClient.get_price_history` has no `adjustment` argument and emits bars
  on exchange holidays.
- **Not built.** The optional `vector_research.run_parameter_sweep` cross-check from
  Step 4.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Look-ahead through relative strength or freshness | Both are derived from bars cut off at `now`. Truncation and injected-future tests cover them. |
| Overfitting configuration to one window | `--split-date`, reporting out-of-sample results first, and the minimum-sample flag |
| Over-trusting results that skipped screening gates | `NOT_EVALUATED` gates appear in the run identity and the report header |
| Survivorship bias | A fixed caveat in every report. Step 6 adds a full universe. |
| Data drift between fetches | The snapshot content hash is part of the run identity, so different data gives a different run |
