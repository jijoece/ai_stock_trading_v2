# Intraday Earnings-Gap Strategy — Build Plan

**Status:** Proposed (2026-11-14). Nothing in this plan is implemented.
**Stage:** Research only. The strategy is added at `mode: backtest` and stays
there until this plan's acceptance gate passes. The global
`activation_stage` in `config/strategies.yaml` does not change.
**Scope:** Historical backtesting, on 5-minute bars covering pre-market,
regular, and post-market sessions, of a long strategy on earnings-gap days.
The core strategy enters and exits within one trading day. One variant holds
overnight from the post-market into the next morning and is reported
separately. No broker path, no model calls, no scheduler changes. Nothing here
is financial advice.

## 1. Goal

Answer one question with evidence: **does a rule-based long trade on stocks
that gap up after an earnings beat make money after costs, out of sample, and
does it beat simple baselines?** Answer it separately for:

- regular-session entries and exits, using pre-market and post-market data as
  signals only;
- entries in the pre-market;
- entries in the post-market right after the report, held to the next morning.

The run must:

- use only information knowable at each 5-minute bar, including the report's
  release time;
- model the thinner liquidity and order-type limits of extended hours;
- reuse the existing calendar, configuration, persistence, and metrics
  conventions where they fit;
- produce the same run ID and metrics from the same inputs;
- run offline after a one-time data fetch.

If the answer is no for a variant, that variant is dropped. That is an
acceptable outcome.

## 2. Motivation and prior evidence

### 2.1 Regular session

An exploratory check on 2026-11-14 used Robinhood 5-minute bars for NMAX
(earnings-reaction days 2026-03-27, 05-15, 08-14, 11-13, plus the 04-22 news
spike and 53 ordinary days) and LFTO (2026-11-13). That check:

| Rule (0.1% slippage per side, flat by the close) | Gap/event days | Ordinary days |
|---|---|---|
| Buy the open, sell the close | +6.3% average (n = 6) | −0.5% average (n = 53) |
| 15-minute opening-range breakout, stop at range low, 2R target | 4 trades, 50% win, +8.5% total | 33 trades, 45% win, −9.3% total |
| VWAP pullback hold | 2 trades, 0% win | 17 trades, −5.9% total |

### 2.2 Pre-market and post-market (2026-11-12 report, 2026-11-13 reaction)

Both companies reported after the close on 2026-11-12. Times are ET.

| | NMAX (prior close $7.29) | LFTO (prior close $18.71) |
|---|---|---|
| Post-market 11-12, first trades after the report | 16:05 $7.93, 16:10 $8.10 | 16:05 $21.50 |
| Post-market 11-12, last price / volume | $8.26 (+13.3%) / 84K shares | $21.94 (+17.3%) / 262K shares |
| Pre-market 11-13, last price / high / volume | $9.24 (+26.8%) / $9.24 at 09:25 / 37K shares | $23.65 (+26.4%) / $24.29 at 09:25 / 145K shares |
| Pre-market VWAP | $8.84 | $23.27 |
| Regular open / close | $9.28 / $9.59 | $23.76 / $24.45 |
| Regular-session 5-minute volume | 2.55M shares | 5.30M shares |
| Post-market 11-13, last price / volume | $9.47 / 18K shares | $24.15 / 529K shares |

Observations that shape this plan:

- **Ordinary days lose money** for every rule tested. The strategy must only
  trade catalyst days.
- **Most of the price discovery happens outside regular hours.** NMAX was
  already +13% after hours and +27% by 09:25, so the regular open captured
  almost none of the move. Pre-market and post-market prices are the earliest
  signal of how the market reads the report.
- **Extended-hours liquidity is very thin.** NMAX traded about 37K shares in the
  whole pre-market, against 2.55M in the regular session. Any backtest that
  fills at extended-hours bar prices without a strict liquidity cap will
  overstate results.
- **The 16:00 post-market bar can include the closing print.** LFTO's 16:00 bar
  shows 165K shares at exactly the official close. That is not tradable
  post-market liquidity and must be excluded.
- **Data sources disagree on volume.** The daily bar reported 5.08M shares for
  NMAX on 11-13, but the regular-session 5-minute bars sum to 2.55M. Volume
  filters must use one consistent source.
- **Opening ranges on gap days are very wide** (NMAX 11-13: 17% in 15 minutes),
  so position size must come from the stop distance, and very wide ranges must
  be skipped.
- **The samples are far too small to trust.** Most regular-session gap-day
  profit came from one non-earnings spike that reversed −27% the next session.
- **The existing `event_catalyst` scanner** rejects gaps above
  `maximum_gap_percent: 8.0` and has no earnings-surprise input. This plan adds
  a separate strategy; it does not loosen `event_catalyst`.

## 3. Current state

| Capability | Where | State |
|---|---|---|
| Daily-bar engine (next-session entry, ATR stop/target, costs) | `src/trading_research/backtesting/engine.py` | Done. Daily only. Cannot model intraday entry or same-day exit |
| Daily bar model with `available_at` | `backtesting/models.py` (`HistoricalBar`) | Done. No intraday or session-tagged equivalent |
| Alpaca bar client | `evidence_providers/market_data_provider.py` (`AlpacaMarketDataClient`) | Done. Hard-codes `timeframe: 1Day`; no extended-hours fetch |
| Trading calendar with early closes | `evaluation/market_calendar.py` (`regular_session_close`, `next_trading_session`) | Done. Regular session only; no pre/post-market boundaries |
| Point-in-time market events | `strategies/events.py` (`MarketEvent`, type `earnings_result`) | Done. Sign only (`positive_or_negative`); no EPS estimate/actual, no am/pm timing |
| Per-strategy metrics | `strategies/strategy_metrics.py` (`compute_strategy_metrics`) | Done. Built for daily round trips |
| Backtest CLI | `cli.py` (`backtest-strategy`) | Done. Daily strategies only |

### Gaps

1. **No intraday bars.** There is no 5-minute bar model, provider, or cache.
2. **No extended-hours session model.** Nothing tags bars as pre-market,
   regular, or post-market, or knows extended-hours boundaries on early-close
   days.
3. **No earnings-surprise record.** Nothing stores EPS estimate, actual, report
   timing, or release time.
4. **No reaction alignment.** An `am` report reacts in that day's pre-market and
   regular session. A `pm` report reacts in that evening's post-market and the
   next day's pre-market and regular session. Nothing encodes this.
5. **No intraday simulator**, and no model of extended-hours order limits
   (limit orders only, no resting stop orders, thin fills).
6. **No intraday metrics** (adverse excursion, time in trade, per-session and
   per-variant breakdowns, capacity).
7. **No entry point or runbook.**

## 4. Decisions required before Step 1

| # | Decision | Recommendation |
|---|---|---|
| D1 | Intraday data source | Alpaca 5-minute bars, split-adjusted, covering 04:00–20:00 ET, fetched once by the operator and cached to disk. Robinhood MCP data is for ad-hoc research by Claude; it is not a Python data path. |
| D2 | Earnings-surprise source | An operator-supplied, versioned CSV snapshot: `symbol, fiscal_period, report_date, timing (am/pm), release_time (optional), eps_estimate, eps_actual, source, captured_at`. Reports state the caveat that the estimate is the last pre-report consensus as captured, not a point-in-time series. |
| D3 | Universe | Every symbol in the CSV that passes the liquidity screen. Reports state the survivorship-bias caveat. Target at least 200 qualifying event days across at least 50 symbols. |
| D4 | Bar interval | 5 minutes, all sessions. Opening-range variants of 5, 15, and 30 minutes. |
| D5 | Regular-session fill model | Enter at the signal bar's close plus slippage. A stop fills at the worse of the stop price and the next bar's open. If stop and target both touch in one bar, the stop fills first. Exit at the last regular bar's close (early-close aware) if nothing else triggers. |
| D6 | Costs | `slippage_bps` per side (default 10) plus a spread proxy: `max(spread_floor_bps, k × first bar's (high − low) / close)`. Extended hours use a separate, larger floor and multiplier. Recorded in the run identity. |
| D7 | Train/test split | Chronological. Parameters are chosen only on the earliest 60% of event dates; the last 40% is evaluated once, with frozen parameters. |
| D8 | New strategy vs. extending `event_catalyst` | New strategy `earnings_gap_intraday`. `event_catalyst` keeps its contract. |
| D9 | Session boundaries | Pre-market 04:00–09:30 ET, regular 09:30–16:00, post-market 16:00–20:00. On early-close days, the post-market ends at a configured time (default 17:00) that must be confirmed against the data vendor and broker before Step 1 is merged. The overnight session (20:00–04:00) is out of scope for v1. |
| D10 | Report release time | Use `release_time` when the CSV has it. Otherwise assume 16:00 for `pm` and 08:00 for `am`, and require the first usable signal bar to start **after** the assumed release, never before. |
| D11 | Extended-hours fill model | Limit orders only. A buy fills only if the bar trades at or below the limit and has volume. Fill size is capped at `extended_participation` (default 2%) of that bar's volume; any remainder is unfilled, not filled later. Bars with zero volume, and the 16:00 bar's closing-print volume, never fill. Stops cannot rest in extended hours: a stop is simulated as "if a bar closes below the stop, sell with a limit at that bar's low minus the extended spread proxy at the next bar," which may also fail to fill. |
| D12 | Overnight variant | Allowed in the backtest, labelled `overnight`, and reported apart from the same-day variants. It has its own acceptance gate, because overnight gap risk is a different risk from a same-day trade. |

## 5. Strategy specification (v1)

All thresholds live in `config/strategies.yaml` under `earnings_gap_intraday`,
loaded by `strategies/config.py`.

### 5.1 Extended-hours signals

Computed point-in-time from session-tagged bars. Each signal records the
`available_at` of the latest bar it used.

| Signal | Definition |
|---|---|
| `post_report_move` | For a `pm` report: last post-market price at `post_signal_time` (default 16:30) vs. the regular close |
| `post_hold` | Last post-market price at 20:00 vs. post-market VWAP (did the after-hours move hold?) |
| `premarket_gap` | Last pre-market price at 09:25 vs. the prior regular close |
| `premarket_rel_volume` | Pre-market volume to 09:25 vs. that symbol's average pre-market volume over the prior 20 sessions |
| `premarket_high`, `premarket_low`, `premarket_vwap` | Levels from 04:00–09:25 |
| `premarket_hold` | Last pre-market price vs. pre-market VWAP |
| `continuation` | `premarket_gap − post_report_move` (did the gap grow or shrink overnight?) |

### 5.2 Candidate filter

Every check below uses only data knowable before the variant's earliest entry
time.

- Earnings beat: `eps_actual > eps_estimate`, both non-null.
- Gap: `premarket_gap ≥ minimum_gap_percent` (default 5%). The regular open's
  gap is recorded too, for the same-day variants.
- Gap holding: `premarket_hold` is true (last pre-market price ≥ pre-market VWAP).
- Pre-market interest: `premarket_rel_volume ≥ minimum_premarket_rel_volume`
  (default 3×). Stocks without 20 sessions of pre-market history are excluded.
- Price: prior regular close ≥ `minimum_price` (default $3).
- Liquidity: 20-session average regular-session dollar volume ≥
  `minimum_avg_dollar_volume` (default $5M), from one consistent bar source.
- History: at least 20 prior sessions (excludes new listings).
- Optional market-trend filter, off in v1 but recorded: benchmark (`SPY`)
  above its 50-session average.

### 5.3 Entry variants

Each run picks one variant; the report compares them.

| Variant | Holding | Entry | Initial stop |
|---|---|---|---|
| `orb` | same day | After the first N regular minutes (N ∈ {5, 15, 30}), first 5-minute close above the opening-range high | Opening-range low |
| `pmh_break` | same day | After 09:35, first 5-minute close above `premarket_high` | The lower of `premarket_vwap` and the opening-range low |
| `vwap_pullback` | same day | After 30 regular minutes, with price already above the opening-range high, first bar whose low touches session VWAP and closes above it | `vwap × (1 − vwap_stop_percent)` |
| `premarket_entry` | same day | Between 08:00 and 09:25, limit buy at `premarket_vwap` once `premarket_hold` is true; fills per D11 | `premarket_low` |
| `post_report_overnight` | overnight | After `post_signal_time`, limit buy at post-market VWAP if `post_report_move ≥ minimum_gap_percent`; fills per D11 | The regular close before the report |

Session VWAP for the regular-session variants is anchored at 09:30. The
report also shows each regular-session variant with VWAP anchored at 04:00.

### 5.4 Guards

- No regular-session entry before `earliest_entry_time` (default 09:45 ET),
  except `pmh_break` (09:35).
- Skip if the risk per share (entry − stop) is more than `maximum_risk_percent`
  of entry (default 8%).
- At most one entry per symbol per event; at most `maximum_trades_per_day`
  across symbols (default 3); stop trading for the day after
  `maximum_losses_per_day` (default 2).
- No new regular-session entry after `latest_entry_time` (default 14:30 ET).
- Extended-hours entries require a minimum filled notional
  (`minimum_extended_fill_usd`, default $1,000); partial fills below it count as
  no trade.

### 5.5 Sizing

`shares = floor(risk_fraction × equity / (entry − stop))`, default
`risk_fraction` 0.5%, capped by `maximum_position_fraction` (default 10% of
equity) and by the participation cap: 1% of the signal bar's volume in the
regular session, `extended_participation` (D11) in extended hours.

### 5.6 Exits

- **Same-day variants:** sell half at +1R; trail the rest at session VWAP (exit
  on a 5-minute close below it). Be flat at `flat_time` (default 15:55 ET, or 5
  minutes before an early close). A configurable `post_market_exit` option
  instead carries the remainder into the post-market until 16:30 under the D11
  fill model. It is off by default and reported as its own row.
- **`premarket_entry`:** the stop is simulated per D11 until 09:30, then becomes
  a regular-session stop. Same partial, trail, and flat-time rules from 09:30.
- **`post_report_overnight`:** the exit is the first of: the stop (simulated per
  D11 in the post-market and pre-market, a regular stop after 09:30), +1R
  partial and VWAP trail from 09:30, or flat by 10:30 ET on the reaction
  session.

## 6. Build steps

Each step is test-first, lands as its own PR, and passes `nox -s ci`.
Safety-relevant steps also pass `nox -s safety_typecheck`.

### Step 1 — Session-tagged intraday bars and data provider

- Add `IntradayBar` to `backtesting/models.py`: `symbol`, `start`, `end`,
  OHLCV, `session` (`pre` / `regular` / `post`), `available_at` (= `end`),
  `source_id`, and an `is_closing_print` flag. Timezone-aware UTC; reject naive
  timestamps and bars outside 04:00–20:00 ET.
- Add extended-hours boundaries to `evaluation/market_calendar.py`
  (`premarket_open`, `postmarket_close`, early-close aware per D9) without
  changing the existing regular-session functions.
- Add `backtesting/intraday_history.py`: an `IntradayHistoryProvider`
  protocol, a fixture provider for tests, and an Alpaca provider that adds
  `timeframe` and extended-hours parameters to `AlpacaMarketDataClient`
  (defaults unchanged, so existing callers don't change). The cache key includes
  symbol, date, interval, adjustment, and session coverage.
- Data checks: tag the 16:00 closing-print bar; flag (don't silently fix)
  sessions where summed regular 5-minute volume differs from the daily bar
  volume by more than a configured tolerance.
- Tests: session tagging on normal and early-close days, timestamp validation,
  closing-print detection, missing and zero-volume bars, and cache determinism
  (same input → same data hash).

### Step 2 — Earnings-surprise records and reaction alignment

- Add `strategies/earnings_surprise.py`: a frozen `EarningsSurprise` record,
  a strict CSV loader (unknown timing, null EPS, and duplicate
  `(symbol, fiscal_period)` are errors, not skips), and
  `reaction_window(report_date, timing, release_time)`. The window returns the
  post-market (for `pm`), pre-market, and regular segments in which the report
  is knowable, using D10 for the release time.
- `MarketEvent` is not changed.
- Tests: `am` → same-day pre-market and regular; `pm` → same-evening
  post-market, then next-day pre-market and regular; Friday `pm` → Friday
  post-market, then Monday; holidays and early closes; a release time later
  than the default; and a fixture row for NMAX 2026-11-12 `pm` → post-market
  11-12, reaction 11-13.

### Step 3 — Extended-hours signals and candidate filter

- Add `strategies/extended_hours_signals.py`: pure functions for every signal in
  section 5.1, each returning its value and its `available_at`.
- Add `strategies/earnings_gap_intraday.py` with
  `select_candidates(surprises, bars, config, variant)`. It returns `ELIGIBLE` /
  `NOT_ELIGIBLE` with reason codes (`no_beat`, `gap_below_minimum`,
  `gap_not_holding`, `low_premarket_interest`, `insufficient_premarket_history`,
  `price_below_minimum`, `illiquid`, `insufficient_history`, `trend_filter`),
  using only data available before the variant's earliest entry.
- Tests: one test per reason code; look-ahead tests proving that a bar after
  the cut-off cannot change eligibility; a test that a post-market bar before
  the release time is never used.

### Step 4 — Intraday simulator

- Add `backtesting/intraday_engine.py`: walk bars across sessions in time
  order for each event window, maintain VWAP (09:30 and 04:00 anchors), the
  opening range, and the pre-market levels incrementally. Apply the variant,
  guards, sizing, regular fill model (D5), extended fill model (D11), costs
  (D6), and exits from section 5.
- Output a frozen `IntradayBacktestResult`: trades (entry/exit time, session,
  price, reason, R multiple, MAE, MFE, filled vs. requested shares), per-day
  equity, rejected candidates and unfilled orders with reasons, configuration
  hash, and data hash.
- Tests: hand-built bar fixtures for every exit path (stop, gap-through stop,
  +1R partial, VWAP trail, flat time, early close); extended-hours limit fills,
  partial fills, zero-volume bars, closing-print exclusion, and a simulated
  stop that fails to fill; the overnight variant across a weekend; same-bar
  stop-and-target ambiguity; the risk-percent skip; the daily loss stop;
  participation caps; and bit-for-bit determinism.

### Step 5 — Metrics, baselines, and report

- Add `strategies/intraday_metrics.py`: trades, win rate, average and median R,
  expectancy, profit factor, max drawdown, average time in trade, MAE
  distribution, and the result with the top 3 trades removed. Break down by
  variant, entry session, opening-range length, gap bucket (5–10%, 10–20%,
  >20%), and `continuation` sign.
- **Capacity:** for extended-hours variants, the median and 10th-percentile
  filled notional per trade, and the share of orders unfilled.
- **Baselines**, on the same candidate set: buy at the regular open and sell at
  the close; buy at 09:45 and sell at the close; buy at the 09:25 pre-market
  price and sell at the close; buy at the 16:30 post-market price and sell at
  the next regular open. **Control:** the same-day rules on ordinary sessions
  for the same symbols.
- A Markdown/JSON report under `reports/`, with the survivorship, estimate,
  release-time, and fill-model caveats printed at the top.

### Step 6 — CLI and runbook

- Add a `backtest-intraday-gap` CLI command beside `backtest-strategy`:
  `--earnings-csv`, `--start`, `--end`, `--variant`,
  `--split in_sample|out_of_sample`, `--cache-dir`. Fetching data is a
  separate, explicit `--fetch` step that needs Alpaca credentials and is run by
  the operator only.
- Persist runs with the existing backtest-run conventions, or as a JSON
  artifact if the schema would need a migration (decide in review).
- Add `docs/runbooks/intraday-gap-backtest.md` and a row in `docs/INDEX.md`.

### Step 7 — Operator-initiated evaluation (not automated)

1. The operator exports the earnings CSV and runs the `--fetch` step.
2. Tune parameters on the in-sample split only, using the fixed grid:
   variant {`orb`, `pmh_break`, `vwap_pullback`, `premarket_entry`,
   `post_report_overnight`} × opening range {5, 15, 30} minutes (where it
   applies) × `maximum_risk_percent` {5, 8, 12}.
3. Freeze the chosen parameters in a commit before running out of sample.
4. Run out of sample once and record the result in this document.

## 7. Acceptance gate (out of sample, after costs)

Each variant is judged on its own. A variant passes only if all of these hold:

- At least 100 out-of-sample trades.
- Expectancy > 0.10R per trade and profit factor ≥ 1.3.
- Still positive with the top 3 trades removed.
- Beats every baseline in Step 5 on the same candidate set.
- Maximum drawdown ≤ 10R.
- The ordinary-day control is not also profitable (otherwise the edge isn't
  from the catalyst).

Extended-hours variants (`premarket_entry`, `post_report_overnight`) must also:

- have a median filled notional of at least $2,000 per trade, with no more than
  30% of orders unfilled;
- stay positive when the extended spread proxy is doubled.

`post_report_overnight` is overnight risk, so it also needs its worst
single-trade loss reported in R, and passes only if it is no worse than −3R.

## 8. After the gate (separate plan, not in scope)

Passing the gate does not activate anything. Paper trading needs a separate
plan because the current paper path is built around daily signals with
`signal_validity_sessions`. That plan would cover:

- real-time 5-minute data in all sessions;
- intraday order intents through `strategies/execution_boundary.py`;
- extended-hours order rules at the broker (limit orders only, extended-hours
  flag, time in force), confirmed against current broker documentation;
- client-side stop handling outside regular hours, since resting stops may not
  be supported there;
- stage progression to `STAGE_5_LOCAL_PAPER_BOOK` and
  `STAGE_6_SUPERVISED_ALPACA_PAPER`;
- pattern-day-trader constraints for the target account.

Live trading stays out of scope per the repository's paper-only boundary.

## 9. Safety boundaries

- The new modules live under `strategies/` and `backtesting/` and must pass
  `tests/unit/test_strategy_safety_boundaries.py::test_strategies_package_has_no_llm_touching_import`.
- `earnings_gap_intraday` is added to `strategy_controls` with
  `mode: backtest`. The config loader must refuse `paper` for it until the
  section 8 plan exists (test this).
- No step adds a broker call, model call, or scheduler entry. Data fetching is
  operator-run and credentialed; Claude never runs it.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Too few events; result driven by a few outliers | Minimum event counts; top-3-removed check; gap-bucket breakdown |
| Optimistic fills on fast gap days | Gap-through stop rule; volatility-scaled spread proxy; participation cap |
| Extended-hours fills that wouldn't happen in reality | Limit-only fills, tight participation cap, zero-volume and closing-print exclusion, minimum fill size, capacity metrics, doubled-spread test |
| Stops that can't execute outside regular hours | Simulated stops that can fail to fill; worst-trade check on the overnight variant |
| Using a post-market price before the report was public | Release-time cut-off (D10) and a look-ahead test |
| Inconsistent volume across data sources | One source for volume filters; reconciliation flag in Step 1 |
| Estimate snapshot isn't truly point-in-time | Stated caveat; beat defined by sign only in v1, not surprise size |
| Survivorship bias in the symbol list | Stated caveat; prefer an event list captured before outcomes are known |
| Overfitting the parameter grid | Fixed small grid; chronological split; single out-of-sample run |
| Over-reading NMAX and LFTO | Motivation only; the gate is evaluated on the full universe |
