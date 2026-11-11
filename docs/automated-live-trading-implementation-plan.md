# Automated Multi-Strategy Live Trading — Remaining Work

**Status:** Proposed 2026-11-11. Only the local paper-path parts of Phases 2
and 3 are implemented (see "Progress" below); Phase 0's matrix and edit list
are written in
[`automated-live-trading-requirements-matrix.md`](automated-live-trading-requirements-matrix.md).
[ADR 0010](adr/0010-automated-live-trading.md) is not accepted, and the
repository remains paper only.
**Basis:** the repository at `feat/historical-backtest-step1` (`8c53e26`) on
2026-11-11. This plan lists only what is still to build or to do. Work the
repository already contains is listed once in Section 1 and then left out.
**Rules for every phase:** one reviewable PR (or a short series); `nox -s ci`
and `nox -s safety_typecheck` pass; no credentials or network in the test
suite; an independent code review before the next phase starts. Do not skip
ahead to live credentials or real-order writes. Credentialed paper runs,
scheduler activation and any order submission happen only when the operator
explicitly asks. Nothing here is financial advice.

## Progress (2026-11-11, awaiting independent review)

| Phase | State |
|---|---|
| 0 | Matrix and doc-edit list written. **Not done**: the owner has not answered O1–O16 or accepted the ADR. |
| 1 | **Not started.** Needs credentialed Alpaca paper calls, which the operator must ask for. |
| 2 | Built for the local paper path: per-strategy mode, stage, allowlist and budgets; signal expiry and chronology at the bridge. Production screening, the trailing rule and `candidates`/`shadow` use of the mode are **not** done. See the matrix, Section 3. |
| 3 | Built for the local paper path: symbol ownership, per-trade, per-strategy and book planned-risk limits, strategy capital budgets, stop validation, signal ordering, property tests. The stale-quote rule is the existing price-age limit. |
| 4–9 | **Not started.** They depend on Phase 1 and on the owner's acceptance of the ADR. |

## 1. Already in the repository (not repeated below)

| ADR 0010 need | What exists | Where |
|---|---|---|
| Versioned strategy signals with limit, stop, target, invalidation level, holding period and configuration hash | `StrategySignal`, `CandidateStrategy` | `strategies/contracts.py` |
| Three deterministic strategies, shared safety gates, point-in-time timestamps | `momentum_breakout`, `mean_reversion`, `event_catalyst` | `strategies/` |
| Strategy configuration with hashes and a seven-stage activation ladder | `config/strategies.yaml`, `strategies/config.py` | |
| Multi-strategy shortlist ranking | `strategies/selector.py` | |
| Rule that an AI overlay can only shrink or remove an order | `strategies/execution_boundary.py`, `research/overlay.py` | |
| Backtest engine, real-data snapshots, point-in-time replay, per-strategy metrics | `backtesting/`, `strategies/backtest_adapter.py`, `strategy_metrics.py` | |
| Append-only signal store, daily candidate list, shadow tracking and report with pre-set bands | `forward_testing/`, `strategy-candidates`, `strategy-shadow-update`, `strategy-shadow-report` | |
| Signal -> risk -> intent -> local simulated fill, with the signal's own stop, target and holding period | `paper_books/strategy_bridge.py`, `forward_testing/paper_cycle.py`, `exit_policy.py`, `lifecycle.py` | |
| Trailing-stop advancement and partial exits (computed locally) | `paper_books/lifecycle_state.py` | |
| Stop-distance position sizing that rejects a stop at or above entry | `risk/position_sizing.py` | |
| Per-book limits: order and daily notional, position weight, symbol concentration, open positions (pending entries counted) | `paper_books/risk.py`, `config/paper_books.yaml` | |
| Daily-loss and drawdown breakers, append-only pause and operator resume | `paper_books/daily_risk.py`, `safety_pause.py` | |
| FIFO lots, append-only cash ledger with settlement dates, cash and share reservations, corporate actions | `paper_books/positions.py`, `cash_ledger.py`, `corporate_actions.py` | |
| Single-order state machine with deterministic client order ID, fenced leases, append-only events, duplicate-order detection, `UNKNOWN_REQUIRES_RECONCILIATION`, evidence-gated retry, account fingerprint | `paper_books/external_broker.py` | |
| Isolated broker process, versioned protocol, deterministic fake gateway | `paper_runtime/` | |
| Book, account and position reconciliation | `paper_books/reconciliation.py`, `execution/account_reconciliation.py` | |
| Readiness review -> activation request -> activation event; run leases; schedule and catch-up logic; pause and kill | `paper_books/recurring_scheduler.py`, `shadow/` | |
| Alert model persisted before delivery | `shadow/alerts.py` (persistence and log sinks only) | |
| Read-only dashboard | `src/dashboard/` (overview, portfolio, decisions, research cycles, system health) | |
| Market calendar with early closes; Alpaca daily-bar client | `evaluation/market_calendar.py`, `evidence_providers/market_data_provider.py` | |

## 2. Work already planned elsewhere that this plan depends on

These are not new work, but none is finished and all come before Phase 7.

| Item | State | Source |
|---|---|---|
| Review and merge the current branch | Forward-testing phases 1–3 are implemented in the working tree, not merged | [`paper-testing-plan.md`](paper-testing-plan.md) |
| Real-data backtest run and go/no-go note | Operator-initiated; not run | [`historical-backtesting-plan.md`](historical-backtesting-plan.md) Step 5 |
| Move `activation_stage` to stages 3, 4 and 5 and operate each | Still `STAGE_2_HISTORICAL_BACKTEST` | [`paper-testing-plan.md`](paper-testing-plan.md) |
| Production screening on live signals | Stored as `NOT_EVALUATED`; not computed | [`paper-testing-plan.md`](paper-testing-plan.md) implementation notes |
| Known parity gaps between backtest, shadow and paper fills | Open | Same |

## 3. Remaining phases

Phase numbers match the roadmap ADR 0010 was written against.

### Phase 0 — Acceptance (no code)

The repository audit is Section 1. What remains:

- Owner answers O1–O16 in the ADR and accepts or rejects amendments A1–A9.
- Write the requirements-to-tests matrix: each ADR invariant and each
  scenario in Section 4 mapped to a test file that will hold it.
- List the exact edits to ADRs 0001, 0002, 0003 and 0007, `README.md`,
  `CLAUDE.md`, `AGENTS.md` and `docs/paper-testing-plan.md`, to be applied in
  the phase that makes each one true.
- **Done when:** the ADR is Accepted with every owner decision filled in. No
  behaviour changes.

### Phase 1 — Broker capability spike (paper only)

Nothing here exists yet: the current runtime knows only `LIMIT`/`DAY` orders.

- A throwaway prototype against the Alpaca **paper** endpoint using the
  official SDK. No changes to `paper_runtime` or the main process.
- Confirm and record, with captured request/response fixtures:
  - OTO (entry + stop) and bracket (entry + stop + target) submission; parent
    and leg IDs; leg states before and after the parent fills;
  - whether one time in force applies to all legs, and whether a `DAY` entry
    can carry a `GTC` stop (ADR A3);
  - stop-market support; behaviour at a gap; regular-session-only triggering;
  - partial fill of the parent: which shares the stop covers and when;
  - replacing a stop in place, and selling shares a resting stop holds
    (ADR A4);
  - cancelling an unfilled parent and what happens to its legs;
  - GTC age limit; behaviour across splits and dividends;
  - client order ID uniqueness and lookup; rate limits; error taxonomy;
  - account-level settings to refuse shorting and margin leverage (ADR A9);
  - day-trade and settlement rules for the intended account type and size,
    and whether that account type accepts attached orders;
  - polling cost and latency versus the order-event stream (ADR A2).
- Confirm the runtime placement decision O11.
- **Done when:** a capability matrix and fixtures are committed and every
  assumption in ADR Section 4A is marked confirmed or revised. **Project
  gate:** if a broker-held stop that persists overnight cannot be shown, stop
  here.

### Phase 2 — Strategy contract extensions

The signal contract, strategies, registry file, hashes and replay exist.
Remaining:

- Per-strategy settings in `config/strategies.yaml`: mode (`backtest`,
  `paper`, `shadow`, `live`), symbol allowlist, capital budget, risk budget.
  Today there is one global `activation_stage`; make stage and mode
  per strategy so strategies can be enabled one at a time.
- Signal validity window (expiry) on `StrategySignal`; reject expired and
  out-of-order signals at the bridge. Duplicate rejection already exists.
- Make `initial_stop_reference` mandatory for a live-eligible signal, and add
  an optional, versioned trailing rule to the signal (ADR A7).
- Compute production screening for live signals (Section 2 dependency).
- Not built: new `TradeSignal`/`StopPolicy`/`ExitPolicy` types, per-strategy
  paper books (attribution stays in `paper_book_strategy_links`), strategy
  `EXIT`/`REDUCE` signals (deferred by O13), `event_catalyst` live events.
- **Done when:** two strategies run side by side in backtest and local paper
  with separate budgets and modes, and an expired or stop-less signal cannot
  produce an intent.

### Phase 3 — Cross-strategy allocation and risk

Per-book limits, breakers, reservations and stop-distance sizing exist.
Remaining:

- Symbol ownership: one strategy per symbol per book, enforced as a
  reservation taken before the intent is built and released only after the
  position is closed and reconciled.
- Per-strategy capital and risk budgets checked alongside the book limits.
- Planned-open-risk cap across all positions and pending entries
  (`shares × (entry − stop)`), and the per-trade risk budget, as limits in
  their own right. Today the caps are notional and weight based.
- Stop validation for live: minimum and maximum distance, tick size,
  rejection of a stop inside the spread.
- Stale-data policy for the quote used at order time.
- One ordering rule for simultaneous signals competing for the same cash or
  symbol (deterministic, from `selector.py` ranking).
- Property-based tests (`hypothesis` is already a dependency) for overlapping
  signals, insufficient cash, bad stops, overexposure and the loss halt.
- **Done when:** no intent can be created without passing these checks, and
  the tests show two strategies cannot spend the same cash or hold the same
  symbol.

### Phase 4 — Execution runtime and autonomous order manager

The single-order state machine, idempotency, leases, events and retry rules
exist for the manual flow. Remaining:

- The new isolated execution runtime (ADR A2): Alpaca adapter behind an
  `ExecutionBroker` interface, mode pinned to `paper` or `live` per
  deployment, refusal to start on a mode/endpoint/credential mismatch,
  credentials from the OS credential store. Built and run in `paper` mode
  only in this phase.
- Protocol operations for attached orders: submit with legs, read an order
  with its legs, replace, cancel. `paper_runtime` and its protocol are not
  changed.
- An autonomous submission entry point (ADR A1) beside, not inside, the
  manual preview/submit flow. It reuses the lease, event, reservation,
  duplicate-detection and reconciliation code, and requires a current
  activation record.
- Order-group states added to the state machine: `PROTECTION_PENDING`,
  `PROTECTED`, `EXITING`, `CLOSED`.
- Cancellation of entries not filled by the end of their validity window
  (ADR A3).
- A fake broker that models legs, partial fills, delayed and duplicated
  events, timeouts and rejects. The existing deterministic gateway models
  single limit orders only.
- **Done when:** crash, restart and timeout tests on the new path cannot
  create a duplicate entry, and no order field comes from model output.

### Phase 5 — Broker-held stops and exits

Stop and target levels and trailing advancement are computed today, but only
simulated locally. Everything at the broker is new:

- OTO for stop-only entries and bracket for stop + target, as confirmed in
  Phase 1.
- Coverage check after every fill: filled quantity versus confirmed stop
  quantity. Uncovered quantity triggers the corrective exit and entry halt
  (ADR A5).
- Stop replacement that only tightens, verified at the broker; replacement
  before the GTC age limit.
- Holding-period and safety exits through the ADR A4 sequence, including
  re-placing the stop if the exit fails.
- Orphan-leg cleanup after a position is closed and reconciled.
- One share per entry stays enforced in code until partial-fill coverage is
  proven.
- **Done when:** tests show no entry is submitted without a stop, no exit
  can sell more than is held, the kill switch never cancels a stop, and every
  fault path halts new entries. Residual broker-side risks are written down.

### Phase 6 — Live book, reconciliation, alerts and operator controls

Books, lots, cash ledger, reconciliation, breakers, the alert model, the
dashboard and the activation pattern exist. Remaining:

- `config/live_execution.yaml`, shipped disabled, separate from
  `config/paper_books.yaml` (which keeps rejecting `allow_live_broker`).
- An isolated `LIVE_ALPACA` book, and an autonomous paper book for Phase 7.
- A reviewed migration that turns `real_orders` from reject-all into
  append-only; UPDATE and DELETE stay rejected.
- Reconciliation extended to protective legs, run at startup, at reconnect,
  at market open and on a fixed interval. An unexplained position, a missing
  or changed stop, or a cash difference halts new entries.
- Validation of the lot, cash and settlement code against real broker
  account data before it is trusted for the live book.
- A real alert sink that reaches the operator (the existing sinks only
  persist and log), with delivery failure itself alerted.
- Live kill switch (blocks new entries and risk increases, leaves stops),
  pause/resume, and a separate operator-only emergency-flatten command.
- Activation record for live (ADR A9), and expiry of activation on a
  strategy, config, risk-limit or credential change.
- Dashboard page per strategy: mode, positions, broker order IDs, stop
  levels, open risk, slippage, failures.
- Service supervision on the host chosen in O14.
- **Done when:** simulated crashes, outages, missed fills, manual broker
  trades and missing legs each end in reconciled state or an explicit halt.

### Phase 7 — Autonomous paper soak

The forward-testing code for stages 3–5 exists. Remaining:

- Complete the Section 2 dependencies (operator actions).
- Write the O12 go-live gates before the soak starts: minimum sessions and
  trades, net return after costs, drawdown, slippage against assumption,
  zero unresolved reconciliation differences, zero uncovered fills.
- Run the full path against the Alpaca paper account through the execution
  runtime, unattended, for the agreed period. For this path, this replaces
  the manual stage 6 in `paper-testing-plan.md`.
- A report comparing backtest, shadow, local paper and Alpaca paper results
  per strategy.
- A written readiness review.
- **Done when:** every gate is met with the numbers fixed in advance, and
  the owner approves the review. A good backtest alone does not pass.

### Phase 8 — Live pilot

All of this is new, and all of it is operator-initiated.

- Live credentials in a second runtime deployment pinned to `live`.
- Broker account configured to refuse shorting and leverage.
- Dedicated account, small symbol allowlist, one share per entry, the
  limits from O15.
- Activation by the owner. One strategy first; a second only after the first
  has run cleanly.
- Rehearse the kill switch, a restart with open positions, and recovery from
  a stop that fired while the service was down.
- Apply the ADR 0001/0003/0007 and README edits listed in Phase 0.
- **Done when:** real entries, stops, exits, restarts and reconciliation
  behave as designed with no unresolved difference, and the owner signs off.

### Phase 9 — Scale (each item its own ADR)

More than one share per entry (only after partial-fill coverage is proven),
strategy `EXIT`/`REDUCE` signals, shared-symbol allocation, intraday signals,
a second broker, a monthly performance and risk review.

## 4. Minimum acceptance scenarios (new execution path)

The manual external-paper flow has tests for timeouts and duplicates on
single limit orders. Every scenario below must be proven again on the new
path, with legs.

- Two strategies signal the same symbol on the same bar: one reservation,
  one entry.
- An entry without a stop: zero orders sent.
- A stop is invalid or rejected: no unprotected entry is placed.
- A one-share entry fills and the stop leg is verified at the broker.
- A multi-share entry partially fills: the uncovered share is detected, new
  entries halt, the corrective policy runs. The test does not assume the stop
  is active.
- An entry times out after the broker accepted it: reconciliation finds it
  and prevents a duplicate.
- An unfilled entry reaches the end of its validity window: it and its legs
  are cancelled.
- A stop fires while the process is down: restart imports the fill and opens
  no opposite position.
- An exit races a stop fill: exactly the held quantity is closed.
- An exit fails after its stop was cancelled: the stop is re-placed and a
  critical alert is raised.
- Pause or daily-loss halt: no new entries, stops remain.
- Market data, network or broker polling fails: new entries stop and the
  state is reported.
- An unexplained position or a manually changed stop at the broker: new
  entries stop pending reconciliation.
- Paper credentials cannot reach the live endpoint, live credentials cannot
  start a paper-mode runtime, and no activation is possible while the ADR is
  not accepted.

## 5. Definition of completion

Judged in two parts (ADR A6):

- **Engineering complete:** the execution path buys and sells automatically
  in live mode for at least one strategy, places and verifies a broker-held
  stop on every entry, enforces the central risk limits, recovers from the
  failures in Section 4, and produces reconciled audit records.
- **Goal complete:** at least two strategies have each passed the O12 gates
  and run concurrently in live mode under those controls.

A manual order relay, or a stop that exists only while the application is
running, satisfies neither.
