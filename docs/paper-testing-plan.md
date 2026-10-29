# Strategy Paper Testing — Build Plan

**Status:** Phases 1–3 implemented in the working tree (2026-10-29) and not yet reviewed or merged;
operator runbook: [`runbooks/strategy-paper-testing.md`](runbooks/strategy-paper-testing.md). Phase 0
(real-data backtest and go/no-go note) is operator-initiated and has not happened. Phases 4–5 are not
implemented. Differences from the plan are listed in "Implementation notes" below.
**Covers:** activation stages 3–7 in `config/strategies.yaml`
(`strategy_candidate_selection.activation_stage`), following the historical
backtest ([`historical-backtesting-plan.md`](historical-backtesting-plan.md)).
**Scope:** Paper only. There is no live-trading path, and this plan adds none.
Every external Alpaca paper order needs an explicit operator request and the
repository's existing confirmation gates. Nothing here is financial advice.

## 1. Goal

Test `momentum_breakout` and `mean_reversion` forward in time, on market data
they have never seen. The stages move from no orders at all, to the local
simulated paper book, to supervised Alpaca paper orders. At every stage we
check that forward behaviour matches what the backtest predicted. Each stage
change is an operator decision based on recorded evidence.

```text
Stage 3  daily read-only candidate list     no orders
Stage 4  shadow tracking                    hypothetical fills only
Stage 5  local paper book                   local simulated fills (BASELINE book)
Stage 6  supervised Alpaca paper            explicit, per-order operator approval
Stage 7  multi-day paper soak               bounded campaign, then activation review
```

## 2. What already exists

| Capability | Where | Relevance |
|---|---|---|
| Strategy scanners, signal contract, content-identity signal IDs | `strategies/momentum_breakout.py`, `mean_reversion.py`, `contracts.py` | Produce the signals |
| Session replay, relative strength, backtest screening profile, Alpaca bar snapshots | `backtesting/signal_replay.py`, `relative_strength.py`, `screening_profile.py`, `alpaca_history.py` | Built for the backtest; reused here so live and backtest signals come from one code path |
| Strategy → order-intent boundary (only `ELIGIBLE` signals; an AI overlay can only shrink size) | `strategies/execution_boundary.py` | Exists, but **nothing calls it outside `strategies/`** |
| Isolated BASELINE/ENHANCED paper books: sizing, risk, intents, simulated fills, lifecycle, reconciliation, comparison | `paper_books/` and [`runbooks/paper-book-operations.md`](runbooks/paper-book-operations.md) | Stage 5 execution substrate |
| Daily-loss and drawdown breakers with append-only pause | `paper_books/daily_risk.py`, `safety_pause.py` | Stage 5–7 kill switch |
| Manual soak, soak campaign, activation review, recurring local runs | [`runbooks/controlled-paper-soak.md`](runbooks/controlled-paper-soak.md), [`runbooks/paper-soak-campaign.md`](runbooks/paper-soak-campaign.md), [`runbooks/recurring-local-paper-trading.md`](runbooks/recurring-local-paper-trading.md) | Stage 7 |
| Isolated Alpaca paper runtime: account check, preview, approval-gated submit, reconcile, cancel, retry | [`runbooks/alpaca-paper-operations.md`](runbooks/alpaca-paper-operations.md) | Stage 6 |

### Gaps

1. **Signals are not stored.** No table holds `StrategySignal`s, so forward
   behaviour cannot be audited or compared with the backtest.
2. **No daily candidate command.** Nothing runs the scanners for "today" on
   fresh data.
3. **No forward tracking.** Nothing records what an eligible signal would
   have done after it was produced.
4. **No bridge into the paper books.** Paper order intents are keyed by
   `recommendation_id` (`paper_books/order_intent.py`) and come from the
   research pipeline. Strategy signals have no route in, and intents have no
   `strategy_id` or `strategy_signal_id`.
5. **Strategy exits are unconfirmed.** Whether the paper lifecycle
   (`paper_books/lifecycle.py`, `exit_policy.py`) can apply a signal's own
   stop, target and holding period, rather than its generic ATR policy, has
   **not been confirmed**. Phase 3 checks this first.

## 3. Decisions required

| # | Decision | Recommendation |
|---|---|---|
| P1 | Screening for live signals | The **full production screener** decides eligibility, since live evidence (SEC filings, quotes) exists. Also record the backtest screening-profile result next to it, so live and backtest eligibility can be compared. |
| P2 | How signals enter the paper books | Add a **strategy recommendation** record whose `recommendation_id` is derived from the signal's content ID. It carries the `StrategyOrderIntentContext`. The existing risk, intent, fill, lifecycle, reconciliation and comparison code then works unchanged, and nothing bypasses risk. |
| P3 | Stage-4 length and pass criteria | At least 20 market days **and** at least 15 eligible signals per strategy. Fill rate, win rate and holding period stay within the bands set in Phase 0. |
| P4 | Experiment arms | `BASELINE_ONLY` (strategy alone) through stage 7. An ENHANCED arm (strategy plus an AI overlay that can only shrink positions) needs model calls and is a separate decision. |
| P5 | Stage-6 scope | One strategy, a fixed symbol list, at most N orders a day, and owner policy B limits ($50/$150/10%, D16 ruling 4). Every submit is an explicit operator action. |
| P6 | Who moves stages | Only the operator, by a reviewed config change to `activation_stage` that cites the evidence. Rollback is always allowed and goes one stage back. |

## 4. Phases

Build each phase test-first and ship it as its own PR. Run `nox -s ci` before
each PR, plus `nox -s safety_typecheck` when a safety-critical module
(`paper_books/`, `runtime/`, `cli.py` external paths) changes. No phase runs
credentialed commands, broker operations or model calls unless the operator
explicitly asks.

### Phase 0 — Backtest gate (before any stage 3 work)

1. Commit and merge the backtest implementation now in the working tree
   (`backtesting/alpaca_history.py`, `signal_replay.py`, and the other new
   modules, plus `backtest-strategy` and its runbook).
2. The operator runs the first real-data backtest (backtest plan, step 5).
3. Record a **go/no-go note** per strategy. It holds the out-of-sample
   metrics after 10 bps of costs (round trips, expectancy, win rate, maximum
   drawdown, fill rate, average holding period) and the **expected bands**
   that stages 4–7 are checked against. The proposed band is the
   out-of-sample value ± one bootstrap standard error; the operator can set
   another.
4. Do not continue for a strategy with `INSUFFICIENT_SAMPLE` or negative
   out-of-sample expectancy after costs.

### Phase 1 — Stage 3: daily read-only candidate list

- **Storage:** a schema migration adds an append-only `strategy_signals`
  table. It holds every evaluation (any status) with its content ID,
  strategy version, configuration hash, `data_as_of`, reason codes, factor
  values, both screening results (P1) and the data snapshot hash. It is
  protected by an append-only trigger, like the other audit tables.
- **Command:** `strategy-candidates --as-of <session> --symbols ...
  --data-source alpaca|fixture` runs after the close.
  - It evaluates exactly one session through the **same** replay function the
    backtest uses.
  - It persists the signals and prints the shortlist via
    `strategies/selector.py`.
  - It is read-only and never imports a broker or runtime module.
- **Parity test:** for a fixed snapshot and date D, `strategy-candidates`
  gives exactly the signals the backtest replay produced for D. This is the
  main guarantee that paper testing measures the strategy that was
  backtested.
- **Config:** `activation_stage: STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST`.
  `enabled: true` becomes legal at stage 3 under the existing loader rule.
- **Tests:**
  - persisting the same signal twice is a no-op;
  - an update or delete is rejected by the trigger;
  - stale or future data gives `STALE` or `INCOMPLETE`, never `ELIGIBLE`;
  - the parity test above.

### Phase 2 — Stage 4: shadow tracking (no orders)

- **Command:** `strategy-shadow-update --as-of <session>`. For every
  `ELIGIBLE` signal not yet resolved, it applies the backtest engine's rules
  to bars that arrived **after** the signal:
  - next-session limit fill or no fill;
  - stop first, then target;
  - maximum holding period, costs included.
- **Storage:** results go to an append-only `strategy_shadow_outcomes`
  table, one terminal row per signal and per-day progress rows.
- **Report:** `strategy-shadow-report --strategy ...` puts the forward
  metrics beside the Phase 0 bands. Each metric gets a status of `WITHIN`,
  `BELOW`, `ABOVE` or `INSUFFICIENT_SAMPLE`.
- **Running it:** manual daily commands. Scheduling through shadow
  operations is optional and needs its own activation.
- **Exit criteria:** P3 is met, and no metric is `BELOW` its band.
- **Tests:**
  - a shadow outcome never uses a bar earlier than its signal, or one that
    was not yet available (planted-bar test);
  - outcomes match the engine on the same bars;
  - replaying a day is idempotent.

### Phase 3 — Stage 5: local simulated paper book

1. **Confirm exits first** (gap 5). Show with tests whether the lifecycle
   applies a signal's own stop, target and holding period. If it doesn't,
   add that as a narrow, separately reviewed change to `paper_books/` and run
   `safety_typecheck`.
2. **Bridge (P2).**
   - A strategy recommendation record is built only through
     `build_strategy_order_intent_context`.
   - The paper order intent links to `strategy_id` and `strategy_signal_id`
     (new link table or columns, via a migration).
   - Sizing and risk go through the unchanged `paper_books/risk.py` and
     `build_order_intent`. A signal never sets its own size.
3. **Command.** A strategy-sourced run uses the existing manual soak path
   (`paper-soak-run`) or a thin `paper-book-run-strategy-cycle`, with
   `--experiment-policy BASELINE_ONLY`. It needs `paper_books.enabled` and
   `paper_books.lifecycle.enabled`, which are both off by default.
4. **Daily routine.**
   - Run candidates, shadow update and paper cycle.
   - Then lifecycle, snapshot and `paper-book-reconcile`.
   - Breaker pauses follow the existing resume procedure.
5. **Exit criteria.**
   - At least 20 market days with clean reconciliation every day.
   - No unexplained breaker pause.
   - Simulated paper fills agree with the shadow outcomes for the same signals.
   - Metrics stay within the Phase 0 bands.
6. **Tests.**
   - A non-`ELIGIBLE` signal cannot create an intent.
   - The same signal on a retried run does not create a duplicate intent.
   - A risk rejection creates no intent.
   - Strategy identity survives through intent, fill and position.
   - Import-boundary tests show no external-broker import on this path.

### Phase 4 — Stage 6: supervised Alpaca paper

- **Tooling:** use the existing isolated runtime and runbook only. For each
  candidate intent:
  1. `external-paper-account-check`;
  2. `external-paper-preview`;
  3. the operator's explicit approval phrase;
  4. `external-paper-submit`;
  5. `external-paper-reconcile` after the close.

  No automation submits.
- **Scope (P5):** one strategy, a fixed symbol list, at most N orders a day,
  inside policy B limits. The external book's credential set serves exactly
  one book, as the config already enforces.
- **New code (small):** a read-only report that ties each Alpaca paper fill
  to its strategy signal and local paper intent, and shows the slippage
  against the shadow and local simulated prices.
- **Exit criteria:**
  - at least 10 supervised orders with every order reconciled;
  - no unresolved ambiguous outcome;
  - median slippage within the cost assumption used in Phase 0.

### Phase 5 — Stage 7: multi-day paper soak

- **Campaign:** run a bounded soak campaign with
  [`runbooks/paper-soak-campaign.md`](runbooks/paper-soak-campaign.md),
  using strategy-sourced cycles. Proposed length is 20 market days.
- **Review:** produce the activation review. Recurring local runs may follow
  **only** through the existing two-step activation
  (`paper-recurring-request-activation`, then `paper-recurring-activate`),
  with the full safety-gate order in
  [`runbooks/recurring-local-paper-trading.md`](runbooks/recurring-local-paper-trading.md).
  Recurring runs stay local simulated. External submission stays manual
  under stage 6 rules.
- **Exit:** the operator reviews the evidence for every stage. What happens
  next is out of scope for this plan.

## Implementation notes (Phases 1–3)

Where the code differs from, or settles, the text above:

- **Stage not moved (P6).** `config/strategies.yaml` still says `STAGE_2_HISTORICAL_BACKTEST`. Phase 1
  listed `activation_stage: STAGE_3…` as config work, but P6 says only the operator moves stages, so each
  command refuses below its own stage instead (stage 3, 4 and 5 respectively).
- **Production screening (P1) is stored but not computed.** `strategy_signals` has both screening columns;
  `production_screening_status` is `NOT_EVALUATED` until a caller supplies a production
  `ScreeningResult`. The live SEC/quote evidence that screener needs is not fetched by these commands, so
  eligibility today is the backtest profile's.
- **Gap 5 confirmed, then closed narrowly.** The lifecycle built stops and targets from generic ATR
  multiples, floored the stop at a fixed percentage and used one config-wide holding limit; a signal's own
  exits were never applied. `paper_books/exit_policy.py` gained `strategy_managed_exits` and
  `paper_books/lifecycle.py` reads the strategy link, so only strategy-linked positions change.
- **Pending entries needed a reservation.** `execution.submit_and_simulate` refuses to fill an existing
  pending intent that has no namespace claim and cash reservation, so the bridge registers all three
  (claim, intent, reservation) plus the link in one transaction. The existing "intent created, fill later"
  branch in `scheduled_integration.py` stores a bare intent and would hit the same refusal; it was not
  changed.
- **Link table, not new columns (P2).** `paper_book_strategy_links` carries `strategy_id`,
  `strategy_signal_id` and the exit levels; the intent, fill and open position resolve to it by join.
- **Prices.** The CLI lifecycle has no price source, so `forward_testing/price_provider.py` serves the
  paper book from the snapshot.
- **Known parity gaps.** The paper book fills near a later session's close, the shadow tracker at the next
  session's limit. A signal without a target gets the generic profit target in the paper book and an ATR
  target in the backtest. The backtest engine always trails its stop, the paper book only if configured.
- **Safety typecheck.** `nox -s safety_typecheck` covers a fixed file list in `pyright-safety.json`.
  Only `storage/schema_version.py` among the changed files is on it (clean). The `paper_books/` changes
  are not on that list; they are covered by the test suite only.
- **Pinned trigger count.** The three new append-only tables raise the production trigger-protected table
  count from 50 to 53; the PR 13 scratch output, `EVALUATION.md` and `test_pr13_evaluation_docs.py` were
  updated to match after re-running `scratch_trigger_orm_vs_core.py`.

## 5. Rollback and kill criteria (all stages)

Any one of these moves the strategy back one stage:

- a daily-loss or drawdown breaker pause without a clear cause;
- a reconciliation mismatch;
- data staleness or a failed parity test;
- a forward metric `BELOW` its band for 10 consecutive market days.

Investigate before you resume. The existing pause and kill commands
(`shadow-pause`, `shadow-kill`, `paper-recurring-deactivate`) are the
immediate stop.

## 6. Out of scope

- Live or real-money trading.
- Automated external submission.
- An ENHANCED (AI-overlay) arm, which needs a separate decision (P4).
- `event_catalyst`.
- Intraday execution.
- Changes to the production screener or `backtesting/engine.py`.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Live signals come from different code than backtested signals | One shared replay function, plus the Phase 1 parity test |
| Overreacting to small forward samples | Minimum counts (P3), `INSUFFICIENT_SAMPLE` status, bands set in advance |
| Strategy exits silently replaced by generic ATR exits | Phase 3 step 1 confirms exits before any paper fill |
| Bridge bypasses risk or sizing | Intents only via the unchanged `risk.py` and `build_order_intent`; tests cover risk rejection |
| Live screening stricter than the backtest profile, so fewer signals | Both screening results stored; the report shows how often they differ |
| Accidental external submission | Stage 6 is manual and approval-gated; import-boundary tests on stage 3–5 code |
