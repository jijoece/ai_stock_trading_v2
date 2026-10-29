# Runbook: Strategy paper testing (activation stages 3–5)

Forward-tests `momentum_breakout` and `mean_reversion` on data they have not seen.
Stage 3 lists candidates, stage 4 tracks hypothetical outcomes, stage 5 runs the
local simulated paper book. No broker, no model call, no scheduler. Nothing here is
financial advice. Design and decisions: [`../paper-testing-plan.md`](../paper-testing-plan.md).

Stage 6 (supervised Alpaca paper) and stage 7 (multi-day soak) are not covered here;
use [`alpaca-paper-operations.md`](alpaca-paper-operations.md) and
[`paper-soak-campaign.md`](paper-soak-campaign.md).

## Gates

Every command refuses to run below its stage, and only you move the stage, by a
reviewed edit of `strategy_candidate_selection.activation_stage` in
`config/strategies.yaml` that cites the backtest evidence. The shipped value is
`STAGE_2_HISTORICAL_BACKTEST`, so nothing below runs until you change it.

| Command | Needs at least | Writes |
|---|---|---|
| `strategy-candidates` | `STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST` | `strategy_signals` |
| `strategy-shadow-update` | `STAGE_4_SHADOW_RECOMMENDATION_TRACKING` | `strategy_shadow_outcomes` |
| `strategy-shadow-report` | none (read-only) | nothing |
| `paper-book-run-strategy-cycle` | `STAGE_5_LOCAL_PAPER_BOOK` | paper-book tables, `paper_book_strategy_links` |

All tables written by these commands are append-only (database triggers reject
UPDATE and DELETE). Each command also refuses a session that has not closed yet.

Move the stage back one step to roll back (plan section 5). A stage change is not
needed to stop: pause with `shadow-pause`/`shadow-kill`, or leave the commands unrun.

## Stage 3: daily candidate list

```bash
python -m trading_research.cli strategy-candidates --as-of 2026-11-02 \
  --symbols SYM1,SYM2 --data-source alpaca --snapshot data/forward/2026-11-02.snapshot.json
```

Run after the close, once per session. It evaluates that one session through the
same replay function the backtest uses, stores every evaluation (all statuses) and
prints the shortlist. Re-running a session is a no-op. If the same session is run
against different data, it fails with `CANDIDATES_CONFLICT` instead of overwriting.

- Fetching a snapshot needs `ALPACA_MARKET_DATA_API_KEY` and
  `ALPACA_MARKET_DATA_API_SECRET`; with an existing snapshot no credential is read.
- Eligibility uses the **backtest screening profile** (daily-bar gates only). The
  production screener needs live filings and quotes that this command does not
  fetch, so `production_screening_status` is stored as `NOT_EVALUATED`. A signal
  passing here is not production-eligible.
- `mean_reversion` stays `INCOMPLETE` without a catalyst/SEC-risk snapshot.

## Stage 4: shadow tracking

```bash
python -m trading_research.cli strategy-shadow-update --as-of 2026-11-03 \
  --data-source alpaca --snapshot data/forward/2026-11-03.snapshot.json
python -m trading_research.cli strategy-shadow-report --strategy momentum_breakout \
  --bands data/forward/phase0-bands.json
```

`strategy-shadow-update` runs the backtest engine's own rules over the bars after
each unresolved ELIGIBLE signal (next-session limit fill, stop before target, holding
limit, costs: `--slippage-bps` defaults to 10). Outcomes are per share and
hypothetical. A signal gets one `PROGRESS` row per update while open and one
`TERMINAL` row when it closes, is never filled or is rejected.

`--bands` is a JSON file you write from the Phase 0 go/no-go note:

```json
{"momentum_breakout": {"expectancy": {"low": "0.002", "high": "0.02"},
                       "win_rate": {"low": "0.4", "high": "0.6"}}}
```

Metrics are `fill_rate`, `round_trips`, `expectancy`, `win_rate`, `maximum_drawdown`
and `average_holding_sessions`. Each is `WITHIN`, `BELOW`, `ABOVE`, `NO_BAND`, or
`INSUFFICIENT_SAMPLE` until at least 20 evaluated sessions and 15 eligible signals
exist. The report never changes a stage.

## Stage 5: local simulated paper book

Prerequisites, all in a config copy you pass with `--paper-books-config` (the shipped
`config/paper_books.yaml` stays disabled):

- `paper_books.enabled: true` and `paper_books.lifecycle.enabled: true`;
- `lifecycle.exits.enabled: true`, so signal exits are evaluated;
- `lifecycle.pending_orders.expire_after_market_days: 2` or more. A strategy intent
  is created after the close and filled by the next session's lifecycle, so the
  shipped value of 1 would cancel it first; the command refuses a shorter expiry;
- the BASELINE book not enabled for external submission.

```bash
python -m trading_research.cli paper-book-run-strategy-cycle --as-of 2026-11-03 \
  --data-source alpaca --snapshot data/forward/2026-11-03.snapshot.json \
  --paper-books-config /path/to/paper_books.stage5.yaml
```

Daily order: `strategy-candidates`, `strategy-shadow-update`, then this command, all
for the same closed session.

Per session the command (1) runs the existing lifecycle against snapshot prices: it
fills or expires earlier pending entries, exits open positions, snapshots,
reconciles and checks the daily-loss and drawdown breakers; then (2) sends that
session's stored ELIGIBLE signals through the unchanged risk and intent code.

Things to know:

- **Size.** A signal never sets its size. The proposal is 1% of the book's equity
  divided by the distance from the signal's limit to its own stop; `risk.py` then
  caps it. A risk rejection creates no intent and is stored as a risk decision.
- **Exits.** A position opened from a signal is exited by that signal's stop, target
  and holding period. The generic fixed-percent stop no longer tightens the stop, and
  the config-wide holding limit no longer applies. Without a signal target the
  generic profit target is used, where the backtest uses an ATR-based one.
- **Lifecycle before entries.** Entries are only evaluated when the lifecycle
  processed and reconciled the BASELINE book (`MATCHED`) with no failure reason. Otherwise
  the command creates no risk decision, claim, reservation, intent or link, reports
  `entries.blocked` with the reasons, and exits non-zero; fix the cause and rerun.
- **First session.** The shipped risk section requires a daily-loss baseline from a
  previous snapshot. Its absence is a lifecycle failure ("start-of-day equity baseline
  is unavailable"), so the first session creates no entries and exits non-zero. Run the
  command one session ahead of the first session whose signals you want.
- **Concurrent runs.** Each entry is registered under the database write lock, only if
  the book's cash, orders, fills and pause state are unchanged since its risk decision;
  otherwise it is re-decided against the committed state (and skipped with
  `SKIPPED_RISK_STATE_CHANGED` if the book keeps changing). The daily new-notional limit
  counts every BUY intent already committed for the day.
- **Fills differ from shadow.** The paper book fills on a later session at a
  simulated price near that session's close. The shadow tracker fills at the next
  session's limit. Compare the two before reading either as evidence.
- **Retries.** Running the same session twice creates no second intent or link
  (`SKIPPED_ALREADY_PROCESSED`).
- **Stops.** Breaker pauses follow the existing resume procedure in
  [`paper-book-operations.md`](paper-book-operations.md). The command also refuses to
  run while the shadow system is paused or killed.
- Only `--experiment-policy BASELINE_ONLY` exists. An AI-overlay arm is a separate
  decision.

## Evidence for moving up a stage

Stage 4 to 5: at least 20 evaluated sessions and 15 eligible signals per strategy,
and no metric `BELOW` its band. Stage 5 to 6: at least 20 market days with clean
reconciliation every day, no unexplained breaker pause, paper fills that agree with
the shadow outcomes for the same signals, and metrics within the bands. Record the
evidence in the config change that moves the stage.
