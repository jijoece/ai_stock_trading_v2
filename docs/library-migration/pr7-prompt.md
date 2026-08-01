# PR 7 prompt: backtest parity report

Implement library-migration PR 7: compare `backtesting/engine.py` against
`backtest_runtime/` over identical fixture input and classify every
difference.

Model: Sonnet
Effort: High

Before editing:

1. Confirm PR 6 (`migration/06-lumibot-backtest-adapter`) has been merged
   into main.
2. Fetch the latest remote state.
3. Switch to main and fast-forward from origin/main.
4. Create and switch to: `migration/07-backtest-parity-report`
5. Confirm the working tree is clean.

Read these sources completely before planning:

- `docs/library-migration/STATUS.md` — "Completed work (PR 6)"
- `docs/adr/0009-lumibot-backtest-distribution-boundary.md` — Decision 5
  ("How PR 7 receives parity results")
- `docs/library-migration/MASTER_PLAN.md` — PR 7 row
- `backtest_runtime/src/backtest_runtime/contract.py` — the input/output
  schemas (`SCHEMA_VERSION_INPUT`/`SCHEMA_VERSION_RESULT`,
  `REFERENCE_STRATEGY_ID`)
- `backtest_runtime/src/backtest_runtime/strategy.py` — the reference
  strategy's exact behavior (read this section below first; it is
  narrower than it may sound)
- `src/trading_research/backtesting/engine.py`
- `src/trading_research/backtesting/models.py`
- `backtest_runtime/tests/support/fixtures.py` — the existing deterministic
  bar fixture already used by `backtest_runtime`'s own test suite

## The scope trap this prompt exists to prevent

PR 6's `backtest_runtime.strategy.ReferenceStrategy` implements exactly one
behavior: buy a caller-specified whole-share `quantity` of one `symbol` on
the first bar with a resolvable price, then hold to the end of the fixture.
No sells, no stop/target exits, no multi-symbol, no re-entry, no order
types beyond a market buy. This was a deliberate PR 6 scope boundary (see
`docs/milestones/rebuild/7.md` scope item 3: "no execution authority ...
no scheduler"), not an oversight, and not something PR 7 should treat as a
parity gap to fix silently.

Before writing any comparison code, PR 7 must decide, and record the
decision in `docs/library-migration/DECISIONS.md`:

- **Option A** — construct the `backtesting/engine.py` fixture run as the
  narrowest possible equivalent: a single `EntrySignal` for one symbol, no
  `initial_stop_reference`/`target_reference`/`maximum_holding_sessions`,
  sized to the same whole-share quantity, held for the fixture's full
  duration. This is the comparison PR 7's bounded prompt anticipates.
- **Option B** — extend `backtest_runtime`'s reference strategy first, in a
  small preparatory change on this same branch, before running any
  comparison. Only do this if Option A cannot express a case the parity
  report actually needs; do not broaden `backtest_runtime`'s scope
  speculatively. Any extension must keep `benchmark_asset=None`,
  `analyze_backtest=False`, and the existing credential-safety guarantees
  unchanged, and must not reintroduce anything ADR 0009 forbids (data
  fetcher, broker integration, scheduler).

Do not silently do both; pick one, record why, and proceed.

## Required scope

1. **Build one canonical fixture set**, checked into the repository (not
   generated at test time only), covering at minimum: a single-symbol
   buy-and-hold case matching `backtest_runtime`'s reference strategy
   exactly, and at least one case exercising a boundary condition already
   covered by `backtest_runtime`'s own tests (e.g. the perturbed-bar case
   in `backtest_runtime/tests/support/fixtures.py`) so both engines can be
   run over bar-for-bar identical input.

2. **Run both engines over the same fixture**, each in its own environment
   — `backtesting/engine.py` in the main project's environment,
   `backtest_runtime/` in its own isolated environment (`pip install -e
   backtest_runtime/`) — per ADR 0009 Decision 5's diagram. Do not install
   both environments together; the comparator reads two result documents,
   it does not import both engines into one process.

3. **Compare, field by field**: orders, fills, entry/exit timestamps,
   prices, quantities, cash, positions, fees, realized/unrealized P&L,
   equity, drawdown, and final value. Use `backtest_runtime`'s result
   schema (`schema_version = "backtest_runtime.result.v1"`) and
   `backtesting/models.py`'s `BacktestResult`/`BacktestFill`/
   `BacktestDailyState` as the two input shapes; write an explicit mapping
   between the two rather than assuming field names or units already
   align (`backtest_runtime` uses `float`; `backtesting/models.py` uses
   `Decimal` — the comparator must state its own tolerance and rounding
   rule, not inherit one from either side).

4. **Classify every difference found** into exactly one of: old-engine
   defect, adapter defect (in `backtest_runtime`), intentional library
   semantic difference (LumiBot vs. the custom engine), or unsupported
   requirement (a case neither side can express). Do not leave a
   difference unclassified, and do not silently patch either engine to
   make a difference disappear as part of this PR — PR 7 is a report, not
   a fix; a fix is a separate, later change with its own review.

5. **Produce a written parity report** (a new file under
   `docs/library-migration/pr7/`, following the `pre-step-06/`/`pr2/`
   directory convention already established) containing the fixture set
   used, both raw result documents (or a pointer to where they are
   checked in), the field-by-field comparison, and the classification
   above for every difference.

6. **Do not remove or modify** `backtesting/engine.py`'s behavior, any
   `paper_books` accounting code, or `backtest_runtime`'s existing
   contract/tests except as required by the Option A/B decision above.
   PR 8 is the removal-decision gate, not this PR.

## Validation

- Run `backtest_runtime`'s existing test suite unmodified (or with only the
  Option B extension, if taken) and confirm it still passes in its
  isolated environment.
- Run the main project's test suite and confirm `backtesting/engine.py`'s
  existing tests are unaffected.
- Confirm the parity report's raw comparison data is reproducible from the
  checked-in fixture alone (re-running both engines over it produces the
  same two result documents `backtest_runtime`'s own determinism tests
  already establish for its side).

## Report

- the Option A/B decision and why;
- the fixture set built and where it lives;
- the field-by-field comparison and every difference's classification;
- both engines' test results;
- commit SHA and PR URL, or whether the PR still needs to be opened.
