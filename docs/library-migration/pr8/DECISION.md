# PR 8 — Removal decision: the custom event-driven backtest engine

**Date:** 2026-08-02 · **Branch:** `migration/08-backtest-removal-decision` ·
**Input:** `docs/library-migration/pr7/PARITY_REPORT.md` and the raw data under
`pr7/results/` · **Depends on:** PR 7 (implemented, not merged, PR #19)

This is the gate ADR 0009 and `MASTER_PLAN.md` row 8 defined: decide whether
`src/trading_research/backtesting/engine.py` (and `models.py`) can be safely
removed in favour of `backtest_runtime/`. It is a decision, not a removal and
not a fix. **No file under `src/`, `scripts/`, `paper_runtime/src/`,
`backtest_runtime/src/`, `config/`, or `tests/` is modified on this branch.**

---

## 1. Verdict

**1. `backtesting/engine.py` and `backtesting/models.py` are NOT approved for
removal.** They remain the authoritative backtest implementation, indefinitely
— not "pending a later parity PR". The `REMOVAL_MANIFEST.md`
"conditionally eligible" row is **closed as not approved**, so the manifest
carries no unresolved backtest target into PR 17 or the PR 18 audit. PR 17
removes nothing on account of PR 8.

**2. `backtest_runtime/` is kept**, in the role `REMOVAL_MANIFEST.md` already
names for this outcome: *an additional, non-replacing option*. ADR 0009's third
possibility — keep the legacy engine and delete the distribution outright — is
**not** taken, for the reasons in §7. Its role is narrowed and written down
here so "additional option" does not decay into "second authority": it is an
**independent offline cross-check and parity harness with no execution
authority and no callers in `src/`**.

**3. Two defects on the legacy side are now mandatory follow-ups, not optional
ones.** Because the component is being kept rather than replaced, D17 (run
identity ignores the dataset) and the never-written `backtest_orders` table
stop being "defects in a component we may delete" and become live correctness
bugs in the authoritative implementation. Both are recorded in §8 with the fix
each needs. Neither is fixed here — both are behavior changes to the legacy
engine and belong to a PR with its own review, exactly as PR 7 said.

**4. This verdict preserves the status quo and therefore needs no superseding
ADR.** Under `DECISIONS.md`'s governing principle, an ADR is required to
*remove* a preserved or gated component, not to decline to. The opposite
verdict would have required one. The owner can overturn this verdict; §9 lists
what would have to be true first.

---

## 2. What the decision rests on

PR 7's report is the input, but this gate does not adopt its conclusions
unexamined. Every claim below marked **(verified here)** was re-checked against
the source on this branch; the rest is cited to PR 7 and was not independently
re-run.

- Run identity excludes the bar dataset, and `_persist_result` silently drops
  the second run — **(verified here**, `engine.py:130-139`, `engine.py:428-429`).
- `BacktestResult` carries no order or position records — **(verified here**,
  `models.py`, and see §8.2: the `backtest_orders` table exists and is never
  written).
- `backtest_runtime` books a single buy of a single symbol, hardcodes
  `fees: 0.0`, and writes `realized_pnl: 0.0` as a constant — **(verified
  here**, `backtest_runtime/src/backtest_runtime/strategy.py:164-260, 338-354`).
- The exact-parity case (`case_f_exact_entry_parity`), the fifteen asserted
  dimensions, the fill-timing evidence from LumiBot's broker trade-event log,
  and the 13/14/93/0 classification tally — **taken from PR 7**, not re-run.
  This gate does not need to re-derive them: they establish agreement on one
  fixture, and agreement is not what the verdict turns on. §3 and §4 are.

The two engines are also both fixture-fed today. `HistoricalDataProvider` has
exactly one implementation, `FixtureHistoricalDataProvider`
(`data_provider.py:16`), and ADR 0009 Decision 5 holds `backtest_runtime` to
the same posture. Neither side has a live historical-price source, so this
decision is about which implementation the repository maintains, not about
which one has better data access.

---

## 3. The replacement gap, measured

Everything the legacy engine does that `backtest_runtime` cannot express today.
Line references are to this branch.

| Capability | Legacy engine | `backtest_runtime` |
|---|---|---|
| Point-in-time bar availability (`available_at`, `point_in_time_safe`) | enforced at three layers — `models.py:26-30`, `data_provider.py:27-29`, plus a future-bar guard at `engine.py:148-149` | **no availability axis in the contract at all** — `_BAR_FIELDS` is `{date, open, high, low, close, volume}` (`contract.py:38`) |
| Money representation | `Decimal` end to end; persisted as exact decimal strings into `TEXT` columns | `float` throughout |
| Multi-symbol | yes — `config.symbols`, per-symbol bar maps (`engine.py:141-156`) | single symbol (`strategy.py:178`) |
| Sells / exits | five reasons: `STOP_GAP`, `HARD_OR_TRAILING_STOP`, `MAXIMUM_HOLDING_PERIOD`, `PARTIAL_PROFIT`, `FINAL_TARGET` | none — no sell is submitted at all |
| ATR stop, ATR target | mandatory, `atr_risk_levels` (`engine.py:318-321`) | absent |
| Ratcheting trailing stop | `engine.py:268-275` | absent |
| Maximum holding period | `engine.py:212-213` | absent |
| Partial-profit staging | `engine.py:230-255`, via `paper_books.lifecycle_state.calculate_partial_close_quantity` | absent |
| Strategy-supplied stop/target override, with rejection on invalid values | `engine.py:327-336` | absent |
| Risk-fraction sizing and cash cap | `min(quantity_hint, risk_qty, cash_qty)` (`engine.py:337-339`) | caller-specified whole-share quantity only |
| Daily-loss limit, drawdown limit | `engine.py:285-290` | absent |
| Economic-event blackout | `engine.py:291-298` | absent |
| Rejected-entry audit trail | 11 distinct reasons | absent — no rejection concept |
| Fees, slippage | per-order fee and bps slippage, per-fill `slippage` amount | `fees: 0.0` hardcoded; no slippage concept |
| Realized P&L | computed per sell (`engine.py:110`) | constant `0.0` |
| Order records, position records | **absent** (D11 — the one capability the runtime has and the legacy result type does not) | `orders[]`, `positions[]` present |
| Persistence and run identity | four tables, idempotent replay, collision guard | none — emits a JSON document |
| Unresolved-evaluation reporting | `unresolved_evaluations` | absent |

Read as a build list, that is a from-scratch re-implementation of a
risk-controlled execution model inside a LumiBot strategy — not an adapter
gap that a bounded extension closes. PR 7's own bounded extension is the
calibration: adding *one* control that only changes **when** the existing
single buy is submitted required a schema version bump on both the input and
the result document, a new digest field, and 16 tests
(`DECISIONS.md` D6). Sizing, exits, limits, blackout and rejections are each
larger than that.

---

## 4. Three properties that are not feature gaps

Most of §3 is missing features, and missing features can be built. These three
are different in kind, and they are what the verdict actually turns on.

**4.1 Point-in-time safety is a structural property of the legacy contract, and
is not expressible in the current file contract.** `HistoricalBar` refuses to
construct unless `available_at` is timezone-aware and `point_in_time_safe` is
true (`models.py:26-30`); the provider refuses to return look-ahead bars
(`data_provider.py:27-29`); the engine refuses a dataset containing a
future-available bar (`engine.py:148-149`). Three independent refusals, at
three layers. `backtest_runtime`'s bar has six fields and no notion of when a
value became knowable, so a look-ahead dataset is not detectable on that side —
it is simply data. This is the same class of invariant `PRESERVATION_MANIFEST.md`
already protects for `paper_books/valuation.py` ("never leaking a future or
current price into a historical snapshot"), and it is why the backtest engine
belongs in that manifest, which this PR adds it to.

Closing this gap is not a matter of adding a field. The availability axis has
to be enforced *inside* the engine that consumes the bars, and LumiBot's
`_process_pandas_daily_data` loop consumes a pandas frame that has no such
axis.

**4.2 `Decimal` versus `float` is an accounting boundary, not a rounding
preference.** The legacy engine computes cash, equity, realized and unrealized
P&L in `Decimal` and persists exact decimal strings; the runtime computes in
double precision. PR 7 handled this correctly *for comparison* by converting
both sides to exact `Decimal` and declaring per-family bounds — ±1e-6 absolute
on money, ±1e-9 relative on fractions. Those bounds are the right instrument
for asking "do these two agree?" They are not a licence to make the float side
authoritative: the repository's accounting layer is `Decimal`-based by
`DECISIONS.md` D1, and a replacement would put a float equity series into the
same tables that today hold exact strings.

**4.3 The engine shares partial-close arithmetic with the preserved accounting
layer.** `engine.py:17` imports `calculate_partial_close_quantity` from
`paper_books/lifecycle_state.py` — the backtest computes a partial close with
the *same function* the paper-books lifecycle uses, including its
minimum-remaining-quantity floor. That is deliberate single-authority design.
Re-implementing exits inside a LumiBot strategy would fork that arithmetic away
from the preserved layer, in a distribution that by ADR 0009 must never import
`trading_research`. The boundary that makes `backtest_runtime` safe is exactly
the boundary that prevents it from reusing this code.

---

## 5. What removal would actually touch

`backtesting/` is not a leaf. Its `models.py` types are the shared vocabulary of
the strategies layer:

```text
backtesting/models.py  ->  strategies/contracts.py       (HistoricalBar)
                           strategies/factors.py          (HistoricalBar)
                           strategies/safety_gates.py     (HistoricalBar)
                           strategies/timestamps.py       (HistoricalBar)
                           strategies/strategy_metrics.py (BacktestFill, BacktestResult, EntrySignal)
                           strategies/backtest_adapter.py (BacktestResult, EntrySignal, BacktestError)
```

`strategies/contracts.py:112` states the intent explicitly — reuse
`backtesting.models.HistoricalBar` "rather than introducing a second bar" type.
So "remove the engine" means either keeping `models.py` as a types-only module
whose engine is gone, or rewriting six strategy modules against a new bar type.
23 tests across `test_advanced_risk_backtest.py` (1),
`test_backtest_identity_and_strategy_exits.py` (10), `test_strategy_backtest.py`
(5) and `test_strategy_metrics_fees_exposure.py` (7) exercise the engine and its
strategy adapter directly, and would have to be re-pointed or deleted.

**Counter-evidence, stated plainly:** `run_backtest` and `run_strategy_backtest`
have **no non-test caller** anywhere in `src/`, `scripts/`, or `.claude/`. The
engine is reachable only through `strategies/backtest_adapter.py`, which is
itself only called from tests. A low-usage component is a weaker preservation
case than a load-bearing one, and this gate should not pretend otherwise. It
cuts both ways: low usage also means low maintenance pressure and no operational
risk in keeping it, while the strategies layer still depends on its type
vocabulary today.

---

## 6. The drawdown item (D13 + D1) is a precondition on any future proposal

PR 7's handoff asks PR 8 to weigh peak-equity seeding and state-series start.
The finding is that the two engines disagree on drawdown for a reason that
survives aligning entry timing: the legacy engine seeds its running peak with
`initial_cash` (`engine.py:175`), `backtest_runtime` seeds with `0.0` and raises
it on the first session it reports, and by D1 that is not the first session in
the dataset. `case_f_exact_entry_parity` agrees on drawdown by construction, not
by equivalence.

**Disposition:** this gate does not resolve it, because nothing in this
repository is currently wrong. The legacy engine is self-consistent, and the
runtime is self-consistent for the run it reports. It is recorded as a
**precondition on any future replacement proposal**: such a proposal must state
what its peak is seeded with and which session its state series starts on, and
must demonstrate agreement on ordinary data rather than on a fixture built to
avoid the question. `max_drawdown_fraction` is not a display number — the legacy
engine gates entries on it (`engine.py:288-290`), so a replacement that disagrees
about drawdown disagrees about which entries are allowed.

---

## 7. Why `backtest_runtime/` is kept rather than deleted

ADR 0009's rollback path allows deleting the distribution outright once its
parity evidence has been collected. The costs are real: a third distribution, a
pinned `lumibot==4.5.78`, a two-leg blocking CI job, and a standing risk that
two backtest implementations drift into two answers.

Kept anyway, for three reasons:

1. **It is what makes a future decision cheap.** The reproducible harness
   (`run_parity.sh`, six checked-in fixtures, the comparator with its
   classification table and non-zero exit) is the mechanism by which anyone can
   re-ask this question against a newer LumiBot without rebuilding the evidence.
   Deleting the distribution deletes the harness.
2. **Its failure modes are bounded by construction.** No credentials, no
   network, no callers in `src/`, no execution authority — asserted by that
   distribution's own blocking suite and by AST import-boundary tests in both
   directions. Its worst failure is a wrong research number.
3. **The drift risk is answered by the role, not by deletion.** It has no
   callers, so there is no path by which its numbers reach a book, an order, or
   a promotion decision.

**Review trigger, so "keep" does not become permanent by default:** if the
`backtest-runtime-tests` CI job requires unplanned maintenance twice in
succession (a LumiBot regression, a pin bump forced by a transitive break, or a
`LUMIBOT_DISABLE_DOTENV` semantics change), the distribution's value is to be
re-argued at that point rather than absorbed. ADR 0009's rollback path is the
answer if the argument fails.

---

## 8. The two legacy defects, weighed independently

PR 7's handoff requires D17 to be weighed independently of any migration
decision. Both items below are exactly that: they are wrong whether or not
anything is ever migrated, and keeping the component makes fixing them
obligatory.

### 8.1 D17 — run identity ignores the bar dataset (confirmed here)

`_configuration_hash` (`engine.py:67-92`) hashes dates, symbols, cash, risk
parameters, blackout config and `code_version`. `input_hash` (`engine.py:135-138`)
combines it with `_signal_set_hash` and `code_version`. **The bars contribute
nothing.** `_persist_result` then treats a matching `backtest_run_id` with a
matching `input_hash` as an idempotent replay and returns without writing
(`engine.py:428-429`), while the collision guard immediately above it
(`engine.py:420-427`) never fires, because the input hash is genuinely
identical — it is the dataset that changed, not the configuration.

Consequence: two runs over provably different bars share one
`backtest_run_id`, and the second run's daily states, fills and metrics are
silently discarded under an identity the second run also claims. Silent, not
loud. PR 7 demonstrated it across `case_a` and `case_b`, which have different
dataset checksums and different final equity (100 025 vs 100 075) under one run
ID.

**Required fix (its own PR, Opus review):** bind a canonical bar-dataset digest
into `input_hash`, backed by a test that two runs differing only in bars produce
different `backtest_run_id` values and that both persist. Note the migration
consideration: existing `backtest_runs` rows were computed under the old
identity, so the fix needs a stance on stored rows — most likely a
`code_version` bump, which already participates in the hash, so historical rows
retain their identity and no row is rewritten.

### 8.2 `backtest_orders` is created and never written (new finding)

`storage/paper_books_schema.py:978-989` creates a `backtest_orders` table with
`order_id`, `side`, `quantity`, `limit_price`, `eligible_date`, `status` and
`rejection_reason`. Nothing in the repository ever writes or reads it: the
string `backtest_orders` occurs exactly once in the entire Python source, in
that `CREATE TABLE`. `_persist_result` writes runs, daily states, fills and
metrics only (`engine.py:430-464`).

This corroborates D11 and extends it. The absence of order records is not only a
result-type gap; the persistence schema was built to hold them, including the
rejection reasons the engine already computes and returns in `rejected_entries`
(11 distinct reasons) — and then no writer followed. A run's rejected entries
live in the in-memory result and inside `report_json`, but never as queryable
rows.

The provenance clarifies what "fix" means here. `docs/milestones/milestone-13.md`
("Schema and migrations") lists `backtest_orders` and `backtest_positions` among
*candidate* structures, with the instruction to "use the minimum number of
tables that preserves immutability, idempotency, auditability, replay, crash
safety, exact `Decimal` round trips, and explicit foreign-key relationships".
`backtest_positions` was correctly never created. `backtest_orders` was created
and then left empty — the one outcome the instruction did not contemplate.

**Required fix (may share the D17 PR):** either persist orders and rejections
into the table the schema already defines, or delete the table and record that
order-level backtest history is deliberately not retained. An empty table that
looks like an audit surface is worse than either.

---

## 9. What would reopen this decision

Numbered so a future PR can cite one:

1. **A replacement proposal that closes §3 and §4.** Concretely: exits and
   sells, risk-fraction sizing with a cash cap, daily-loss and drawdown limits,
   the economic-event blackout, rejected-entry records, fees and slippage,
   realized P&L, multi-symbol, and a stated resolution of §6's peak-seeding and
   series-start question — with parity demonstrated on ordinary data rather
   than on a fixture constructed to avoid a known divergence.
2. **A point-in-time availability axis that is enforceable on the runtime
   side** (§4.1), not merely carried as an unused field.
3. **An accounting-boundary decision for `float` equity series** (§4.2),
   consistent with `DECISIONS.md` D1.
4. **A LumiBot capability change** that makes any of the above cheap — e.g. a
   documented risk-exit model expressible without re-implementing the legacy
   engine's semantics inside a strategy callback.

Reopening requires an ADR superseding this decision only if it proposes
removal; a proposal that merely widens `backtest_runtime`'s capabilities inside
its existing boundary does not.

---

## 10. Scope of this PR

Documentation only:

```text
docs/library-migration/pr8/DECISION.md   this file
docs/library-migration/DECISIONS.md      D7 — the decision record
docs/library-migration/REMOVAL_MANIFEST.md    conditional row closed as not approved
docs/library-migration/PRESERVATION_MANIFEST.md   backtest engine added, with its invariant
docs/library-migration/COMPONENT_MATRIX.md    row 21 updated to the decided state
docs/library-migration/MASTER_PLAN.md    PR 8 marked decided; PR 17's conditional
                                          dependency resolved; the D17/backtest_orders
                                          follow-up added as a tracked row
docs/library-migration/STATUS.md         completed work and next phase
docs/INDEX.md                            one row pointing at this record
```

No behavior changed anywhere. `src/`, `scripts/`, `paper_runtime/src/`,
`backtest_runtime/src/`, `config/` and `tests/` are byte-unchanged on this
branch. No test was added or modified; the four engine/strategy-adapter test
files and PR 7's artifact regression tests were re-run unchanged to confirm the
preserved component is green — `51 passed, 0 failed` (see `STATUS.md`). No trading limit, authorization rule, `paper_books` accounting code or
scheduling behavior was touched; no broker, provider, model or market-data
service was called; no live data was fetched; the scheduler was not enabled.
