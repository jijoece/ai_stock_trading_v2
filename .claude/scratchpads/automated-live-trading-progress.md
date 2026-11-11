# Automated live trading: progress and handoff

Last updated 2026-11-11. Read this first, then `docs/automated-live-trading-implementation-plan.md` (the plan),
`docs/adr/0010-automated-live-trading.md` (the ADR, **Proposed, not accepted**) and
`docs/alpaca-capability/README.md` (Phase 1 evidence). The repository stays **paper only**.

## Where the work is

- Branch `feat/automated-live-trading-phases-0-3-spike`, one commit on top of `feat/historical-backtest-step1`.
- PR #39 (https://github.com/jijoece/ai_stock_trading_v2/pull/39), base `feat/historical-backtest-step1`
  (28 commits ahead of `main`; retarget to `main` only after that branch merges).
- Nothing in this work is reviewed by an independent reviewer yet.

## Phase state

| Phase | State | Notes |
|---|---|---|
| 0 Acceptance | Docs done, **owner decision pending** | ADR not accepted; O1-O16 and A1-A9 unanswered. Matrix: `docs/automated-live-trading-requirements-matrix.md` (also lists the exact doc edits per later phase). |
| 1 Broker spike | **Mostly done, gate item open** | `read-only`, `rest`, `fill` run on Alpaca paper. Overnight GTC stop survival and several items below NOT shown. |
| 2 Strategy contract | Built for local paper path | `strategies/controls.py`, `strategies/config.py`, `config/strategies.yaml: strategy_controls`. Not done: production screening, trailing rule (A7), `candidates`/`shadow` using per-strategy mode. |
| 3 Allocation and risk | Built for local paper path | `paper_books/allocation.py`, `strategy_exposure.py`, `risk.evaluate_paper_risk(allocation=...)`, `strategy_bridge.py` gating/ordering. Limits in `config/paper_books.yaml: risk.allocation` (operator starting values, not the ADR live limits, O15). |
| 4-9 | **Not started** | Depend on Phase 1 gate, ADR acceptance, independent review. |

Tests: `tests/unit/test_allocation.py` (24, incl. Hypothesis), `tests/unit/test_strategy_controls.py` (39).
`nox -s ci` was green after Phases 2-3 (before the spike was added; the spike is outside pytest/pyright).

## Phase 1 findings (details and fixture names in `docs/alpaca-capability/README.md`)

- A2 confirmed (polling enough, streams untested). A3 confirmed (all legs inherit the parent's time in force; SDK
  cannot set a leg's TIF). A4 confirmed (resting stop blocks a competing sell; only cancel-confirm-sell works).
  A9 **revised** (shorting enabled; a stale sell can open a short).
- A replace creates a NEW order id (`replaces` / `replaced_by`); the old id cannot be cancelled or replaced.
- **Bracket stop leg reads `held` after the entry fills while take-profit is `new`. Not established that it
  protects the position. Blocks Phase 5 stop verification.** OTO stop becomes `new` at the fill.
- Cancelling the bracket take-profit leg also cancelled the stop (linked). Resting legs block a flatten.
- Held legs are not listed as their own open orders; read legs through the parent (nested).

## Next steps, in order

1. **Ask the operator** (do not assume): (a) run the overnight check, (b) run `/code-review` on Phases 2-3 (the plan
   requires an independent review before the next phase), (c) the owner answers O1-O16 / accepts the ADR.
2. Overnight check (only if asked): submit a GTC **OTO** one-share SPY order that fills late in the session, leave it
   open past the close, read the stop leg's state and order id the next session (cancel it and flatten afterwards).
   Also try to resolve the bracket `held`-stop question (does it trigger? paper cannot easily prove a gap).
3. Fix the spike defects only if the spike is re-run: stop-loosen must follow `replaced_by`; the flatten must cancel
   legs first. The script is throwaway (`spikes/alpaca_phase1/capability_spike.py`).
4. After review and ADR acceptance: Phase 4 (new isolated execution runtime, autonomous entry point, fake broker with
   legs and partial fills). Apply the doc edits in the matrix, Section 4, in the phase named there.
5. Re-run `nox -s ci` and `nox -s safety_typecheck` after any change to `src/`. After Markdown link changes run
   `scripts/check_links.sh` (one unrelated external link, alphagaindaily.com, times out; ignore).

## Still not demonstrated (stays open on the Phase 1 exit list)

Partial parent fills, gap through a stop, GTC age expiry, splits/dividends, rate-limit headers, event stream,
overnight stop survival, stop loosening, PDT/settlement rules on a real account (O11).

## How to run the spike (credentialed, paper only)

```
.venv/bin/python spikes/alpaca_phase1/capability_spike.py plan                       # no network
.venv/bin/python spikes/alpaca_phase1/capability_spike.py read-only --env-file .env
.venv/bin/python spikes/alpaca_phase1/capability_spike.py rest --env-file .env       # unfillable resting orders
.venv/bin/python spikes/alpaca_phase1/capability_spike.py fill --env-file .env --i-understand-this-trades-paper
```

- The operator authorised using the repo `.env` for the `ALPACA_*` keys. The loader reads only allowlisted
  `ALPACA_*` keys and ignores the rest; it requires `ALPACA_IS_PAPER=true` and the paper URL. Never print or commit
  key values.
- `fill` trades one SPY share for real on paper; regular hours only (9:30-16:00 ET); it refuses if SPY is held or
  has open orders. Do not run `rest` or `fill` unless the operator asks. After any run verify zero open SPY orders
  and no SPY position (the account's AAPL share is pre-existing; leave it).
- Re-running overwrites fixtures with new numbering; delete stale `rest_*`/`fill_*` files that are not in the
  `*_run.json` step list.

## Project rules to keep following

- CLAUDE.md: Python computes, Claude explains; broker interaction read-only by default; no order submission,
  credentialed run, model call or scheduler activation unless the operator explicitly asks; not financial advice.
- Use Edit/Write for repo edits, never Python/shell rewrite scripts. Install packages with
  `.venv/bin/python -m pip`, not `.venv/bin/pip`. Run nox as `.venv/bin/python -m nox -s <session>`.
- Prefer Serena/Pyright symbol tools over broad grep or full-file reads.
- Commit and push only when the operator asks. Commit trailer:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Deviations from the plan (for the reviewer; also in the matrix, Section 3)

Signal validity is derived from `session_date` + `signal_validity_sessions` (no new `StrategySignal` field, so
content IDs are unchanged); trailing rule (A7) not added; production screening not computed; the paper default
allocation limits are not the live pilot limits; symbol ownership is derived from open lots and pending intents
(no reservation table until the live book, Phase 6).
