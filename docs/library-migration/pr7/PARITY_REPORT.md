# PR 7 — Backtest parity report

**Date:** 2026-08-02 · **Branch:** `migration/07-backtest-parity-report` ·
**Depends on:** PR 6 (merged, `bbd7a1f`)

Compares `src/trading_research/backtesting/engine.py` (the "legacy engine")
against `backtest_runtime/` (the LumiBot 4.5.78 adapter PR 6 shipped) over one
checked-in fixture set, and classifies every difference.

This is a **report, not a fix.** No engine behavior changed on this branch. No
difference below was made to disappear by patching either side; PR 8 is the
gate that decides what, if anything, is done about them.

---

## 1. Decision: Option A

Recorded in full in `docs/library-migration/DECISIONS.md` **D6**, taken before
any comparison code was written.

**Option A** — express the legacy run as the narrowest possible equivalent of
`backtest_runtime`'s reference strategy: one `EntrySignal`, one symbol, no
`initial_stop_reference` / `target_reference` / `maximum_holding_sessions`, the
same whole-share quantity, a non-binding limit, and zero fees and slippage.

**Option B was not taken.** It was conditional on Option A being unable to
express a case the report needs, and Option A expressed every case. The one
thing Option A cannot do — switch off the legacy engine's mandatory risk exits
— is a *finding* (D9 below), not a gap in the construction. Extending
`backtest_runtime` to chase it would have broadened that distribution's scope
speculatively and would have measured a strategy written for the comparison
rather than the one PR 6 shipped. `backtest_runtime/src/` and
`backtest_runtime/tests/` are byte-unchanged by this PR.

---

## 2. Fixture set

Checked in under `fixtures/`, built by `build_fixtures.py` (pure standard
library; imports neither engine). Each `*.input.json` is a
`backtest_runtime.input.v1` document and is the **single source of bars for
both engines**. `parity_manifest.json` carries each case's Option A
legacy-engine parameters.

| Case | Bars | Provenance | Why it is in the set |
|---|---|---|---|
| `case_a_buy_and_hold` | 5 | `backtest_runtime/tests/support/fixtures.py::BARS`, verbatim | the single-symbol buy-and-hold case that matches the reference strategy exactly |
| `case_b_perturbed_last_close` | 5 | `…::perturbed_input_document()`, verbatim | boundary case already covered by `backtest_runtime`'s `test_determinism.py` |
| `case_c_falling_equity` | 5 | `…::FALLING_BARS`, verbatim | boundary case already covered by `backtest_runtime`'s `test_drawdown.py` (non-zero drawdown) |
| `case_e_gapped_opens` | 5 | synthetic | every array above is gap-free (`open[i] == close[i-1]`), which makes two different fill-price models produce the same number; this one gaps the opens so the models become distinguishable |
| `case_d_long_hold_default_atr` | 30 | synthetic | long enough for the legacy engine's **default** `atr_period=14`, which a five-bar fixture cannot reach |

All bars are `SPKE`, quantity 10, budget 100 000.

**Identical input is proved, not assumed.** Each side computes a SHA-256 over
the canonical bar payload independently — `backtest_runtime.contract.bars_digest`
on one side, a separate re-implementation in `run_legacy_engine.py` on the
other (the main environment must never import `backtest_runtime`). The
comparator aborts if the two digests disagree. They agree for all five cases.

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

Reproduce everything under `results/` with:

```bash
python3.11 -m venv /tmp/bt-venv
/tmp/bt-venv/bin/python -m pip install -e backtest_runtime/[dev]
docs/library-migration/pr7/run_parity.sh /tmp/bt-venv/bin/python .venv/bin/python
```

The script runs each `backtest_runtime` case **twice** and fails if the two
result documents are not byte-identical. They were byte-identical for all five
cases, which is the reproducibility requirement of the prompt's validation
item 3 extended to the two fixtures `backtest_runtime`'s own determinism tests
do not cover.

`backtest_runtime` was installed alone in a Python 3.11 venv: `pip check`
clean, `lumibot 4.5.78` resolved.

---

## 4. Mapping between the two result shapes

The two documents are **not** forced into one schema. Each side is serialized
faithfully — `backtest_runtime.result.v1` as the CLI emits it,
`pr7.legacy_engine.result.v1` as a direct serialization of `BacktestResult` /
`BacktestFill` / `BacktestDailyState` with `Decimal` preserved as exact
decimal strings — and the comparator holds the mapping. Machine-readable copy
in `results/comparison.json` under `field_mapping`.

| Dimension | `backtest_runtime` | legacy engine | Note |
|---|---|---|---|
| bar set | `historical_bar_dataset_checksum` | recomputed independently | equality proves identical input |
| orders | `orders[]` | *(none)* → `derived.orders_from_fills[]` | `BacktestResult` has no order records — D11 |
| fills | `fills[]` | `fills[]` | aligned by index in execution order |
| fill quantity / price / fees | `fills[].quantity` / `.fill_price` / `.fees` | same names | `float` vs `Decimal` |
| fill slippage | *(absent)* | `fills[].slippage` | no runtime counterpart — D7 |
| fill timestamp | `fills[].market_date` | `fills[].market_date` | runtime value is the `on_filled_order` observation date — D4 |
| exit reason | *(absent)* | `fills[].exit_reason` | the reference strategy never sells — D8/D9 |
| positions | `positions[]` | *(none)* → `derived.end_positions_from_fills[]` | D11 |
| cash / equity / P&L / drawdown | `daily_states[].*` | `daily_states[].*` | aligned by `market_date` |
| final cash / equity / value | `final_cash` / `final_equity` / `final_value` | last daily state's `cash` / `equity` / `equity` | the legacy result has no distinct "final value" |
| max drawdown | `max_drawdown_fraction` | `metrics.maximum_drawdown` | both are the minimum daily `drawdown_fraction` |

Orders and end-of-run positions are **reconstructed** on the legacy side from
its fill stream and are labelled as such in the emitted document
(`orders: null`, `positions: null`, plus a separate `derived` block) — the
report never presents a reconstruction as something the engine reported.

### Numeric rule (the comparator's own, inherited from neither side)

`backtest_runtime` emits IEEE-754 doubles; `backtesting/models.py` uses
`Decimal`. Both are converted to exact `Decimal` — the legacy side from its
decimal string, the runtime side through `repr()`, the shortest string that
round-trips a double — and the **exact** difference is compared against a
declared bound. Nothing is pre-rounded or quantized.

| Family | Bound | Why |
|---|---|---|
| money (cash, equity, P&L) | ±1e-6 **absolute** | values run 1e2–1e5, where one double ULP is 1e-14–1e-11; 1e-6 is far above representation noise and four orders below one cent |
| price | ±1e-6 absolute | same magnitudes |
| fraction (drawdown) | ±1e-9 **relative**, absolute floor 1e-15 | drawdowns here run as small as ~1e-5, so an absolute bound is the wrong instrument — an absolute 1e-9 would call two values differing in their fifth significant figure "equal". It did exactly that on the comparator's first pass, hiding real case-D differences; the relative bound is the fix |
| share quantity | exact, no tolerance | whole numbers on both sides |

**The bounds are not load-bearing.** `comparison.json` reports the smallest
non-zero difference measured in each family: **1.0** for money, **0.1** for
price, **2.0e-10** for fractions (which the relative bound correctly reports as
*differing*, being ~1e-5 in relative terms). Every value compared was either
exactly equal or differed by an economically meaningful amount; no "equal"
verdict in this report depends on a tolerance absorbing float/`Decimal` noise.

---

## 5. What the two engines did

| Case | Entry (session priced against) | Entry price | Exit | Final equity | Max drawdown |
|---|---|---|---|---|---|
| A | rt 01-03 · leg 01-04 | 100.5 · 102.0 | none · none | 100 040 · 100 025 | 0 · −0.00005 |
| B | rt 01-03 · leg 01-04 | 100.5 · 102.0 | none · **FINAL_TARGET** 109.5 on 01-08 | 100 090 · 100 075 | 0 · −0.00005 |
| C | rt 01-03 · leg 01-04 | 100.0 · 110.0 | none · **STOP_GAP** 90.0 on 01-08 | 99 920 · 99 800 | −0.001 · −0.002 |
| E | rt 01-03 · leg 01-04 | 104.0 · 106.0 | none · none | 100 045 · 100 025 | 0 · 0 |
| D | rt 01-03 · leg 01-24 | 100.8 · 100.9 | none · none | 100 010 · 100 009 | −0.00011 · −0.000109991… |

(`rt` = `backtest_runtime`, `leg` = legacy engine. Quantity was **10 shares,
exactly equal, in every case**; fees were 0 on both sides in every case.)

**Both engines fill an entry at the open of the entry session** — measured, not
assumed. Case E gaps its opens away from the previous closes, and LumiBot still
filled at **104.0**, the submission session's open, rather than **100.0**, the
previous close (`results/probe_output.txt`). The entry *price* differences above
are therefore entirely a consequence of *which* session each engine entered on,
not of two different execution models.

---

## 6. Every difference, classified

Classifications are held in `compare_parity.py`'s `CLASSIFICATIONS` table, and
the comparator **exits non-zero if any emitted difference is missing from it** —
so "no difference left unclassified" is enforced mechanically, not by review
attention. Totals across the five cases: **10** old-engine defect, **5**
adapter defect, **82** intentional library semantic difference, **12**
unsupported requirement.

### Adapter defect (in `backtest_runtime`)

**D4 — a fill's `market_date` is one session later than the fill.**
`strategy.py` stamps a fill with `self.get_datetime()` inside
`on_filled_order`, which LumiBot invokes on the iteration *after* the fill is
booked. Measured on case E: the recorded fill price is **104.0**, the open of
**2024-01-03**, but the recorded `market_date` is **2024-01-04** — a session
whose own open is 106.0. The document contradicts itself about when the entry
happened, and the `2024-01-03` daily state still reports zero position and
untouched cash. Fires in all five cases.

Contributing LumiBot limitation, recorded so a future fix does not rediscover
it: `order.broker_date` and `order.broker_create_date` are both `None` in
backtesting, so LumiBot offers no authoritative fill timestamp — which is what
makes the wrong one easy to reach for. The adapter does, however, already
observe the correct session inside `on_trading_iteration`. **Not fixed here**
(PR 7 is a report); this is the clearest candidate for a follow-up change.

### Old-engine defect

**D11 — `BacktestResult` carries no order records and no position records.**
ADR 0009 Decision 3 names orders and positions as parity dimensions, and the
legacy result type has neither: it exposes fills, daily states, rejected
entries, metrics and unresolved evaluations only. This report could compare
them only by reconstructing both from the fill stream inside
`run_legacy_engine.py`; a consumer holding a `BacktestResult` cannot. Classified
as a defect of the result *type*, not of the engine's arithmetic — no computed
value is wrong. Fires in all five cases (once for orders, once for positions).

### Unsupported requirement

**D7 — fees and slippage.** The legacy engine models a per-order fee and a bps
slippage and reports a per-fill `slippage` amount. `backtest_runtime` hardcodes
`fees: 0.0` and has no slippage concept or field at all. They compared equal
here only because the Option A configuration deliberately used the engine's
zero-cost defaults; a non-zero cost model cannot be expressed on the runtime
side at all.

**D8 — realized P&L.** `_normalize_result` writes `realized_pnl: 0.0` as a
constant. The reference strategy never sells, so the runtime side has no
realized-P&L path to exercise; the legacy engine computes it per exit. The
field exists in both schemas but only one side can ever populate it. The same
applies to `exit_reason`, which has no runtime counterpart.

### Intentional library semantic difference

**D2 — entry session.** The legacy engine's earliest possible entry is the
**third** bar: a signal is eligible only on the session *after*
`generated_after_session`, and `average_true_range` needs `atr_period + 1` bars
before that. LumiBot's reference strategy buys on its first iteration, which is
the **second** bar. A one-session offset is structural on the narrowest Option A
construction and cannot be tuned away; at the engine's default `atr_period=14`
(case D) the offset is 14 sessions.

**D1 — daily-state series start.** LumiBot's first `on_trading_iteration` lands
on the second fixture bar, so `backtest_runtime` emits one fewer daily state
than there are bars and never reports the first session (4 vs 5, and 29 vs 30
in case D). Both engines *use* the first bar as data; only the legacy engine
reports it as a state. Confirmed by `probe_output.txt`.

**D9 — mandatory risk exits (the substantive behavioral difference).** The
legacy engine always attaches an ATR stop, a ratcheting trailing stop, an ATR
target and a maximum holding period. None is optional, and Option A cannot
switch any of them off. Two of the five fixtures touch one:

* **case B** — the perturbed final close raises the bar's high to 109.5, which
  reaches the ATR target of 109.5 exactly; the engine sells the whole position
  on the last session while the reference strategy holds.
* **case C** — the trailing stop ratchets from 87.0 to 97.0 after the
  2024-01-05 bar, and the 2024-01-08 open of 90.0 gaps through it; the engine
  exits `STOP_GAP` at 90.0 while the reference strategy holds.

This is the engine's designed risk behavior, not a defect — and it is exactly
the behavior `backtest_runtime` cannot express today.

**D10** (end-of-run position: flat vs. held) follows directly from D9.
**D3** (entry fill price) reduces to D2, since both engines fill at the entry
session's open. **D12 / D13** (the daily cash, equity, unrealized-P&L and
drawdown series) are consequences of D2 and D3: both engines mark open
positions at the session's close and both define drawdown as
`(equity − running peak equity) / running peak equity`, non-positive, aggregated
by minimum — the convention PR 6's review round aligned. **D5** (`buy`/`BUY`,
`fill`/`FILLED`, `market`/`LIMIT`) and **D6** (identity schemes) and **D14**
(run-identity fields) are representational only.

### Where they agreed

Share quantity was exactly equal in every case. Fees were equal (0) in every
case. Realized P&L was equal across every co-dated session in every case. Both
engines fill at the entry session's open. Both use the same drawdown definition
and sign convention. In case E, every co-dated drawdown value was equal. And
the bar-set checksums matched on all five cases, computed independently on each
side.

---

## 7. What this does and does not establish

It establishes that, on a single-symbol whole-share buy held to the end of a
fixture, the two engines agree on quantity, on the fill-price convention, on
the valuation convention and on the drawdown definition, and that their
remaining numeric differences are fully explained by one structural entry-timing
offset plus one adapter timestamp defect.

It does **not** establish that `backtest_runtime` could replace
`backtesting/engine.py`. Everything under *unsupported requirement*, plus D9 and
D11, is the list of things that would have to be built or accepted first — most
of it on the `backtest_runtime` side (no sells, no exits, no stops or targets,
no maximum holding period, no fees, no slippage, no realized P&L, no rejected
entries, no multi-symbol, no risk-based sizing, no daily-loss or drawdown
limits). PR 8 is the gate that weighs that; this report is its input.

---

## 8. Artifacts

```text
docs/library-migration/pr7/
  PARITY_REPORT.md                this file
  build_fixtures.py               builds the checked-in fixture set
  run_legacy_engine.py            main environment  -> legacy result documents
  probe_lumibot_fill_timing.py    isolated environment; evidence for D1 and D4
  compare_parity.py               reads two documents; classifies; enforces classification
  run_parity.sh                   reproduces everything under results/
  fixtures/
    case_*.input.json             5 input documents (the only source of bars)
    parity_manifest.json          per-case Option A legacy-engine parameters
  results/
    case_*.backtest_runtime.json  raw result documents, backtest_runtime.result.v1
    case_*.legacy_engine.json     raw result documents, pr7.legacy_engine.result.v1
    comparison.json               full field-by-field data, per-session tables, classifications
    comparison_output.txt         human-readable comparison
    probe_output.txt              LumiBot iteration/fill-timing transcripts (cases A and E)
```
