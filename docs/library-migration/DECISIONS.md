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

PR 6 is **implemented, not merged** (branch
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

**Decision date:** 2026-08-02 (PR 7). **Revised the same day**, in PR 7's first
review round, from Option A to a bounded Option B. The Option A record is
superseded by this section and is not restated; the reasoning that replaced it
is below.

### What the first pass got wrong

PR 7 initially took **Option A** — express the legacy run as the narrowest
`backtesting/engine.py` equivalent and change nothing in `backtest_runtime/` —
and claimed its `case_a_buy_and_hold` was "the single-symbol buy-and-hold case
that matches the reference strategy exactly." It was not. Measured:
`backtest_runtime` entered on **2024-01-03 at 100.5**; the legacy engine entered
on **2024-01-04 at 102.0**. Every downstream number — cash, equity, position
cost basis, drawdown, final value — differed by that one-session, 1.50-per-share
offset, and the report's central claim of a like-for-like case was unsupported.

The offset is structural, not a tuning error. The legacy engine cannot enter
before its **third** bar: a signal is eligible only on the session *after*
`generated_after_session`, and `average_true_range` needs `atr_period + 1` bars
of history before that. `backtest_runtime`'s reference strategy v1 always bought
on its first iteration, which is the **second** bar. No configuration of either
side closes that gap, so Option A could never produce an identical case — which
is exactly the condition `pr7-prompt.md` set for taking Option B.

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
`run_configuration_checksum` values. A value at or after the last bar is
rejected, rather than producing a run that silently never enters.

**What was deliberately not added.** No sell, no stop, no target, no second
order, no order type, no scheduler, no data fetcher, no broker interaction, no
multi-symbol, no sizing rule. `benchmark_asset=None` and
`analyze_backtest=False` remain hardcoded, and every ADR 0009 Decision 2
credential-, network-, and isolation-safety property is untouched and still
asserted by that distribution's own blocking test suite (`70 passed`, including
`test_entry_timing.py`'s explicit "no sell and no second order" assertion).

### What it bought

`case_f_exact_entry_parity` pairs `entry_after_session = 2024-01-03` with the
legacy engine's earliest possible entry. Both engines enter on **2024-01-04 at
101.0 for 10 shares**, and agree exactly on final cash (98 990), final equity
(99 992), final value, end position (10 @ 101.0), maximum drawdown (−0.00018),
and on cash, equity, unrealized P&L, realized P&L and drawdown for **every
co-dated session except the entry session itself**. The comparator asserts all
fifteen of those dimensions and exits non-zero if any fails.

The single excluded session is the classified adapter defect
`D15-entry-session-state-lag`: `backtest_runtime`'s daily state for session D
reflects fills booked through D−1, because the snapshot is taken inside
`on_trading_iteration` before LumiBot's broker processes that session's order.
It is the same fill-observation lag as `D4-fill-market-date-lag`, and it is
reported, not worked around.

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

### Consequence for PR 8

The exact case establishes that, on one identical buy-and-hold, the two engines
agree on every economic number. It does **not** establish that
`backtest_runtime` could replace `backtesting/engine.py`: the adapter capability
defects, plus the legacy engine's mandatory risk exits, are the list of things
that would have to be built first. PR 8 is the gate that weighs them.
