# Migration Decisions

This document records every decision that changes, narrows, or reconciles an
accepted ADR, plus the library-adoption decisions established during PR 0
planning. It is the authoritative record for "why is the migration plan not
following `docs/milestones/rebuild/plan.md` literally in this area."

Governing principle (established 2026-07-26):

```text
Replace generic commodity infrastructure with open-source libraries.

Preserve project-specific domain infrastructure when it encodes safety,
book isolation, accounting invariants, auditability, operator authorization,
or ambiguous-side-effect recovery that the proposed library does not provide.
```

Any change that contradicts an accepted ADR must, before implementation:

1. identify the exact ADR decision being changed;
2. explain what repository evidence invalidates the original decision;
3. prove the replacement satisfies every original requirement;
4. include parity, safety, migration, and failure-recovery tests;
5. add a new ADR explicitly superseding the previous ADR;
6. be approved before implementation.

Until those conditions are met for a given item, the accepted ADR remains
authoritative and the original `plan.md` PR is not implemented as written.

---

## D1 — ADR 0006 (isolated paper books): custom accounting preserved

**Original plan.md item:** PR 9 "LumiBot portfolio and paper-accounting
integration" (make LumiBot authoritative for orders, fills, cash, positions,
portfolio value) and PR 10 "Remove custom generic ledgers."

**Status:** Rejected as originally scoped. No superseding ADR written or
required — no repository evidence invalidates ADR 0006's original reasoning.

**Reasoning:** `paper_books/` (`cash_ledger.py`, `positions.py`,
`valuation.py`, `reconciliation.py`) implements `book_id`-scoped isolation
(BASELINE/ENHANCED), FIFO lot accounting, a settled/reserved/available cash
model, point-in-time valuation with staleness flags, and immutable
append-only fill/cash evidence. LumiBot's paper-broker gateway
(`paper_runtime/.../lumibot_gateway.py`) keys state per runtime *process*,
not per `book_id` — ADR 0006 Decision 9 recorded this explicitly as the
reason Milestone 8 built an in-process simulator instead of routing through
`paper_runtime`. Nothing discovered during PR 0 inventory changes that fact.

**Revised scope:**

```text
LumiBot remains authoritative only for the isolated broker/runtime boundary.

paper_books remains authoritative for:
  logical book accounting, local cash, lots, positions, reservations,
  book-level valuation, book-level reconciliation, experiment isolation.
```

Replacement PRs: **PR 9 — Strengthen the LumiBot runtime normalization
contract** (LumiBot may supply normalized orders/statuses/fills/positions/
account snapshots) and **PR 10 — Broker-to-paper_books reconciliation parity
tests** (the application reconciles those observations into `paper_books`;
LumiBot never mutates book state directly). The custom book ledger is not a
removal target.

**Do not delete** `paper_books/cash_ledger.py`, `paper_books/positions.py`,
book-scoped lot accounting, cash/share reservations, book valuation, or
external reconciliation state unless a future *approved* ADR demonstrates
exact replacement semantics.

**PR 10 resolution (2026-08-22):** implemented on
`migration/10-broker-paper-books-reconciliation-parity` (`STATUS.md`
"Completed work (PR 10)"). `paper_books/external_broker.py`'s reconciliation
functions already existed (Milestone 11); the gap PR 10 closed was test
coverage — every prior reconciliation test used a hand-rolled double that
bypassed PR 9's normalization contract, so nothing proved a *normalized*
broker observation (via the only production type that satisfies
`ExternalPaperRuntime`, `RuntimeClient`) reconciles correctly into the real
`paper_books` ledger. `paper_books/cash_ledger.py`, `positions.py`, and the
reconciliation functions themselves are unchanged — the book ledger was not
removed, matching this decision's requirement.

---

## D2 — ADR 0001 (frozen dataclasses): Pydantic narrowed to trust boundaries

**Original plan.md item:** PR 2 "Canonical data contracts" (introduce
Pydantic models broadly, per the original migration sequence).

**Status:** Narrowed, not superseded.

**Corrected 2026-07-26 (PR 1) — single ADR rule.** This document previously
stated two inconsistent rules: "no ADR unless Pydantic expands beyond
boundary use" and, separately, "an ADR is required if limited Pydantic
adoption is implemented." Those two statements conflict once PR 2 actually
implements boundary-only adoption. They are replaced by one rule:

```text
PR 2 is an evaluation PR and adds no Pydantic dependency by default.

If PR 2 recommends adopting Pydantic at any boundary, it must create an ADR
that explicitly supplements/narrows ADR 0001 before adding Pydantic to the
application dependencies.

Expansion beyond the approved trust-boundary scope requires another ADR.
```

PR 1 does not add `pydantic` to any dependency declaration.

**Reasoning:** ADR 0001 Decision 3 rejected `pydantic.BaseModel` for internal
contracts specifically because the repository had no other pydantic
dependency and dataclasses with `__post_init__` already provided the needed
fail-closed validation. That reasoning is undisturbed — every domain type
inventoried in PR 0 (`models/trading_models.py`,
`analysis/screener.py::ScreeningConfig`, `analysis/scorer.py::ScoringConfig`,
`recommendations/builder.py::FrozenRecommendation`, `paper_books/models.py`)
is still a `@dataclass(frozen=True)`.

**Revised scope — permitted Pydantic uses:**

```text
untrusted dict / YAML / JSON
        |
Pydantic boundary model
        |
explicit conversion
        |
frozen domain dataclass
```

YAML/environment configuration loading, CLI request validation, external
provider response parsing, broker/runtime JSONL message validation, and
API/serialized DTO validation are in scope. Internal domain code (strategy,
risk, accounting, persistence) continues to receive only frozen dataclasses.
Pydantic must never be passed through those layers merely for uniformity.

Replacement PR: **PR 2 — Introduce boundary validation without replacing
domain models**, comparing current hand-written validation against a
Pydantic boundary implementation on: dependency/performance impact,
error-message behavior, unknown-field rejection, secret-field handling.
Adopt only where it produces a clear reduction in custom boundary-validation
code. See the single ADR rule recorded above (corrected 2026-07-26, PR 1)
for when an ADR is required.

**PR 2 outcome (2026-07-26): do not adopt.** Full inventory and comparison
in `docs/library-migration/pr2/EVALUATION.md`. No boundary showed a clear
reduction in custom validation code — safety-critical business-rule
validators relocate into Pydantic `field_validator` methods of equal size
rather than shrinking; the one mechanical win (`extra="forbid"` unknown-field
rejection) is already implemented by hand at every safety-critical boundary.
No `pydantic` dependency added; no ADR required, per the single ADR rule
above.

---

## D3 — Hard safety layer: preserved regardless of any library adoption

No ADR conflict — this is a standing constraint restated for this migration.
The following must remain enforced at the application's authoritative
gateway, never delegated to a library default:

```text
$50 maximum single trade
$150 maximum submitted paper notional per UTC day
10% maximum symbol allocation
no options / no margin / no shorting / whole shares only
LIMIT orders only / DAY time in force
paper environment verification
account fingerprint verification
explicit operator preview and submit
exact approval phrase
never auto-retry an ambiguous broker transmission
authoritative NOT_FOUND evidence required before retry
attempt-specific retry limits
cash and share reservations
append-only event and reconciliation evidence
```

Authoritative files: `paper_books/config.py` (limit values),
`paper_books/external_broker.py` (enforcement gateway, ambiguous-submission
recovery, fingerprint verification). These are excluded from every removal
manifest in this migration.

---

## D4 — Library adoption decisions (dependency-compatibility pass, 2026-07-26)

Full research detail in `DEPENDENCY_MATRIX.md`. Summary of decisions that
diverge from `plan.md`'s literal library list:

| Item | plan.md said | Decision | Reason |
|---|---|---|---|
| Empyrical | "Empyrical-compatible package" | Use **empyrical-reloaded** (stefan-jansen fork), not original `empyrical` | Original is abandoned (Quantopian archive) |
| QuantStats | "QuantStats" | Use **quantstats-lumi** (Lumiwealth fork), not original `quantstats` | Same maintainer as already-adopted LumiBot; more consistent release cadence |
| Indicators | "VectorBT indicators or TA-Lib" | **TA-Lib** primary; **pandas-ta-classic** evaluated only as a fallback if TA-Lib's native C-library requirement blocks a target environment | Original `pandas-ta` is functionally abandoned (beta-only, facing archival) |
| Time control (new, not in original plan.md) | — | Adopt **time-machine** over freezegun for test suites | freezegun's monkeypatch approach misses C-extension/pandas datetime internals; no Python 3.14 support |
| Python floor | Not specified in plan.md | Remains >=3.10 project-wide (PR 5 correction, 2026-07-26). The `research` extra alone requires Python >=3.11 (VectorBT's own floor), verified by a dedicated CI job rather than a global `requires-python` bump — see "VectorBT status" below. | VectorBT approved via explicit owner exception (PR 5). A global floor bump was rejected to avoid touching PR 4's `indicators-tests` CI matrix, which PR 5's bounded scope excludes. |
| Pandera, PyArrow | PR 2 / dataset storage | **Defer** — no concrete DataFrame-contract or bulk-storage need exists yet | Adding either speculatively increases install weight (PyArrow wheel 28–53MB) with no current consumer |

### VectorBT status (resolved 2026-07-26, PR 5)

```text
VectorBT status: APPROVED (explicit owner exception, 2026-07-26)
```

The open-source `vectorbt` package (re-verified 2026-07-26 against the PyPI
JSON API: version 1.1.0, `Requires-Python: >=3.11,<3.15`, `numpy>=2.4.6`,
`pandas>=3.0.3,<4.0`, prebuilt wheel present) is licensed Apache-2.0 **with
Commons Clause** — a fair-code restriction on selling a product whose value
derives substantially from the software, not a restriction on internal
research or paper-trading use. This makes it **source-available/fair-code,
not conventional OSI-approved open source**.

**Resolution:** presented with the choice of (a) evaluating an OSI-approved
alternative or (b) explicitly approving VectorBT's license terms, the
repository owner chose (b): explicit, recorded approval of VectorBT's
Apache-2.0 + Commons Clause terms for this repository's internal,
non-commercial research/paper-trading use. Commercial or hosted use would
still require separate review; this approval does not extend there.

Consequently:

- PR 5 adds `vectorbt>=1.1.0,<1.2` to a new `research` optional-dependency
  group in the root `pyproject.toml` (see `DEPENDENCY_MATRIX.md` Section 3).
- PR 5 does **not** raise the project's `requires-python` floor. VectorBT's
  own `>=3.11,<3.15` classifier is narrower than this project's `>=3.10`
  floor, so the `research` extra specifically requires a Python 3.11+
  interpreter — installing it on Python 3.10 fails to resolve, by design,
  not silently. Raising the global floor was considered (the historical
  paragraph in `DEPENDENCY_MATRIX.md` Section 2 anticipated this) but
  rejected for this PR: it would require editing the `indicators-tests` CI
  matrix (`indicators-tests` was added in PR 4, and this PR's bounded scope
  explicitly excludes touching any PR 4 file). A project-wide floor bump
  remains available to a future PR that isn't constrained that way.
- A new `src/trading_research/vector_research/` package holds the adapter;
  a new `research-tests` CI job (Python 3.11 only, since 3.10 cannot resolve
  VectorBT) and a `research` entry in the `dependency-extras-smoke` matrix
  verify it. See `STATUS.md`'s "Completed work (PR 5)" section for full
  detail.

This is a licensing-classification and project-decision record, not a legal
conclusion.

### PR 5 review-fix round (2026-07-26)

Post-merge review of PR 5 (#13) found the original adapter permitted
look-ahead bias (VectorBT fills a signal at its own bar's close, verified
directly), under-validated temporal/parameter structure, exposed VectorBT's
own Sharpe/drawdown as if they were this repository's authoritative
metrics, and enforced its advisory-only boundary only by an attribute
absence check rather than an import-boundary test. All four were fixed on
the same branch before any PR 6/LumiBot work began — see `STATUS.md`'s "PR
5 review-fix round" for the full record. Net effect on this decision
record: the license approval above is unchanged; the *adapter*
implementing it now (a) shifts signal-generation matrices one bar forward
before execution, (b) requires timezone-aware, daily-session-only,
minimum-10-bar input, (c) labels every metric `metric_source =
"VECTORBT_EXPLORATORY"` and wraps each in an explicit `ok`/`no_trades`/
`zero_variance`/`non_finite` status (never a raw non-finite value), and
(d) is now guarded by a repository-wide AST test barring any production
module outside `vector_research/` from importing it, not merely a curated
list of paths.

### Open items requiring resolution before implementation (not before PR 0)

1. ~~**LumiBot backtest-mode dependency/process boundary.**~~ **Resolved
   2026-08-01** — the repository owner accepted ADR 0009 and selected Option B,
   an isolated, credential-free `backtest_runtime/` distribution. The Opus
   architecture review, the pinned-version feasibility spike, and the
   sentinel-`.env` suppression proof are complete, and owner acceptance
   completes the pre-step. PR 6 is unblocked and not started. Creating
   `backtest_runtime/` and its blocking CI job is PR 6's work, not a
   precondition for it — see "LumiBot backtest-mode boundary" below. An entry
   here briefly marked this resolved on 2026-07-26 on the strength of a
   withdrawn first pass; the resolution recorded now rests on the Opus review
   and owner acceptance instead.
2. ~~VectorBT license note.~~ **Resolved 2026-07-26 (PR 5)** — see
   "VectorBT status" above; superseded by the explicit owner approval
   recorded there.

### LumiBot backtest-mode boundary (design selected 2026-07-26; **accepted 2026-08-01**)

**Status: RESOLVED.**
`docs/adr/0009-lumibot-backtest-distribution-boundary.md` is **Accepted** —
the repository owner reviewed the architecture review and feasibility spike and
selected Option B. The pre-step is complete and PR 6 is unblocked (not
started).

`backtest_runtime/` does not exist yet, and its absence does **not** block
PR 6. Creating the directory, its installable `pyproject.toml`, its tests, and
its blocking CI job are PR 6 acceptance criteria (ADR 0009 Decision 4) — merge
conditions, not preconditions.

**Correction to the first pass (2026-07-26).** An earlier version of this
section recorded a *different* decision — a second in-process import boundary
at `src/trading_research/backtesting/lumibot_backtest/` — and declared the
item resolved. That pass ran under **Sonnet**, not the **Opus review**
`MASTER_PLAN.md`'s "Pre-step before PR 6" row requires, reasoned from a stale
`lumibot==4.5.74` local installation rather than the repository's
`4.5.78` pin, and ran no feasibility spike. The required Opus review has now
run, with a pinned-version spike, and found the earlier proposal's premises
false. Both the decision and the "resolved" status are withdrawn.

**Decision (accepted 2026-08-01):** LumiBot backtest mode gets
its own isolated, credential-free distribution, `backtest_runtime/` — a third
top-level package beside the main project and `paper_runtime/`, with its own
`pyproject.toml`, its own explicitly declared `requires-python`,
`lumibot==4.5.78` as a base dependency, no broker credentials, no live
submission operations, a deterministic file-based fixture/result contract,
dedicated tests, and a blocking CI job.

**Do not** put a LumiBot import in the main source tree, and **do not** add
backtest operations to `paper_runtime`'s credentialed protocol.

Full review, decision matrix, and raw evidence:
`docs/library-migration/pre-step-06/EVALUATION.md` and `spike_output.txt`.

**Reasoning — what the feasibility spike measured** (clean venv, exactly
`lumibot==4.5.78`, Python 3.11.15, network patched to fail closed, every
`os.environ` read recorded):

1. **`import lumibot` looks for broker credentials unconditionally.** 277
   distinct environment variables read at import, **64 of them
   credential-named** — `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `IB_PASSWORD`,
   `COINBASE_PRIVATE_KEY`, `SCHWAB_APP_SECRET`, and `ANTHROPIC_API_KEY`, which
   this repository genuinely uses. There is no backtest-only import path that
   avoids this, so the requirement imposed on `backtest_runtime/` is **not**
   "zero credential reads" — it is that none of those reads finds a value (see
   the five-part proof set in ADR 0009 Decision 2).
2. **With credentials visible, an offline backtest opens a live broker
   connection.** The run produced **177 blocked outbound attempts to
   `paper-api.alpaca.markets:443`** (696 in an earlier identical run — a
   background retry thread drives the count), with LumiBot logging `Waiting
   for the socket stream connection to be established`. With credentials
   scrubbed: **zero** attempts and byte-identical results. The connection
   contributes nothing to the backtest; it is pure side effect.
3. **LumiBot loads `.env` from the current working directory** at import
   (`lumibot/credentials.py::find_and_load_dotenv`), walking *upward* from
   both the script directory and the CWD to the filesystem root. This
   repository's `.env.example` documents exactly `ALPACA_API_KEY` and
   `ALPACA_API_SECRET` and `.env` is gitignored, so an operator machine is
   expected to have one. An in-process import from a repo-root `pytest` run
   would load the operator's real paper-broker credentials and then connect —
   during a "deterministic offline backtest." That is exactly what ADR 0002
   exists to prevent, and it is what the first pass's proposal would have
   permitted.

   **The suppression mechanism is `LUMIBOT_DISABLE_DOTENV=1`, set before
   `import lumibot`, and nothing else works.** It is read at
   `credentials.py` module scope before any discovery runs and skips both
   walks entirely. Because the walks ascend to the filesystem root and their
   base directories (`sys.argv[0]`'s directory and `os.getcwd()`) are not
   configurable, running from an empty directory does *not* help. Proved with
   a sentinel `.env`/`.env.local` holding unique fake Alpaca values:
   `docs/library-migration/pre-step-06/dotenv_sentinel_output.txt`.
4. **The offline backtest itself works and is deterministic** —
   `PandasDataBacktesting` replayed a caller-supplied 10-bar DataFrame,
   repeated runs were bit-identical, and perturbing one input bar moved the
   result, proving the bars are genuinely caller-supplied. So the capability
   PR 6 wants is real; only the *boundary* the first pass chose was wrong.
5. **Option C remains unavailable.** Re-verified at `4.5.78`: `pip install -e
   <root> lumibot==4.5.78` fails `ResolutionImpossible` on two independent
   walls — `litellm`'s exact `jsonschema==4.23.0` against this repository's
   `jsonschema>=4.26.0` floor (confirming D5 below at the current pin), plus
   a `google-genai`/`google-adk` conflict. In its own environment LumiBot
   resolves `jsonschema==4.23.0` and `pip check` is clean.
6. **Routing through `paper_runtime` (Option A) was evaluated concretely, not
   dismissed.** `paper-runtime.v2` caps one envelope at 65,536 bytes; measured
   DTO sizes are 218 B per `HistoricalBar`, 143 B per `BacktestDailyState`,
   226 B per `BacktestFill`. A 3-symbol × 2-year parity fixture is 329,616 B
   in (**5.0×** the cap) and 85,632 B out (**1.3×**), so chunking operations
   would be required. But the disqualifier is credential proximity, not size:
   `paper_runtime` is the one process authorized to reach a real broker, and
   finding 2 means every backtest there would run next to live credentials.

**Corrections to figures this document previously repeated:** the protocol is
`paper-runtime.v2` with **19 operations**, not "`paper-runtime.v1`, 9
operations"; and LumiBot 4.5.78 installs **309 packages, ~1.9 GB**, not the
"~140 transitive packages" carried forward from ADR 0001/0002.

**Collateral finding — the AST import boundary does not run in CI.**
`tests/unit/test_lumibot_adapter.py` begins with a module-level
`pytest.importorskip("lumibot")`, so
`test_no_lumibot_import_outside_runtime_package` **skips** under `main-tests`
(which installs `.[dev]` only). The claim, repeated in this document and in
`MASTER_PLAN.md`, that the constraint is "AST-enforced, not
documentation-only" is not true as things stand — the only boundary test that
actually runs is `tests/unit/test_runtime_client_no_lumibot_import.py`, which
checks an explicit list of 17 named files. PR 6 must move the tree-walking
test into a file that runs with LumiBot absent. Because `backtest_runtime/`
sits outside `src/trading_research/`, that test needs **no new permitted
directory** — under the selected design the AST rule gets stricter
enforcement rather than a new exception, which is a further advantage over the
withdrawn in-process proposal.

**Pre-step gates — all met (2026-08-01):**

```text
[x] Opus architecture review complete
[x] pinned-version feasibility spike passes
[x] sentinel-.env suppression proof passes
[x] ADR 0009 accepted by the repository owner
```

PR 6 is **merged** (`bbd7a1f`, PR #18; branch
`migration/06-lumibot-backtest-adapter`; see `STATUS.md` "Completed work (PR
6)" for the full record). The reproducible install path (`backtest_runtime/`
with its `pyproject.toml`, tests, and blocking `backtest-runtime-tests` job)
that ADR 0009 Decision 4 required has been delivered.

---

## D5 — Root `paper` extra removed: `paper_runtime` is the sole LumiBot dependency authority

**Discovered 2026-07-26, during PR 1 dependency-resolution validation.**

> **Scope note.** "Sole authority" describes the repository's current state.
> Under the accepted ADR 0009 there will be two isolated authorities once PR 6
> adds `backtest_runtime/pyproject.toml`. The invariant that governs in both
> states — the root `pyproject.toml` declares no LumiBot dependency, ever — is
> spelled out in "Reconciliation with the backtest-mode boundary" below.

`pip install -e ".[paper]"` in the root `pyproject.toml` fails with a hard
`ResolutionImpossible`, not a soft version downgrade: LumiBot's
`google-adk[extensions]` requirement pulls in `litellm`, which pins
`jsonschema==4.23.0` **exactly** across every `litellm` release compatible
with LumiBot's `4.5.x` series, while this repository's base dependencies
require `jsonschema>=4.26.0`. No version combination satisfies both
constraints. This is confirmed true for every published `lumibot==4.5.x`
release (`4.5.0` through the current `4.5.78`), not a regression introduced
by this PR's patch bump — it reproduces identically against the previous
`4.5.74` pin.

`docs/adr/0002-isolated-lumibot-runtime.md`'s Context section already
flagged this exact risk ("downgrades this repository's own pinned
dependency floor `jsonschema>=4.26.0` → `4.23.0`") as one motivation for
moving LumiBot behind the `paper_runtime` process boundary. At that time it
was a silent downgrade pip's resolver could still complete; it has since
become an unconditional failure now that `jsonschema>=4.26.0` is a hard
floor with no lower ceiling.

**Decision:** remove the `paper` extra from the root `pyproject.toml`
entirely rather than retain a declared, publicly-advertised install target
that cannot resolve. `paper_runtime/pyproject.toml` becomes the sole
LumiBot dependency declaration in the repository. This does not contradict
ADR 0001 or ADR 0002:

- ADR 0001 constrains where LumiBot may be *imported* (`runtime/lumibot/`
  only, AST-enforced) — it does not require the root project to declare an
  installable extra for that import to be legal.
- ADR 0002 Decision 1 already states "the main trading-desk process's
  `pyproject.toml` gains zero new dependencies from this milestone" and
  isolates LumiBot's ~140-package transitive footprint to `paper_runtime/`.
  Removing the root `paper` extra is a direct continuation of that decision,
  not a reversal of it.

`src/trading_research/runtime/lumibot/adapter.py` and
`tests/unit/test_lumibot_adapter.py` are unchanged in behavior: the test
still guards itself with `pytest.importorskip("lumibot")` and skips cleanly
when lumibot is not importable. A developer who wants to exercise that file
locally installs `lumibot` into a scratch virtualenv by hand; this is no
longer offered as a `pyproject.toml`-declared extra since it cannot resolve
against this repository's own floor.

### Reconciliation with the backtest-mode boundary (2026-07-26; ADR 0009 accepted 2026-08-01)

ADR 0009 is **accepted**, so this section's phrase
"`paper_runtime/pyproject.toml` becomes the sole LumiBot dependency
declaration in the repository" describes the repository's **current** state
only. It stops being literally true the moment PR 6 creates
`backtest_runtime/pyproject.toml`, which declares `lumibot==4.5.78` as well.

* **Today:** `paper_runtime/pyproject.toml` is the only LumiBot declaration in
  the repository.
* **After PR 6:** there are exactly two — `paper_runtime/` and
  `backtest_runtime/` — each owning an isolated declaration in its own
  separately-installed environment.

Neither state changes what the root `pyproject.toml` declares, which is
nothing. The narrower rule below is the one that governs, and it holds in both
states:

```text
The root pyproject.toml declares no LumiBot dependency and no extra
containing one, ever.

Every LumiBot declaration lives in a separately installed distribution that
is never resolved in the same environment as the main project:
    paper_runtime/      credentialed, live/paper broker submission (ADR 0002)
    backtest_runtime/   uncredentialed, offline backtesting  (ADR 0009, Accepted;
                                                              created by PR 6)

Both pin the same exact version. A test asserts they do not drift apart.
```

The substantive commitment being preserved is ADR 0002 Decision 1 — "the main
trading-desk process's `pyproject.toml` gains zero new dependencies" and
LumiBot's transitive footprint is never absorbed by the main environment.
That commitment is **strengthened**, not weakened, by ADR 0009: the withdrawn
in-process proposal would have put a LumiBot import inside
`src/trading_research/` with no declared dependency at all, whereas a second
isolated distribution keeps the main environment exactly as clean as it is
today while making the backtest dependency declared, resolvable, and
CI-verified for the first time.

"Sole dependency *authority*" in the sense that mattered when it was written —
"the main project does not own, declare, or install LumiBot" — remains true
without qualification.

---

## D6 — PR 7 parity comparison: bounded Option B

**Decision date:** 2026-08-02 (PR 7). Revised twice the same day: from Option A
to a bounded Option B in the first review round, and then **re-derived in the
second review round from LumiBot's authoritative order-lifecycle timestamps**
rather than from inferred fill sessions. Option B is retained, and the
justification below is the one that survived the authoritative evidence. The
superseded records are not restated.

### What the first pass got wrong

PR 7 initially took **Option A** — express the legacy run as the narrowest
`backtesting/engine.py` equivalent and change nothing in `backtest_runtime/` —
and claimed its `case_a_buy_and_hold` matched the reference strategy exactly. It
did not: `backtest_runtime` entered a session earlier and at a different price,
and every downstream number differed by that offset.

### How the booking session is established

The first two passes both reasoned about *when* a fill happened from indirect
evidence — the callback that reported it, and the bar whose open matched its
price. Neither is authoritative, and they disagree with each other. LumiBot does
keep an authoritative record: its broker stamps every order event with
`data_source._datetime` at the moment the event is processed, in the trade-event
log (`lumibot/brokers/broker.py`). Reading it settles the question:

| clock | case A | case F |
|---|---|---|
| broker trade-event log (**authoritative**) | 2024-01-03 @ 100.5 | 2024-01-04 @ 101.0 |
| `on_filled_order` callback | 2024-01-04 | 2024-01-05 |
| first iteration with changed cash | 2024-01-04 | 2024-01-05 |

The mechanism is in `strategy_executor.py::_process_pandas_daily_data`, which
runs one session as `_update_datetime(session)` → `_on_trading_iteration()` →
`process_pending_orders()`. The order is submitted and filled inside the **same**
session, with the broker's clock unmoved; the strategy is told on its next
iteration. So the two lagging clocks are observation delay, not execution delay,
and the booking session is the submission session.

`docs/library-migration/pr7/probe_lumibot_fill_timing.py` records all three
clocks for three fixtures; the transcript is `results/probe_output.txt`.

### Why Option A is impossible

With the booking session established, both engines' floors are structural and
one session apart:

* **LumiBot** cannot submit before the **second** bar — `_process_pandas_daily_data`
  seeds its cursor with the first session strictly after `backtesting_start` — and
  books the fill in the submission session. Earliest booking: **bar 2**.
* **The legacy engine** cannot enter before the **third** bar: a signal is eligible
  only on the session *after* `generated_after_session`, and `average_true_range`
  needs `atr_period + 1` bars before that. With the smallest legal `atr_period`
  of 1, `generated_after_session` can be no earlier than bar 2, so the entry can
  be no earlier than **bar 3**.

The second review round asked specifically whether a fixture with intentionally
matching consecutive opens could close the gap under Option A. It cannot: matched
opens would make the two fill *prices* equal while leaving the two *booking
sessions* one apart, and the exact case is required to agree on the authoritative
fill date, not only on the price. The gap is a property of the two execution
loops, not of the data, so no fixture and no configuration removes it. Option B
stands.

### The bounded extension

`backtest_runtime`'s reference strategy is now **v2**, adding exactly one
control:

```text
strategy.entry_after_session : null | "YYYY-MM-DD"
```

`null` reproduces v1 behavior exactly — submit on the first iteration with a
resolvable price. A date defers that same single buy to the first iteration
strictly after it. That is the whole extension.

**Versioned, not defaulted.** `SCHEMA_VERSION_INPUT` becomes
`backtest_runtime.input.v2` and `REFERENCE_STRATEGY_ID` becomes
`backtest_runtime.reference_strategy.v2`. `contract.py` rejects both unknown
*and* missing strategy fields, so a v1 document is not a valid v2 document and
is told so explicitly rather than silently defaulted. The field participates in
`strategy_digest`, so two runs that differ only in entry timing have different
`run_configuration_checksum` values.

**Boundary.** The rule is that at least one *iterable* session must remain
strictly after the delay, so the order can be both submitted and filled. The
domain is `bars[1:]`, because the first bar is never a trading iteration;
submission and booking are the same session, so one such session is both
necessary and sufficient. A value on the penultimate session is therefore
accepted, and `test_entry_on_the_last_session_is_reported_and_not_silently_dropped`
proves that accepted input produces a real fill, a real end position and a
reduced final cash rather than a document that finishes flat.

**What was deliberately not added.** No sell, no stop, no target, no second
order, no order type, no scheduler, no data fetcher, no broker interaction, no
multi-symbol, no sizing rule. `benchmark_asset=None` and
`analyze_backtest=False` remain hardcoded, and every ADR 0009 Decision 2
credential-, network-, and isolation-safety property is untouched and still
asserted by that distribution's own blocking test suite (`75 passed`, including
`test_entry_timing.py`'s explicit "no sell and no second order" assertion).

### The reporting fix that came with it

Reference strategy v1 reported two things from clocks that lag the broker's:
each fill's `market_date` (from `on_filled_order`) and each daily state (sampled
in `on_trading_iteration`, before that session's orders are processed). Both are
now taken from the broker's own event log, and the result schema is bumped to
`backtest_runtime.result.v2` — the field names are unchanged but their meaning
is not, so a v1 consumer reading a v2 document would silently mis-date every
fill. Each session's state re-applies the fills the broker stamped with that
session, and two invariants are checked as hard errors: each session's observed
balances must equal the reconstruction as of the previous reported session, and
`observed cash + observed quantity × mark price` must equal the `portfolio_value`
LumiBot reported. This changes no trading behavior — it changes which clock the
adapter believes.

### What it bought

`case_f_exact_entry_parity` pairs `entry_after_session = 2024-01-03` with the
legacy engine's earliest possible entry. Both engines book the entry on
**2024-01-04 at 101.0 for 10 shares** — each from its own authoritative record,
neither inferred from a price — and agree exactly on final cash (98 990), final
equity (99 992), final value, end position (10 @ 101.0), maximum drawdown
(−0.00018), and on cash, equity, unrealized P&L, realized P&L and drawdown for
**every co-dated session, the entry session included**. The comparator asserts
all fifteen dimensions, excludes no session, and exits non-zero if any fails.

`D4` and `D15` are consequently **reclassified from adapter defects to library
semantic differences**: the observation lag is a real and permanent property of
LumiBot's execution loop, which the legacy engine has no equivalent of, and it
was only ever an adapter defect because v1 published the lagging clock as truth.
The comparator records both under `RESOLVED_BY_REFERENCE_STRATEGY_V2` and exits
non-zero if either is emitted as a difference again.

The other five cases keep `entry_after_session = null`, so the default-timing
behavior PR 6 shipped remains measured and reported rather than being replaced
by the extension.

### Classification semantics, corrected in the same round

"Unsupported requirement" is reserved for a case **neither** side can express.
Fees/slippage and realized-P&L/exit support are capabilities the legacy engine
has and `backtest_runtime` does not, so they are **adapter capability defects**,
not unsupported requirements — the requirement is demonstrably supportable
because one side already supports it. The comparator carries a subcategory
(`BEHAVIOR` — reports a value that contradicts its own run; `CAPABILITY` —
cannot express something the legacy engine can) and fails if a `CAPABILITY`
difference is labelled `UNSUPPORTED`.

After the second round the tally is **13 old-engine defect / 14 adapter defect
(0 behavior, 14 capability) / 93 intentional library semantic difference / 0
unsupported requirement**. The behavior subcategory is now empty because the two
differences that occupied it, D4 and D15, were the reporting defects v2 fixed.

### Binding results to fixtures

The comparator recomputes each fixture's canonical bar checksum itself and
requires both result documents to carry it. Two stale results agree with each
other perfectly, so mutual agreement proves nothing about currency; only
comparison against the fixture does. A regression test changes one fixture's
*volume* — a field that alters no number either engine computes — and asserts
the comparator rejects the previously valid results.

### Consequence for PR 8

The exact case establishes that, on one identical buy-and-hold, the two engines
agree on every economic number. It does **not** establish that
`backtest_runtime` could replace `backtesting/engine.py`: the adapter capability
defects, plus the legacy engine's mandatory risk exits, are the list of things
that would have to be built first. PR 8 is the gate that weighs them.
**Weighed and decided 2026-08-02 — see D7.**

---

## D7 — PR 8 removal gate: the custom backtest engine is not removed

**Decision date:** 2026-08-02 (PR 8, branch
`migration/08-backtest-removal-decision`). Full reasoning and the source
evidence behind every claim here:
`docs/library-migration/pr8/DECISION.md`. Input:
`docs/library-migration/pr7/PARITY_REPORT.md`.

**Status:** decided. Status quo preserved, so **no superseding ADR is required
or drafted** — under this document's governing principle an ADR is needed to
*remove* a gated component, not to decline to. ADR 0009 is untouched and stays
Accepted.

### The five rulings

**Revised 2026-08-02 (PR #20, review round).** Ruling 5 is new and the "Why"
below is re-derived: an earlier revision of this record claimed the legacy
engine enforces "no bar may be used before it was knowable". It does not. The
verdict is unchanged; the reasoning no longer rests on that property.

1. **`src/trading_research/backtesting/engine.py` and `models.py` are NOT
   approved for removal.** They remain authoritative indefinitely — not
   "pending a later parity PR". `REMOVAL_MANIFEST.md`'s conditionally-eligible
   row is closed as *not approved*, so no unresolved backtest target reaches
   PR 17 or the PR 18 audit, and PR 17 removes nothing on account of PR 8. The
   engine is added to `PRESERVATION_MANIFEST.md` with its invariant named.
2. **`backtest_runtime/` is kept** in the role `REMOVAL_MANIFEST.md` already
   defined for this outcome — an additional, **non-replacing** option, narrowed
   here to *an independent offline cross-check and parity harness with no
   execution authority and no callers in `src/`*. ADR 0009's third possibility
   (keep the engine, delete the distribution) is not taken. A review trigger is
   recorded so "keep" does not become permanent by default: if
   `backtest-runtime-tests` needs unplanned maintenance twice in succession,
   the distribution's value is re-argued rather than absorbed.
3. **Three legacy-side items become mandatory follow-ups**, because keeping the
   component turns them from "defects in something we may delete" into live
   bugs in the authoritative implementation — D17 (run identity ignores the bar
   dataset), the `backtest_orders` table that is created and never written, and
   the **run-level-only availability check** (ruling 5). All three are behavior
   changes to the legacy engine and get their own PR with its own review; none
   is fixed in PR 8. See the follow-up row in `MASTER_PLAN.md`.
4. **The drawdown/peak-seeding question (PR 7's D13 + D1) is not resolved, by
   design** — nothing in the repository is currently wrong, since each engine is
   self-consistent for the run it reports. It is recorded as a **precondition on
   any future replacement proposal**, which must state what its running peak is
   seeded with and which session its state series starts on. This is not
   cosmetic: the legacy engine gates entries on `max_drawdown_fraction`, so a
   replacement that disagrees about drawdown disagrees about which entries are
   allowed.
5. **The legacy engine's point-in-time enforcement is run-level, not
   per-session — recorded as fact, not as a reason.** `HistoricalBar` trusts a
   caller-set `point_in_time_safe` flag and only checks that `available_at` is
   timezone-aware (`models.py:26-30`); `FixtureHistoricalDataProvider` filters
   `available_at` against whatever `as_of` it is given
   (`data_provider.py:25-30`); `run_backtest` supplies exactly one `as_of` for
   the entire run — `end_date 23:59:59 UTC` — and its own guard repeats that
   same cutoff (`engine.py:140-149`), after which the bars are consumed at every
   simulated session with no further filtering; and
   `strategy_signal_to_entry_signal` reduces `data_as_of` to a date
   (`strategies/backtest_adapter.py:44`). A bar available *after* a signal or
   session but on or before the run's end is therefore usable in that earlier
   simulated period. What the engine does enforce is **session-date ordering**
   (entry only after `generated_after_session`, entry ATR only from bars at or
   before it — `engine.py:164-171`, `engine.py:303-306`), which is a different
   and weaker property. Closing the gap is a legacy-engine change tracked in
   `MASTER_PLAN.md` row 8a; it is **not** attempted in PR 8, and it is **not**
   an argument for replacement, since `backtest_runtime`'s six-field bar has no
   availability axis at all.

### Why

Not because the two engines disagreed — on `case_f_exact_entry_parity` they
agree on every economic number. Because of what a replacement would have to
carry, and because two of those things are not feature gaps:

- **`Decimal` versus `float` is an accounting boundary**, consistent with D1's
  preservation of exact accounting — PR 7's numeric bounds are the right
  instrument for asking whether two runs agree, not a licence to make the float
  side authoritative.
- **The engine shares `calculate_partial_close_quantity` with
  `paper_books/lifecycle_state.py`.** Re-implementing exits inside a LumiBot
  strategy would fork safety-adjacent arithmetic away from the preserved
  accounting layer — and ADR 0009's boundary, which is what makes
  `backtest_runtime` safe, is exactly what forbids it from importing that code.

Those two, plus the capability list in `pr8/DECISION.md` §3 — mandatory ATR
exits, ratcheting trailing stop, maximum holding period, partial-profit staging,
risk-fraction sizing with a cash cap, daily-loss and drawdown entry gates,
economic-event blackout, an 11-reason rejection trail, fees and slippage,
realized P&L, multi-symbol, and the persistence layer — carry the verdict on
their own. **Point-in-time safety is deliberately absent from this list**
(ruling 5): it was cited in the earlier revision and is withdrawn, because the
engine enforces one run-level cutoff rather than per-session knowability. That
correction weakens the preservation case honestly stated, but it does not
support replacement either — the runtime has no availability axis at all, so
migrating would delete the axis rather than complete it.

Also weighed: `backtesting/models.py` supplies the strategies layer's shared
type vocabulary (six modules import `HistoricalBar`/`EntrySignal`/
`BacktestResult`), so removal is not confined to the engine. And, stated
against the verdict rather than for it: `run_backtest` and
`run_strategy_backtest` have no non-test caller today, which is a weaker
preservation case than a load-bearing component would be — it also means
keeping the engine carries no operational risk.

### Reopening

Enumerated in `pr8/DECISION.md` §9: a replacement proposal that closes the
capability list *and* ruling 4's precondition on ordinary data; a per-session
point-in-time availability axis that is actually enforced on the runtime side
(a bar the legacy engine does not clear either — ruling 5); an
accounting-boundary decision for float equity series; or a LumiBot capability
change that makes those cheap. A superseding ADR is required only if the
proposal is removal.

## D8 — PR 9: one normalization contract, mirrored, with `ERROR` left deliberately unmapped

**Context.** PR 9 (`MASTER_PLAN.md` row 9) set out to "strengthen the LumiBot
runtime normalization contract." Reading the chain end to end found that no
single contract existed: the normalized order-status vocabulary was declared
independently in three places, and they disagreed.

**Ruling 1 — the contract is declared twice, on purpose, and drift-tested.**
ADR 0002 (reaffirmed by ADR 0009) forbids the main package and the isolated
`trading_paper_runtime` distribution from importing each other, so a shared
module is not available. The contract therefore lives in
`src/trading_research/runtime/normalization.py` and
`paper_runtime/src/trading_paper_runtime/normalization.py`, declaring
identical constants and identically-named helpers, and
`tests/unit/test_runtime_normalization_contract.py` AST-parses both files and
compares them literally. The two sides share a vocabulary and a set of rules;
they still share no Python type, and each raises its own error class. This is
the same technique the repository already uses for the LumiBot import
boundary — source inspection, not a cross-distribution import.

**Ruling 2 — `EXPIRED` and `CANCEL_REQUESTED` join the main-side vocabulary.**
`lumibot_gateway._ALPACA_STATUS_MAP` could emit both (Alpaca `expired` and
`pending_cancel`), and neither existed in
`execution/broker_snapshots.py::SUBMISSION_STATES`. Because
`update_submission_status` writes the status unvalidated while
`_row_to_submission` reads it back through a validating dataclass, a single
expired or cancel-pending broker order wrote a row that `get_submission` and
`list_unresolved_submissions` could never read again. `EXPIRED` is terminal;
`CANCEL_REQUESTED` is not. `list_unresolved_submissions`' hardcoded SQL
terminal list — a fourth copy, which also omitted `EXPIRED` — is now bound
from `TERMINAL_SUBMISSION_STATES`.

**Ruling 3 — two conformance levels, not two vocabularies.** The in-process
ADR 0001 adapter emits `execution/models.py::EVENT_TYPES`, a strict subset of
the contract: a `PaperExecutionEvent` has no `EXPIRED` or `CANCEL_REQUESTED`
because `adapter.submit()` is synchronous and always returns a resolved
outcome. That is why LumiBot's `expired` maps to `CANCELLED` there while the
runtime gateway maps Alpaca's `expired` to `EXPIRED`. The difference was
already correct; what was missing was anything asserting it stayed
deliberate. Both subset relationships are now enforced at import time.

**Ruling 4 — `ERROR` stays unmapped in `external_broker._state_from_order`,
and that is a decision, not an oversight.** An order the broker reports as
`stopped` or `suspended` normalizes to `ERROR`, for which
`_state_from_order` raises `UNKNOWN_BROKER_STATUS`. There is no safe
automatic ledger state for such an order, so failing closed and leaving it
for manual reconciliation is the correct posture, consistent with D3's hard
safety layer. PR 9 does **not** change that state machine; it pins the
coverage in a test, so the set of statuses `external_broker` refuses can
never widen or narrow without a deliberate edit to an assertion that names
the trade-off. Changing this stance is a separate, reviewed decision.

**Ruling 5 — normalization fails closed and never repairs.** No helper
defaults, coerces, or truncates. Concretely closed in this PR: a `None` limit
price no longer becomes the string `"None"` (which crashed the consumer's
`Decimal(...)` parse); a broker FILL activity missing `qty`/`price` no longer
becomes `"0"` (which would have booked free shares); a non-enum
`time_in_force` no longer silently becomes `DAY`; a float `NaN` fill price no
longer survives as `Decimal('NaN')` past a `<= 0` guard; and a fractional
broker quantity fails rather than truncating. This extends the posture
already established for TA-Lib in PR 4 and for the vectorized adapter in
PR 5 to the broker boundary.

**Ruling 6 — the broker-status polling path now actually reaches
`CANCEL_REQUESTED` and `EXPIRED`, not just the submission row.**
`services/sync_paper_orders.py::_sync_one` validated the polled status
against `execution/models.py::EVENT_TYPES` — the narrower, synchronous-
adapter-compatible vocabulary from Ruling 3 — not against
`BROKER_REPORTABLE_STATUSES`. Since `EVENT_TYPES` has no `CANCEL_REQUESTED`
or `EXPIRED`, the very first poll that observed either status raised
`UNKNOWN_BROKER_STATUS` and crashed the polling loop, before Ruling 2's fix
to the submission row could ever matter in practice. `_sync_one` now
validates against `BROKER_REPORTABLE_STATUSES`. `CANCEL_REQUESTED` needed no
further change: it is nonterminal, so the existing `delta > 0 or status in
TERMINAL_SUBMISSION_STATES` guard already skips building an event/result for
it, and the submission row update (which was never restricted to
`EVENT_TYPES`) leaves it on the unresolved-submissions queue for the next
poll — Ruling 3's "two conformance levels" holds exactly as designed once the
crash is removed. `EXPIRED` is terminal and has no `RESULT_STATUSES`
counterpart; rather than widen the domain vocabulary shared with the
synchronous ADR 0001 adapter (which can never emit `EXPIRED` at all — see
Ruling 3), `sync_paper_orders._DOMAIN_STATUS_PROJECTION` projects it to
`CANCELLED` for the `PaperExecutionEvent`/`PaperExecutionResult` the ledger
sees, while `paper_broker_submissions.submission_status` and the event's
`raw_status` both keep the true `EXPIRED` value — nothing is lost, only the
ledger-facing status is coarsened, exactly as an operator cancellation
already is. Covered by `tests/unit/test_sync_paper_orders.py`'s
`CANCEL_REQUESTED`/`EXPIRED`-before-fill/`EXPIRED`-after-partial-fill tests.

**Ruling 7 — `RuntimeClient` re-validates every response, not just the two
new typed dataclasses that sat unused beside it.** PR 9 originally added
`RuntimeOrderSnapshot`/`RuntimeAccountSnapshot`/`RuntimePositionSnapshot`
under `runtime/client/models.py` but never wired them into
`runtime/client/process_client.py::RuntimeClient` — every one of its typed
operations (`submit_order`, `get_order`, `cancel_paper_order`,
`list_open_orders`, `list_recent_orders`, `get_account`, `list_positions`)
still returned the runtime's raw dict untouched. The parsers now sit on the
request path itself: each method parses the raw response through the
matching `from_payload`, then serializes back through a new `to_dict()` to
the same wire-compatible shape callers already expected, so
`submit_credentialed_paper_order`/`sync_paper_orders`/`reconcile_paper`
needed no shape changes — only every value they read is now guaranteed to
have passed the boundary check instead of merely being available to a caller
that remembered to invoke it. `tests/unit/test_runtime_client.py` adds
fake-transport tests proving a malformed status, a non-finite fill price, a
fractional quantity, and a missing required field are all rejected with
`ProtocolViolationError` before reaching a service.

**Ruling 8 — constant/name equality is not decision equality; a shared
corpus closes that gap.** `test_runtime_normalization_contract.py` proves
both `normalization.py` files declare the same constants and the same
function names by AST comparison. It does not prove the two implementations
make the same accept/reject decision for a given input, or produce the same
canonical output — two independently maintained fail-closed rule sets can
still drift on an edge case (e.g. one side accepting `"1E+2"` and the other
rejecting it) without either drift test noticing. `tests/fixtures/
normalization_corpus.json` is one declarative list of (function, args,
accept/reject, canonical-output) cases, read as plain JSON — not a Python
import — by both `tests/unit/test_normalization_corpus.py` (against
`trading_research.runtime.normalization`) and `paper_runtime/tests/
test_normalization_corpus.py` (against `trading_paper_runtime.
normalization`). Each side catches its own `NormalizationError` subclass, as
Ruling 1 already permits; only the accept/reject verdict and the canonical
output are required to match.

**Ruling 9 — the remaining silent repairs in `lumibot_gateway.py` are
closed, and the two that survive as intentional defaults are now documented
contract rules with regression tests, not unstated coercions.** Reviewed
against Ruling 5's "no helper defaults, coerces, or truncates" claim, three
more repairs existed in `_order_to_snapshot`/`get_account`: `order.filled_qty
or 0` turned a genuinely missing `filled_qty` into the same value as a
broker reporting zero shares filled; a missing `submitted_at`/`updated_at`
was replaced with `datetime.now(timezone.utc)` at translation time,
fabricating a broker timestamp this process never observed; and a missing
account `currency` was defaulted to `"USD"`. All three now fail closed
(`normalize_exact_int`/`normalize_timestamp_string` on the raw attribute,
and `AccountSnapshotPayload.__post_init__`'s existing `currency` check with
no default upstream of it). Two defaults remain, deliberately, each now
documented in-line and pinned by a regression test rather than left as an
unstated coercion: an *absent* `time_in_force` still normalizes to `DAY`,
because this runtime only ever submits DAY LIMIT orders (its own capability
advertisement enforces that), so an order with no `time_in_force` attribute
at all can only be one this runtime itself created; and a naive
(tzinfo-less) broker timestamp is still treated as UTC by
`normalize_timestamp_string`, because Alpaca's paper API reports UTC — this
is the contract's stated timestamp rule, not an unstated guess, and applies
identically on both sides of the process boundary. **The `time_in_force`
default described here was narrowed by Ruling 10, then removed entirely by
Ruling 13 — see those rulings for the current behavior.** The timestamp
rule is unaffected and remains current.

**Ruling 10 (Milestone 11 follow-up 2; narrowed further by Ruling 13 below
— retained for the decision history) — the `time_in_force` default from
Ruling 9 was narrower than claimed; it now applies only to orders this
runtime can prove it created.** Ruling 9's "this runtime only ever submits
DAY LIMIT orders" justification is true for `submit_order`/`get_order`, but
`list_open_orders`/`list_recent_orders`/`get_order_by_broker_id` are
account-wide broker reads that can return an order placed manually, or by
another application, against the same paper account — for such an order the
DAY assumption does not hold. `_order_to_snapshot` now defaults an absent
`time_in_force` to `DAY` only when the order's own `client_order_id` is
inside this project's own id namespace (`"intent-"` for in-process ADR 0001
orders, `"epb-"` for external paper-book orders —
`_is_runtime_owned_client_order_id` in `lumibot_gateway.py`); for any other
`client_order_id`, an absent `time_in_force` now fails closed like every
other malformed field. A present-but-unrecognized value still fails closed
regardless of ownership, unchanged from Ruling 9.

**Ruling 11 (Milestone 11 follow-up) — `RuntimeOrderSnapshot` now matches
`OrderSnapshotPayload`'s behavior instead of a narrower subset of it.**
Before this follow-up, `RuntimeOrderSnapshot` (the main-process parser for
the synchronous `submit_order`/`get_order`/`list_open_orders`/
`list_recent_orders` responses) accepted a non-positive `quantity`, treated
`submitted_at`/`updated_at` as opaque required strings instead of validating
them as timestamps, and silently dropped `book_id`/`symbol`/`side`/
`limit_price`/`time_in_force`/`account_fingerprint` from its `to_dict()` even
though the runtime always sends them (`OrderSnapshotPayload.to_dict()`
includes all of them). It now rejects `quantity <= 0`, canonicalizes both
timestamps through `normalize_timestamp_string`, and preserves the six
previously-dropped fields (validated: `side` through `normalize_side` when
present, `limit_price` through the client's positive-decimal check,
`time_in_force` through `normalize_time_in_force` — required, since the
runtime always sets it — and `book_id`/`symbol`/`account_fingerprint` as
optional strings).

**Ruling 12 (Milestone 11 follow-up) — the enriched external-order wire ops
are now re-validated at the `RuntimeClient` boundary, not left as raw dicts
until `external_broker.py`'s own checks run.** `get_order_by_client_order_id`,
`get_order_by_broker_order_id`, `cancel_external_order`, and
`list_recent_external_orders` all return the same enriched shape
(`dispatcher._external_order_dict`: `RuntimeOrderSnapshot`'s fields plus
`provider`/`environment`/`rejection_code` scoping) and now parse it through a
new `ExternalOrderSnapshot`; `list_order_fills` parses each entry through a
new `ExternalFillSnapshot`; `get_external_positions` and
`get_external_account_snapshot` parse their book/account-scoped envelopes
through new `ExternalPositionsSnapshot`/`ExternalAccountSnapshot` wrappers
(the latter reusing `RuntimeAccountSnapshot` for its shared fields). As with
`RuntimeOrderSnapshot`, these parsers validate structure and type (status
vocabulary, side/time-in-force vocabulary, finite/positive decimals, exact
integers, canonical timestamps) and leave `external_broker.py`'s own
business-rule checks (matching the approved intent, paper-endpoint scoping)
in place downstream — a malformed nested field now fails with
`ProtocolViolationError` before it can reach `paper_books` at all, rather
than surfacing later as an ad hoc `MALFORMED_RUNTIME_RESPONSE`.

**Ruling 13 (Milestone 11 follow-up 3) — a `client_order_id` namespace
prefix is not proof of ownership; the `time_in_force` default is removed
entirely, not narrowed further.** Ruling 10's `_is_runtime_owned_client_order_id`
treated an `"intent-"`/`"epb-"`-prefixed `client_order_id` as evidence this
runtime created the order. But `client_order_id` is broker-echoed data on
an account-wide read (`list_open_orders`/`list_recent_orders`) — nothing
stops a manually placed order, or one from an unrelated application against
the same paper account, from coincidentally or deliberately using an id in
the same shape. A namespace prefix is a pattern match, not a trust
boundary: it carries no cryptographic or transactional binding to "this
runtime submitted this exact order." `_order_to_snapshot` no longer
defaults an absent `time_in_force` under any condition — `submit_order`
and `get_order` do not need the default in practice (this runtime always
requests `TimeInForce.DAY` explicitly, so Alpaca's response echoes it back
for any order this runtime actually just submitted), and
`list_open_orders`/`list_recent_orders`/`get_order_by_broker_id` have no
trusted submission context to fall back on at all. `_is_runtime_owned_client_order_id`
and `_time_in_force_with_ownership_default` are removed;
`normalize_time_in_force` is called directly on the raw broker attribute
and fails closed on `None` like every other field.

**Ruling 14 (Milestone 11 follow-up 3) — the Milestone 11 external-order
submission and lookup wire ops are now fully validated at the
`RuntimeClient` boundary.** Two gaps remained after Ruling 12: (1)
`submit_limit_order` returned `SUBMIT_LIMIT_ORDER`'s raw response
unparsed — it now goes through the same `ExternalOrderSnapshot` used for
`cancel_external_order`/`list_recent_external_orders`, with no change to
its no-retry behavior (`SUBMIT_LIMIT_ORDER` was already absent from
`_RETRYABLE_ON_TIMEOUT`). (2) `get_order_by_client_order_id`/
`get_order_by_broker_order_id` trusted the `{"found": ...}` envelope itself
— a missing or non-boolean `found`, a not-found response that failed to
echo the requested `book_id`/`client_order_id`, or a contradictory
`found`/`order` combination would previously fall through `if not
result.get("found"): return None` and be read as an authoritative broker
NOT_FOUND. That distinction matters beyond the immediate call:
`external_broker.py::_run_reconciliation` treats an *exception* from this
lookup as ambiguous (`authoritative=0`, cannot unlock a retry) but a
*return value of `None`* as genuine, authoritative evidence
(`authoritative=1`) that `retry_external_paper_order` accepts as
sufficient to allow a second submission of the same order — so a
runtime that could produce a malformed envelope could otherwise have
forged retry-authorizing evidence. New `parse_client_order_lookup_response`/
`parse_broker_order_lookup_response` validate each documented envelope
shape exactly and raise `ProtocolViolationError` on anything else,
which `_run_reconciliation`'s existing `except Exception` handling already
downgrades to non-authoritative — closing the gap with no change needed on
the `paper_books` side. `tests/unit/test_external_paper_broker.py::
test_malformed_lookup_response_cannot_create_authoritative_not_found_or_unlock_retry`
is the end-to-end regression.

**Ruling 15 (review fix on commit `3193b0b`) — a structurally valid *found*
lookup response was not bound to the identifiers that were requested, and
the repeat-submission path never validated it against the approved
intent.** Ruling 14's envelope parsers validated that a `found: true`
response parsed as a well-typed `ExternalOrderSnapshot`, but not that the
enclosed order actually *was* the order asked for: `parse_client_order_
lookup_response`/`parse_broker_order_lookup_response` accepted an order
with a different `book_id`/`client_order_id` (or `book_id`/
`broker_order_id`), or one claiming `environment: "live"` / an unrelated
`provider`. Separately, `submit_external_paper_order`'s duplicate-submit
branch (`current["new_state"] not in (STATE_PREVIEWED,)`) returned a found
order straight to its caller without ever calling `_validate_order_
response` — every other broker-response path in this module runs that
check, but this one, added by Milestone 11.2, did not. Combined, a runtime
bug or a compromised runtime process could report an unrelated book's
order, a foreign account's order, or a live (non-paper) order as this
book's successful existing submission. Fixed on both sides: the two lookup
parsers now reject a *found* response whose `book_id`/`client_order_id`
(or `book_id`/`broker_order_id`) does not match the request, or whose
`provider`/`environment` is not paper-scoped, before returning it; and the
duplicate-submit branch now calls `_validate_order_response(order, intent,
client_order_id, fingerprint, now)` — the same check reconciliation and
cancellation already run — before returning, closing the
account-fingerprint/quantity/price/side/time-in-force gap the parser layer
cannot close on its own (it has no access to the expected account
fingerprint or the approved intent). New tests: 9 `RuntimeClient` cases
covering mismatched `book_id`/`client_order_id`/`broker_order_id` and a
claimed live environment/unrelated provider for both lookup methods (plus
two "correctly matched" happy-path cases); 3 `external_broker.py`
regressions proving the duplicate-submit path now rejects a foreign
account fingerprint, a live environment, and a mismatched quantity instead
of reporting success.

---

## D9 — PR 11: proves parity, does not replace `evaluation/metrics.py`

**Context.** `MASTER_PLAN.md` row 11's terse description, "Replace
`evaluation/metrics.py` formulas with `quantstats-lumi` +
`empyrical-reloaded`," reads the same way rows 3 and 4 did before those PRs
ran — and PR 3/PR 4 both replaced their target formulas immediately,
deleting the custom implementation in the same PR. Read alone, row 11
suggests PR 11 should do the same to `sharpe_ratio`/`sortino_ratio`/
`max_drawdown`/`calmar_ratio`/`cumulative_return`. Three other planning
documents disagree, consistently, with each other and not just in passing:

1. `REMOVAL_MANIFEST.md`'s header states the *default* rule for every row:
   "a passing fixture-parity test in its assigned PR before deletion
   happens in the corresponding later PR. Until then, the current custom
   implementation remains authoritative." PR 3 and PR 4's rows carry an
   explicit, named override ("Done in PR 3, not deferred to PR 17,"
   "removed in PR 4 itself, not deferred to PR 17"). The analytics formulas
   row carries no such override — it still reads the un-overridden default,
   "PR 17 (parity proven in PR 11)."
2. `MASTER_PLAN.md` row 17 itself says it will "execute only
   removal-manifest entries that passed parity in PR 3/4/11/15." If PR 11
   already executed its own removal (like PR 3/PR 4), row 17 would have
   nothing left to execute for it — exactly as row 17 already treats PR 8's
   contribution as "resolved — contributes nothing." Row 17 listing PR 11
   as a source of pending removal work only makes sense if PR 11 stops at
   parity.
3. The logging-formatter row (PR 15) is worded identically to the analytics
   row ("PR 17 (parity proven in PR 15)"), and PR 15's own `MASTER_PLAN.md`
   row 15 description ("parity test for redaction behavior," not
   "delete `logging_config.py`'s formatter") reads as parity-only too — the
   same pattern repeated twice is a documented convention, not a one-off
   inconsistency.

**Ruling.** PR 11 proves fixture parity; it does not remove or replace
`evaluation/metrics.py`'s formulas. `evaluation/analytics_parity.py` is a
new, additive, non-authoritative module — `evaluation/metrics.py` is
untouched, remains the sole implementation every existing caller
(`research_comparison.py`, `paper_books/comparison.py`, `cli.py`) uses, and
stays authoritative until PR 17 decides whether and how to execute the
removal `REMOVAL_MANIFEST.md` already conditions on this PR's parity proof.
This mirrors PR 5's `vector_research/adapter.py` pattern (new, additive,
AST-import-boundary-enforced, zero production callers) more than it mirrors
PR 3/PR 4's in-place replacement, precisely because PR 3/PR 4 are the two
rows the manifest explicitly marked as an exception to its own default.

**Numeric findings supporting the parity claim**
(`docs/library-migration/pr11/comparison_output.txt`,
`evaluation/analytics_parity.py`'s module docstring for the full detail):

- `cumulative_return`, `sharpe_ratio` (both annualized and raw), and
  `sortino_ratio` (both annualized and raw) match `empyrical-reloaded`
  bit-for-bit or to floating-point noise, given `period="daily"` (252
  trading days/year, matching `ANNUALIZATION_TRADING_DAYS`) or
  `annualization=1` respectively.
- `max_drawdown` matches to ~1e-16 floating-point noise.
- `calmar_ratio` does **not** match `empyrical.calmar_ratio()` under any
  annualization setting tested — it applies a CAGR-style annualized-return
  numerator (compounding across `len(returns)` periods) rather than this
  repository's raw cumulative-return numerator, which does not fit
  independent per-recommendation returns the way it fits a fixed-frequency
  daily bar series. `calmar_ratio_parity` is composed from
  `cum_returns_final`/`max_drawdown` instead of calling
  `empyrical.calmar_ratio()` directly — a documented adapter composition,
  the same pattern PR 4 used for `macd`/`trix` over `talib.MACD()`/
  `talib.TRIX()`.
- Neither `empyrical.sharpe_ratio` nor `empyrical.sortino_ratio`
  special-cases a zero (or floating-point-noise-near-zero) variance/
  downside-deviation input — each returns a large finite float or `inf`
  rather than an explicit undefined signal. `analytics_parity.py`
  reproduces `evaluation/metrics.py`'s existing
  `math.isclose(..., abs_tol=1e-12)` fail-closed boundary rather than
  trusting either library's raw output for this case.
- `quantstats_lumi.stats.calmar` was evaluated and rejected for
  `presentation_summary()` too, for a sharper reason than a convention
  mismatch: it requires a real `DatetimeIndex` (its `cagr()` calls
  `.total_seconds()` on the index range) and raises `AttributeError` on the
  plain integer-indexed `Series` this repository's returns take the shape
  of.

**Scope not touched by this decision.** `hit_rate`, `average_return`,
`median_return`, `gain_loss_ratio`, `recommendation_to_fill_rate`, and
`group_by` have no `empyrical`/`quantstats-lumi` equivalent and are outside
`REMOVAL_MANIFEST.md`'s analytics-formulas row (which names exactly
`sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `calmar_ratio`,
`cumulative_return`) — PR 11 leaves them untouched in both
`evaluation/metrics.py` and `analytics_parity.py`.

## D10 — PR 12: Riskfolio-Lib evaluated, deferred (not adopted)

**Context.** `MASTER_PLAN.md` row 12 scoped PR 12 as evaluation only, bound
to the same "advisory only, never authoritative" pattern ADR 0003 established
for Claude's research overlay (also required of PR 2's Pydantic evaluation).
Two questions were open going in, per `DEPENDENCY_MATRIX.md` Section 6's PR 1
correction record: whether Riskfolio-Lib's license resolves OSI-compatibly,
and whether its dependency weight (and hard `vectorbt>=0.28.0` dependency) is
justified. Full detail in `docs/library-migration/pr12/EVALUATION.md`.

**License.** Re-verified live against the PyPI JSON API: `riskfolio-lib`
7.3.0 is BSD-3-Clause, `License :: OSI Approved :: BSD License` — unlike
VectorBT (D4), this needed no owner exception.

**Dependency weight and the VectorBT hard-dependency.** A wheel-only install
into a clean scratch virtualenv resolved 82 packages, `pip check` clean, no
source compilation. Its `vectorbt>=0.28.0` floor resolved to
**`vectorbt==1.1.0`** — the exact version already pinned by the `research`
extra's `vectorbt>=1.1.0,<1.2` (PR 5) — confirming no conflict with the
already-adopted dependency at Python 3.11.15 and 3.14.5rc1 only — the two
interpreters live-tested, live rather than by reading declared metadata
alone, both within VectorBT 1.1.0's declared `>=3.11,<3.15` range (Python
3.12 and 3.13 were not installed or tested and remain unverified); the same
`vectorbt>=1.1.0,<1.2` range cannot resolve on this repository's `>=3.10`
project-wide floor without also raising it to `>=3.11`, nor on Python
3.15+ without a future VectorBT upgrade. The closure includes several packages with no other purpose in this
repository: Jupyter widget support (`ipywidgets`, `anywidget`,
`jupyterlab_widgets`, `widgetsnbextension`), a second charting library
(`plotly`, alongside the existing `streamlit`), multiple QP/conic solver
backends (`clarabel`, `SCS`, `osqp`, `highspy`, `qdldl`), and `astropy`.

**Functional check.** A scratch smoke test
(`pr12/scratch_smoke_test.py`/`scratch_output.txt`) confirmed
`rp.Portfolio(...).optimization(...)` returns a plain `pandas.DataFrame` of
per-asset weights with no `submit_order`/`shares`/`quantity`/`order_type`/
`side` surface — structurally compatible with an advisory-only boundary, the
same shape VectorBT's `Portfolio` object required bounding in PR 5's
review-fix round (D4). Also surfaced: Riskfolio-Lib 7.3.0's own internal code
triggers a `cvxpy` 1.9.2 deprecation warning (its use of the now-deprecated
`*` matrix-multiplication operator) — not a functional blocker on the
version pair verified here, but worth re-checking before any future
adoption, since it originates inside Riskfolio-Lib's own code.

**Need.** `COMPONENT_MATRIX.md`'s "Portfolio optimization" row lists no
existing implementation — this would be a green-field capability, not a
migration off deprecated/abandoned code (unlike TA-Lib/empyrical-reloaded/
`exchange_calendars`), and no module under `src/trading_research/`
constructs a multi-position target allocation today.

**Ruling: defer, do not adopt.** Applying the same bar `DECISIONS.md` used
for Pandera/PyArrow ("no concrete current need exists" → Defer) rather than
Pydantic's bar (no existing code to compare against here) or VectorBT's
(an owner-approved exception consumed immediately by a scoped adapter):
Riskfolio-Lib is legally unblocked and technically installable without
conflict at Python 3.11.15 and 3.14.5rc1 only — the two interpreters
actually tested, both within VectorBT's declared `>=3.11,<3.15` range
(3.12/3.13 untested; not on this repository's `>=3.10` project-wide floor
without also raising it, nor on Python 3.15+ without a future VectorBT
upgrade) but not justified by any current consumer,
and even an adopted Riskfolio-Lib could not become
authoritative over sizing or risk decisions per the binding advisory-only
constraint. **`riskfolio-lib` is not added to any dependency declaration.**
No ADR is required — per the single-ADR rule already established in D2, an
ADR is needed only if adoption is recommended, and none is here. Re-evaluate
once a concrete portfolio-construction consumer is scoped; at that point,
also re-verify whether the `cvxpy` deprecation warning noted above has been
resolved upstream, and design the advisory-boundary adapter (analogous to
`vector_research/`'s import-boundary/`metric_source`-labeling pattern)
before any dependency is added.

## D11 — PR 13: SQLAlchemy/Alembic evaluated, deferred (not adopted)

**Context.** `MASTER_PLAN.md` row 13 scoped PR 13 as feasibility-and-ADR
only, requiring two questions to be *explicitly tested*, not just reasoned
about: (a) whether trigger-protected tables (append-only tables,
`real_orders`) can be restricted to SQLAlchemy Core statements only, on the
theory `DEPENDENCY_MATRIX.md` Section 5 recorded at PR 0 that "the ORM's
unit-of-work flush ordering and identity-map caching can mask a
trigger-rejected write"; (b) whether Alembic's branching revision graph can
be constrained to linear-only history matching
`storage/schema_version.py`'s existing monotonic ledger. Full detail in
`docs/library-migration/pr13/EVALUATION.md`.

**License and dependency weight.** Re-verified live against the PyPI JSON
API: SQLAlchemy 2.0.52 and Alembic 1.19.1 (a `alembic>=1.18,<1.19` scratch
pin resolved and tested 1.18.5, one minor version behind latest) are both
MIT, both OSI-approved, both light (SQLAlchemy's own core dependencies are
`typing-extensions`/`greenlet`; Alembic adds only `Mako` on top of
SQLAlchemy). `Requires-Python` for Alembic (`>=3.10`) matches this
repository's floor exactly; SQLAlchemy's (`>=3.7`) is well inside it. No
license or dependency-weight blocker exists for either package.

**Question (a), empirically tested — masking hypothesis withdrawn.** A
scratch reproduction (`pr13/scratch_trigger_orm_vs_core.py`) copy-pasted the
exact production trigger DDL for `real_orders` (fully reserved) and
`paper_book_cash_ledger` (append-only) and drove both via Core and ORM,
against a file-backed SQLite database so independent visibility checks use
a genuinely separate DBAPI connection, not the same connection an in-memory
`StaticPool` engine would silently reuse. Three adversarial cases went
beyond the original hypothesis: an unhandled failed flush (`PendingRollbackError` on the next
operation), a `cascade="all, delete-orphan"` DELETE (still a real,
trigger-rejected `DELETE`), and an ORM UPDATE on an already-loaded row
through the identity map (rejected identically; re-reading the mutated
attribute pre-rollback itself raises `PendingRollbackError`, and the value
post-rollback matches the database exactly). All seven cases failed closed:
no rejected INSERT object falsely transitioned to persistent; the
already-loaded UPDATE target correctly remained persistent while its
rejected attribute mutation was expired, and no attribute returned a
stale/masked value. This is **withdrawn as unsubstantiated** for
SQLAlchemy 2.0.52 across INSERT/UPDATE/cascade DELETE — a correction to
`DEPENDENCY_MATRIX.md` Section 5, not a reason to adopt. An eighth case
proves the Core-only boundary can be *enforced*: a `before_flush` guard on
the ORM `Session` class rejects a flush against either table pre-SQL,
through both a `sessionmaker()` session and `Session(bind=...)`, while Core
still works. A ninth case proves that guard's table policy is *complete*,
not just correct for these two tables: `TRIGGER_PROTECTED_TABLES` is
derived by scanning production schema modules for write-rejecting triggers
(50 tables, not 2) and the guard rejects
ORM writes pre-SQL against every one, including `paper_book_fills`,
`research_attempts`, `research_attempt_failures`, and
`research_cycle_provider_provenance_links`, omitted by the prior allowlist; re-deriving from production means a future protected table
cannot fall outside guard coverage. A tenth case then found and fixed a real
gap in the guard's *mechanism*: checking only each changed object's own
`__table__` misses a `relationship(..., secondary=protected_table)`
collection write (reproduced using `research_cycle_provider_provenance_links`
itself as the `secondary` table) and an ancestor table in a
joined-table-inheritance mapper; the guard now also inspects each mapper's
full table set and every relationship's `secondary` table, and both
adversarial paths are rejected pre-SQL. This does not cover ORM-enabled
bulk `update()`/`delete()` statements via `Session.execute()`, which bypass
`before_flush` entirely — an open gap, not claimed as closed. Core-only for
trigger-protected tables remains the recommendation regardless, as an
auditability preference with a proven, self-updating mechanism for
unit-of-work flush writes — not because the ORM is unsafe.

**Question (b), empirically tested — constrainable, but only with an added
guard.** A second scratch reproduction (`pr13/scratch_alembic_linearity.py`)
built a real, disposable Alembic environment and found Alembic resists a
*sequential* accidental branch by default (`CommandError: ... please
specify --splice`; `CommandError: Multiple head revisions are present
...`) only when the offending state is already visible in one script
directory when the guarded command runs. A ninth case proved the default
does *not* resist a *concurrent*-development branch: two independent
checkouts each create a revision off the same parent, neither sees the
other's file, both succeed without `--splice` or any error, and the
branch surfaces only once the two checkouts' files are combined. Neither
guard is
absolute: `splice=True` still creates a real branch, and `alembic merge`
converges a branch back to one head while leaving a merge revision (a tuple
`down_revision`, i.e. two parents) that is not a linear predecessor. A
custom gate (asserting exactly one head **and** no revision has more than
one child **and** no revision's `down_revision` is a tuple **and** no
revision has a non-empty `depends_on`) caught every branching and merge
case tested, including the case where "one head" alone would have looked
linear but was not. The `depends_on` check was added after two further
adversarial cases showed it was necessary, not optional: `depends_on` is a
dependency edge separate from `down_revision` that Alembic does not count
toward `get_heads()` or down-revision fan-out, so a revision with one or
several `depends_on` targets reports zero violations against a
`down_revision`-only gate while still introducing a graph edge
`schema_version.py`'s ledger has no counterpart for. **Conclusion: yes,
constrainable to linear-only history, but only by building and permanently
maintaining a gate that checks heads, down-revision fan-out, merge
revisions, *and* `depends_on` as a blocking CI check** —
`schema_version.py`'s `dict[int, ...]` ledger has no branch or dependency
concept to guard against in the first place, so this would be new
maintenance surface, not a like-for-like replacement.

**Need.** `COMPONENT_MATRIX.md`'s "Persistence" and "Migrations" rows
describe the existing hand-written `storage/*_schema.py` DDL modules and
`storage/schema_version.py`'s ordered-migration ledger as available for
evaluation, not as broken or unmaintained — unlike TA-Lib/empyrical-reloaded/
`exchange_calendars`, there is no abandoned or hand-rolled formula this
would replace, and no module today has a persistence or migration problem
the existing pattern does not already solve.

**Ruling: defer, do not adopt.** Applying the same bar already used for
Pandera/PyArrow/Riskfolio-Lib ("no concrete current need exists" → Defer):
both packages are legally unblocked and, after this PR's testing, more
technically de-risked than the PR 0 record assumed (the trigger-masking
concern is withdrawn), but adopting either would mean migrating roughly
twenty existing schema/repository modules — including every safety-critical
trigger-protected table — for no current capability gap, while adding a
maintenance obligation (the linear-only CI gate) that does not exist today.
**Neither `sqlalchemy` nor `alembic` is added to any dependency
declaration.** No ADR is required — per the single-ADR rule already
established in D2 and reapplied in D10, an ADR is needed only if adoption is
recommended, and none is here. Re-evaluate once a concrete need for a richer
persistence/migration layer is scoped; at that point, re-verify against
whatever Alembic release is then current (1.19.1 or later — not directly
tested here), build the linear-only gate before the first real revision is
committed rather than after, and separately test bulk Core insert/update
constructs (`sqlalchemy.dml.Insert`/`Update` used for multi-row bulk
operations), which bypass ORM events but not Core execution and were not
covered by this PR's ORM-session-focused testing.

## D12 — PR 14: APScheduler and Tenacity evaluated, deferred (not adopted); Tenacity gains a structural guard

**Context.** `MASTER_PLAN.md` row 14 reframed this evaluation at PR 0 from
"replace" to "coexist": could APScheduler v3 take over due-time triggering
while the existing lease/generation-fencing logic stays custom, and could
Tenacity's retry scoping be structurally restricted away from the ambiguous
external-broker-retry path (`external_broker.py`)? Full detail in
`docs/library-migration/pr14/EVALUATION.md`.

**Live re-verification (2026-08-30).** APScheduler 3.11.3 stable, MIT,
`>=3.8`; v4's newest release remains alpha-only (`4.0.0a6`), reconfirming
"not production-ready" from PR 0. Tenacity 9.1.4, Apache-2.0, `>=3.10`,
unchanged. Neither is a current dependency of this repository.

**APScheduler, empirically tested — defer, do not adopt.** Two independent
reasons. First, an architectural conflict: ADR 0005 Decision 1 (Accepted,
unamended) states plainly that this repository has "no `while True` loop, no
background thread, and no self-installing OS schedule anywhere," with
recurring behavior entirely the external invoker's responsibility
(cron/launchd/operator). APScheduler's normal mode of use — an in-process
`BlockingScheduler`/`AsyncIOScheduler` loop — **is** that daemon; adopting it
that way would reopen an Accepted ADR's core decision, out of this
feasibility PR's bounded scope. Second, even the narrowest possible use —
calling only APScheduler's stateless trigger classes
(`CronTrigger.get_next_fire_time`) to compute a next-due timestamp, never
running the scheduler loop — was tested directly
(`pr14/scratch_apscheduler_trigger_gaps.py`): a `CronTrigger(hour=9,
minute=30, day_of_week="mon-fri")` computed its next fire time as
2026-09-07 09:30 ET, which is Labor Day, a real NYSE closure `CronTrigger`
has no concept of — the same market-holiday gap `evaluation/
market_calendar.py`'s `exchange_calendars` integration (PR 3) already
solves, so making a trigger holiday-aware would only wire that same
dependency in a second, unrelated way, not remove any code. Its
`IntervalTrigger`/`CronTrigger` surface also has no catch-up/idempotency
concept at all (confirmed by inspecting the public API directly) — the
`MISSED_WITHIN_CATCHUP`/`MISSED_TOO_OLD`/`ALREADY_COMPLETED` classification
in `shadow/schedule.py::resolve_due_status` depends entirely on this
repository's own persisted `shadow_scheduler_runs` table regardless of
whether a trigger class is involved. The jobstore/lease limitation already
recorded at PR 0 was independently re-confirmed at the source level:
`BaseScheduler`'s internal locks are plain in-process locks, not a
cross-process distributed lease, and `SQLAlchemyJobStore` provides no
fencing token or heartbeat/TTL comparable to
`paper_recurring_scheduler_leases`/`shadow_run_leases`. **Not added to any
dependency declaration.**

**Tenacity, structurally scoped — deferred as a dependency, but its guard is
added regardless.** Its retry scoping remains decorator/context-manager only
(`tenacity.retry`, `tenacity.Retrying`), with no global interception,
reconfirmed against the current 9.1.4 API. No concrete current need exists:
`evidence_providers/http_client.py`'s existing hand-rolled backoff already
handles `Retry-After` parsing, rate-limiter coordination, and a
deterministic injectable sleep function, with no defect motivating
replacement — the same "no concrete current need exists" bar already
applied to Pandera/PyArrow/Riskfolio-Lib/SQLAlchemy/Alembic. **Not added to
any dependency declaration.** However, per this row's explicit requirement,
a structural regression test was added regardless of the adoption decision:
`tests/unit/test_external_broker_no_tenacity_import_boundary.py`, modeled on
the existing `test_lumibot_import_boundary.py` AST-based precedent — it
parses `external_broker.py`'s source and asserts no `tenacity` import node
exists, runs unconditionally (no `importorskip`, no dependency on
`tenacity` being installed), and includes a proof test confirming the
detector fires against a synthetic offending file. Scoped to
`external_broker.py` alone — `COMPONENT_MATRIX.md`'s "Generic transient
retries" row leaves the per-provider transport backoff open to a future,
separately-evaluated Tenacity adoption.

This guard is a static, file-scoped source check, not a runtime backstop: it
parses only `external_broker.py`'s own text, so it has no visibility into a
wrapper applied from another module at import or call time (monkeypatching,
`globals()`/`setattr` rebinding), and, by design, cannot distinguish an
arbitrary externally-named wrapper from an ordinary helper call without
inspecting that external module. PR 14 review rounds 3-5 progressively
closed several source-level bypasses (aliased imports, transitively-called
helpers, dynamic imports, module-level reassignment including annotated/
nested-block/walrus forms, `functools.partial`-wrapped decorators); each
round's fix demonstrates the technique converges on closing syntactic
variants, not on eliminating the class of gap outside this file's own
source. See `_find_protected_function_offenders`'s docstring in the test
file for the residual gaps this mechanism cannot structurally close.

**Ruling: defer both, do not adopt either package.** No ADR is required —
per the single-ADR rule established in D2 and reapplied at D9/D10/D11,
an ADR is needed only if adoption is recommended, and neither is here.
Re-evaluate APScheduler only if a future milestone proposes reopening ADR
0005 itself; re-evaluate Tenacity once a concrete generic-retry gap is
scoped (e.g. a new external HTTP integration point outside
`http_client.py`'s existing coverage).
