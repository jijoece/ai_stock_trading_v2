# PR 7 — Backtest parity report

**Date:** 2026-08-02 (revised the same day, review rounds 1 and 2) ·
**Branch:** `migration/07-backtest-parity-report` (PR #19) ·
**Depends on:** PR 6 (merged, `bbd7a1f`)

Compares `src/trading_research/backtesting/engine.py` (the "legacy engine")
against `backtest_runtime/` (the LumiBot 4.5.78 adapter PR 6 shipped) over one
checked-in fixture set, and classifies every difference.

This is a **report, not a fix.** The legacy engine's behavior is unchanged — no
difference below was made to disappear by patching it, and `src/` is
byte-unchanged on this branch. PR 8 is the gate that decides what, if anything,
is done about the findings.

---

## 1. Decision: bounded Option B (revised)

Recorded in full in `docs/library-migration/DECISIONS.md` **D6**.

**The first pass took Option A and was wrong about what it had.** It claimed
`case_a_buy_and_hold` matched the reference strategy exactly. It did not.

**Which session a fill belongs to is now read from LumiBot's own books.** Both
earlier passes reasoned from indirect evidence — the callback that reported the
fill, and the bar whose open matched its price — and those two disagree. LumiBot's
broker stamps every order event with `data_source._datetime` as it processes it,
in its trade-event log (`lumibot/brokers/broker.py`), and that is authoritative:

| clock | case A | case F |
|---|---|---|
| broker trade-event log (**authoritative**) | 2024-01-03 @ 100.5 | 2024-01-04 @ 101.0 |
| `on_filled_order` callback | 2024-01-04 | 2024-01-05 |
| first iteration with changed cash | 2024-01-04 | 2024-01-05 |

`strategy_executor.py::_process_pandas_daily_data` runs a session as
`_update_datetime(session)` → `_on_trading_iteration()` →
`process_pending_orders()`, so the order is submitted **and** booked within one
session and the strategy is told on the next. The lag is observation, not
execution. All three clocks are recorded for three fixtures in
`results/probe_output.txt`.

**Option A is therefore impossible, and matching opens do not rescue it.** With
the booking session established, the two floors are one session apart and both
structural: LumiBot cannot submit before the **second** bar (the first is never
a trading iteration) and books in the submission session; the legacy engine
cannot enter before the **third** (eligibility is next-session-only, and ATR
needs `atr_period + 1` prior bars, which even at `atr_period = 1` puts
`generated_after_session` no earlier than bar 2). A fixture with deliberately
matching consecutive opens would equalise the two fill *prices* while leaving the
two *booking sessions* one apart — and the exact case must agree on the
authoritative fill date, not only on the price.

**The bounded extension.** Reference strategy **v2** adds exactly one control,
`strategy.entry_after_session` (`null` = v1 behavior; a date defers the same
single buy to the first iteration strictly after it). Versioned, not defaulted:
`backtest_runtime.input.v2` / `backtest_runtime.reference_strategy.v2`, with a
v1 document now explicitly rejected. It adds **no** sell, stop, target, second
order, order type, scheduler, fetcher, or broker interaction;
`benchmark_asset=None` and `analyze_backtest=False` stay hardcoded; every
ADR 0009 credential, network and isolation guarantee is untouched and still
asserted by that distribution's own blocking suite.

**And one reporting fix.** v1 published both the fill date and the daily state
from the lagging clocks above. v2 takes the fill's session from the broker's
event log and re-applies each session's booked fills to that session's state,
so a state row means end-of-session on both sides. The result schema is bumped
to `backtest_runtime.result.v2` because the field *names* are unchanged while
their meaning is not. Two invariants are checked as hard errors, not warnings:
each session's observed balances must equal the reconstruction as of the
previous reported session, and `observed cash + observed quantity × mark price`
must equal the `portfolio_value` LumiBot reported. No trading behavior changed —
only which clock the adapter believes.

---

## 2. Fixture set

Checked in under `fixtures/`, built by `build_fixtures.py` (pure standard
library; imports neither engine). Each `*.input.json` is a
`backtest_runtime.input.v2` document and is the **single source of bars for
both engines**.

| Case | Bars | `entry_after_session` | Why it is in the set |
|---|---|---|---|
| `case_f_exact_entry_parity` | 5 | **2024-01-03** | the one genuinely identical buy-and-hold case (§5) |
| `case_a_buy_and_hold` | 5 | null | `backtest_runtime`'s own `BARS`, verbatim — measures the *default* entry-timing offset |
| `case_b_perturbed_last_close` | 5 | null | `…::perturbed_input_document()`, verbatim — boundary case from its `test_determinism.py` |
| `case_c_falling_equity` | 5 | null | `…::FALLING_BARS`, verbatim — boundary case from its `test_drawdown.py` |
| `case_e_gapped_opens` | 5 | null | every array above is gap-free (`open[i] == close[i-1]`), which makes two different fill-price models produce the same number; this one gaps the opens |
| `case_d_long_hold_default_atr` | 30 | null | long enough for the legacy engine's **default** `atr_period=14` |

All bars are `SPKE`, quantity 10, budget 100 000.

**Identical input is proved, not assumed — and so is *current* input.** Each
side computes a SHA-256 over the canonical bar payload independently —
`backtest_runtime.contract.bars_digest` on one side, a separate
re-implementation in `run_legacy_engine.py` on the other (the main environment
must never import `backtest_runtime`). The comparator computes a **third**,
straight from the checked-in fixture, and requires all three to agree; it exits
non-zero otherwise. The third one matters because two stale result documents
agree with each other perfectly while describing bars that no longer exist, so
mutual agreement proves nothing about currency. A regression test edits one
fixture's *volume* — a field that changes no number either engine computes — and
asserts the previously valid results are rejected.

**No look-ahead in the legacy signals.** The first pass derived each signal's
`limit_price` from the *entry* session's high — a bar that had not happened when
the signal was generated. It is now a fixed band (`×1.10`) above the last close
visible at signal time, which is wide enough never to bind, so the fill still
lands on the entry session's open. `build_fixtures.assert_point_in_time_safe`
runs on every case at build time and asserts that the limit equals that band,
that no future OHLC value could have produced it even by coincidence, and that
it neither binds against nor rejects the entry.
`tests/unit/test_pr7_parity_report.py` re-checks the same property against the
committed fixtures.

---

## 3. How the two engines were run

Per ADR 0009 Decision 5, two separate environments; nothing imports both.

```text
fixtures/<case>.input.json
        |
        +--> backtesting/engine.py      (main .venv)          --> <case>.legacy_engine.json
        |
        +--> backtest_runtime/          (own venv, lumibot)    --> <case>.backtest_runtime.json
                                |
                     compare_parity.py reads the two documents
```

```bash
python3.11 -m venv /tmp/bt-venv
/tmp/bt-venv/bin/python -m pip install -e backtest_runtime/[dev]
docs/library-migration/pr7/run_parity.sh /tmp/bt-venv/bin/python .venv/bin/python
```

`run_parity.sh` runs each `backtest_runtime` case **twice** and fails if the two
result documents are not byte-identical. All six were byte-identical, and a
second full run of the script reproduces every file under `results/` with a
clean `git diff`.

---

## 4. Mapping between the two result shapes

The two documents are **not** forced into one schema. Each side is serialized
faithfully — `backtest_runtime.result.v2` as the CLI emits it,
`pr7.legacy_engine.result.v1` as a direct serialization of `BacktestResult` /
`BacktestFill` / `BacktestDailyState` with `Decimal` preserved as exact decimal
strings — and the comparator holds the mapping (machine-readable in
`results/comparison.json` under `field_mapping`).

| Dimension | `backtest_runtime` | legacy engine | Note |
|---|---|---|---|
| bar set | `historical_bar_dataset_checksum` | recomputed independently | both must equal a third checksum the comparator recomputes from the fixture (§2) |
| orders | `orders[]` | *(none)* → `derived.orders_from_fills[]` | `BacktestResult` has no order records — D11 |
| fills | `fills[]` | `fills[]` | aligned by index in booking order on both sides |
| fill quantity / price / fees | `.quantity` / `.fill_price` / `.fees` | same names | `float` vs `Decimal` |
| fill slippage | *(absent)* | `fills[].slippage` | D7 |
| fill timestamp | `fills[].market_date` | `fills[].market_date` | both sides' own booking session: the runtime's from LumiBot's broker event log, the legacy engine's assigned as it creates the fill |
| exit reason | *(absent)* | `fills[].exit_reason` | D8 |
| positions | `positions[]` | *(none)* → `derived.end_positions_from_fills[]` | D11 |
| cash / equity / P&L / drawdown | `daily_states[].*` | `daily_states[].*` | aligned by `market_date` |
| final cash / equity / value | `final_cash` / `final_equity` / `final_value` | last daily state's `cash` / `equity` / `equity` | the legacy result has no distinct "final value" |
| max drawdown | `max_drawdown_fraction` | `metrics.maximum_drawdown` | both are the minimum daily `drawdown_fraction` |

**Orders are paired by economic role, never by list position or sorted id.**
The first pass reconstructed legacy orders sorted lexicographically by
`order_id`, which put an exit (`bt-order-SPKE-…-STOP_GAP`) ahead of the entry
that created it (`bt-order-pr7-entry`) and aligned the legacy **SELL** against
the runtime **BUY**. Reconstruction is now in engine execution order (by first
`fill_sequence`), and the comparator groups both sides by normalized side and
pairs within each group, asserting that no pair ever crosses roles. In cases B
and C the BUY pairs with the BUY and the unmatched legacy SELL is reported as
the mandatory-exit difference (D9).

**A vocabulary difference can never conceal an economic one.** A difference may
carry `D5-enum-vocabulary` only if both values normalize to the same token in
`VOCABULARY_EQUIVALENCE` (`fill`/`FILLED`, `buy`/`BUY`, …). Sides are compared
as the pairing key, and order type is compared as an economic field: `market`
against `LIMIT` is now `D16-order-type-model`, not vocabulary. Five tests in
`tests/unit/test_pr7_parity_report.py` pin this.

### Numeric rule (the comparator's own, inherited from neither side)

Both sides are converted to exact `Decimal` — the legacy side from its decimal
string, the runtime side through `repr()`, the shortest string that round-trips
a double — and the **exact** difference is compared against a declared bound.
Nothing is pre-rounded or quantized.

| Family | Bound | Why |
|---|---|---|
| money, price | ±1e-6 **absolute** | values run 1e2–1e5, where one double ULP is 1e-14–1e-11; four orders below one cent |
| fraction (drawdown) | ±1e-9 **relative**, absolute floor 1e-15 | drawdowns run as small as ~1e-5, so an absolute bound is the wrong instrument — an absolute 1e-9 called two values differing in their fifth significant figure "equal" on the comparator's first pass |
| share quantity | exact, no tolerance | whole numbers on both sides |

`comparison.json` reports the smallest non-zero difference measured per family
(**1.0** money, **0.1** price, **2.0e-10** fraction), so no "equal" verdict in
this report rests on a bound absorbing float/`Decimal` noise.

---

## 5. The exact case

`case_f_exact_entry_parity` is the case the revised decision exists to produce.
One property of its bar levels is load-bearing, and it follows from D1 rather
than from any fill-timing difference: the legacy engine seeds its running peak
equity with `initial_cash` while `backtest_runtime` seeds it with zero and
raises it on the first session it reports — and the legacy engine's first
session (2024-01-02) is one `backtest_runtime` never reports. Setting
`entry_after_session = 2024-01-03` guarantees the runtime's first reported
session is still flat and therefore marks at exactly 100 000, so both peaks
coincide for the whole run; every later session closes below that, and the
aggregate drawdown is set by 2024-01-05, which both engines report.

| Dimension | `backtest_runtime` | legacy engine | |
|---|---|---|---|
| entry session (authoritative booking date) | 2024-01-04 | 2024-01-04 | equal |
| entry price | 101.00 | 101.00 | equal |
| quantity | 10 | 10 | equal |
| exits | none | none | equal |
| end position | 10 @ 101.00 | 10 @ 101.00 | equal |
| final cash | 98 990 | 98 990 | equal |
| final equity | 99 992 | 99 992 | equal |
| final value | 99 992 | 99 992 | equal |
| max drawdown | −0.00018 | −0.00018 | equal |
| cash / equity / unrealized P&L / realized P&L / drawdown, per session | — | — | equal on **every** co-dated session, the entry session included |

The comparator asserts all fifteen dimensions and **exits non-zero** if any
fails; `run_parity.sh` therefore cannot produce a green run with a broken exact
case. All fifteen pass, and `excluded_sessions` is empty.

**No session is exempt.** Review round 1 excluded the entry session under
`D15-entry-session-state-lag`, and excluded the fill's own date under
`D4-fill-market-date-lag`. Both exemptions are gone: the entry session's cash,
equity, unrealized P&L, realized P&L and drawdown are compared and equal, and
both engines report the same booking date from their own records. What differs
on this case is now only representational (D5, D6, D11, D14, D16) or an adapter
capability gap (D7, D8).

Both entry dates are corroborated but not derived: each engine's reported
booking session is checked to be a session whose open equals that engine's own
reported fill price, and a mismatch is emitted as a difference. The session is
never inferred from the price.

---

## 6. What the two engines did across the set

| Case | Booking session | Entry price | Exit | Final equity | Max drawdown |
|---|---|---|---|---|---|
| F | 01-04 · 01-04 | 101.0 · 101.0 | none · none | 99 992 · 99 992 | −0.00018 · −0.00018 |
| A | 01-03 · 01-04 | 100.5 · 102.0 | none · none | 100 040 · 100 025 | −0.0000499925… · −0.00005 |
| B | 01-03 · 01-04 | 100.5 · 102.0 | none · **FINAL_TARGET** 109.5 | 100 090 · 100 075 | −0.0000499925… · −0.00005 |
| C | 01-03 · 01-04 | 100.0 · 110.0 | none · **STOP_GAP** 90.0 | 99 920 · 99 800 | −0.001998001… · −0.002 |
| E | 01-03 · 01-04 | 104.0 · 106.0 | none · none | 100 045 · 100 025 | 0 · 0 |
| D | 01-03 · 01-24 | 100.8 · 100.9 | none · none | 100 010 · 100 009 | −0.000109996… · −0.000109991… |

(`backtest_runtime` · legacy. Booking sessions are each engine's own record.
Quantity was **10 shares, exactly equal, in every case**; fees were 0 on both
sides in every case.)

**Both engines fill an entry at the open of the session they book it in** —
measured, not assumed. Case E gaps its opens away from the previous closes, and
LumiBot still filled at **104.0**, the 2024-01-03 open, rather than **100.0**,
the 2024-01-02 close (`results/probe_output.txt`). Outside case F the entry
*price* differences are therefore entirely a consequence of *which* session each
engine entered on.

The drawdown columns are close but unequal on the default-timing cases for a
reason that is **not** entry timing and would survive aligning it: the legacy
engine seeds its running peak equity with `initial_cash`, while
`backtest_runtime` seeds it with zero and raises it on the first session it
reports — which, by D1, is not the first session in the dataset. On these cases
the entry is already booked in that first reported session, so the runtime's
seed includes the entry's mark and the legacy seed does not. Case F avoids it by
construction (§5), which is why it is the case that can be exact. See D13 in §7.

---

## 7. Every difference, classified

The comparator holds the classifications and **exits non-zero** if any emitted
difference is missing from the table, if a one-sided capability gap is labelled
`UNSUPPORTED`, or if the exact case fails. Totals over all six cases plus the
cross-case finding:

| Category | Count |
|---|---|
| old-engine defect | **13** |
| adapter defect (in `backtest_runtime`) | **14** — 0 behavior, 14 capability |
| intentional library semantic difference | **93** |
| unsupported requirement (neither side can express it) | **0** |

**On the category semantics.** "Unsupported requirement" is reserved for a case
*neither* side can express. The first pass used it for fees/slippage and
realized P&L, which the legacy engine supports and `backtest_runtime` does not —
those are **adapter capability defects**, because the requirement is
demonstrably supportable. Nothing in this fixture set is genuinely unsupported
by both sides, so that category is legitimately zero; short selling, intraday
bars and multi-symbol portfolios would qualify, and none is exercised here.

**The behavior subcategory is now empty**, down from 10. It held D4 and D15,
which review round 2 established are properties of LumiBot's execution loop
rather than defects of either engine, and which reference strategy v2 no longer
reports wrongly. They are reclassified below and recorded in the comparator's
`RESOLVED_BY_REFERENCE_STRATEGY_V2`, which fails the run if either is emitted
as a difference again.

### Old-engine defect

**D17 — the legacy run identity ignores the dataset (cross-case).** Detected by
comparing two cases against each other, which no single-case comparison can do.
`case_a_buy_and_hold` and `case_b_perturbed_last_close` have **different**
`historical_bar_dataset_checksum` values (`e5cf5f68…` vs `3c7abef9…`) and
**different results** (final equity 100 025 vs 100 075), yet share one
`backtest_run_id` (`backtest-fdc36c96…`) and one `configuration_hash`. Run
identity comes from `_configuration_hash` and `_signal_set_hash` only; the bars
contribute nothing.

This is not cosmetic. `_persist_result` treats a matching `backtest_run_id`
whose `input_hash` also matches as an idempotent replay and returns without
writing — so persisting both runs stores only the first, and the second run's
daily states, fills and metrics are silently discarded under an identity the
second run also claims. The collision-with-different-input guard immediately
above it never fires, because the input hash is genuinely identical: it is the
*dataset*, not the configuration, that changed. **Reported, not fixed** —
changing the legacy engine's run identity is a behavior change and belongs to a
later PR with its own review.

**D11 — `BacktestResult` carries no order records and no position records.**
ADR 0009 Decision 3 names orders and positions as parity dimensions; the legacy
result type exposes fills, daily states, rejected entries, metrics and
unresolved evaluations only. Both had to be reconstructed from the fill stream,
labelled as such in the emitted document. A defect of the result *type*; no
computed value is wrong.

### Adapter defect — behavior

None. This subcategory held D4 and D15 after review round 1; both are
reclassified below.

### Adapter defect — capability

**D7 — fees and slippage.** The legacy engine models a per-order fee and a bps
slippage and reports a per-fill `slippage` amount. `backtest_runtime` hardcodes
`fees: 0.0` and has no slippage concept or field at all. They compared equal
here only because the legacy configuration deliberately used its zero-cost
defaults.

**D8 — realized P&L and exit support.** `_normalize_result` writes
`realized_pnl: 0.0` as a constant and the result schema has no `exit_reason`
field; the reference strategy never sells, so there is no realized-P&L path at
all.

### Intentional library semantic difference

**D4 — LumiBot tells a strategy about a fill one session after booking it**
*(reclassified from adapter defect in review round 2).*
`_process_pandas_daily_data` runs `process_pending_orders` after
`on_trading_iteration` within a session, so the fill is booked with the broker's
clock unmoved, but `on_filled_order` is dispatched on the next iteration — and
`order.broker_create_date` / `order.broker_update_date` stay `None` in
backtesting, so the callback clock is the only one a naive adapter sees. The
legacy engine has no equivalent split: it creates the fill inline in the session
it is processing. That difference between the two execution loops is permanent
and is nobody's defect. Reference strategy v1 *published* the lagging clock as
the fill date, which was an adapter defect; v2 reads the broker's trade-event
log instead, and the comparator fails if the lagging date ever reappears.

**D15 — a strategy cannot observe its own session's fill**
*(reclassified from adapter defect in review round 2).* The same split, in the
state series: the balances a strategy can sample on the booking session
necessarily exclude that session's own fill, while the legacy engine's state for
a session reflects everything that happened in it. v1 published the raw sample
and so reported the entry session flat; v2 re-applies the session's booked fills,
which is what lets the exact case agree on the entry session too.

**D2 — entry session** (the five default-timing cases). LumiBot cannot book
before the second bar and the legacy engine cannot enter before the third, both
structurally. At the engine's default `atr_period=14` (case D) the offset widens
to 14 sessions. Case F closes it with `entry_after_session`.

**D1 — daily-state series start.** LumiBot's first `on_trading_iteration` lands
on the second fixture bar, so `backtest_runtime` emits one fewer daily state
than there are bars and never reports the first session (4 vs 5; 29 vs 30 in
case D). Both engines *use* the first bar as data. Confirmed by
`probe_output.txt`.

**D9 — mandatory risk exits.** The legacy engine always attaches an ATR stop, a
ratcheting trailing stop, an ATR target and a maximum holding period; none is
optional, and the narrowest construction cannot switch any of them off. Two
fixtures touch one — case B exits `FINAL_TARGET` at 109.5 when the perturbed
close raises the bar's high to exactly the target, and case C exits `STOP_GAP`
at 90.0 after the trailing stop ratchets 87.0 → 97.0 — while the reference
strategy holds in both. This is the engine's designed risk behavior, and it is
exactly what `backtest_runtime` cannot express.

**D16 — order model.** The reference strategy submits a market order; the legacy
engine has no market order type and can only express an entry as a limit order.
Kept separate from D5 because a market order and a limit order are not the same
fact spelled two ways. It has no effect on fills here only because every limit
is constructed to be non-binding.

**D10** (end position: flat vs. held) follows from D9. **D3** (entry fill price)
reduces to D2, since both engines fill at the open of the session they book in.
**D12** (the daily cash, equity and unrealized-P&L series) is a consequence of
D2 and D3 on the default-timing cases; both engines mark open positions at the
session's close.

**D13 — the drawdown series — is not fully explained by entry timing, and this
is the one place where saying so would be wrong.** Both engines use the same
formula, `(equity − running peak equity) / running peak equity`, non-positive,
aggregated by minimum: that much is genuinely aligned, and PR 6's review round
aligned it. But the *running peak* is seeded differently, and that is a second,
independent cause:

- the legacy engine seeds its peak with `initial_cash`, so a drawdown can be
  measured from the very first session;
- `backtest_runtime` seeds its peak with `0.0` and raises it on the **first
  session it reports**;
- by **D1**, that is not the first session in the dataset — the two series do
  not start from the same session at all.

On the default-timing cases the entry is already booked in `backtest_runtime`'s
first reported session, so its seed includes the entry's mark while the legacy
seed does not, and the two peaks stay apart for the rest of the run. That is why
case A reports −0.0000499925… against the legacy −0.00005 rather than an exact
match, even though both engines agree on cash, on the equity path after the
entry, and on the formula. Aligning entry timing in general would *not* remove
it; the exact-parity case avoids it only by construction (§5), by guaranteeing
that `backtest_runtime`'s first reported session is still flat and therefore
marks at exactly the budget.

It stays classified as a library semantic difference rather than an adapter
defect, because it is a consequence of D1 — which session series each engine can
report at all — and neither seed contradicts its own run. It is carried into the
PR 8 handoff as an explicit item for any general replacement adapter.

**D5** (`fill`/`FILLED`), **D6** (identity schemes) and **D14** (run-identity
fields) are representational only.

---

## 8. What this does and does not establish

It establishes that, on one genuinely identical buy-and-hold, the two engines
agree on **every** economic number and on every co-dated session with none
excluded — booking session, entry price, quantity, position, cash, equity,
unrealized and realized P&L, drawdown, final value and maximum drawdown. Across
the wider set it establishes that both share the fill-price convention, the
valuation convention and the drawdown *formula*.

It does **not** establish that entry timing explains everything else. The
default-timing cases' drawdowns differ for a second reason that survives any
timing alignment: the two engines seed their running peak equity differently and
start their state series on different sessions (D13 above, a consequence of D1).
Cash, equity and unrealized P&L on those cases *are* fully explained by entry
timing; the drawdown series is not.

It also establishes something about the evidence itself: fill timing here is
read from LumiBot's own order-lifecycle record, not deduced from which bar's
open matches a fill price. The two earlier passes each deduced it differently
and each got a different answer.

It does **not** establish that `backtest_runtime` could replace
`backtesting/engine.py`. The adapter capability defects (D7, D8) plus D9 and D11
are the list of things that would have to be built or accepted first — most of
it on the `backtest_runtime` side: no sells, no exits, no stops or targets, no
maximum holding period, no fees, no slippage, no realized P&L, no rejected
entries, no multi-symbol, no risk-based sizing, no daily-loss or drawdown
limits. It also surfaces one defect on the legacy side that a removal decision
should weigh independently of any migration: D17, where two runs over different
data share a persisted identity.

PR 8 is the gate that weighs all of it; this report is its input.

---

## 9. Artifacts

```text
docs/library-migration/pr7/
  PARITY_REPORT.md                this file
  build_fixtures.py               builds the fixture set; asserts point-in-time safety
  run_legacy_engine.py            main environment  -> legacy result documents
  probe_lumibot_fill_timing.py    isolated environment; records the broker's own
                                  event clock against the two lagging clocks
                                  (evidence for D1, D2, D4 and D15)
  compare_parity.py               reads two documents; classifies; enforces every rule above
  run_parity.sh                   reproduces everything under results/
  fixtures/
    case_*.input.json             6 input documents (the only source of bars)
    parity_manifest.json          per-case legacy-engine parameters and entry timing
  results/
    case_*.backtest_runtime.json  raw result documents, backtest_runtime.result.v2
    case_*.legacy_engine.json     raw result documents, pr7.legacy_engine.result.v1
    comparison.json               field-by-field data, per-session tables, classifications,
                                  exact-parity checks, cross-case findings, fixture binding
    comparison_output.txt         human-readable comparison
    probe_output.txt              LumiBot iteration/fill-timing transcripts, three clocks
                                  per fill, for cases A, E and F

tests/unit/test_pr7_parity_report.py   28 regression tests over the committed artifacts
backtest_runtime/tests/test_entry_timing.py   16 tests over the v2 entry-timing control
```
