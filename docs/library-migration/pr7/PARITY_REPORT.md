# PR 7 — Backtest parity report

**Date:** 2026-08-02 (revised the same day, review round 1) ·
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
`case_a_buy_and_hold` matched the reference strategy exactly. It did not:
`backtest_runtime` entered on **2024-01-03 at 100.5**, the legacy engine on
**2024-01-04 at 102.0**, and every downstream number differed by that
one-session, 1.50-per-share offset.

The offset is structural. The legacy engine cannot enter before its **third**
bar — a signal is eligible only on the session *after*
`generated_after_session`, and `average_true_range` needs `atr_period + 1` bars
before that — while reference strategy v1 always bought on its first iteration,
the **second** bar. No configuration of either side closes that gap, which is
exactly the condition for taking Option B.

**The bounded extension.** Reference strategy **v2** adds exactly one control,
`strategy.entry_after_session` (`null` = v1 behavior; a date defers the same
single buy to the first iteration strictly after it). Versioned, not defaulted:
`backtest_runtime.input.v2` / `backtest_runtime.reference_strategy.v2`, with a
v1 document now explicitly rejected. It adds **no** sell, stop, target, second
order, order type, scheduler, fetcher, or broker interaction;
`benchmark_asset=None` and `analyze_backtest=False` stay hardcoded; every
ADR 0009 credential, network and isolation guarantee is untouched and still
asserted by that distribution's own blocking suite.

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

**Identical input is proved, not assumed.** Each side computes a SHA-256 over
the canonical bar payload independently — `backtest_runtime.contract.bars_digest`
on one side, a separate re-implementation in `run_legacy_engine.py` on the other
(the main environment must never import `backtest_runtime`). The comparator
aborts if the two digests disagree. They agree for all six cases.

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
faithfully — `backtest_runtime.result.v1` as the CLI emits it,
`pr7.legacy_engine.result.v1` as a direct serialization of `BacktestResult` /
`BacktestFill` / `BacktestDailyState` with `Decimal` preserved as exact decimal
strings — and the comparator holds the mapping (machine-readable in
`results/comparison.json` under `field_mapping`).

| Dimension | `backtest_runtime` | legacy engine | Note |
|---|---|---|---|
| bar set | `historical_bar_dataset_checksum` | recomputed independently | equality proves identical input |
| orders | `orders[]` | *(none)* → `derived.orders_from_fills[]` | `BacktestResult` has no order records — D11 |
| fills | `fills[]` | `fills[]` | aligned by index in execution order |
| fill quantity / price / fees | `.quantity` / `.fill_price` / `.fees` | same names | `float` vs `Decimal` |
| fill slippage | *(absent)* | `fills[].slippage` | D7 |
| fill timestamp | `fills[].market_date` | `fills[].market_date` | runtime value is the `on_filled_order` observation date — D4 |
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
Its bar levels are chosen so that the entry session closes *below* the entry
price (neither side's running peak equity ever rises above the starting
100 000) and a *later* session is a strictly deeper drawdown (the aggregate is
set by a session both engines report identically).

| Dimension | `backtest_runtime` | legacy engine | |
|---|---|---|---|
| entry session | 2024-01-04 | 2024-01-04 | equal |
| entry price | 101.00 | 101.00 | equal |
| quantity | 10 | 10 | equal |
| exits | none | none | equal |
| end position | 10 @ 101.00 | 10 @ 101.00 | equal |
| final cash | 98 990 | 98 990 | equal |
| final equity | 99 992 | 99 992 | equal |
| final value | 99 992 | 99 992 | equal |
| max drawdown | −0.00018 | −0.00018 | equal |
| cash / equity / unrealized P&L / realized P&L / drawdown, per session | — | — | equal on **every** co-dated session except the entry session |

The comparator asserts all fifteen dimensions and **exits non-zero** if any
fails; `run_parity.sh` therefore cannot produce a green run with a broken exact
case. All fifteen pass.

The one excluded session, and the fill's own reported date, are the two
independently classified defects below (D15 and D4) — the same LumiBot
fill-observation lag. Everything else that differs on this case is
representational (D5, D6, D11, D14, D16) or an adapter capability gap (D7, D8).

---

## 6. What the two engines did across the set

| Case | Entry session | Entry price | Exit | Final equity | Max drawdown |
|---|---|---|---|---|---|
| F | 01-04 · 01-04 | 101.0 · 101.0 | none · none | 99 992 · 99 992 | −0.00018 · −0.00018 |
| A | 01-03 · 01-04 | 100.5 · 102.0 | none · none | 100 040 · 100 025 | 0 · −0.00005 |
| B | 01-03 · 01-04 | 100.5 · 102.0 | none · **FINAL_TARGET** 109.5 | 100 090 · 100 075 | 0 · −0.00005 |
| C | 01-03 · 01-04 | 100.0 · 110.0 | none · **STOP_GAP** 90.0 | 99 920 · 99 800 | −0.001 · −0.002 |
| E | 01-03 · 01-04 | 104.0 · 106.0 | none · none | 100 045 · 100 025 | 0 · 0 |
| D | 01-03 · 01-24 | 100.8 · 100.9 | none · none | 100 010 · 100 009 | −0.00011 · −0.000109991… |

(`backtest_runtime` · legacy. Quantity was **10 shares, exactly equal, in every
case**; fees were 0 on both sides in every case.)

**Both engines fill an entry at the open of the entry session** — measured, not
assumed. Case E gaps its opens away from the previous closes, and LumiBot still
filled at **104.0**, the submission session's open, rather than **100.0**, the
previous close (`results/probe_output.txt`). Outside case F the entry *price*
differences are therefore entirely a consequence of *which* session each engine
entered on.

---

## 7. Every difference, classified

The comparator holds the classifications and **exits non-zero** if any emitted
difference is missing from the table, if a one-sided capability gap is labelled
`UNSUPPORTED`, or if the exact case fails. Totals over all six cases plus the
cross-case finding:

| Category | Count |
|---|---|
| old-engine defect | **13** |
| adapter defect (in `backtest_runtime`) | **24** — 10 behavior, 14 capability |
| intentional library semantic difference | **93** |
| unsupported requirement (neither side can express it) | **0** |

**On the category semantics.** "Unsupported requirement" is reserved for a case
*neither* side can express. The first pass used it for fees/slippage and
realized P&L, which the legacy engine supports and `backtest_runtime` does not —
those are **adapter capability defects**, because the requirement is
demonstrably supportable. Nothing in this fixture set is genuinely unsupported
by both sides, so that category is legitimately zero; short selling, intraday
bars and multi-symbol portfolios would qualify, and none is exercised here.

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

**D4 — a fill's `market_date` is one session later than the fill.**
`strategy.py` stamps a fill with `self.get_datetime()` inside `on_filled_order`,
which LumiBot invokes on the iteration *after* the fill is booked. Case E is
decisive: the recorded fill price is **104.0**, the open of **2024-01-03**, but
the recorded `market_date` is **2024-01-04**, a session whose own open is 106.0.
On the exact case the fill prices against 2024-01-04 and is stamped 2024-01-05.
LumiBot leaves `order.broker_date` and `order.broker_create_date` as `None` in
backtesting, so it offers no authoritative fill timestamp — which is what makes
the wrong one easy to reach for — but the adapter already observes the correct
session inside `on_trading_iteration`.

**D15 — the entry session's daily state does not reflect the entry.** Same root
cause, in the state series rather than the fill record: `backtest_runtime`'s
state for session D reflects fills booked through D−1, because the snapshot is
taken inside `on_trading_iteration` before LumiBot's broker processes that
session's order. On the exact case this is the *only* daily-state
disagreement — one session out of four, on cash, equity, unrealized P&L and
drawdown.

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

**D2 — entry session** (the five default-timing cases). The legacy engine's
earliest possible entry is the third bar; reference strategy v1 timing buys on
the second. At the engine's default `atr_period=14` (case D) the offset is 14
sessions. Case F closes it with `entry_after_session`.

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
reduces to D2, since both engines fill at the entry session's open.
**D12 / D13** (the daily cash, equity, unrealized-P&L and drawdown series) are
consequences of D2 and D3 on the default-timing cases; both engines mark open
positions at the session's close and both define drawdown as
`(equity − running peak equity) / running peak equity`, non-positive, aggregated
by minimum. **D5** (`fill`/`FILLED`), **D6** (identity schemes) and **D14**
(run-identity fields) are representational only.

---

## 8. What this does and does not establish

It establishes that, on one genuinely identical buy-and-hold, the two engines
agree on **every** economic number — entry session, entry price, quantity,
position, cash, equity, unrealized and realized P&L, drawdown, final value and
maximum drawdown — with the sole exception of the entry session's own snapshot,
which is a classified adapter defect with a known mechanism. Across the wider
set it establishes that both share the fill-price convention, the valuation
convention and the drawdown definition, and that their other numeric
differences are fully explained by entry timing plus that same lag.

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
  probe_lumibot_fill_timing.py    isolated environment; evidence for D1, D4 and D15
  compare_parity.py               reads two documents; classifies; enforces every rule above
  run_parity.sh                   reproduces everything under results/
  fixtures/
    case_*.input.json             6 input documents (the only source of bars)
    parity_manifest.json          per-case legacy-engine parameters and entry timing
  results/
    case_*.backtest_runtime.json  raw result documents, backtest_runtime.result.v1
    case_*.legacy_engine.json     raw result documents, pr7.legacy_engine.result.v1
    comparison.json               field-by-field data, per-session tables, classifications,
                                  exact-parity checks, cross-case findings
    comparison_output.txt         human-readable comparison
    probe_output.txt              LumiBot iteration/fill-timing transcripts (cases A and E)

tests/unit/test_pr7_parity_report.py   21 regression tests over the committed artifacts
backtest_runtime/tests/test_entry_timing.py   13 tests over the v2 entry-timing control
```
