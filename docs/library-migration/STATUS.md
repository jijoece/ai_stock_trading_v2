# Migration Status

**Current phase: PR 13 — SQLAlchemy/Alembic feasibility and ADR — EVALUATED, NOT MERGED**
(branch `migration/13-sqlalchemy-alembic-feasibility`; `MASTER_PLAN.md` row
13, `DECISIONS.md` D11). No implementation — documentation
(`docs/library-migration/pr13/EVALUATION.md`) and two scratch reproductions
(`pr13/scratch_trigger_orm_vs_core.py`, `pr13/scratch_alembic_linearity.py`,
with raw output in `scratch_trigger_output.txt`/`scratch_alembic_output.txt`),
no production code under `src/`, `scripts/`, or `paper_runtime/src/`;
`tests/` gained only the documentation-consistency regression coverage
described below. **Outcome: defer, not added.** Both packages re-verified
live as OSI-approved MIT; empirical testing against the exact production
trigger DDL (`real_orders`, `paper_book_cash_ledger`) found no case, across
six scenarios including an unhandled failed flush and an ORM relationship
cascade, where SQLAlchemy's ORM masked a trigger-rejected write — the PR 0
theoretical concern behind this row is withdrawn as unsubstantiated; and
Alembic's branching revision graph was confirmed constrainable to
linear-only history matching `storage/schema_version.py`'s existing
monotonic ledger, but only via a new, unbuilt CI gate, not by any Alembic
default. Neither finding is a correctness blocker, but neither identifies a
current capability gap either (`COMPONENT_MATRIX.md`'s "Persistence"/
"Migrations" rows: the existing hand-written schema/repository layer is not
broken or unmaintained) — so `sqlalchemy`/`alembic` are **not added** to any
dependency declaration, and no ADR was produced (none is required when
adoption is not recommended, per `DECISIONS.md` D2's single-ADR rule). See
"Completed work (PR 13)" below.

**Next phase: PR 14 — APScheduler/Tenacity feasibility**
(`MASTER_PLAN.md` row 14), which depends only on PR 1 (already merged). PR 13
above is now evaluated; row 14 is the next unstarted row whose dependency is
already satisfied (row 8a remains independent of the numbered sequence, per
its own note below, and is not "next" in this ordering).

PR 11 — QuantStats/analytics migration — is **merged** (PR #28, `611b3df`,
branch `migration/11-quantstats-analytics-parity`; `MASTER_PLAN.md` row
11, `DECISIONS.md` D9). This entry previously read "IMPLEMENTED, NOT
MERGED" after the merge landed — corrected here, per `AUTOMATION.md`'s
"GitHub is authoritative for merge status" rule, rather than left to
contradict `git log` (the same correction pattern already applied to PR 9's
entry elsewhere in this file). New, additive `evaluation/analytics_parity.py`
proves fixture parity for `evaluation/metrics.py`'s `cumulative_return`,
`sharpe_ratio`, `sortino_ratio`, `max_drawdown`, and `calmar_ratio` against
`empyrical-reloaded`, with `quantstats-lumi` exercised only for a
non-authoritative presentation summary. **`evaluation/metrics.py` is
unmodified and remains the sole production authority** — per
`REMOVAL_MANIFEST.md`'s default rule and `MASTER_PLAN.md` row 17, PR 11
proves parity only; removal is PR 17's job. See "Completed work (PR 11)"
below.

PR 10 — broker-to-`paper_books` reconciliation parity tests — is
**IMPLEMENTED** (branch
`migration/10-broker-paper-books-reconciliation-parity`; `MASTER_PLAN.md`
row 10, `DECISIONS.md` D1). See "Completed work (PR 10)" below.

PR 9 — the LumiBot runtime normalization contract — is **merged** (PR #22,
`22f2cdc`, branch `migration/09-lumibot-normalization-contract`;
`MASTER_PLAN.md` row 9, `DECISIONS.md` D8). This file's "Current phase"
line above previously still read "IMPLEMENTED, NOT MERGED" after the merge
landed — corrected here rather than left to contradict `git log`.

PR 8 — the backtest removal-decision gate — is **merged** (PR #20). Its
outcome stands: the custom backtest engine is **not** approved for removal,
stays authoritative indefinitely, and `backtest_runtime/` is kept as an
additional, non-replacing offline cross-check. PR 7 (backtest parity report,
`5b9e1e3`, PR #19) and PR 6 (`bbd7a1f`, PR #18) are also merged.

PR 8 created row **8a**, the tracked follow-up for the three legacy-side
items it decided must now be fixed rather than tolerated: run identity
ignores the bar dataset (PR 7 D17), the `backtest_orders` table is created
and never written, and bar availability is enforced once per run rather than
per session. Row 8a is **not started** and remains independent of the
numbered migration sequence — it can run at any point.

PR 12 — Riskfolio-Lib evaluation only — is **merged** (PR #29, `641f5da`,
branch `migration/12-riskfolio-lib-evaluation`; `MASTER_PLAN.md` row 12,
`DECISIONS.md` D10). This entry previously read "EVALUATED, NOT MERGED"
after the merge landed — corrected here, per `AUTOMATION.md`'s "GitHub is
authoritative for merge status" rule, the same correction pattern already
applied to PR 9's and PR 11's entries elsewhere in this file. See
"Completed work (PR 12)" below for the full record.

PR 13 — SQLAlchemy/Alembic feasibility and ADR — is **EVALUATED, NOT
MERGED** (branch `migration/13-sqlalchemy-alembic-feasibility`;
`MASTER_PLAN.md` row 13, `DECISIONS.md` D11). See this file's "Current
phase" note above and "Completed work (PR 13)" below for the full record;
the next phase is now PR 14 (see above).

PR 6 is **merged** (`bbd7a1f`, PR #18) and delivered everything ADR 0009
Decision 4 requires:

```text
[x] backtest_runtime/ exists, implementing ADR 0009 Decisions 1-3
[x] backtest_runtime/pyproject.toml installs alone via `pip install -e backtest_runtime/`
[x] its own tests exist, none guarded by importorskip
[x] blocking backtest-runtime-tests CI job added (.github/workflows/ci.yml)
[x] the AST import-boundary repair (tests/unit/test_lumibot_import_boundary.py)
```

See "Completed work (PR 6)" below for the full record, evidence, and test
results, and "Completed work (PR 7)" for the parity report that followed it.

The pre-step before PR 6 is complete. All of its gates were met:

```text
[x] Opus architecture review complete   (docs/library-migration/pre-step-06/EVALUATION.md)
[x] feasibility spike passes            (pinned lumibot==4.5.78, same directory)
[x] sentinel-.env suppression proved    (pre-step-06/dotenv_sentinel_output.txt)
[x] ADR accepted by the owner           (docs/adr/0009-..., Accepted 2026-08-01)
```

The repository owner accepted ADR 0009 and selected Option B: an isolated,
credential-free `backtest_runtime/` distribution.

## Completed work (PR 0)

- Targeted repository inventory across infra, backtesting/accounting, and
  trading/signals/safety (three parallel read-only agents; see
  `ARCHITECTURE.md` and `COMPONENT_MATRIX.md` for the consolidated result).
- Dependency-compatibility research against live PyPI/GitHub data for the
  library candidates evaluated (see `DEPENDENCY_MATRIX.md` Section 1, 23
  table entries — the earlier "21 proposed libraries" figure did not match
  the table and has been corrected here rather than repeated).
- ADR reconciliation for the two direct conflicts between the original
  `docs/milestones/rebuild/plan.md` and accepted ADRs 0001 and 0006 (see
  `DECISIONS.md` D1/D2). Neither required a superseding ADR — both were
  resolved by narrowing scope rather than overriding the original decision.
- Revised PR sequence (`MASTER_PLAN.md`), removal manifest
  (`REMOVAL_MANIFEST.md`), and preservation manifest
  (`PRESERVATION_MANIFEST.md`).

## Completed work (PR 1)

**Scope:** `pyproject.toml`, `paper_runtime/pyproject.toml`,
`.github/workflows/ci.yml`, `docs/adr/0002-isolated-lumibot-runtime.md`
(one amendment section), one doc-comment-only edit in
`tests/unit/test_lumibot_adapter.py` (no behavior change — see "Root
`paper` extra removed" below), and the `docs/library-migration/*`
documentation set — not `pyproject.toml` only, and not limited to
dependency declarations; PR 1 also corrected inaccurate planning guidance
recorded during PR 0 and, after review, removed an unresolvable install
target and added CI coverage for the new extras and the Python floor.

### Dependencies added

Base dependencies (`pyproject.toml`, not an extra):

```text
exchange_calendars>=4.13,<5
```

New `indicators` extra:

```text
TA-Lib>=0.7.1,<0.8
```

New `analytics` extra:

```text
empyrical-reloaded>=0.5.12,<0.6
quantstats-lumi>=1.1.5,<2
```

New `observability` extra:

```text
structlog>=26.1,<27
opentelemetry-api>=1.44,<2
opentelemetry-sdk>=1.44,<2
```

`dev` extra additions:

```text
hypothesis>=6.161,<7
time-machine>=3.2,<4
```

`paper_runtime/pyproject.toml` (the root `pyproject.toml` no longer declares
a `paper` extra — see "Root `paper` extra removed" below):

```text
lumibot==4.5.78   (bumped from 4.5.74)
```

### Verified resolved versions (PyPI JSON API, 2026-07-26)

| Package | Verified version | Requires-Python |
|---|---|---|
| exchange_calendars | 4.13.2 | `>=3.10,<4` |
| TA-Lib | 0.7.1 | `>=3.9` |
| quantstats-lumi | 1.1.5 | `>=3.6` |
| empyrical-reloaded | 0.5.12 | `>=3.9` |
| structlog | 26.1.0 | `>=3.10` |
| opentelemetry-api | 1.44.0 | `>=3.10` |
| opentelemetry-sdk | 1.44.0 | `>=3.10` |
| hypothesis | 6.161.5 | `>=3.10` |
| time-machine | 3.2.0 | `>=3.10` |
| lumibot | 4.5.78 | `>=3.10` |

Full detail in `DEPENDENCY_MATRIX.md` ("PR 1 re-verification" section) and
Section 6 ("PR 1 correction record").

### Python floor decision

**Unchanged, `>=3.10`,** in both `pyproject.toml` and
`paper_runtime/pyproject.toml`. The `>=3.11` floor proposed during PR 0 was
contingent solely on adding VectorBT; PR 1 does not add VectorBT, and every
package verified above supports `>=3.10`. Pyright's `pythonVersion` in both
`[tool.pyright]` blocks is unchanged (`3.10`). CI continues running on
Python 3.11, which is compatible with a `>=3.10` package floor.

### VectorBT licensing status

`BLOCKED_PENDING_LICENSE_DECISION` (recorded in `DECISIONS.md`). VectorBT
1.1.0 is Apache-2.0 with Commons Clause — source-available/fair-code, not
conventional OSI-approved open source, without an explicit owner-approved
exception. Not added to any dependency declaration in PR 1. PR 5 (retitled
"Vectorized research library selection and adapter") must evaluate an
OSI-approved alternative or obtain explicit owner approval before adding a
vectorized-research dependency.

### Riskfolio-Lib deferral

Not added in PR 1. Remains PR 12 evaluation-only. Its `vectorbt>=0.28.0`
hard dependency was re-verified against current PyPI metadata and confirmed
accurate (not a documentation error) — see `DEPENDENCY_MATRIX.md` Section 6.

### TA-Lib wheel result

Confirmed: TA-Lib 0.7.1 provides prebuilt wheels for manylinux2014/
musllinux (x86_64/aarch64), macOS 13/14 (x86_64/arm64), and Windows across
cp39–cp314. `pip install -e ".[indicators]"` resolved a compatible wheel on
this machine (macOS arm64, Python 3.11) with no compilation and
`import talib` succeeded (`talib.__version__ == "0.7.1"`). `DEPENDENCY_MATRIX.md`
and `MASTER_PLAN.md` corrected to require a wheel first, with system
installation only as an explicit fallback (PR 4 scope). This is now also a
blocking CI check (`dependency-extras-smoke` / `indicators` in `ci.yml`, on
`ubuntu-latest`), so the wheel result is a reproducible merge gate, not only
a local scratch-environment observation.

### Root `paper` extra removed (`DECISIONS.md` D5)

The root `pyproject.toml`'s `paper` extra was **removed**, not merely
version-bumped. `pip install -e ".[paper]"` cannot resolve for any
published `lumibot==4.5.x` release: LumiBot's `google-adk[extensions]`
requirement pulls in `litellm`, which pins `jsonschema==4.23.0` **exactly**
across every compatible release, unconditionally conflicting with this
repository's `jsonschema>=4.26.0` base floor. (An earlier diagnosis in this
PR incorrectly attributed the failure to a `google-genai` version-floor
mismatch between `lumibot` and `google-adk`; that mismatch is real but is
not the blocking constraint — bisecting each base dependency individually
against `lumibot==4.5.78` isolated `jsonschema>=4.26.0` as the actual
unsatisfiable constraint, confirmed by inspecting `litellm`'s wheel metadata
directly.) `docs/adr/0002-isolated-lumibot-runtime.md`'s Context section
already flagged this exact risk as a *silent downgrade* motivating the
`paper_runtime` process-boundary architecture; it has since become an
unconditional resolution failure now that `jsonschema>=4.26.0` is a hard
floor. `paper_runtime/pyproject.toml` is now the sole LumiBot dependency
declaration in the repository. (Still true today. Under the accepted ADR 0009
it becomes one of two isolated declarations once PR 6 adds
`backtest_runtime/pyproject.toml`; the root `pyproject.toml` declares none in
either state — see `DECISIONS.md` D5's reconciliation section.)
`runtime/lumibot/adapter.py` (ADR 0001) and
its test (`tests/unit/test_lumibot_adapter.py`) are unaffected in behavior —
the test still guards itself with `pytest.importorskip("lumibot")` and a
developer who wants to exercise it installs `lumibot` into a scratch
virtualenv by hand, not via any declared extra.

### Environments tested

No Python 3.10 interpreter was available on this machine; all scratch
environments used Python 3.11 (the interpreter CI already uses), which is
compatible with the unchanged `>=3.10` floor.

- **Environment A (standard application, `.[dev]`):** installed cleanly;
  `import exchange_calendars, hypothesis, time_machine` succeeded; `pip
  check` reported no broken requirements; full offline suite —
  **2746 passed, 18 skipped, 0 failed**.
- **Environment B (`.[indicators]`):** installed cleanly; `import talib`
  succeeded (`0.7.1`); `pip check` clean.
- **Environment C (`.[analytics]`):** installed cleanly; `import empyrical,
  quantstats_lumi` succeeded (`0.5.12`, `1.1.5` — note the importable module
  is `quantstats_lumi`, not `quantstats`); `pip check` clean. No network
  calls or benchmark data were fetched.
- **Environment D (`.[observability]`):** installed cleanly; `import
  structlog; import opentelemetry.sdk` succeeded (`structlog 26.1.0`); `pip
  check` clean. No exporter configured.
- **Environment E (isolated `paper_runtime[dev]` — the only LumiBot install
  target that exists after this PR):** installed cleanly; `pip check`
  clean; resolved `lumibot==4.5.78`; paper_runtime suite —
  **59 passed, 0 failed**. The root `pyproject.toml` no longer declares a
  `paper` extra (see "Root `paper` extra removed" above), so there is no
  root-package LumiBot install path to validate separately, and no
  cross-distribution version assertion to make — `lumibot==4.5.78` is
  declared in exactly one place.
- Indicators/analytics/observability were never installed together in one
  combined environment, consistent with the isolation requirement. Each is
  now also independently re-verified on every PR by the
  `dependency-extras-smoke` CI matrix (`ci.yml`).
- A blocking `python-3-10-floor` CI job (`ci.yml`) now installs `.[dev]` and
  runs the full offline suite under the declared minimum Python version on
  every PR; no Python 3.10 interpreter was available on this development
  machine, so this substantiates the floor that local validation could not.

### Documentation corrections applied

- `exchange_calendars` corrected from a proposed optional `core` extra to a
  base application dependency (`DEPENDENCY_MATRIX.md`, `pyproject.toml`).
- VectorBT reclassified `BLOCKED_PENDING_LICENSE_DECISION`; not added.
  PR 5 retitled from "VectorBT research adapter" to "Vectorized research
  library selection and adapter" (`MASTER_PLAN.md`, `DECISIONS.md`,
  `COMPONENT_MATRIX.md`).
- Riskfolio-Lib removed from the proposed PR 1 `analytics` group; remains
  PR 12 evaluation-only. Its VectorBT hard-dependency claim was re-verified
  and confirmed accurate, not corrected.
- TA-Lib installation guidance corrected from unconditional
  `brew install`/`apt install` to wheel-first with a documented,
  explicitly-triggered fallback (`DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`).
- Analytics authority boundary made unambiguous: empyrical-reloaded is
  authoritative for primitive metrics; quantstats-lumi is reporting/
  presentation only.
- LumiBot version aligned to `4.5.78` in both `pyproject.toml` and
  `paper_runtime/pyproject.toml`.
- Python floor documentation corrected from "raise to `>=3.11`" to "remains
  `>=3.10` until an approved dependency requires a raise."
- The Pydantic ADR contradiction in `DECISIONS.md` (two inconsistent
  statements about when an ADR is required) replaced with one rule: PR 2
  adds no Pydantic dependency by default; an ADR is required only if PR 2
  recommends adopting Pydantic at a boundary, before adding the dependency.
- The "21 proposed libraries" count in this file, which did not match
  `DEPENDENCY_MATRIX.md`'s 23 table entries, corrected above.
- This file no longer states the PR is `pyproject.toml only`.
- `DEPENDENCY_MATRIX.md` Section 1's VectorBT, TA-Lib, and LumiBot rows were
  edited in place to state the current decision directly, rather than
  leaving the superseded PR 0 proposal to contradict the corrections
  recorded later in the same document (a future read of only Section 1
  could otherwise reach the wrong conclusion).
- Root `paper` extra removed from `pyproject.toml` after review identified
  it as a declared, unresolvable install target (`DECISIONS.md` D5;
  `docs/adr/0002-isolated-lumibot-runtime.md` Amendment).
- `ci.yml` gained a blocking `dependency-extras-smoke` matrix (one job per
  new extra: `indicators`, `analytics`, `observability`, each installed
  alone) and a blocking `python-3-10-floor` job, so the new dependency
  groups and the declared Python floor are reproducible merge gates, not
  only local scratch-environment observations.

## Custom code removed

None. PR 1 changed no behavior under `src/`, `scripts/`,
`paper_runtime/src/`, `tests/`, or `config/`. The one edit under `tests/`
(`tests/unit/test_lumibot_adapter.py`) is a docstring/skip-reason text
update reflecting the removed `paper` extra — the test's guard
(`pytest.importorskip("lumibot")`) and its pass/skip behavior are
unchanged.

## Library authority established

None yet beyond dependency declaration. No production code imports any PR 1
dependency; PR 3/4/11/15/16 wire the respective libraries into code.

## Tests run

- Main offline suite (Environment A): **2746 passed, 18 skipped, 0 failed.**
- `paper_runtime` suite (Environment E2): **59 passed, 0 failed.**
- No test file under `tests/` or `paper_runtime/tests/` was modified.

## Remaining blockers

1. ~~**LumiBot-backtest-mode dependency/process-boundary question**~~
   (`DECISIONS.md` D4, open item 1). **Resolved 2026-08-01:** the owner
   accepted ADR 0009 (Option B, `backtest_runtime/`). The pre-step is complete
   and PR 6 is unblocked — see "Completed work (pre-step before PR 6)" below.
   Was never a blocker for PR 1 or PR 2.
2. **PR 13/14 feasibility outcomes** (SQLAlchemy/Alembic trigger-safety,
   APScheduler lease-coexistence) are unknown until those PRs run — PR 1
   does not depend on them.

The root `.[paper]` extra `ResolutionImpossible` finding from an earlier
draft of this PR is resolved, not deferred: the extra was removed (see
"Root `paper` extra removed" above and `DECISIONS.md` D5), so there is no
outstanding blocker to track for it.

## Completed work (PR 2)

**Scope:** evaluation only — `docs/library-migration/pr2/EVALUATION.md`
(full inventory, comparison, and recommendation),
`docs/library-migration/pr2/boundary_comparison_scratch.py` and
`comparison_output.txt` (scratch Pydantic v2 comparison implementation, not
merged into `src/`), and this file. No file under `src/`, `scripts/`,
`paper_runtime/src/`, or `tests/` was modified.

**Outcome: do not adopt.** Every untrusted-input boundary (YAML config
loading across 9 modules, CLI argument parsing, the JSONL broker protocol,
and external provider response parsing) was inventoried in
`docs/library-migration/pr2/EVALUATION.md`. A scratch Pydantic v2
implementation of the two highest-stakes boundaries
(`runtime/paper_runtime_config.py`, `paper_runtime/.../protocol.py`) showed:
dependency/performance impact is low (4 lightweight transitive packages,
~3.7us/call, no material perf difference); Pydantic's `extra="forbid"` gives
unknown-field rejection for free, but every safety-critical boundary already
implements this by hand; the safety-critical business-rule validators
(`real_money_enabled` must be false, pinned base URL, exact allowed-sides/
order-types) do not shrink under Pydantic — they relocate into
`field_validator` methods of equal size, producing a longer implementation
overall. No boundary showed the "clear reduction in custom
boundary-validation code" required by `DECISIONS.md` D2's adoption bar.
**No `pydantic` dependency was added to `pyproject.toml`. No ADR was
required or drafted**, per the single ADR rule in `DECISIONS.md` D2 (an ADR
is needed only if adoption is recommended).

Non-blocking gaps identified for future cleanup (not part of this PR, no new
dependency needed): `analysis/scorer.py`, `analysis/screener.py`, and
`execution/config.py` do not reject unknown YAML keys, unlike most other
loaders; `scripts/indicators.py` and `scripts/score.py` perform no shape
validation of their JSON input, unlike `scripts/macro_pillar.py`.

## Completed work (PR 3)

**Scope:** `src/trading_research/evaluation/market_calendar.py` and
`tests/unit/test_market_calendar.py` only. No other file under `src/`,
`scripts/`, `paper_runtime/src/`, or `config/` was modified.

**Outcome: `exchange_calendars`' XNYS calendar is now the sole authority**
for U.S. equity sessions, replacing the hand-written fixed-rule
NYSE/Nasdaq federal-holiday calendar. All public function names and
signatures (`is_weekend`, `is_market_holiday`, `is_trading_day`,
`next_trading_session`, `add_trading_days`, `is_market_open`,
`regular_session_close`, `MARKET_TIMEZONE_NAME`, `MarketCalendarError`)
are unchanged — caller-analysis (below) found no case that safely
allowed or required a signature change.

**Custom code removed** (deleted in place, not left for a later PR):
`_nth_weekday_of_month`, `_last_weekday_of_month`, `_easter_sunday`,
`_observed`, `_federal_holidays`, the fixed-rule holiday-set construction,
and the fixed `MARKET_CLOSE_TIME = time(16, 0)` / `MARKET_OPEN_TIME`
constants and their use in `is_market_open`'s boundary check. No custom
fallback was added for `exchange_calendars` resolution failure; any
failure to resolve the XNYS calendar or a session/minute query raises
`MarketCalendarError` (fail-closed), caught narrowly around each
`exchange_calendars` call site — the library's own `ValueError` subclasses
(`NotSessionError`, `DateOutOfBounds`, `RequestedSessionOutOfBounds`, etc.)
are not allowed to leak past this module's boundary.

**Newly supported, not possible with the previous fixed-rule calendar:**
early closes (e.g. the day after Thanksgiving, Christmas Eve — both
1:00 PM regular closes) and one-off exchange closures (e.g. December 5,
2018, the National Day of Mourning for President George H.W. Bush) are now
correctly reflected in `is_market_open` and `regular_session_close`,
instead of being silently treated as full ordinary sessions.

**Caller analysis:** every caller of the listed functions was located and
re-verified against the new implementation —
`src/trading_research/paper_books/exit_policy.py` (`is_trading_day`),
`src/trading_research/paper_books/recurring_scheduler.py`
(`is_trading_day`), `src/trading_research/paper_books/soak_campaign.py`
(`MARKET_TIMEZONE_NAME`, `is_trading_day`, `regular_session_close`),
`src/trading_research/evidence_providers/market_data_provider.py`
(`regular_session_close`), `src/trading_research/shadow/schedule.py`
(`is_trading_day`), and
`src/trading_research/evaluation/evaluation_service.py`
(`add_trading_days`, `next_trading_session`). None import the removed
private helper functions or the removed `MARKET_OPEN_TIME`/
`MARKET_CLOSE_TIME` constants. `tests/unit/test_runtime_client_no_lumibot_import.py`
only AST-scans the file for `lumibot` imports and is unaffected.

**Caching:** the XNYS `ExchangeCalendar` object is constructed once via a
process-lifetime `functools.lru_cache(maxsize=1)`-wrapped accessor, not
reconstructed on every call.

**Correction (2026-07-26, docs/milestones/rebuild/5.md PR #9 review):** the
initial PR 3 implementation called `get_calendar("XNYS")` with no explicit
`start`/`end`, which resolves to `exchange_calendars`' own moving default
window ("now minus ~20 years" to "now plus ~1 year" at construction time).
Combined with the process-lifetime `lru_cache`, that window would have
frozen at whatever range happened to be current the first time a process
called `_calendar()`, silently narrowing the supported range over the life
of a long-running process. `_calendar()` now passes an explicit, fixed
range — `_CALENDAR_START = 1990-01-01`, `_CALENDAR_END = 2035-12-31` — so
the supported range no longer depends on wall-clock time at construction.
`regular_session_close()` was also narrowed to distinguish, via
`cal.first_session`/`cal.last_session` bounds-checking and catching
`NotSessionError` vs. `DateOutOfBounds`/`RequestedSessionOutOfBounds`
separately: an actual weekend/exchange holiday, a date outside the
supported range, a failure to construct/query XNYS, and a timezone-database
failure now each raise `MarketCalendarError` with a distinct message
instead of the previous blanket "`<date>` is not a trading session" for
every case. The module docstring now documents the exact supported range,
how it is constructed, what happens outside it, and that future one-off
emergency closures cannot be known before `exchange_calendars` itself is
upgraded.

**Correction (2026-07-26, follow-up to PR #10 review):**
`regular_session_close()`'s range pre-check used
`cal.first_session`/`cal.last_session` as the range boundary, not
`_CALENDAR_START`/`_CALENDAR_END` directly. Those differ whenever XNYS's
own actual first/last session falls a few days inside the configured range
— concretely, `_CALENDAR_START` (1990-01-01) is itself New Year's Day, a
holiday, so `cal.first_session` is 1990-01-02, one day later. A date in
that one-day gap (1990-01-01 itself) was misclassified as "outside the
supported range" instead of "inside the supported range but not a trading
session." The pre-check now compares directly against
`_CALENDAR_START`/`_CALENDAR_END`, and the exception handling for
`cal.session_close()` now classifies `NotSessionError`,
`DateOutOfBounds`, and `RequestedSessionOutOfBounds` identically as
"not a trading session" once a date has already passed that pre-check —
since exchange_calendars raises the bounds-style exceptions (not
`NotSessionError`) for a date in that gap, even though, from this module's
configured range, it is not an out-of-range date.

A maintenance guard was also added: `_calendar_end_margin_ok(as_of)` is a
deterministic, injectable-date helper asserting `_CALENDAR_END` remains at
least `_CALENDAR_END_MINIMUM_MARGIN_YEARS` (5) beyond `as_of`. A dedicated
test calls it with the real current date, so that test starts failing for
real once fewer than five years remain before `_CALENDAR_END`, forcing a
deliberate range extension rather than a silent lapse; separate tests call
it with injected fixed dates to verify the helper's own logic
deterministically, independent of when the suite runs.

The module docstring was corrected: it previously claimed every public
function in this module enforces the configured range. `is_weekend` is
pure weekday arithmetic — it does not consult XNYS at all, accepts any
date in or out of range, and never raises `MarketCalendarError`.
`is_market_holiday` short-circuits to `False` for weekend dates without
consulting XNYS, but is otherwise range-enforcing for non-weekend dates.
The docstring now scopes the range-enforcement claim to the XNYS-dependent
functions (`is_trading_day`, `next_trading_session`, `add_trading_days`,
`is_market_open`, `regular_session_close`) and states `is_weekend`'s
exception explicitly.

**Known remaining gap (not fixed in this correction, out of scope):**
`is_trading_day`, `next_trading_session`, `add_trading_days`, and
`is_market_open` still fail closed (raise `MarketCalendarError`) for a
date in the narrow gap between `_CALENDAR_START`/`_CALENDAR_END` and
XNYS's own actual first/last session, but via the existing generic
"could not resolve XNYS session status" message rather than a dedicated
non-session classification like `regular_session_close` now has. This is
a message-precision gap, not a correctness gap — the docstring's
"correct result or `MarketCalendarError`" contract still holds.

**Tests run:**
- `pytest tests/unit/test_market_calendar.py -q --tb=short` —
  **67 passed** (28 original fixtures plus the 13 added during PR 3, all
  re-verified against the corrected implementation; 19 cases added during
  the PR #9 review correction — a historical date before the library's own
  moving-default lookback, `regular_session_close` for that historical
  session, a future date beyond the library's own moving-default
  lookahead, `next_trading_session` and `add_trading_days` at/past the
  configured upper boundary, `regular_session_close` outside the
  configured range in both directions, and `add_trading_days`/
  `next_trading_session` from non-session starts — Saturday, Sunday, a
  fixed-rule holiday, a one-off exchange closure, and an early-close
  session; plus 7 new cases added during this follow-up correction —
  `regular_session_close(1990-01-01)` raising the non-session error, not
  the out-of-range error, `regular_session_close(1989-12-31)` raising the
  out-of-range error, `regular_session_close(1990-01-02)` (the first valid
  1990 session) still resolving correctly, and four `_calendar_end_margin_ok`
  cases — two with injected dates proving sufficient/insufficient headroom,
  one at `_CALENDAR_END` itself, and one live guard using the real current
  date). Expected values were computed independently (well-known
  market-hours facts and manually verified calendar dates), not derived by
  calling the same `exchange_calendars` API the implementation calls.
- `pytest tests/ -q --tb=short` — **2795 passed, 17 skipped, 0 failed**
  (full offline suite; no test outside `test_market_calendar.py` was
  modified).

**No fallback authority remains:** the fixed-rule federal-holiday
calculation is fully deleted; `exchange_calendars`' packaged offline XNYS
calendar data is the only session-authority code path in this module.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live calendar data was fetched.

## Completed work (PR 4)

**Scope:** `scripts/indicators.py` and `tests/unit/test_indicators.py`
only, plus this file, `COMPONENT_MATRIX.md`, `REMOVAL_MANIFEST.md`, and a
new blocking `indicators-tests` CI job in `.github/workflows/ci.yml`. No
other file under `src/`, `scripts/`, `paper_runtime/src/`, or `config/`
was modified. `analysis/indicators.py`'s `Decimal`-based ATR was **not**
touched — it was not already in the tracked PR 4 scope and no parity
tests were added for it in this PR, so per the bounded prompt it is left
for a separate change.

**Outcome: TA-Lib is authoritative for the EMA, RSI, and BBANDS
primitives.** `macd` and `trix` are composed from TA-Lib's EMA primitive
in a thin custom adapter that also implements this stack's documented
compatibility semantics (flat-price RSI override, MACD/TRIX signal
alignment, %B, zero-denominator handling) — TA-Lib is **not** the sole
authority for every calculation end-to-end. See "Correction (PR #12
review): authority boundary" below for the exact boundary and why an
earlier draft of this section overstated it. All public
function names, signatures, return shapes (`list[Optional[float]]` /
tuples), and dictionary keys of `compute()` are unchanged — caller
analysis (`scripts/score.py`'s `import indicators as I`,
`.claude/skills/run-agentic-trading-desk/driver.py`'s subprocess and
direct-import exercises) found no caller that safely allowed or required a
signature change.

**Custom formulas removed** (deleted in place, not left for a later PR):
the hand-written recursive EMA loop, the Wilder gain/loss smoothing loop,
the manual MACD/TRIX EMA composition, and the `statistics.pstdev`-based
Bollinger calculation. `ema_series` now calls `talib.EMA`; `rsi_wilder`
calls `talib.RSI`; `bollinger` calls `talib.BBANDS` (`matype=0`, population
stddev). `macd` and `trix` are built from `talib.EMA` primitives rather
than `talib.MACD()`/`talib.TRIX()` directly — see "Intentional semantic
differences and reconciliations" below for why. Five behavioral spy tests
in `tests/unit/test_indicators.py` (`test_ema_series_invokes_talib_ema`,
`test_rsi_wilder_invokes_talib_rsi`, `test_bollinger_invokes_talib_bbands`,
`test_macd_is_composed_from_talib_ema_and_never_calls_talib_macd`,
`test_trix_is_composed_from_talib_ema_and_never_calls_talib_trix`) prove
this behaviorally — they spy on the real `talib.*` functions and assert
each is actually called (and, for MACD/TRIX, that the corresponding
single-shot `talib.MACD()`/`talib.TRIX()` is never called) — rather than
the weaker source-text substring check an earlier draft of this PR used.

**Dependency behavior:** `scripts/indicators.py` now does `import talib`
at module scope inside a `try`/`except ImportError` that re-raises with
`"scripts/indicators.py requires TA-Lib. Install the indicators extra:
pip install -e \".[indicators]\""` — a concise, actionable message, not a
silent fallback to the old formulas (there are none left to fall back
to). `tests/unit/test_indicators.py::test_missing_talib_raises_actionable_
import_error` simulates the missing-package case via `sys.modules["talib"]
= None` and asserts this exact message, so it passes in *every*
environment regardless of whether TA-Lib happens to be installed.

**Warm-up, null/NaN, and rounding parity** — established by capturing
reviewed golden outputs from the pre-migration implementation *before*
editing it (recorded as literals in `tests/unit/test_indicators.py`, never
re-derived by calling the removed implementation), then verifying the
TA-Lib-backed implementation against those literals:

* `ema_series`: `talib.EMA`'s SMA-seeded warm-up and `NaN` boundary are
  bit-for-bit equivalent to the pre-migration seed-then-recurse formula
  (verified for period 12 and 20 across increasing/oscillating fixtures);
  `NaN` is converted to `None` at the boundary via the new `_nan_to_none`
  helper, preserving list length exactly.
* `rsi_wilder`: numerically identical to the pre-migration Wilder
  smoothing for every non-degenerate fixture (increasing, decreasing,
  oscillating, the 250-bar realistic series) — **with one documented,
  deliberately preserved intentional difference**, see below.
* `bollinger`: `talib.BBANDS(matype=0)` uses population standard deviation
  (its `nbdevup`/`nbdevdn` multiply the population stddev), matching the
  pre-migration `statistics.pstdev`-based formula's semantics and, for
  exactly-representable prices, its exact values too (verified: a flat
  100.0 series over 20 bars gives `upper == mid == lower == 100.0`
  bit-exactly, both before and after migration).
* `macd` / `trix`: rebuilt from chained `talib.EMA` calls (not
  `talib.MACD()`/`talib.TRIX()`), reproducing the pre-migration warm-up
  index, signal-line alignment, and (for TRIX) percent scaling and
  zero-denominator convention exactly — see below.

**Intentional semantic differences and reconciliations** (all covered by
a dedicated test in `tests/unit/test_indicators.py`):

1. **Flat-price RSI (`rsi_wilder`).** `talib.RSI` returns `0.0`, not
   `100.0`, for the degenerate case where the average gain *and* average
   loss are both exactly zero (every price from the start of `close`
   through the current bar is identical). The pre-migration formula
   treated that zero-loss case as maximally bullish (Wilder's
   divide-by-zero, `RS -> infinity`, conventionally `RSI = 100`).
   **Decision: preserve the pre-migration `100.0` semantic**, not
   TA-Lib's `0.0` — `rsi_wilder` detects the exact condition (the whole
   `close` prefix through index `i` is constant, equivalent to
   avg_gain == avg_loss == 0 given Wilder's recursive smoothing can only
   reach exactly zero on both from an all-zero history) and overrides
   just that boundary, without reintroducing a competing RSI formula.
   Verified for a monotonic-increase series (`avg_loss == 0`, `avg_gain >
   0`) that both implementations already agreed gives `100.0` without any
   override, and for a "flat then moves" fixture that the override does
   not leak past the point the price starts actually changing.
2. **MACD line availability (`macd`).** `talib.MACD()` withholds the MACD
   line itself until enough bars exist for the *signal* EMA too (verified:
   for a 40-bar fixture, both the line and signal from `talib.MACD()`
   first become non-`NaN` at index 33). The pre-migration convention — and
   this stack's callers — expect the line available `signal - 1` bars
   earlier, as soon as the fast and slow EMAs both exist (index 25 for the
   same fixture). `macd` is therefore built from `talib.EMA(fast)` -
   `talib.EMA(slow)` directly, with the signal computed via `talib.EMA`
   over the trimmed valid line and re-aligned exactly as the pre-migration
   formula did.
3. **TRIX zero-denominator and alignment (`trix`).** Built from three
   chained `talib.EMA` passes plus an explicit `_pct_change` helper
   (`(cur - prev) / prev * 100.0 if prev != 0 else 0.0`), rather than
   `talib.TRIX()` directly, to keep explicit, tested control of the
   zero-denominator convention and of the signal-line end-alignment,
   matching the pre-migration formula exactly rather than trusting
   `talib.TRIX()`'s undocumented internal handling of a degenerate
   triple-EMA-equals-zero input.
4. **Bollinger Bands floating-point noise on a non-exactly-representable
   flat price.** `talib.BBANDS` computes variance via a single-pass
   sum-of-squares formula that leaves ~1e-6 absolute floating-point noise
   for a constant window when the price itself is not exactly
   representable in binary (e.g. `123.45`), whereas the pre-migration
   `statistics.pstdev` gave exactly `0.0`. Both round identically at the
   `_round()` CLI boundary (4 decimals), and `%B` still resolves to
   exactly `0.5` in that case by symmetry (`upper`/`lower` are
   equidistant from `mid`, which equals `close[-1]`). Proven not to affect
   the `%B == 0.5` guard's reachability: a flat, exactly-representable
   price (`100.0`) gives bit-identical `upper == mid == lower` both before
   and after migration.

**Tests run** (updated counts after the PR #12 review round — see the
correction section below; the original submission's counts were 25/1+24
skipped/2820 before the fail-closed validation and behavioral-spy tests
were added):
- `pytest tests/unit/test_indicators.py -q --tb=short` (TA-Lib installed
  via `.[indicators]`, verified wheel resolution on this machine, macOS
  arm64, Python 3.14) — **48 passed**. Covers: increasing, decreasing,
  flat, and oscillating prices; short inputs below every warm-up
  threshold; inputs at the exact warm-up threshold for EMA20, RSI14,
  Bollinger-20, the MACD signal (34 bars), the TRIX line (44 bars), and
  the TRIX signal (52 bars); a 250-bar long realistic synthetic series
  (matching the CLI self-test's sin+drift formula); the missing-TA-Lib
  actionable-error path; each intentional semantic difference; five
  behavioral spy tests proving TA-Lib invocation; the fail-closed
  input-validation boundary (NaN at the first/middle/final bar,
  +/-infinity, empty input, nested arrays, booleans, numeric strings,
  invalid periods, MACD `fast >= slow`, a non-warm-up NaN injected via a
  mocked `talib.EMA`/`talib.BBANDS` call); and CLI input-shape validation
  (missing `"close"` key, a non-list/non-dict JSON root, a malformed price
  element, and the `allow_nan=False` JSON-output guard).
- `pytest tests/unit/test_indicators.py -q --tb=short` with TA-Lib
  *uninstalled* (simulating the `main-tests` CI job, which only installs
  `.[dev]`) — **1 passed** (the missing-dependency guard), **47 skipped**
  (the `I` fixture's `pytest.importorskip("talib")`), **0 failed** — proves
  the ordinary default suite stays green without masking a real TA-Lib
  regression, since the dedicated `indicators-tests` CI job (added to
  `ci.yml`, installs `.[dev,indicators]`, and now runs on both Python 3.10
  and 3.11 — see the CI correction below) runs all 48 for real.
- `python3 .claude/skills/run-agentic-trading-desk/driver.py` (TA-Lib
  installed) — all checks passed, including the CLI self-test, file-input,
  short-series-warning, and direct-import (`compute()` callable) paths for
  `indicators.py`, plus the unaffected `macro_pillar.py`/`score.py` checks.
- `pytest tests/ -q --tb=short` (full offline suite, TA-Lib installed) —
  **2843 passed, 17 skipped, 0 failed** (2795 passed in PR 3's baseline +
  the 48 tests now in this file; the 17 skipped count is unchanged from
  PR 3, confirming no other test file was affected).

**Wheel installation result:** `pip install -e ".[indicators]"` resolved
`TA-Lib==0.7.1` from a prebuilt wheel (`ta_lib-0.7.1-cp314-cp314-
macosx_14_0_arm64.whl`) with no compilation, reconfirming PR 1's finding
on this machine (macOS arm64) against a newer local Python (3.14, ahead of
the declared `>=3.10` floor and CI's 3.11). No system package or source
install fallback was needed or added.

**No legacy fallback path remains:** the hand-written EMA/RSI/MACD/TRIX/
Bollinger formulas are fully deleted from `scripts/indicators.py` — there
is no code path that computes these values without going through
`talib.EMA`/`talib.RSI`/`talib.BBANDS` (proven behaviorally by the spy
tests above, not just by their absence from the source). The thin custom
adapter described in "Correction (PR #12 review): authority boundary"
below is retained *on top of* those TA-Lib primitives, not as an
alternative to them — it is not a fallback, since it never runs instead
of TA-Lib, only in composition with it. No `pandas-ta-classic` dependency
was added — wheel resolution did not fail on any platform this PR could
verify (macOS arm64 locally; the `indicators-tests` CI job verifies
Linux/`ubuntu-latest` on both Python 3.10 and 3.11 — see the CI correction
below).

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live data was fetched; no real
provider/broker/paper/live order calls were made; the scheduler was not
enabled.

**Correction (2026-07-26, PR #12 review): authority boundary, fail-closed
input validation, and CI matrix.** Three issues from the initial PR 4
submission were fixed on the same branch, without starting PR 5:

1. **Authority claim was overstated.** The original text above claimed
   "TA-Lib is now the sole calculation authority" and "the custom EMA/
   RSI/MACD/TRIX/Bollinger formulas are fully deleted ... TA-Lib is the
   only calculation code path" without qualification. That is not
   accurate: `macd` and `trix` compose `talib.EMA` calls with custom
   subtraction/alignment/percent-change/zero-denominator logic, `%B` is a
   custom division on top of `talib.BBANDS`'s bands, and the flat-price
   RSI override is a custom boundary correction on top of `talib.RSI`.
   These adapters are justified (documented above, under "Intentional
   semantic differences and reconciliations") and are not from-scratch
   competing formulas, but the docs should not have implied no custom
   calculation logic remains. **Corrected boundary:** TA-Lib is
   authoritative for the EMA, RSI, and BBANDS *primitives*; MACD/TRIX
   composition and this stack's compatibility semantics remain in a thin
   custom adapter, retained indefinitely (not pending further removal).
   `COMPONENT_MATRIX.md` and `REMOVAL_MANIFEST.md` are corrected to match
   — the removal-manifest row is no longer described as a full closure.
   `tests/unit/test_talib_is_sole_authority_no_custom_formulas_remain`
   (a source-text substring check, which cannot prove the claim its name
   made) is removed and replaced by five behavioral spy tests (listed
   above) that patch the real `talib.*` functions with `wraps=` and
   assert each primitive is actually invoked, and that `talib.MACD()`/
   `talib.TRIX()` are never called.
2. **`_nan_to_none` could mask stale/corrupted data as warm-up.**
   Converting every TA-Lib `NaN` to `None` unconditionally meant a `NaN`
   caused by malformed upstream data — not just the documented warm-up —
   would be indistinguishable from warm-up, get discarded by `_strip()`,
   and let `compute()` silently return an older, stale indicator value.
   Fixed by: (a) a new shared validation boundary, `_validate_prices`
   (one-dimensional, non-nested, real int/float only — explicitly
   rejecting booleans, strings, `None`, `NaN`, and +/-infinity) and
   `_validate_period` (positive integers only), called at the top of
   every public function (`ema_series`, `rsi_wilder`, `macd` — which also
   now requires `fast < slow` — `trix`, `bollinger`, `compute`) before any
   TA-Lib call, raising the new `IndicatorInputError(ValueError)` with a
   specific, indexed message rather than silently coercing anything; (b)
   `_nan_to_none` now takes an `expected_warmup` bound (computed per call
   site from the documented lookback, e.g. `min(period - 1, n)` for EMA)
   and only converts a `NaN` within that window to `None` — a `NaN` at or
   after it raises `IndicatorInputError` instead, including one injected
   by mocking `talib.EMA`/`talib.BBANDS` directly in a test, proving the
   check fires even when TA-Lib itself is the (simulated) source of the
   unexpected `NaN`, not only when caught upstream at the validation
   boundary; (c) the CLI (`main()`) no longer does a blind
   `raw["close"]` (a raw `KeyError` for a malformed shape) or
   `[float(x) for x in close]` (which silently coerced numeric strings);
   a new `_extract_close_from_cli_input` gives an actionable
   `IndicatorInputError` for a missing `"close"` key or a non-list/
   non-dict JSON root, and `json.dumps(..., allow_nan=False)` is now the
   CLI's own last-line defense against ever writing `NaN`/`Infinity`
   tokens into JSON output, independent of whether `compute()` itself
   stays well-behaved. 23 new tests in `tests/unit/test_indicators.py`
   cover every category from the review: NaN at the first/middle/final
   bar, +infinity, -infinity, empty input, a nested array, booleans, a
   numeric string, an invalid (zero/negative/non-integer) period, MACD
   `fast >= slow`, a non-warm-up NaN from a mocked `talib.EMA` call, a
   non-warm-up NaN from a mocked `talib.BBANDS` call, and four CLI-shape
   cases (missing key, wrong root type, malformed element with no stdout
   leaked, and the `allow_nan=False` guard exercised end-to-end by
   monkeypatching `compute` itself).
3. **CI only ran the indicators-focused tests on Python 3.11**, even
   though the project declares `requires-python = ">=3.10"` and the
   separate `python-3-10-floor` job installs only `.[dev]` (no
   `indicators` extra), so the TA-Lib-backed tests silently skipped on
   3.10 rather than actually running. `indicators-tests` in `ci.yml` is
   now a matrix over Python 3.10 and 3.11; both legs install
   `.[dev,indicators]`, run `pip check`, and run
   `pytest tests/unit/test_indicators.py -q --tb=short` as a blocking
   step — confirmed locally to actually execute (not skip) all 48 tests
   on 3.10-equivalent behavior via the same `.[dev,indicators]` install
   path the 3.11 leg uses (this development machine only has Python 3.14
   available; both CI legs are verified via their explicit
   `actions/setup-python` version pin, not a local floor check).

Re-run after these fixes: `pytest tests/unit/test_indicators.py -q
--tb=short` — **48 passed**; `pytest tests/ -q --tb=short` — **2843
passed, 17 skipped, 0 failed**. All three findings addressed on the
existing `migration/04-talib-indicators` branch; PR 5 was not started.

## Completed work (PR 5)

**Scope:** `pyproject.toml` (new `research` optional-dependency group),
`.github/workflows/ci.yml` (new `research-tests` job; `research` added to
the existing `dependency-extras-smoke` matrix — that matrix was added in
PR 1, not PR 4, so extending it is in scope), a new
`src/trading_research/vector_research/` package
(`__init__.py`, `adapter.py`), two new test files
(`tests/unit/test_vector_research_adapter.py`,
`tests/unit/test_vector_research_import_boundary.py`), plus this file,
`COMPONENT_MATRIX.md`, and `DECISIONS.md` (D4 resolution). No file under
`scripts/`, `analysis/`, `paper_runtime/src/`, or `config/` was modified;
`scripts/indicators.py`, `analysis/indicators.py`,
`tests/unit/test_indicators.py`, and the `indicators-tests` /
`python-3-10-floor` CI jobs (both PR 4/PR 1 files, respectively) were not
touched.

**License decision:** presented with the choice required by
`DECISIONS.md` D4 — evaluate an OSI-approved vectorized-research
alternative, or obtain explicit owner approval for VectorBT's Apache-2.0 +
Commons Clause terms — the repository owner explicitly approved VectorBT
for this repository's internal, non-commercial research/paper-trading use.
No OSI-approved alternative was evaluated as a result (the owner-approval
branch of the bounded prompt's conditional was taken, not the
alternative-evaluation branch). Recorded in `DECISIONS.md` D4 with the
date and scope of the approval (commercial/hosted use is explicitly out of
scope of this approval and would need separate review).

**Re-verified 2026-07-26 against the PyPI JSON API** (live lookup, not
recalled — matching PR 1/PR 4 practice): `vectorbt` 1.1.0,
`Requires-Python: >=3.11,<3.15`, Apache-2.0 + Commons Clause license
metadata, prebuilt wheel present, `requires_dist` pins `numpy>=2.4.6` and
`pandas>=3.0.3,<4.0`. Identical to the figures already recorded in
`DEPENDENCY_MATRIX.md` Section 1 from the PR 0 pass — no drift found.

**New `research` optional-dependency group** (`pyproject.toml`):
`vectorbt>=1.1.0,<1.2`. Verified wheel-only install (`pip install
--only-binary=:all:`) on a scratch Python 3.11 macOS arm64 venv, both
combined with `.[dev]` and alone (matching the "never combine extras"
isolation convention already used for `indicators`/`analytics`/
`observability`): `pip check` clean in both cases, `import vectorbt`
succeeds (`1.1.0`), resolved `numpy==2.4.6`/`pandas==3.0.5` — no conflict
with this project's `pandas>=2.2.0` base floor or any other base
dependency. No source compilation was needed on this platform.

**Python floor: left unchanged at `>=3.10` project-wide.** VectorBT's own
`Requires-Python: >=3.11,<3.15` classifier is narrower than the project
floor, so `pip install -e ".[research]"` specifically requires a Python
3.11+ interpreter — this fails to resolve on Python 3.10, by design and
documented in `pyproject.toml`'s `research` extra comment, not silently.
`DEPENDENCY_MATRIX.md`'s historical PR 0 paragraph anticipated raising the
global floor to `>=3.11` once VectorBT was approved; this PR deliberately
does not, because doing so would also require editing the `indicators-tests`
CI matrix (added in PR 4) to stop asserting a `python-3-10-floor`-compatible
install path that would no longer be true — and this PR's bounded prompt
explicitly excludes touching any PR 4 file. A future PR without that
constraint remains free to raise the floor project-wide. No Python 3.10
interpreter was available on this development machine (consistent with
PR 1/PR 4's note); the floor-mismatch conclusion rests on VectorBT's own
declared PyPI classifier, not a local reproduction.

**New capability: `src/trading_research/vector_research/`** — a thin
adapter for vectorized signal-matrix parameter sweeps, built on
`vectorbt.Portfolio.from_signals`:

* `run_parameter_sweep(close, entries, exits, *, init_cash=100_000.0,
  fees=0.0) -> ParameterSweepResult` — `entries`/`exits` are boolean
  signal-matrix DataFrames (one column per parameter combination) sharing
  `close`'s index; returns per-column `total_return`/`sharpe_ratio`/
  `max_drawdown` plus the underlying `vectorbt.Portfolio` for deeper
  inspection.
* A shared fail-closed validation boundary (`VectorResearchInputError`,
  a `ValueError` subclass) rejects non-`Series`/non-`DataFrame` input,
  empty input, NaN or non-positive prices, an index mismatch between
  `close` and the signal frames, non-boolean signal frames, non-positive
  `init_cash`, and negative `fees` — all before any VectorBT call,
  matching the fail-closed pattern already established for TA-Lib in
  `scripts/indicators.py`.
* `import vectorbt` happens at module scope inside a `try`/`except
  ImportError` that re-raises with an actionable
  `pip install -e ".[research]"` message — no fallback formula, same
  pattern as `scripts/indicators.py`'s TA-Lib guard.
* **No execution authority:** `ParameterSweepResult` is a frozen dataclass
  with no `submit`/`order`/`broker`/`authorize` surface (asserted by a
  dedicated test); nothing in this package is imported by `paper_books`,
  `external_broker`, any scheduler, or any service module — it has zero
  callers today, matching the "New capability" / "no removal" row in
  `COMPONENT_MATRIX.md`.

**Import-boundary tests** (`tests/unit/test_vector_research_import_boundary.py`,
AST-based, analogous to
`test_lumibot_adapter.py::test_no_lumibot_import_outside_runtime_package`,
not a source-text substring check):

1. `vectorbt` is never imported anywhere under `src/trading_research/`
   outside `vector_research/`.
2. `vector_research/` never imports `trading_paper_runtime` (or
   `paper_runtime`).
3. `paper_runtime/src/` never imports `vector_research`.

All three passed against the actual new files (verified both in an
environment without VectorBT installed, where they still run
unconditionally since they only parse source with `ast`, and in the
Python 3.11 scratch environment with VectorBT installed).

**Tests run:**
- `pytest tests/unit/test_vector_research_adapter.py
  tests/unit/test_vector_research_import_boundary.py -q --tb=short`,
  VectorBT **not** installed (simulating `main-tests` CI, `.[dev]` only) —
  **5 passed** (missing-dependency guard plus the 4 import-boundary tests,
  none of which require VectorBT), **10 skipped** (the `adapter` fixture's
  `pytest.importorskip("vectorbt")`), **0 failed**.
- Same command, VectorBT installed via `.[dev,research]` on a scratch
  Python 3.11 venv (simulating the new `research-tests` CI job) —
  **15 passed, 0 failed** — every behavioral test (parameter-sweep shape,
  no-execution-authority surface, and all nine fail-closed input-validation
  cases: non-Series close, empty close, NaN price, non-positive price,
  signal-frame index mismatch, non-boolean signal frame, non-positive
  `init_cash`, negative `fees`) passes for real, not just via the skip
  path.
- `pytest tests/ -q --tb=short` (full offline suite, VectorBT **not**
  installed, matching `main-tests`) — **2848 passed, 27 skipped, 0
  failed** (2843 passed in PR 4's baseline + 5 new unconditional passes;
  17 skipped in PR 4's baseline + 10 new VectorBT-gated skips — no other
  test file's pass/skip count changed).
- `pytest tests/ -q --tb=short` on the Python 3.11 `.[dev,research]`
  scratch venv — **2801 passed, 65 skipped, 0 failed** (fewer passes than
  the `.[dev]`-only run above because `indicators`/`analytics`/
  `observability` are not installed in this venv, so their tests skip
  there instead — expected, and consistent with the project's
  never-combine-extras isolation convention).

**Wheel installation result:** `vectorbt==1.1.0` resolved from a prebuilt
wheel with `--only-binary=:all:` on a scratch macOS arm64 Python 3.11 venv;
no compilation, no system package required.

**No legacy fallback path remains — because there was no prior
implementation to fall back to.** This is wholly new, additive capability;
no file under `src/`, `scripts/`, `paper_runtime/src/`, or `config/` was
replaced or removed.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live data was fetched; the scheduler was
not enabled; the new adapter has zero callers and is not reachable from any
scheduled or live code path.

## Completed work (pre-step before PR 6) — COMPLETE, ADR 0009 Accepted

**Scope:** `docs/library-migration/pre-step-06/` (new: `EVALUATION.md`,
`spike_output.txt`, `guards.py`, `spike_backtest.py`,
`dotenv_sentinel_output.txt`, `run_dotenv_sentinel.sh`,
`summarize_dotenv_sentinel.py`, `sentinel_dotenv/`),
`docs/adr/0009-lumibot-backtest-distribution-boundary.md` (new, **Accepted**),
`DECISIONS.md` (D4 correction, resolution, and D5 reconciliation),
`ARCHITECTURE.md`, `MASTER_PLAN.md`, `docs/INDEX.md`, and this file. No file
under `src/`, `scripts/`, `paper_runtime/src/`, `tests/`, or `config/` was
modified.

**Outcome:** the repository owner accepted ADR 0009 and selected Option B — an
isolated, credential-free `backtest_runtime/` distribution. The pre-step is
complete; PR 6 is unblocked and not started.

### First pass withdrawn

The first pass at this pre-step ran under **Sonnet**, not the **Opus review**
`MASTER_PLAN.md`'s "Pre-step before PR 6" row requires. It recorded a decision
— a second in-process import boundary at
`src/trading_research/backtesting/lumibot_backtest/` — marked the pre-step
complete on that basis, declared PR 6 unblocked, and wrote a PR 6
implementation prompt. All four are withdrawn. The required Opus review has
since run, with the pinned-version feasibility spike the first pass did not
run, and found the earlier proposal's premises false.

The pre-step is now complete and PR 6 is unblocked again — but on entirely
different grounds: the Opus review, the feasibility spike, the
sentinel-`.env` suppression proof, and the owner's acceptance of ADR 0009,
selecting `backtest_runtime/` rather than an in-process package. The withdrawn
PR 6 implementation prompt has **not** been reinstated; writing one is PR 6's
own first step.

### Decision accepted (2026-08-01)

LumiBot backtest mode gets its own isolated, credential-free distribution,
**`backtest_runtime/`** — a third top-level package beside the main project
and `paper_runtime/`: own `pyproject.toml`, explicitly declared
`requires-python`, `lumibot==4.5.78` as a base dependency, no broker
credentials, no live-submission operations, a deterministic file-based
fixture/result contract, dedicated tests, and a blocking CI job.

Rejected: extending `paper_runtime`'s credentialed protocol (Option A);
installing LumiBot into the main environment (Option C, still
`ResolutionImpossible`); and the first pass's in-process package (Option D).
Decision matrix and full reasoning in
`docs/library-migration/pre-step-06/EVALUATION.md`.

### Feasibility spike (exactly `lumibot==4.5.78`)

Clean disposable venv, Python 3.11.15, macOS arm64. Network patched to fail
closed (`getaddrinfo`/`create_connection`/`connect`/`connect_ex`) and every
`os.environ` read recorded, both installed **before** `lumibot` was imported.
Raw output: `pre-step-06/spike_output.txt`.

Passed:

- installs alone and `pip check` is clean — 309 packages, 40.7s, ~1.9 GB;
- installed version asserted **exactly `4.5.78`** in-process;
- `lumibot.backtesting.pandas_backtesting.PandasDataBacktesting` imports;
- one minimal caller-supplied 10-bar DataFrame backtest runs to completion;
- all input bars are caller supplied — perturbing one bar's close moved
  `total_return` from `0.00065` to `0.00115`, with no other input changed;
- **deterministic** — two identical runs produced bit-identical results;
- **zero** outbound network attempts *when the environment holds no broker
  credentials*.

Failed — and these findings decided the architecture:

1. **`import lumibot` looks for broker credentials unconditionally.** 277
   distinct environment variables read at import, 64 credential-named,
   including `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `IB_PASSWORD`, and
   `ANTHROPIC_API_KEY`. Nothing suppresses this, which is why the requirement
   placed on `backtest_runtime/` is that no credential *value* is available —
   not "zero credential reads."
2. **With credentials visible, the offline backtest opened a live broker
   connection** — 177 blocked attempts to `paper-api.alpaca.markets:443` (696
   in an earlier identical run; a background retry thread drives the count),
   with LumiBot logging `Waiting for the socket stream connection to be
   established`. Credentials scrubbed: zero attempts, byte-identical results.
   The connection contributes nothing to the backtest.
3. **LumiBot loads `.env` from the current working directory** at import,
   walking upward from both the script directory and the CWD to the filesystem
   root. This repository's `.env.example` documents exactly `ALPACA_API_KEY`
   and `ALPACA_API_SECRET`, and `.env` is gitignored — so an in-process import
   from a repo-root `pytest` run would load the operator's real paper-broker
   credentials and then connect.
4. **stdout is contaminated** — 382–543 bytes per run (startup banner plus
   ANSI-escaped progress bars), unchanged from the 4.5.74 behavior ADR 0002
   already had to work around in `paper_runtime`.

### Credential-safety sentinel proof (2026-08-01)

Finding 3 above says an operator's `.env` gets loaded; this proof establishes
the exact mechanism that stops it, and measures a second leak path — broker
credentials inherited from the process environment — alongside it. Raw
evidence: `pre-step-06/dotenv_sentinel_output.txt`; reproduce with
`pre-step-06/run_dotenv_sentinel.sh <disposable-workdir>`. The summariser
asserts every claim against the measured JSON and exits non-zero on any
failure, so the record fails closed rather than being narrated.

**First mechanism — `.env` suppression:** `LUMIBOT_DISABLE_DOTENV=1`, set in `os.environ` **before**
`import lumibot`. `lumibot/credentials.py` reads it at module scope before any
discovery runs and then skips both the script-directory walk and the
working-directory walk, so `dotenv.load_dotenv` is never called. No other
mechanism works at the pinned versions (`lumibot==4.5.78`,
`python-dotenv==1.2.2`): the two base directories come from `sys.argv[0]` and
`os.getcwd()`, neither configurable, and `find_and_load_dotenv()` walks upward
to the filesystem root — so running from an empty directory does not help.

**Second mechanism — the credential scrub.** The dotenv flag protects one path
only. Broker credentials already present in the process environment are
removed by deleting every credential-named variable from `os.environ` before
the import, which is what ADR 0009 Decision 2 requires of the entry point.

**Setup:** a sentinel `.env` and `.env.local` holding unique, obviously-fake
Alpaca values (`SENTINEL-DOTENV-7f3a9c21e4b8-…`) placed in the working
directory the spike is launched from, and a second, distinct set
(`SENTINEL-PROCENV-3d5b18ca9027-…`) exported into the child process
environment before the interpreter starts. Two tokens so the two leak paths
can never be confused. No real credential was read, used, or exposed; the
runner refuses to start if a pre-existing `.env` sits anywhere in its ancestor
chain, and where a control inherits the operator's real ambient environment
the record reports a count rather than enumerating variable names.

**Positive controls — the sentinels are real.** Each control omits one
protection and demonstrates the hazard it prevents; the summariser fails if a
control stops leaking. In every one, sentinel values reach `os.environ`,
propagate into `ALPACA_CONFIG`, construct a live Alpaca broker object, and
drive blocked outbound attempts to `paper-api.alpaca.markets:443`:

- **S1** (62 attempts) — `.env` in the CWD, suppression off;
- **S5** (56 attempts) — CWD is an **empty subdirectory** whose parent holds
  the `.env`; this is what rules out `chdir` as a mechanism;
- **P1** (56 attempts) — fake credentials inherited from the parent process
  with the scrub off, **even with `LUMIBOT_DISABLE_DOTENV=1` set**; the flag
  does not protect this path.

The attempt counts vary between runs (a background retry thread drives them),
so the proof asserts that a control attempts *at all*, never how many times.

**In the protected runs (S0/S2/S3/S4 with the sentinel dotenv files still in
the CWD, and P2 with the fake credentials still inherited):**

- credential-named variables whose value came **from the process
  environment**: **0** — the strict metric;
- credential-named variables resolving to a value at all: exactly **3**, all
  LumiBot's own hardcoded `.get()` defaults (`COINBASE_SANDBOX` → `"false"`,
  `IB_USE_PAPER_ACCOUNT` → `"true"`, `DATADOWNLOADER_API_KEY_HEADER` →
  `"X-Downloader-Key"`, a header name). Attribution is measured, not
  interpreted: the tracer resolves key presence separately from the value, so
  a default returned for an absent key is recorded as not-from-environment.
  The set is asserted **exactly**, so a fourth credential-named value fails
  the check;
- sentinel values in `os.environ` after the scrub, after import, and after the
  run: **none**; in any LumiBot `*_CONFIG`: **none**. For P2 the scrub is
  shown working at the exact point it must — inherited keys present before it,
  gone immediately after, still before `import lumibot`;
- `lumibot.credentials.broker` and `.data_source` after import: **both
  `None`** — no broker or live data provider initialized;
- outbound network attempts: **0**;
- determinism: S2 and S3 produced bit-identical result dicts, and both equal
  the no-`.env` baseline (S0) exactly — as does P2;
- caller-supplied data: S4 perturbed one input bar and the result moved
  (`total_return` `0.00065` → `0.00115`), so the backtest consumed only the
  10-bar fixture the caller passed in.

Option C re-verified at the current pin: `pip install -e <root>
lumibot==4.5.78` fails `ResolutionImpossible` on two independent walls —
`litellm`'s exact `jsonschema==4.23.0` against this repository's
`jsonschema>=4.26.0` floor (confirming `DECISIONS.md` D5 at `4.5.78`), plus a
`google-genai`/`google-adk` conflict.

### Collateral finding: the AST import boundary does not run in CI

`tests/unit/test_lumibot_adapter.py` opens with a module-level
`pytest.importorskip("lumibot")`, so
`test_no_lumibot_import_outside_runtime_package` **skips** under `main-tests`
(`.[dev]` only). The repeated claim that the LumiBot import boundary is
"AST-enforced, not documentation-only" is not true as things stand; the only
boundary test that actually runs is
`tests/unit/test_runtime_client_no_lumibot_import.py`, over an explicit list
of 17 named files. Repairing this is a PR 6 requirement recorded in ADR 0009.
Not fixed here — this pre-step touches no test file.

### Figures corrected

`paper-runtime.**v2**`, **19 operations** (not "v1, 9 operations"); LumiBot
4.5.78 installs **309 packages / ~1.9 GB** (not "~140 transitive packages").
Both figures had been carried forward unchecked from ADR 0001/0002.

### Tests run

This pre-step changes documentation only, so no test outcome can be
attributed to it; the suite was run to confirm exactly that.

- `pytest tests/ -q --tb=short` on a scratch Python 3.11 `.[dev]`-only venv —
  **2791 passed, 75 skipped, 0 failed.** This is not directly comparable to
  PR 5's recorded 2848 passed / 27 skipped: that figure came from an
  environment that also had the `indicators` extra present, so its TA-Lib
  tests passed where they skip here. No test file was modified by this
  pre-step.
- `pytest tests/unit/test_lumibot_adapter.py
  tests/unit/test_runtime_client_no_lumibot_import.py -q -rs` on the same
  venv — **2 passed, 1 skipped**, the skip being
  `test_lumibot_adapter.py:27` taking the whole module with it. This is the
  evidence for the collateral finding above.
- `pre-step-06/run_dotenv_sentinel.sh` on a disposable Python 3.11.15 venv
  holding `lumibot==4.5.78` alone — **8 runs, all assertions passed**
  (6 `.env`-path runs, 2 process-environment runs). Output committed as
  `pre-step-06/dotenv_sentinel_output.txt`. The summariser's fail-closed
  behavior was itself verified by tampering with copies of the result JSON:
  an injected extra credential-named value and a silently-disabled scrub were
  both caught, exit 1.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live data was fetched; the scheduler was
not enabled. Every network attempt made during the spike and the sentinel
proof was blocked by the fail-closed guard and is itemized in
`pre-step-06/spike_output.txt` and `pre-step-06/dotenv_sentinel_output.txt`.
The sentinel proof used fabricated credential values only, ran entirely inside
a disposable scratch directory, and never read the repository's own `.env`.
### PR 5 review-fix round (2026-07-26)

Review of the merged PR (#13) found five categories of issue in the
original `vector_research` adapter, all fixed on this branch before any
LumiBot/PR 6 work began. **Scope:** `src/trading_research/vector_research/`
(`adapter.py`, `__init__.py`), both PR 5 test files, and `.github/workflows/
ci.yml`'s `research-tests` job. No PR 4 file, no LumiBot/backtest file, and
no file outside this PR's original scope was touched.

**1. Look-ahead bias eliminated — next-session execution policy.**
VectorBT's `Portfolio.from_signals` was verified (directly, not assumed) to
fill a `True` entry/exit at that *same* bar's close — confirmed with a
controlled fixture: an entry set at index 5 of a 10-bar series fills at
index 5, not index 6. Passed straight through, a signal computed from bar
`t`'s own close could therefore trade at that identical close: look-ahead
bias. `run_parameter_sweep`'s parameters were renamed `entry_signals`/
`exit_signals` (from `entries`/`exits`) to make explicit that they are
signal-*generation* matrices, not execution-ready ones, and the function
now shifts both forward exactly one bar
(`.shift(1, fill_value=False)`) before calling VectorBT — matching this
repository's existing backtest convention that a signal generated during a
session becomes eligible only on the next session. There is no parameter
to disable this shift; a signal on the final bar has no later bar to
execute on and produces no fill, which is intentional. Proven by five
tests: a one-bar price spike cannot be bought at the spike bar (the fill
lands one bar later, at the following bar's close, not the spike price); a
signal on the final bar produces zero trades; entry lands on the first
eligible subsequent bar; exit follows the identical rule; and a
whole-portfolio parity check compares the adapter's output against calling
VectorBT directly with a manually pre-shifted signal matrix (`orders.
records_readable` compared frame-for-frame), proving the shift is applied
faithfully rather than merely on the specific indices the other four tests
happen to check.

**2. Temporal structure and parameter alignment validated —
daily-session-only, timezone-aware, minimum-history contract.** The
validation boundary (still entirely before any VectorBT call) now also
requires: `close.index` is a `DatetimeIndex`; timezone-aware (`tz is not
None`) — this mirrors `evaluation/market_calendar.py`'s existing
`is_market_open` convention, which already "requires a timezone-aware
datetime"; unique and strictly increasing; daily-session spacing (each
consecutive gap `>= 1` day and `<= 10` days — tolerates weekend/holiday
clusters, rejects intraday, weekly, monthly, or otherwise irregular bar
spacing); and at least 10 bars (**insufficient-data behavior**: below this,
`run_parameter_sweep` raises `VectorResearchInputError` before calling
VectorBT at all, rather than returning a degraded or partially-meaningful
result — a stricter, more fail-closed choice than exposing an
"insufficient_history" *metric status*, made because a too-short series
makes the entire sweep meaningless, not just one metric on it).
`entry_signals`/`exit_signals` must share `close`'s index and column set
*exactly*, including timezone — `pandas.Index.equals` was verified
(directly) to treat two same-instant indexes with different timezone
labels as unequal, so the existing index-equality check already catches a
timezone mismatch without needing a separate check. Signal columns must be
unique, and `entry_signals`/`exit_signals` must have identical columns in
identical order (previously unchecked). `freq="1D"` is no longer an
unconditional assumption applied to arbitrary input — it is now the
documented, enforced contract for **supported time-series frequency**:
daily-session data only, validated up front, not silently assumed for
whatever spacing happens to be supplied. 15 new/updated tests cover every
listed scenario: non-`DatetimeIndex`, tz-naive index, duplicate timestamps,
unsorted timestamps, intraday spacing, an irregular (30-day) gap, a
too-short series, infinite price, signal-frame index mismatch, a signal
timezone mismatch, duplicate signal columns, mismatched entry/exit column
sets, and non-finite/negative `init_cash`/`fees`.

**3. Analytics authority preserved — VectorBT metrics are explicitly
exploratory, never authoritative.** `evaluation/metrics.py` (migrating to
`empyrical-reloaded` in PR 11) remains the sole authority for any reported,
compared, or audited performance figure; VectorBT's own vectorized
statistics are useful only for coarse relative ranking inside a sweep.
`ParameterSweepResult.metric_source` is now a literal constant,
`"VECTORBT_EXPLORATORY"` (exported as `vector_research.METRIC_SOURCE`),
plus explicit `frequency` (`"1D"`) and `year_freq` (`"365 days"`, passed
explicitly to `Portfolio.sharpe_ratio(year_freq=...)` rather than relying
on VectorBT's internal default, so the annualization assumption cannot
silently drift with a VectorBT version bump) fields recording exactly what
annualization assumption produced the numbers — verified these are
VectorBT's own defaults, not an invented figure, by calling
`sharpe_ratio(year_freq="365 days")` and `sharpe_ratio()` side by side and
confirming identical output. `total_return`/`sharpe_ratio`/`max_drawdown`
are no longer raw `pandas.Series`; each column now maps to an
`ExploratoryMetric(value, status)`, `status` being `"ok"`, `"no_trades"`,
`"zero_variance"`, or `"non_finite"` — `value` is `None` whenever
`status != "ok"`, so a raw NaN/inf can never reach ranking or selection
logic. Verified directly against real VectorBT output (not assumed): an
all-`False` signal column reports `sharpe_ratio = inf` from VectorBT itself
(not NaN) with a zero-trade count, classified `"no_trades"`; a flat
(constant) price with one round-tripped trade reports the same `inf` from
VectorBT but with a non-zero trade count and zero return-variance,
classified `"zero_variance"`, distinguishing "never traded" from "traded
into a degenerate, zero-volatility case" — the review's requested
distinction; one real trade on ordinary data gives a finite
`"ok"`-status Sharpe. A sixth test forces a `"non_finite"` classification
independent of both of the above by patching
`vectorbt.Portfolio.sharpe_ratio` to return `NaN` on a fixture with
non-zero trades and non-zero return variance, proving the catch-all path
fires on its own rather than only ever being reachable through the
zero-variance path.

**4. Advisory-only boundary enforced structurally, repository-wide.** The
previous "no `submit`/`order`/`broker`/`authorize` attribute" check on
`ParameterSweepResult` is retained but was correctly identified as
insufficient on its own. `tests/unit/test_vector_research_import_boundary.py`
now also asserts, by AST inspection (not a source-text substring check,
and correctly handling both absolute and relative import forms — a
naive top-level-name check misses `from trading_research.vector_research
import adapter` entirely, since `vector_research` is a *sub*-package, not
a top-level import target): **no production module anywhere under
`src/trading_research/` outside `vector_research/` itself may import it.**
This is deliberately a single repository-wide rule rather than an
enumerated list of `execution/`, `paper_books/`, `runtime/`, scheduler, or
broker-gateway paths — a curated list would need updating every time a new
package is added and could silently under-cover a future path; the
single rule cannot. A future consumer requires an explicit architecture
decision and a test update, not a silent import — documented directly in
`adapter.py`'s module docstring, including that `ParameterSweepResult.
portfolio` (the raw `vectorbt.Portfolio`) must never be passed to
`paper_books`, `execution`, `runtime`, or any broker/scheduling interface
without an explicit, reviewed conversion into a framework-neutral research
DTO — no such conversion exists yet because no consumer exists yet, and
this repository-wide import-boundary test is what would force that
decision to be explicit whenever a consumer is eventually proposed.

**5. Research-environment CI gate strengthened.** The `research-tests`
CI job (Python 3.11, `.[dev,research]`) now runs the full offline suite
(`pytest tests/ -q --tb=short`) as a second blocking step, immediately
after the focused `vector_research` file — not just the focused file alone.
**Full-suite research-environment CI result:** run locally against the
same `.[dev,research]` environment this job installs — **2826 passed, 65
skipped, 0 failed**. Every skip in that run was confirmed to be an expected
optional-extra skip (`indicators`/`analytics`/`observability` are not
installed in this environment, so their `pytest.importorskip`-guarded tests
skip there, exactly as `indicators-tests` already demonstrates for the
`indicators` extra in reverse) — none is a failure hidden because a module
could not import; `pip check` was also clean in this environment.

**Tests run (review-fix round):**
- `pytest tests/unit/test_vector_research_adapter.py
  tests/unit/test_vector_research_import_boundary.py -q --tb=short`,
  VectorBT **not** installed — **6 passed** (missing-dependency guard plus
  5 import-boundary tests, none of which require VectorBT), **34 skipped**,
  **0 failed**.
- Same command on a Python 3.11 scratch venv with `.[dev,research]`
  installed — **40 passed, 0 failed** — every behavioral test (signal
  timing, temporal validation, exploratory-metric classification,
  advisory-only surface, and the repository-wide import boundary) passes
  for real.
- `pytest tests/ -q --tb=short` on the same `.[dev,research]` scratch venv
  — **2826 passed, 65 skipped, 0 failed**.
- `pytest tests/ -q --tb=short` on the plain `.[dev]` `.venv` (no
  VectorBT) — **2849 passed, 51 skipped, 0 failed** (one apparent failure,
  `test_codex_provider.py::test_timeout_maps_to_provider_timeout_and_
  reaps_threads`, was observed on one run of the full suite under system
  load and reproduced as a pass both in isolation and on an immediate
  full-suite re-run; it is pre-existing thread-timing flakiness unrelated
  to this PR — no file it covers was touched here).

**API note:** `run_parameter_sweep`'s `entries`/`exits` parameters were
renamed to `entry_signals`/`exit_signals` as part of fix 1 above. This is a
breaking rename of a function that has zero callers anywhere in the
repository (confirmed by the import-boundary test in fix 4), so it carries
no migration cost.
## Completed work (PR 7) — MERGED (`5b9e1e3`, PR #19)

**Scope:** `docs/library-migration/pr7/` (fixture set, two runner scripts, a
LumiBot timing probe, a comparator, a shell driver, the written report), the
bounded reference-strategy v2 extension in `backtest_runtime/`, and
`tests/unit/test_pr7_parity_report.py`. Plus `DECISIONS.md` D6, this file, and
the `MASTER_PLAN.md` PR 7 row. **The legacy engine is untouched:**
`backtesting/engine.py`, `backtesting/models.py` and every `paper_books` module
are byte-unchanged (`git diff main -- src/` is empty).

### Review round 1 (2026-08-02)

Six items addressed on the same branch, before merge.

**1. The Option A/B decision was corrected to bounded Option B.** The first
pass claimed `case_a_buy_and_hold` matched the reference strategy exactly. It
did not: `backtest_runtime` entered 2024-01-03 at 100.5, the legacy engine
2024-01-04 at 102.0. The offset is structural — the legacy engine cannot enter
before its third bar (eligibility is next-session-only and ATR needs
`atr_period + 1` prior bars), while reference strategy v1 always bought on its
first iteration, the second bar — so Option A could never produce an identical
case, which is exactly the condition for Option B.

Reference strategy **v2** therefore adds exactly one control,
`strategy.entry_after_session` (`null` = v1 behavior; a date defers the same
single buy to the first iteration strictly after it). Versioned, not defaulted:
`backtest_runtime.input.v2` / `backtest_runtime.reference_strategy.v2`, a v1
document now explicitly rejected, the field folded into `strategy_digest`, and a
value at or after the last bar rejected rather than silently never entering.
**No** sell, stop, target, second order, order type, scheduler, fetcher or
broker interaction was added; `benchmark_asset=None` and
`analyze_backtest=False` stay hardcoded; every ADR 0009 Decision 2 credential-,
network- and isolation-safety property is untouched and still asserted by that
distribution's blocking suite.

**The exact case it bought** (`case_f_exact_entry_parity`): both engines enter
**2024-01-04 at 101.0 for 10 shares** and agree exactly on final cash (98 990),
final equity (99 992), final value, end position (10 @ 101.0), maximum drawdown
(−0.00018), and on cash, equity, unrealized P&L, realized P&L and drawdown for
**every co-dated session except the entry session**. *(Superseded by review
round 2: the entry session is no longer excluded, and the exclusion's premise —
that `backtest_runtime`'s state lagged by one session — was itself derived from
an inferred fill session rather than from LumiBot's own record.)*

**2. Look-ahead removed from the legacy fixture.** Each signal's `limit_price`
was the *entry* session's high — a bar that had not happened when the signal was
generated. It is now a fixed ×1.10 band above the last close visible at signal
time, still non-binding, so the fill still lands on the entry session's open.
`build_fixtures.assert_point_in_time_safe` runs on every case at build time
(limit equals the band; no future OHLC value could have produced it; the limit
neither binds against nor rejects the entry), and the root test suite re-checks
it against the committed fixtures.

**3. Order alignment fixed.** Legacy orders were reconstructed sorted
lexicographically by `order_id`, which put an exit
(`bt-order-SPKE-…-STOP_GAP`) ahead of the entry that created it and aligned the
legacy SELL against the runtime BUY. Reconstruction is now in engine execution
order (first `fill_sequence`), and the comparator groups both sides by
normalized side and pairs within each group, asserting no pair crosses roles. In
cases B and C the BUY pairs with the BUY and the unmatched legacy SELL is the
mandatory-exit difference (D9). A difference may now carry `D5-enum-vocabulary`
only if both values normalize to the same token, so `market` vs `LIMIT` became
its own economic difference (`D16-order-type-model`) and a BUY can never be
explained away as a SELL.

**4. Cross-case identity collision detected — a new old-engine defect (D17).**
`case_a_buy_and_hold` and `case_b_perturbed_last_close` have different
`historical_bar_dataset_checksum` values (`e5cf5f68…` vs `3c7abef9…`) and
different results (final equity 100 025 vs 100 075), yet share one
`backtest_run_id` (`backtest-fdc36c96…`) and one `configuration_hash`: run
identity comes from `_configuration_hash` and `_signal_set_hash` only, and the
bars contribute nothing. Not cosmetic — `_persist_result` treats a matching
`backtest_run_id` with a matching `input_hash` as an idempotent replay and
returns without writing, so persisting both runs stores only the first and
silently discards the second under an identity it also claims. The
collision-with-different-input guard never fires, because it is the *dataset*,
not the configuration, that changed. **Reported, not fixed** — changing run
identity is a legacy-engine behavior change for a later PR.

**5. Classification semantics corrected.** "Unsupported requirement" is now
reserved for a case *neither* side can express. Fees/slippage (D7) and
realized-P&L/exit support (D8) are capabilities the legacy engine has and
`backtest_runtime` does not, so they are **adapter capability defects**; the
comparator carries a `BEHAVIOR`/`CAPABILITY` subcategory and exits non-zero if a
capability gap is labelled `UNSUPPORTED`. Tally at the end of round 1: **13**
old-engine defect, **24** adapter defect (10 behavior, 14 capability), **93**
intentional library semantic difference, **0** unsupported requirement.
*(Superseded by review round 2, which reclassified the 10 behavior defects.)*

**6. Fixture set** (`pr7/fixtures/`, six cases, all `SPKE`/10 shares/100 000):
`case_a`/`case_b`/`case_c` reuse `backtest_runtime/tests/support/fixtures.py`'s
`BARS`, `perturbed_input_document()` and `FALLING_BARS` **verbatim**;
`case_e_gapped_opens` gaps its opens (every other array has
`open[i] == close[i-1]`, which hides the fill-price model);
`case_d_long_hold_default_atr` is 30 bars, long enough for the engine's default
`atr_period=14`; `case_f_exact_entry_parity` is the exact case above. Identical
input is *proved*: each side computes the bar-set SHA-256 independently
(`backtest_runtime.contract.bars_digest` on one side, a separate
re-implementation in `run_legacy_engine.py` on the other, since the main
environment must never import `backtest_runtime`). All six matched.

**Two environments, never combined** (ADR 0009 Decision 5): `backtest_runtime`
in its own Python 3.11 venv (`pip install -e backtest_runtime/[dev]`), the
legacy engine in the main `.venv`. The comparator reads two result documents and
imports neither engine.

**Other findings, unchanged across both rounds:** both engines fill an entry at
the **open of the session they book it in** (measured on the gapped fixture:
LumiBot filled at 104.0, the 2024-01-03 open, not 100.0, the 2024-01-02 close);
quantity was exactly equal in every case; the drawdown definition and sign
convention are identical; the legacy engine's **mandatory** ATR stop / trailing
stop / target / holding period fire in cases B and C while the reference
strategy holds.

**Comparator numeric rule** (inherited from neither side): both sides into exact
`Decimal` — legacy from its decimal string, runtime through `repr()` of the
double — exact difference, no rounding, against ±1e-6 absolute for money and
prices, ±1e-9 **relative** (1e-15 floor) for fractions, exact for share
quantities. The relative rule is deliberate: drawdowns here run as small as
~1e-5, and an absolute 1e-9 bound hid real case-D differences on the first pass.
Smallest non-zero difference measured: 1.0 (money), 0.1 (price), 2.0e-10
(fraction, correctly reported as differing), so no "equal" verdict rests on a
bound.

### Review round 2 (2026-08-02)

**1. Fill timing re-derived from authoritative evidence.** Both earlier passes
inferred when a fill happened — round 1 from the `on_filled_order` callback,
the comparator from which bar's open matched the fill price — and the two
disagree. LumiBot does keep an authoritative record: its broker stamps every
order event with `data_source._datetime` as it processes it, in the trade-event
log (`lumibot/brokers/broker.py`). It says the fill is booked in the
**submission** session, because
`strategy_executor.py::_process_pandas_daily_data` runs a session as
`_update_datetime` → `_on_trading_iteration` → `process_pending_orders`. Case A:
booked 2024-01-03 @ 100.5; callback 2024-01-04; cash first changes 2024-01-04.
The two lagging clocks are observation delay, not execution delay.

`probe_lumibot_fill_timing.py` now records all three clocks (and says
"UNRESOLVED" if the authoritative log is ever absent, rather than falling back
silently), across three fixtures including the exact case.

**2. Option A re-evaluated and Option B retained, on authoritative timing.** The
round asked specifically whether a fixture with intentionally matching
consecutive opens could give Option A genuine parity. It cannot: matching opens
equalise the two fill *prices* while leaving the two *booking sessions* one
apart, and the exact case must agree on the authoritative fill date. Both floors
are structural — LumiBot cannot submit before bar 2 (bar 1 is never a trading
iteration) and books in the submission session; the legacy engine cannot enter
before bar 3 (next-session eligibility, and ATR needs `atr_period + 1` prior
bars, so even `atr_period = 1` puts `generated_after_session` no earlier than
bar 2). `entry_after_session` is therefore kept, not reverted.

**D4 and D15 are reclassified from adapter defect to library semantic
difference**, and reference strategy v2 stops reporting the lagging clocks: fill
dates come from the broker's event log, and each session's state re-applies the
fills booked in that session. The result schema is bumped to
`backtest_runtime.result.v2` (same field names, different meaning). Two
invariants guard the realignment as hard errors — each session's observed
balances must equal the reconstruction as of the previous reported session, and
`observed cash + observed quantity × mark price` must equal the reported
`portfolio_value`. The comparator records both ids under
`RESOLVED_BY_REFERENCE_STRATEGY_V2` and exits non-zero if either is emitted
again.

**The exact case is now exact with no session excluded.** Both engines book
2024-01-04 @ 101.0 for 10 shares — each from its own record — and agree on final
cash (98 990), final equity (99 992), final value, end position (10 @ 101.0),
maximum drawdown (−0.00018), and on cash, equity, unrealized P&L, realized P&L
and drawdown for **every** co-dated session, the entry session included. All
fifteen comparator dimensions pass and `excluded_sessions` is empty.

**3. `entry_after_session` boundary closed.** The rule is now that at least one
*iterable* session must remain strictly after the delay (domain `bars[1:]`,
since the first bar is never an iteration); submission and booking are the same
session, so one such session is necessary and sufficient. A penultimate-session
value is accepted, and a new regression test proves accepted input cannot finish
without a fill — it asserts a real fill, a real end position and a reduced final
cash on the last session, which reference strategy v1 would have reported flat.

**4. Results bound to the current fixtures.** The comparator now recomputes each
fixture's canonical bar checksum itself and requires both result documents to
carry it, exiting 6 otherwise. Two stale results agree with each other
perfectly, so mutual agreement proves nothing about currency. A regression test
edits one fixture's *volume* — changing no number either engine computes — and
asserts the previously valid results are rejected.

**5. `run_parity.sh` path resolution fixed.** `BT_PYTHON` and `MAIN_PYTHON` are
resolved to absolute paths (and checked executable) before anything changes
directory. The documented invocation passes `.venv/bin/python` relative to the
repository root, while step 2 runs the isolated interpreter from a scratch
directory so LumiBot's `logs/` never lands in the repo — a relative path
resolved against that scratch directory instead. Verified with the documented
relative invocation.

**Corrected tally** across all six cases plus the cross-case finding: **13**
old-engine defect, **14** adapter defect (**0** behavior, 14 capability), **93**
intentional library semantic difference, **0** unsupported requirement. The
behavior subcategory is empty because it held only D4 and D15.

**Tests run:**

- `backtest_runtime/` in its own isolated Python 3.11 venv — `pip check` clean,
  `lumibot 4.5.78`, `pytest tests/ -q --tb=short` — **75 passed, 0 failed**
  (70 after round 1, plus three authoritative-timing/penultimate-boundary tests
  in `test_entry_timing.py` and two result-schema-version cases).
  `test_entry_timing.py` asserts both halves of the bounded extension — the
  delay works, and the strategy is still exactly one buy-and-hold order with no
  sell — plus that the fill carries the booking session and never the callback
  session.
- `pytest tests/ -q --tb=short` in the main project's `.venv` — **2878 passed,
  57 skipped, 0 failed**: the 2850 PR 6 recorded on the same `.venv`, plus the
  28 PR 7 regression tests. No pre-existing test changed behavior, and
  `backtesting/engine.py`'s own tests are unaffected because the engine is
  untouched.
- `tests/unit/test_pr7_parity_report.py` — **28 passed**: order alignment and
  role pairing, vocabulary rules that cannot conceal BUY/SELL or market/limit,
  cross-case identity-collision detection (including a negative case), exact-case
  parity checked both through the comparator and directly against the two raw
  documents with no session excluded, booking dates corroborated against each
  engine's own fill price, the resolved-defect regression barrier, fixture
  binding and the volume-only staleness case, `run_parity.sh` path resolution,
  classification-category validity, and fixture point-in-time safety.
- Reproducibility: `run_parity.sh` runs every `backtest_runtime` case **twice**
  and fails if the two result documents are not byte-identical; all six were,
  and a second full run of the script leaves a clean `git diff`.

## Completed work (PR 6) — MERGED (`bbd7a1f`, PR #18)

**Scope:** new top-level distribution `backtest_runtime/` (`pyproject.toml`;
`src/backtest_runtime/{__init__.py,__main__.py,credential_guard.py,
contract.py,strategy.py,cli.py}`; `tests/{conftest.py,support/*,test_*.py}`
— 8 test files, 47 tests, none `importorskip`-guarded); a new
`tests/unit/test_lumibot_import_boundary.py` (the AST walk moved out of
`tests/unit/test_lumibot_adapter.py`, which now has a comment pointing to
its new location instead of the test); a new `backtest-runtime-tests` job in
`.github/workflows/ci.yml`; `backtest_runtime` added to the root
`pyproject.toml`'s `[tool.pyright]` exclude list (parity with the existing
`paper_runtime` entry — both are outside the `include=["src","tests"]` scope
already, this is defensive/documentation only); `docs/library-migration/
pr7-prompt.md` (new); this file. No file under `src/`, `scripts/`,
`paper_runtime/src/`, or `config/` was modified; the only `tests/` change
outside the new `backtest_runtime/tests/` and `tests/unit/
test_lumibot_import_boundary.py` files is the one-comment edit removing the
moved test from `test_lumibot_adapter.py`.

**Architecture, exactly as ADR 0009 specifies:** `backtest_runtime/` is a
third top-level distribution, never installed alongside the root project or
`paper_runtime/`. `strategy.py` is the only module that imports `lumibot`;
`credential_guard.py`, `contract.py`, and `cli.py` do not. `__main__.py`
scrubs every credential-named variable from `os.environ`, sets
`LUMIBOT_DISABLE_DOTENV=1`, and redirects `sys.stdout` to `sys.stderr` — all
three, in that order — before importing `.cli` (which transitively imports
`.strategy`, which imports `lumibot`).

**The reference strategy is intentionally minimal** (documented in
`strategy.py`'s module docstring and in `docs/library-migration/
pr7-prompt.md`'s "scope trap" section): buy one caller-specified whole-share
`quantity` of one `symbol` on the first bar with a resolvable price, then
hold to the end of the fixture. No sells, stops, targets, multi-symbol, or
re-entry — matching the pre-step spike's own `SpikeStrategy` shape. This was
a deliberate scope boundary per the original prompt's item 3 ("no execution
authority ... no scheduler"), not an oversight; PR 7's prompt records the
decision PR 7 must make about how to construct an equivalent
`backtesting/engine.py` fixture run.

**File contract:** input (`backtest_runtime.input.v1`) is `{schema_version,
strategy: {strategy_id, symbol, quantity, budget}, bars: [{date, open, high,
low, close, volume}, ...]}`. Output (`backtest_runtime.result.v1`) is
`{schema_version, historical_bar_dataset_checksum,
run_configuration_checksum, strategy_identity, lumibot_version, orders,
fills, daily_states, positions, final_cash, final_equity, final_value,
max_drawdown_fraction}` — shaped to, but independent of (no shared types),
`backtesting/models.py`'s `BacktestFill`/`BacktestDailyState`/
`BacktestResult`. Both checksums are `sha256` over a canonical
(`sort_keys=True`, compact separators) JSON serialization of the bars and
the strategy configuration respectively. Both documents are validated
independently in `contract.py` (`parse_input_document`,
`validate_result_document`): unknown fields, non-finite values, malformed
dates, and OHLC-invalid bars are all rejected before any `lumibot` call, and
`cli.py` validates its own output before writing the result file, not only
the input it received. No `lumibot` object (`Order`, `Asset`, `Position`,
...) crosses into the result document — `strategy.py` converts every field
to a JSON primitive before it leaves the module.

**Determinism, proved by running the actual CLI as a subprocess twice**
(`backtest_runtime/tests/test_determinism.py`): identical input produces a
byte-identical output file; a perturbed bar changes
`historical_bar_dataset_checksum` and every downstream daily state while
leaving `run_configuration_checksum` unchanged; a changed `quantity` changes
`run_configuration_checksum` while leaving `historical_bar_dataset_checksum`
unchanged.

**Credential and network safety, proved against the real entry point, not
re-implemented test logic** (`backtest_runtime/tests/
test_credential_safety.py`, `test_network_safety.py`,
`test_lumibot_version.py`): each scenario runs `backtest_runtime.__main__`
(the actual bootstrap path) in its own subprocess, with a fail-closed
network guard and an `os.environ` read tracer installed first — the same
methodology as `docs/library-migration/pre-step-06/EVALUATION.md` section
2.3, now permanently maintained in `backtest_runtime/tests/support/` instead
of imported from `docs/`. Covers, each as its own test:

- inherited process-environment sentinel credentials are scrubbed before
  `lumibot` is imported, and never reach it (`credential_values_from_environment`
  is empty; `broker`/`data_source` stay `None`);
- a sentinel `.env`/`.env.local` placed in the working directory is never
  loaded;
- exactly the three documented benign LumiBot defaults
  (`COINBASE_SANDBOX`, `IB_USE_PAPER_ACCOUNT`,
  `DATADOWNLOADER_API_KEY_HEADER`) resolve during a real backtest run, and no
  other credential-named variable resolves to any value;
- `lumibot.credentials.broker` and `.data_source` remain `None` across a
  real backtest run, not only at import;
- zero outbound network attempts occur across a real backtest run
  (`test_network_safety.py`), and `benchmark_asset=None`/
  `analyze_backtest=False` are proved to be the actual arguments passed to
  `Strategy.run_backtest` (a call-spy test, not a source-text check);
- the resolved `lumibot` version is exactly `4.5.78`
  (`test_lumibot_version.py`);
- a **negative control**
  (`test_without_the_guard_the_same_sentinel_would_leak`) imports `lumibot`
  directly, bypassing `backtest_runtime`'s guard entirely, from a script
  copied to a scratch location outside the repository (so LumiBot's
  script-directory `.env` walk cannot reach this repository's own real,
  gitignored `.env`) — and confirms the same sentinel *does* leak and *does*
  construct a broker without the guard, proving the protected-path tests
  above are proving something real, not passing vacuously.

`backtest_runtime/tests/conftest.py` applies the same credential scrub and
`LUMIBOT_DISABLE_DOTENV=1` at collection time, before any test module's own
top-level `import lumibot` can run — this was necessary in practice: an
early scratch verification of the real LumiBot API, run from the repository
root with no guard, loaded this repository's actual `.env` and attempted a
live broker connection (blocked only by the developer machine's own
network), directly reproducing ADR 0009's Finding 2/3 hazard. That
observation is the reason `conftest.py` exists as a session-wide, import-time
protection rather than a per-test fixture.

**Import boundary** (`backtest_runtime/tests/test_import_boundary.py`,
AST-based): every file under `backtest_runtime/src` imports only the
standard library (via `sys.stdlib_module_names`), `pandas`, `lumibot`, or its
own package; `backtest_runtime` never imports `trading_research` or
`trading_paper_runtime`; `src/trading_research` never imports
`backtest_runtime`; `paper_runtime/src` never imports `backtest_runtime`.

**LumiBot version pin match**
(`backtest_runtime/tests/test_lumibot_pin_matches_paper_runtime.py`): reads
both `pyproject.toml` files as text (not `tomllib`, which is 3.11-only and
would contradict this distribution's own declared `>=3.10` floor) and
asserts `backtest_runtime/pyproject.toml` and `paper_runtime/pyproject.toml`
pin the identical exact string `lumibot==4.5.78`, and that the root
`pyproject.toml` declares no LumiBot dependency at all.

**The AST import-boundary repair**
(`docs/adr/0009-lumibot-backtest-distribution-boundary.md` section 4 /
Decision 4 item 5): `test_no_lumibot_import_outside_runtime_package` moved
from `tests/unit/test_lumibot_adapter.py` (module-level
`pytest.importorskip("lumibot")`, so the walk previously skipped under
`main-tests`) into new `tests/unit/test_lumibot_import_boundary.py`, which
has no LumiBot dependency at all and therefore always runs. Confirmed by
execution in the main project's environment (which happens to have a
hand-installed scratch `lumibot==4.5.74` — see `EVALUATION.md`'s "stale
`4.5.74` version labels" note): the new file's test passes for real, not via
skip.

**Tests run:**

- `backtest_runtime/` in its own isolated Python 3.11 venv (`pip install -e
  ".[dev]"`; no Python 3.10 interpreter was available on this development
  machine, matching every prior PR's note — the declared `>=3.10` floor is
  substantiated by the CI job's `actions/setup-python` matrix, not a local
  reproduction) — `pip check`: clean. `pytest tests/ -q --tb=short` —
  **56 passed, 0 failed** (9 new tests added in the review-fix round below).
- `tests/unit/test_lumibot_import_boundary.py
  tests/unit/test_lumibot_adapter.py
  tests/unit/test_runtime_client_no_lumibot_import.py -v` in the main
  project's `.venv` — **13 passed, 0 skipped** (this `.venv` happens to have
  a hand-installed scratch `lumibot==4.5.74`, so `test_lumibot_adapter.py`'s
  other tests ran for real here rather than skipping; the boundary test's
  own file has no LumiBot dependency and would pass identically either way).
- `pytest tests/ -q --tb=short` in the main project's `.venv` — **2850
  passed, 57 skipped, 0 failed**. This `.venv` has several optional extras
  and a scratch LumiBot installed beyond the plain `.[dev]` baseline other
  PRs recorded, so the exact pass/skip counts are not directly comparable to
  earlier entries in this file; no test failed, and no test file outside
  the ones listed above as changed was modified.

**Credential and network-safety evidence:** see the bulleted list above; raw
assertions live in `backtest_runtime/tests/test_credential_safety.py` and
`test_network_safety.py`, re-run as part of the `56 passed` result above,
and re-run again by the new blocking `backtest-runtime-tests` CI job on
every future PR against this distribution — no evidence file under `docs/`
was produced or is required for this PR, since the properties are asserted
by a permanent, blocking test suite rather than a one-time spike.

**No behavior changed under `src/`, `scripts/`, `paper_runtime/src/`, or
`config/`.** `backtesting/engine.py` is untouched. No trading limit,
authorization rule, `paper_books` accounting code, or scheduling behavior
was touched; no broker, provider, model, or market-data service was called;
no live data was fetched; the scheduler was not enabled.

### PR 6 review-fix round (2026-08-02)

Five review items addressed on the same branch, before merge:

1. **Drawdown sign convention.** `daily_states[*].drawdown_fraction` and
   `max_drawdown_fraction` now follow `backtesting/engine.py`'s existing
   convention — `(equity - running_peak_equity) / running_peak_equity`,
   zero or negative, never positive. `contract.py` validation flipped from
   `_require_non_negative_finite` to a new `_require_non_positive_finite`
   for both fields. New regression fixture `FALLING_BARS` (`support/
   fixtures.py`) and `tests/test_drawdown.py` assert the exact sign and
   that the aggregate equals the minimum daily value.
2. **Exact date syntax.** `contract.py::_require_date` now gates on
   `^\d{4}-\d{2}-\d{2}$` before calling `date.fromisoformat`, since
   `fromisoformat` accepts a version-dependent superset (Python 3.11+ also
   accepts compact `20240102` and ISO week `2024-W01-2` forms; 3.10 does
   not) — the regex keeps accepted syntax identical on both. New tests
   cover both rejected forms for input bar dates and result
   `daily_states[*].market_date`.
3. **AST boundary exemption tightened.** `tests/unit/
   test_lumibot_import_boundary.py` previously exempted any path
   containing a directory literally named `runtime` anywhere in
   `src/trading_research` (so a hypothetical `runtime/client/` or any
   other future `runtime`-named package would have been silently
   exempted, not just `runtime/lumibot/`). Now scoped to paths under
   `src/trading_research/runtime/lumibot/` specifically, via
   `Path.relative_to`. New regression test
   `test_import_under_another_runtime_directory_is_reported_as_an_offender`
   proves a `lumibot` import under `runtime/client/` (a real sibling
   package) is reported, not silently allowed.
4. **Docs cleanup.** `docs/milestones/rebuild/7.md` (the completed PR 6
   execution prompt) removed — it was a one-time instruction set, not
   enduring documentation, and this file plus `MASTER_PLAN.md`/
   `DECISIONS.md`/`COMPONENT_MATRIX.md` are the durable record.
   `pr7-prompt.md`'s citation of it replaced with ADR 0009 Decision 3 and
   this file's "Completed work (PR 6)" section.
5. **CI matrix.** `backtest-runtime-tests` now matrixes
   `python-version: ["3.10", "3.11"]` (`fail-fast: false`, matching the
   existing `indicator-tests` job's pattern) instead of only the declared
   3.10 floor, so both the floor and CI's historical ceiling are proven in
   CI on every push (this development machine has no 3.10 interpreter, so
   3.10 is proven by CI, not locally).

Tests-run counts above already reflect this round's additions.

**Collateral CI fix:** the original PR 6 commit's `backtest-runtime-tests`
job had a YAML scanner bug — the unquoted `run: pip show lumibot | grep -qx
"Version: 4.5.78"` scalar contains a bare `: ` inside the quoted string,
which YAML parses as a mapping-key separator inside a plain scalar. Both CI
runs on this branch prior to this fix failed immediately with "workflow
file issue" and never actually executed a single job — this was not caught
before because CI had not yet been observed running end-to-end. Fixed by
switching that one step to a block scalar (`run: |`). Confirmed on this
branch's push after the review-fix round: all 15 CI jobs succeeded,
including both `backtest-runtime-tests (3.10)` and
`backtest-runtime-tests (3.11)` matrix legs
(https://github.com/jijoece/ai_stock_trading_v2/actions/runs/30737287212).

## Completed work (PR 8) — DECIDED, IMPLEMENTED, MERGED (PR #20)

**Scope:** documentation only — `docs/library-migration/pr8/DECISION.md` (new,
the decision record), `DECISIONS.md` (**D7**), `REMOVAL_MANIFEST.md`,
`PRESERVATION_MANIFEST.md`, `COMPONENT_MATRIX.md`, `MASTER_PLAN.md` (row 8
decided, new row **8a**, PR 17's conditional dependency resolved, the non-goals
paragraph updated), and this file. **No file under `src/`, `scripts/`,
`paper_runtime/src/`, `backtest_runtime/src/`, `config/`, or `tests/` was
modified** — this is a decision gate, and it is not a fix.

**Branch:** `migration/08-backtest-removal-decision` (PR #20). It was opened off
the PR 7 branch tip, since PR 8's input — `pr7/PARITY_REPORT.md` and the raw
data under `pr7/results/` — had not yet merged at the time; PR 7 has since
merged (`5b9e1e3`), and that merge commit is PR #20's base, so the branch now
sits directly on `main` content. It is a separate branch and a separate session,
as PR 7's handoff required.

### Outcome: not approved for removal

1. **`backtesting/engine.py` and `backtesting/models.py` are NOT approved for
   removal.** Authoritative indefinitely — not "pending a later parity PR". The
   `REMOVAL_MANIFEST.md` conditionally-eligible row is **closed as not
   approved**, so that manifest now carries **no unresolved removal target**
   into PR 17 or the PR 18 audit, and PR 17 removes nothing on account of PR 8.
   The engine is added to `PRESERVATION_MANIFEST.md` with the invariants it
   protects — and, after the review round, with the one it does **not** (ruling
   5) stated in the same row; `COMPONENT_MATRIX.md` reclassifies it from
   Evaluate (Category B) to Domain-specific.
2. **`backtest_runtime/` is kept**, in the role `REMOVAL_MANIFEST.md` already
   named for this outcome — an additional, **non-replacing** option, narrowed
   in D7 to *an independent offline cross-check and parity harness with no
   execution authority and no callers in `src/`*. ADR 0009's third possibility
   (keep the engine, delete the distribution) is not taken. A review trigger is
   recorded so "keep" does not become permanent by default.
3. **Three legacy-side items became mandatory follow-ups** — D17 (run identity
   ignores the bar dataset), the never-written `backtest_orders` table, and
   availability enforced once per run rather than per session (ruling 5) —
   tracked as `MASTER_PLAN.md` row **8a**, not fixed here (all three are
   behavior changes to the legacy engine and need their own review).
4. **PR 7's D13 + D1 (peak-equity seeding, state-series start) is not resolved,
   by design** — each engine is self-consistent for the run it reports, so
   nothing in the repository is currently wrong. It is recorded as a
   **precondition on any future replacement proposal**. The mechanism, carried
   forward from PR 7's handoff so it is not lost with that section: the legacy
   engine seeds its running peak equity with `initial_cash` (`engine.py:175`),
   so a drawdown is measurable from the first session in the dataset;
   `backtest_runtime` seeds its peak with `0.0` and raises it on the first
   session it reports; and by D1 those are not the same session, because
   LumiBot's first trading iteration is the second bar. On the five
   default-timing cases the entry is already booked in the runtime's first
   reported session, so its seed carries the entry's mark while the legacy seed
   does not, and the two peaks stay apart for the whole run (case A:
   −0.0000499925… vs −0.00005). **Aligning entry timing does not fix this** —
   `case_f_exact_entry_parity` agrees on drawdown by construction, being built
   so the runtime's first reported session is still flat and therefore marks at
   exactly the budget. Any replacement adapter must state what its peak is
   seeded with and which session its state series starts on, and must show
   agreement on ordinary data rather than on a fixture built to avoid the
   question. This is not a display concern: the legacy engine gates entries on
   `max_drawdown_fraction` (`engine.py:288-290`), so an adapter that disagrees
   about drawdown disagrees about which entries are allowed.
5. **The legacy engine's point-in-time enforcement is run-level, not
   per-session — recorded as fact, and a claim to the contrary is withdrawn.**
   Added in the PR #20 review round. `run_backtest` computes one `as_of` for
   the whole run, `end_date 23:59:59 UTC`, passes it to the provider, and its
   own guard repeats that same cutoff (`engine.py:140-149`); the bars are then
   loaded once and consumed at every simulated session with no further
   availability filtering. `FixtureHistoricalDataProvider` filters only against
   the `as_of` it is handed (`data_provider.py:25-30`); `HistoricalBar` checks
   that `available_at` is timezone-aware and otherwise **trusts** the
   caller-set `point_in_time_safe` flag (`models.py:26-30`); and
   `strategy_signal_to_entry_signal` reduces `data_as_of` to a date
   (`strategies/backtest_adapter.py:44`). So a bar available *after* a signal
   or session, but on or before the run's end, can be used in that earlier
   simulated period. The engine does enforce a different, weaker property —
   **session-date ordering** (entry only on the first session strictly after
   `generated_after_session`, entry ATR only from bars at or before it,
   `engine.py:164-171`, `engine.py:303-306`). Closing the gap is legacy-engine
   work, tracked in row **8a**; it is not implemented here, and it is not an
   argument for replacement, since `backtest_runtime`'s six-field bar contract
   has no availability axis at all.

**No superseding ADR was required or drafted.** The verdict preserves the
status quo; under `DECISIONS.md`'s governing principle an ADR is needed to
*remove* a gated component, not to decline to. ADR 0009 is untouched and stays
Accepted.

### Why — the decisive reasons are not the parity numbers

PR 7 established that on `case_f_exact_entry_parity` the two engines agree on
every economic number. The verdict does not turn on that. It turns on what a
replacement would have to carry — the capability list in `pr8/DECISION.md` §3 —
and on two items that are **not feature gaps** (full detail and source line
references in `pr8/DECISION.md` §3–§4):

- **`Decimal` versus `float` is an accounting boundary**, not a rounding
  preference. PR 7's numeric bounds are the right instrument for asking whether
  two runs agree; they are not a licence to make the float side authoritative
  over tables that today hold exact decimal strings.
- **The engine shares `calculate_partial_close_quantity` with
  `paper_books/lifecycle_state.py`** (`engine.py:17`). Re-implementing exits
  inside a LumiBot strategy would fork safety-adjacent arithmetic away from the
  preserved accounting layer — and ADR 0009's boundary, which is what makes
  `backtest_runtime` safe, is exactly what forbids it from importing that code.

An earlier revision of this section listed a third item — point-in-time safety
"enforced at three layers". It is **withdrawn** (ruling 5): those are one
run-level cutoff applied three times, not a per-session knowability guarantee.
The verdict is re-derived without it and does not change. Stated plainly, the
correction cuts against the preservation case — the invariant is weaker than
claimed — but not in favour of replacement, because migrating would delete the
availability axis rather than complete it.

Also weighed: `backtesting/models.py` is the strategies layer's shared type
vocabulary (`HistoricalBar`/`EntrySignal`/`BacktestResult` are imported by
`strategies/contracts.py`, `factors.py`, `safety_gates.py`, `timestamps.py`,
`strategy_metrics.py`, `backtest_adapter.py`), so removal would not be confined
to the engine, and 23 tests across four files exercise it directly. Recorded
**against** the verdict rather than for it: `run_backtest` and
`run_strategy_backtest` have no non-test caller anywhere in `src/`, `scripts/`,
or `.claude/` today — a weaker preservation case than a load-bearing component
would be, and equally a reason keeping the engine costs nothing operationally.

### Independent verification performed in this PR

PR 7's report is the input, but its conclusions were not adopted unexamined.
Re-checked against the source on this branch:

- **D17 confirmed.** `_configuration_hash` (`engine.py:67-92`) and `input_hash`
  (`engine.py:135-138`) exclude the bar dataset entirely; `_persist_result`
  returns without writing when the run ID and input hash both match
  (`engine.py:428-429`), and the collision guard above it
  (`engine.py:420-427`) cannot fire, because the input hash is genuinely
  identical — it is the dataset that changed.
- **New finding, not in PR 7: `backtest_orders` is created and never written.**
  `storage/paper_books_schema.py:978-989` defines the table, including a
  `rejection_reason` column; the string occurs **exactly once** in the entire
  Python source. `_persist_result` writes runs, daily states, fills and metrics
  only. So D11's "no order records" is a persistence gap as well as a
  result-type gap: the schema was built to hold orders and rejections and no
  writer followed, while the engine's 11 rejection reasons live only in the
  in-memory result and inside `report_json`.
  `docs/milestones/milestone-13.md` listed `backtest_orders` and
  `backtest_positions` as *candidate* tables under "use the minimum number of
  tables"; `backtest_positions` was correctly never created, and
  `backtest_orders` was created and then left empty — the one outcome that
  instruction did not contemplate.
- **Availability enforcement traced end to end (review round).** One run-wide
  `as_of` at `engine.py:140`, used at `engine.py:144`, repeated as a guard at
  `engine.py:148-149`; bars cached once at `engine.py:141-156` and read from
  `bar_maps` inside the per-session loop from `engine.py:194` with no further
  check; `data_provider.py:25-30` filtering only against the supplied `as_of`;
  `models.py:26-30` trusting the caller-set flag; `data_as_of.date()` at
  `strategies/backtest_adapter.py:44`. This **contradicted** the earlier
  revision of this record and of `pr8/DECISION.md`, which is corrected rather
  than defended.
- **`backtest_runtime`'s single-buy surface confirmed** — one symbol
  (`strategy.py:178`), `realized_pnl` written as a literal constant `0.0`
  (`strategy.py:353`), no sell submitted at all. Corrected in the review round:
  `fees` is **not** hardcoded — it is copied from LumiBot's `trade_cost`
  (`strategy.py:157`) and is `0.0` in every parity run because the contract has
  no fee or slippage input with which to configure a commission model
  (`contract.py:38`).
- **Both engines are fixture-fed.** `HistoricalDataProvider` has exactly one
  implementation, `FixtureHistoricalDataProvider` (`data_provider.py:16`), and
  ADR 0009 Decision 5 holds `backtest_runtime` to the same posture. This
  decision is about which implementation the repository maintains, not about
  which has better data access.

Taken from PR 7 without re-running: the exact-parity case and its fifteen
asserted dimensions, the broker-trade-event-log fill timing, and the
13/14/93/0 classification tally.

### Tests run

No test was added or modified — no code changed on this branch. The existing
suites that cover the preserved component are unchanged and remain the evidence
that it works; they were re-run here to confirm the decision is being made about
a green component, not a broken one:

- `pytest tests/unit/test_advanced_risk_backtest.py
  tests/unit/test_backtest_identity_and_strategy_exits.py
  tests/unit/test_strategy_backtest.py
  tests/unit/test_strategy_metrics_fees_exposure.py
  tests/unit/test_pr7_parity_report.py -q --tb=short` (main `.venv`) —
  **51 passed, 0 failed** (the 23 engine/strategy-adapter tests plus PR 7's 28
  artifact regression tests).

`backtest_runtime/`'s own blocking suite and the AST import-boundary tests in
both directions are unchanged from PR 6/PR 7 and were not re-run here, since
neither distribution's code was touched.

**Safety:** no trading limit, authorization rule, `paper_books` accounting code,
or scheduling behavior was touched; no broker, provider, model, or market-data
service was called; no live data was fetched; the scheduler was not enabled.

## Next PR

**PR 9 — strengthen the LumiBot runtime normalization contract. IMPLEMENTED,
NOT MERGED** (see "Completed work (PR 9)" below and `DECISIONS.md` D8; this
paragraph is left as the historical "next PR" note written when PR 8 closed).
`runtime/lumibot/adapter.py` and `paper_runtime/.../lumibot_gateway.py`:
normalize orders, statuses, fills, positions and account snapshots
(`MASTER_PLAN.md` row 9, `DECISIONS.md` D1). PR 10 then proves reconciliation
against `paper_books` without removing the book ledger.

**PR 8a — legacy backtest run identity, order records, and per-session bar
availability** is also now open (`MASTER_PLAN.md` row 8a). It is independent of
the migration sequence and can run at any point now that PR 8 has merged: bind a
canonical bar-dataset digest into the legacy engine's `input_hash` so two runs
over different bars cannot collide onto one persisted identity; resolve the
`backtest_orders` table — persist orders and rejections, or delete the table and
record that order-level backtest history is deliberately not retained; and
thread a per-session (or per-signal) `as_of` through the simulation so a bar is
visible only once it was knowable, keeping `data_as_of` at timestamp resolution
and deciding whether `point_in_time_safe` stays caller-asserted. The third item
carries fixture and test churn — the 23 existing engine/adapter tests encode the
current run-level semantics. All three are behavior changes to
`backtesting/engine.py` and need their own review; see `pr8/DECISION.md` §8.

## Completed work (PR 9)

**Scope:** two new contract modules
(`src/trading_research/runtime/normalization.py`,
`paper_runtime/src/trading_paper_runtime/normalization.py`); the producers
and consumers of normalized broker observations
(`paper_runtime/.../lumibot_gateway.py`, `paper_runtime/.../models.py`,
`src/trading_research/runtime/lumibot/adapter.py`,
`src/trading_research/runtime/lumibot/event_mapper.py`,
`src/trading_research/runtime/client/models.py`,
`src/trading_research/execution/broker_snapshots.py`,
`src/trading_research/storage/execution_repositories.py`); three new test
files and additions to two existing ones; plus this file, `MASTER_PLAN.md`
and `DECISIONS.md` (D8). **`src/trading_research/paper_books/external_broker.py`
was not modified** — see "The one thing PR 9 deliberately did not change"
below. No configuration, scheduler, trading limit, or authorization rule was
touched.

**Outcome: there is now one normalization contract**, declared once per
distribution and enforced in both directions. Before PR 9 the normalized
order-status vocabulary was declared independently in four places
(`paper_runtime/models.py::SUBMISSION_STATES`,
`execution/broker_snapshots.py::SUBMISSION_STATES`, a SQL literal inside
`list_unresolved_submissions`, and the mapping in
`external_broker._state_from_order`) and they disagreed with each other.

### The defect that motivated the PR

`lumibot_gateway._ALPACA_STATUS_MAP` maps Alpaca's `expired` to `EXPIRED` and
`pending_cancel` to `CANCEL_REQUESTED`. Neither status existed in
`execution/broker_snapshots.py::SUBMISSION_STATES`.
`submit_credentialed_paper_order.py` writes `response["status"]` straight
into `update_submission_status`, which persists it with no validation and no
`CHECK` constraint; `_row_to_submission` reads it back through
`BrokerOrderSubmission.__post_init__`, which validates. So one expired or
cancel-pending broker order wrote a row that `get_submission` and
`list_unresolved_submissions` could never read again — a permanently
unreadable submission record, discovered by reading the chain rather than by
any failing test. Separately, `list_unresolved_submissions` compared against
a hardcoded SQL terminal list that also omitted `EXPIRED`, so such an order
would additionally have stayed in the polling loop's work queue forever.

`tests/unit/test_runtime_normalization_contract.py::
test_expired_submission_round_trips_through_storage_and_leaves_the_work_queue`
reproduces the whole path through real SQL and is the regression guard.

### How the contract is declared

ADR 0002 (reaffirmed by ADR 0009) forbids the two distributions from
importing each other, so there is no shared module to put this in. The
contract is declared **twice, identically**, and the copies are kept honest
by `test_runtime_normalization_contract.py`, which AST-parses both files and
compares the declared constants literally, plus asserts both expose the same
public helper names and that neither imports the other's package. This is the
same source-inspection technique `tests/unit/test_lumibot_import_boundary.py`
already uses; the test process never imports `trading_paper_runtime`.

Declared vocabulary (`NORMALIZATION_CONTRACT_VERSION =
"runtime-normalization.v1"`):

```text
NORMALIZED_ORDER_STATUSES   11 states, PENDING_SUBMISSION .. ERROR
BROKER_REPORTABLE_STATUSES  the 9 a gateway may report
TERMINAL_ORDER_STATUSES     FILLED, CANCELLED, EXPIRED, REJECTED, ERROR
NORMALIZED_SIDES            BUY, SELL
NORMALIZED_TIME_IN_FORCE    DAY, GTC, IOC, FOK, OPG, CLS
```

`execution/broker_snapshots.py::SUBMISSION_STATES` and
`TERMINAL_SUBMISSION_STATES`, and `paper_runtime/models.py::SUBMISSION_STATES`,
are now bound from the contract rather than repeated as literals; the SQL
terminal list is parameterized from `TERMINAL_SUBMISSION_STATES`.

### Two conformance levels, enforced at import time

The in-process ADR 0001 adapter emits `execution/models.py::EVENT_TYPES`, a
strict *subset* of the contract — a `PaperExecutionEvent` has no `EXPIRED`,
`CANCEL_REQUESTED`, `PENDING_SUBMISSION` or `SUBMISSION_UNKNOWN`, because
`adapter.submit()` is synchronous and always returns a resolved outcome
(docs/milestone-3.md Step 5). That is why LumiBot's `expired` maps to
`CANCELLED` in `event_mapper.py` while the runtime gateway maps Alpaca's
`expired` to `EXPIRED`: the same broker concept at the conformance level each
boundary supports. That difference was already correct — what was missing was
anything asserting it stayed deliberate. `event_mapper.py` now fails at import
if `_STATUS_MAP`'s values leave `EVENT_TYPES`, or if `EVENT_TYPES` leaves the
contract; `adapter.py` fails at import if its last-event-to-final-status map
does not cover `EVENT_TYPES` exactly (previously a literal inside
`_build_result`, so a new event type would have raised `KeyError` at fill
time, on a live order).

### Fail-closed normalization defects closed

Each of these was a silent repair that is now a rejection:

| Where | Was | Now |
|---|---|---|
| `_order_to_snapshot` limit price | `str(getattr(order, "limit_price", "")) or None` produced the literal string `"None"` for a market order — truthy, so it survived the `or None` — which then crashed the main process's `Decimal(...)` | `None` stays `None`; a malformed value fails closed |
| `_order_to_snapshot` time-in-force | `getattr(<enum-or-str>, "value", "day")` reported a broker's plain-string `"gtc"` as `DAY`, misstating the order's lifetime | normalized, no default; absent means `DAY`, unknown fails |
| `list_order_fills` | a FILL activity missing `qty`/`price` was stringified to `"0"`, fabricating a zero-price fill `paper_books` would have booked as free shares | fails closed |
| `get_account` / `list_positions` | raw `str(...)`, so a missing value became `"None"`; quantities alone were exact-checked | every numeric field validated as a finite decimal |
| adapter fill price | `Decimal(str(price))` turns a float `nan` into `Decimal('NaN')`, and `Decimal('NaN') <= 0` is `False`, so `PaperExecutionEvent`'s positive-fill-price guard accepted it | non-finite and non-positive both rejected |
| adapter filled quantity / notional | unvalidated | exact whole numbers only, never a truncation; non-finite notional rejected |
| `runtime/client/models.py` | bare `Decimal(payload[...])` raised an untyped `decimal.InvalidOperation`, and accepted `"NaN"`/`"Infinity"` | typed `ProtocolViolationError`, status checked against the vocabulary, `0 <= filled <= quantity`, a reported fill requires a positive price |

Validation moved into `__post_init__` on `OrderSnapshotPayload`,
`FillPayload`, `AccountSnapshotPayload` and `PositionSnapshotPayload`, so both
the credentialed gateway and the deterministic double are held to the same
contract, and `dataclasses.replace` re-checks it. The payloads also
canonicalize in place — Alpaca's `str(datetime)` space separator becomes true
ISO 8601, and decimals become plain notation rather than `1E+2`.

### The one thing PR 9 deliberately did not change

`paper_books/external_broker.py::_state_from_order` maps every
broker-reportable status **except `ERROR`**, for which it raises
`UNKNOWN_BROKER_STATUS`. An order the broker reports as `stopped` or
`suspended` has no safe automatic ledger state, so failing closed and leaving
it for manual reconciliation is the correct posture. PR 9 does not touch that
safety-critical state machine; it pins the coverage in
`test_external_broker_state_mapping_coverage_is_pinned`, which asserts the
unmapped set is exactly `{"ERROR"}`, so the gap cannot widen or narrow
without editing an assertion that names the trade-off. Recorded as
`DECISIONS.md` D8 ruling 4.

### Follow-up: completing the polling lifecycle, production wiring, drift protection, and the last silent repairs

A second pass over this PR found the contract declared correctly but not yet
fully load-bearing in four places. All four are recorded as `DECISIONS.md`
D8 rulings 6-9:

1. **The broker-status polling path crashed on the very statuses PR 9 added.**
   `services/sync_paper_orders.py::_sync_one` validated a polled status
   against `execution/models.py::EVENT_TYPES` (the narrower, synchronous-
   adapter vocabulary), not `BROKER_REPORTABLE_STATUSES` — so the first poll
   that observed `CANCEL_REQUESTED` or `EXPIRED` raised
   `UNKNOWN_BROKER_STATUS` before the submission-row fix could matter.
   `_sync_one` now validates against `BROKER_REPORTABLE_STATUSES`.
   `CANCEL_REQUESTED` stays nonterminal and pollable with no further change
   needed. `EXPIRED` has no `RESULT_STATUSES` counterpart; rather than widen
   the vocabulary shared with the synchronous ADR 0001 adapter (which can
   never emit it), a new `_DOMAIN_STATUS_PROJECTION` maps it to `CANCELLED`
   for the `PaperExecutionEvent`/`PaperExecutionResult` the ledger sees,
   while the submission row and the event's `raw_status` keep the true
   `EXPIRED` value. Tested end to end, both before and after a partial fill,
   in `tests/unit/test_sync_paper_orders.py`.
2. **`RuntimeClient` never called the parsers PR 9 added.**
   `runtime/client/process_client.py` returned every runtime response as a
   raw, unvalidated dict — `RuntimeOrderSnapshot`/`RuntimeAccountSnapshot`/
   `RuntimePositionSnapshot` existed but nothing invoked them. `submit_order`,
   `get_order`, `cancel_paper_order`, `list_open_orders`, `list_recent_orders`,
   `get_account`, and `list_positions` now parse through the matching
   `from_payload` and re-serialize through a new `to_dict()` to the same
   wire-compatible shape, so no caller needed to change. New fake-transport
   tests in `tests/unit/test_runtime_client.py` prove a malformed status, a
   non-finite price, a fractional quantity, and a missing required field are
   all rejected with `ProtocolViolationError` at the client boundary.
3. **Constant/name equality does not prove decision equality.**
   `test_runtime_normalization_contract.py` proves both `normalization.py`
   files declare the same constants and function names; it cannot prove they
   accept/reject the same input the same way. `tests/fixtures/
   normalization_corpus.json` is one declarative corpus of (function, args,
   accept/reject, canonical-output) cases, read as plain JSON by both
   `tests/unit/test_normalization_corpus.py` and `paper_runtime/tests/
   test_normalization_corpus.py`, each run independently against its own
   distribution's module.
4. **Three more silent repairs in `lumibot_gateway.py`.** `order.filled_qty
   or 0` conflated a genuinely missing `filled_qty` with a broker reporting
   zero shares filled; a missing `submitted_at`/`updated_at` was replaced
   with this process's own clock reading; a missing account `currency` was
   defaulted to `"USD"`. All three now fail closed. Two intentional defaults
   remain — an absent `time_in_force` still normalizes to `DAY` (this
   runtime only ever submits DAY orders) and a naive broker timestamp is
   still treated as UTC (Alpaca's paper API reports UTC) — both are now
   documented in-line as contract rules and pinned by regression tests in
   `paper_runtime/tests/test_normalization.py` rather than left as unstated
   coercions.

### Follow-up 2: Milestone 11 external-order validation and a corrected time-in-force default

A third pass extended validation to the Milestone 11 external-paper methods
and tightened `RuntimeOrderSnapshot` to match `OrderSnapshotPayload`'s
behavior. Recorded as `DECISIONS.md` D8 rulings 10-12:

1. **`RuntimeOrderSnapshot` now matches `OrderSnapshotPayload`'s behavior
   instead of a narrower subset of it.** It rejects a non-positive
   `quantity`, canonicalizes `submitted_at`/`updated_at` through
   `normalize_timestamp_string` instead of treating them as opaque required
   strings, and preserves `book_id`/`symbol`/`side`/`limit_price`/
   `time_in_force`/`account_fingerprint` in `to_dict()` instead of silently
   dropping them — the runtime always sends all six.
2. **The Milestone 11 external-order methods on `RuntimeClient` never
   validated their responses.** `get_order_by_client_order_id`,
   `get_order_by_broker_order_id`, `cancel_external_order`,
   `list_order_fills`, `get_external_positions`,
   `get_external_account_snapshot`, and `list_recent_external_orders`
   returned raw dicts straight from the wire. Four new parsers —
   `ExternalOrderSnapshot`, `ExternalFillSnapshot`,
   `ExternalPositionsSnapshot`, `ExternalAccountSnapshot` — now validate and
   re-serialize every one of them to the exact enriched shape
   `external_broker.py` already expects (`_validate_order_response`'s
   `expected_fields`, the fill shape checked in `apply_external_fills`, and
   the `_BASELINE_POSITIONS_FIELDS`/`_BASELINE_ACCOUNT_FIELDS` envelopes), so
   no caller needed to change. New fake-transport tests prove a malformed
   status, a non-positive quantity, a malformed limit price, a missing
   `broker_order_id`, a non-positive fill price, a malformed side, and a
   malformed nested position/cash value all fail with
   `ProtocolViolationError` before reaching `paper_books`.
3. **The `time_in_force` default from ruling 9 was too broad.** It applied
   regardless of an order's origin, but `list_open_orders`/
   `list_recent_orders` are account-wide broker reads that can return an
   order this runtime never submitted. `_order_to_snapshot` now defaults an
   absent `time_in_force` to `DAY` only when the order's `client_order_id`
   is inside this project's own id namespace (`"intent-"` or `"epb-"` —
   `_is_runtime_owned_client_order_id`); otherwise it fails closed. The
   test that previously asserted `None -> DAY` unconditionally now asserts
   that behavior only for a runtime-owned `client_order_id`, plus a new
   test asserting the fail-closed path for a foreign one.
   **Superseded by follow-up 4 below:** a namespace prefix turned out not to
   be proof of ownership either — see D8 Ruling 13.

### Follow-up 4: submission/lookup wire-op validation, and a prefix is not proof of ownership

A fourth pass closed the last two unvalidated Milestone 11 wire calls and
removed the `time_in_force` default entirely, rather than narrowing it
further. Recorded as `DECISIONS.md` D8 rulings 13-14:

1. **`submit_limit_order` returned `SUBMIT_LIMIT_ORDER`'s response
   unparsed.** It now goes through `ExternalOrderSnapshot`, the same parser
   `cancel_external_order`/`list_recent_external_orders` use, with no
   change to its no-retry behavior (`SUBMIT_LIMIT_ORDER` remains absent
   from `_RETRYABLE_ON_TIMEOUT`). New tests reject an unknown status, a
   zero quantity, a malformed timestamp, a missing `broker_order_id`, and a
   non-finite price.
2. **`get_order_by_client_order_id`/`get_order_by_broker_order_id` trusted
   the `{"found": ...}` envelope itself.** `if not result.get("found"):
   return None` treated a missing/non-boolean `found`, a not-found response
   that failed to echo the requested `book_id`/`client_order_id`, or a
   contradictory `found`/`order` combination as an ordinary, authoritative
   NOT_FOUND — indistinguishable from a genuine one. That distinction is
   load-bearing: `external_broker.py::_run_reconciliation` treats an
   *exception* from this lookup as non-authoritative (cannot unlock a
   retry) but a *return value of `None`* as authoritative evidence
   `retry_external_paper_order` accepts as sufficient to allow a second
   submission. New `parse_client_order_lookup_response`/
   `parse_broker_order_lookup_response` validate each envelope's exact
   documented shape and raise `ProtocolViolationError` on anything else —
   which `_run_reconciliation`'s existing `except Exception` handling
   already downgrades to non-authoritative, closing the gap with no change
   needed in `paper_books`. New tests cover a missing `found`, `found=0`,
   `found=None`, a mismatched echoed `book_id`/`client_order_id`, a
   contradictory `found`/`order` combination, and an unexpected extra
   field, for both wire ops. A new end-to-end regression,
   `test_malformed_lookup_response_cannot_create_authoritative_not_found_or_unlock_retry`,
   proves a lookup that raises `ProtocolViolationError` (simulating what
   the fix above now does for a corrupted envelope) produces a
   non-authoritative `NOT_FOUND` lookup row and that
   `retry_external_paper_order` still refuses to unlock a retry from it.
3. **A `client_order_id` namespace prefix is not proof of ordering
   ownership.** Follow-up 3's `_is_runtime_owned_client_order_id` treated an
   `"intent-"`/`"epb-"`-prefixed `client_order_id` as evidence this runtime
   created the order, to gate the `time_in_force` DAY default. But
   `client_order_id` is broker-echoed data on an account-wide read — a
   manually placed order, or one from an unrelated application, could carry
   an id in the same shape by coincidence or by deliberate forgery; a
   namespace prefix is a pattern match, not a trust boundary. The default is
   now removed entirely rather than narrowed further:
   `_order_to_snapshot` calls `normalize_time_in_force` directly on the raw
   broker attribute and fails closed on `None` regardless of
   `client_order_id`. In practice this does not affect `submit_order`/
   `get_order` — this runtime always requests `TimeInForce.DAY` explicitly,
   so Alpaca's response echoes it back for any order this runtime actually
   just submitted. `_is_runtime_owned_client_order_id` and
   `_time_in_force_with_ownership_default` are removed.
   `test_gateway_rejects_an_absent_time_in_force_regardless_of_client_order_id`
   replaces the two follow-up-3 tests, and a new
   `test_gateway_rejects_an_absent_time_in_force_from_an_account_wide_listing`
   uses a forged `"intent-"`-prefixed `client_order_id` to prove the
   namespace match alone no longer grants a default.

### Tests run

- `nox -s ci` — **all four blocking sessions passed** (re-run after all
  three follow-ups and the review fix below): `tests` (3028 passed, 105
  skipped), `paper_tests` (160 passed), `safety_typecheck` (pyright, 0
  errors), `migration_smoke` (OK).
- New in the original PR: `tests/unit/test_runtime_normalization_contract.py`
  (42 tests), `tests/unit/test_runtime_client_normalization.py` (31),
  `paper_runtime/tests/test_normalization.py` (34, now 40 after all three
  follow-ups). Added 11 tests to `tests/unit/test_lumibot_adapter.py`, 1 to
  `tests/unit/test_lumibot_event_mapper.py`.
- New in follow-up 1: 3 lifecycle tests in `tests/unit/test_sync_paper_orders.py`
  (`CANCEL_REQUESTED` stays pollable; `EXPIRED` before and after a partial
  fill); 11 malformed-response tests plus 2 fixture updates in
  `tests/unit/test_runtime_client.py`; `tests/unit/test_normalization_corpus.py`
  and `paper_runtime/tests/test_normalization_corpus.py` (61 shared-corpus
  cases each); 4 new gateway regression tests in
  `paper_runtime/tests/test_normalization.py` (missing `filled_qty` on a
  `FILLED` order, missing `submitted_at`/`updated_at`, missing account
  `currency`).
- New in follow-up 2: 20 new tests in `tests/unit/test_runtime_client.py`
  covering the Milestone 11 external-order methods (found/not-found,
  canonical-shape, and malformed-nested-field cases for each of the seven
  methods) plus `get_order`'s preserved-shape/non-positive-quantity/missing-
  `time_in_force` cases; 3 fixture updates in
  `tests/unit/test_sync_paper_orders.py`, `tests/unit/
  test_submit_credentialed_paper_order.py`, and `tests/unit/
  test_runtime_client_normalization.py` to add the six now-required/
  preserved order fields; 2 new gateway tests in `paper_runtime/tests/
  test_normalization.py` (`DAY` default applies for a runtime-owned
  `client_order_id`, fails closed for a foreign one) — **both replaced in
  follow-up 4** (see below), since the ownership check they pinned was
  itself removed.
- New in follow-up 4: 18 new tests in `tests/unit/test_runtime_client.py`
  (7 for `submit_limit_order`'s canonicalization/no-retry/malformed-response
  cases; 11 for the `GET_ORDER_BY_CLIENT_ID`/`GET_ORDER` envelope validation
  — missing/zero/`None` `found`, mismatched echoed `book_id`/
  `client_order_id`, contradictory `found`/`order`, unexpected fields); 1
  new end-to-end reconciliation regression in `tests/unit/
  test_external_paper_broker.py`
  (`test_malformed_lookup_response_cannot_create_authoritative_not_found_or_unlock_retry`);
  2 gateway tests in `paper_runtime/tests/test_normalization.py` replacing
  follow-up 2's ownership-gated pair
  (`test_gateway_rejects_an_absent_time_in_force_regardless_of_client_order_id`,
  `test_gateway_rejects_an_absent_time_in_force_from_an_account_wide_listing`
  — the latter using a forged `"intent-"`-prefixed `client_order_id`).
- New in the review fix on commit `3193b0b` (D8 Ruling 15): a *found*
  lookup response was validated for shape but not bound to the requested
  identifiers, and `submit_external_paper_order`'s duplicate-submit path
  never ran `_validate_order_response` at all — together allowing an
  unrelated book's order, a foreign account's order, or a live (non-paper)
  order to be reported as a successful existing submission. 9 new
  `RuntimeClient` tests in `tests/unit/test_runtime_client.py` (mismatched
  `book_id`/`client_order_id`/`broker_order_id`, claimed live
  environment/unrelated provider, for both lookup methods, plus two
  correctly-matched happy-path cases); 3 new regressions in `tests/unit/
  test_external_paper_broker.py` proving the duplicate-submit path now
  rejects a foreign account fingerprint, a live environment, and a
  mismatched quantity instead of reporting success.
- `nox -s typecheck` is not part of `nox -s ci` and carries a large
  pre-existing baseline (2530 errors). This PR takes it to 2535: all five are
  the *same* pre-existing `ScriptedGateway`-does-not-satisfy-`PaperBrokerGateway`
  error the six existing uses of that test double already produce, from the
  five new adapter tests. No new error appears in any source file.

**Known coverage note:** `tests/unit/test_lumibot_adapter.py` is guarded by
`pytest.importorskip("lumibot")` and LumiBot is not installable via any root
extra (`DECISIONS.md` D5), so the 11 new adapter tests skip in the `tests`
nox session and run locally. The runtime-side gateway tests do run for real in
`paper_tests`, where LumiBot is a base dependency. This is the pre-existing
arrangement; PR 9 did not change it.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, external-broker state machine, or scheduling behavior was modified; no
broker, provider, model, or market-data service was called; no credentials
were read; no order of any kind was submitted.

## Completed work (PR 10)

**Scope:** one new test file,
`tests/unit/test_external_broker_runtime_client_parity.py`; plus this file,
`MASTER_PLAN.md`, and `DECISIONS.md` (D1 resolution note). No file under
`src/`, `scripts/`, `paper_runtime/src/`, or `config/` was modified — this
PR adds test coverage for reconciliation logic that already exists; per
D1 and the bounded prompt, the book ledger is not removed, and no
production behavior changed.

**The gap this PR closes:** every existing `paper_books/external_broker.py`
reconciliation test (`tests/unit/test_external_paper_broker.py`, 2700+
lines) drives the `ExternalPaperRuntime` protocol through `FakeRuntime`, a
hand-rolled test double that returns raw dicts directly. It never goes
through `RuntimeClient` (`runtime/client/process_client.py`), so it never
exercises the PR 9 normalization contract
(`ExternalOrderSnapshot`/`ExternalFillSnapshot`/`ExternalPositionsSnapshot`/
`ExternalAccountSnapshot`, `parse_client_order_lookup_response`) that sits
between the wire and `external_broker.py` in production. `RuntimeClient` is
the only production type that structurally satisfies `ExternalPaperRuntime`
(confirmed by inspecting both — all nine protocol methods match by name and
signature) — `services/reconcile_paper.py` and every Milestone 11
external-order call site pass a real `RuntimeClient` as `runtime=`. So
nothing previously proved that a *normalized* broker observation —
validated exactly as production validates it — reconciles correctly into
`paper_books`' real SQLite-backed cash ledger and positions tables. This is
precisely D1's requirement for PR 10: "the application reconciles those
observations into `paper_books`; LumiBot never mutates book state
directly," proven end to end rather than only at the reconciliation-logic
layer in isolation (which was already covered).

Two other reconciliation paths in this repository were inventoried and
found not to be `paper_books`-scoped, confirming the gap is real rather
than already covered elsewhere: `execution/account_reconciliation.py`
(exercised by `tests/unit/test_account_reconciliation.py`) reconciles
against plain `Decimal` cash/quantity values, not a real book ledger; and
`services/reconcile_paper.py::reconcile_paper_account_and_positions`
(exercised by `tests/unit/test_reconcile_paper.py`, which already uses a
real `RuntimeClient` + `FakeTransport`) reconciles against
`trading_research.paper.ledger.PaperLedger` — a separate, older,
book-agnostic ledger from the Milestone 4 era, not `paper_books`.

**Five new tests**, each driving `activate_external_reconciliation_
baseline`, `preview_external_paper_order`, `submit_external_paper_order`,
and/or `reconcile_external_paper_order` with a real `RuntimeClient` wired to
a scripted `FakeTransport` (`tests/support/runtime_client_fixtures.py`, the
same double `test_reconcile_paper.py`/`test_runtime_client.py` use),
asserting against the real `paper_books` ledger (`cash_ledger`,
`positions`) in a real SQLite connection:

1. `test_matched_submission_reconciles_through_real_normalized_runtime_client`
   — a full baseline-activation/preview/submit flow with a normalized
   `FILLED` order and one matching fill reconciles to `MATCHED`; the local
   order moves to `STATE_FILLED`; `cash_ledger.settled_cash` and
   `positions` reflect the exact fill.
2. `test_cash_mismatch_detected_through_real_normalized_runtime_client` — a
   normalized account snapshot reporting the pre-trade cash figure (the
   broker never deducted the trade) is detected as `CASH_MISMATCH`; the
   local ledger is never silently repaired to agree with the broker.
3. `test_position_mismatch_detected_through_real_normalized_runtime_client`
   — a normalized positions snapshot reporting one fewer share than the
   normalized fill applied is detected as `POSITION_MISMATCH`; local
   positions are never silently repaired.
4. `test_expired_order_round_trips_through_real_normalized_runtime_client`
   — the PR 9 motivating defect, reproduced through the real client: an
   order the broker later reports as `EXPIRED` (previously outside
   `execution/broker_snapshots.py::SUBMISSION_STATES`, producing a
   permanently unreadable submission row) reconciles cleanly through
   `RuntimeClient`'s normalization, reaches the terminal `STATE_EXPIRED` in
   the local event chain, and leaves `paper_books` exactly as it was before
   submission — no shares, no cash movement.
5. `test_malformed_order_lookup_fails_closed_without_corrupting_book` — a
   malformed `GET_ORDER_BY_CLIENT_ID` envelope (a non-boolean `found`)
   raises `ProtocolViolationError` at the `RuntimeClient` boundary;
   `_run_reconciliation` downgrades this to a non-authoritative `UNKNOWN`
   outcome and returns before calling `GET_POSITIONS`/
   `GET_ACCOUNT_SNAPSHOT`, proving a malformed broker response cannot
   silently repair or corrupt `paper_books` state.

**Tests run:**
- `pytest tests/unit/test_external_broker_runtime_client_parity.py -q
  --tb=short` — **5 passed**.
- `pytest tests/unit/test_external_broker_runtime_client_parity.py
  tests/unit/test_external_paper_broker.py tests/unit/test_reconcile_paper.py
  tests/unit/test_account_reconciliation.py
  tests/unit/test_paper_books_execution_and_reconciliation.py
  tests/unit/test_runtime_client.py
  tests/unit/test_runtime_normalization_contract.py -q --tb=short` — **256
  passed** (the full reconciliation/normalization-adjacent surface, to
  confirm no existing test's fixtures or assumptions were disturbed).
- `nox -s ci` — **all four blocking sessions passed**: `tests` (3116
  passed, 105 skipped), `paper_tests` (160 passed), `safety_typecheck`
  (pyright, 0 errors — the new test file is outside `pyright-safety.json`'s
  scope), `migration_smoke` (OK).
- `scripts/check_links.sh` — 186 links checked, 0 errors.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, external-broker state machine, or scheduling behavior was modified;
no broker, provider, model, or market-data service was called; no
credentials were read or referenced; no order of any kind was submitted;
the scheduler was not enabled. All broker interaction in the new tests is
through `FakeTransport`, a fully scripted, offline, in-process double — no
subprocess is spawned and no network call is made.

## Completed work (PR 11)

**Scope:** one new module, `src/trading_research/evaluation/
analytics_parity.py`; two new test files,
`tests/unit/test_analytics_parity.py` and
`tests/unit/test_analytics_parity_import_boundary.py`; a scratch
comparison script and its captured output under
`docs/library-migration/pr11/`; a new blocking `analytics-tests` CI job in
`.github/workflows/ci.yml`; a one-paragraph docstring correction in
`src/trading_research/vector_research/adapter.py` (no behavior change); and
this file, `MASTER_PLAN.md`, `DECISIONS.md` (D9), `REMOVAL_MANIFEST.md`,
and `COMPONENT_MATRIX.md`. **`evaluation/metrics.py` was not modified.** No
other file under `src/`, `scripts/`, `paper_runtime/src/`, or `config/` was
touched.

**Scope clarification (see `DECISIONS.md` D9 for the full reasoning):**
`MASTER_PLAN.md` row 11's terse text ("Replace `evaluation/metrics.py`
formulas with ...") reads the same way rows 3 and 4 did, and those two PRs
removed their target formulas immediately. `REMOVAL_MANIFEST.md` and
`MASTER_PLAN.md` row 17 say otherwise for this row, consistently and by
name: the manifest's default rule ("current custom implementation remains
authoritative" until its *own* assigned removal PR) applies here because,
unlike the PR 3/PR 4 rows, this row was never given an explicit early-close
override — it still reads "PR 17 (parity proven in PR 11)." Row 17 itself
lists PR 11 as a source of pending removal work to "execute," which would
be meaningless if PR 11 had already executed it. **Conclusion: PR 11 proves
fixture parity; it does not replace or remove `evaluation/metrics.py`'s
formulas.** `evaluation/metrics.py`'s `sharpe_ratio`, `sortino_ratio`,
`max_drawdown`, `calmar_ratio`, and `cumulative_return` remain unchanged and
are still the only implementation `research_comparison.py`,
`paper_books/comparison.py`, and `cli.py` call. PR 17 decides whether and
how to execute the removal this PR's parity proof now conditions.

**New module: `evaluation/analytics_parity.py`.** A library-backed
candidate implementation, structurally parallel to `evaluation/metrics.py`:
`cumulative_return_parity`, `sharpe_ratio_parity`, `sortino_ratio_parity`,
`max_drawdown_parity`, and `calmar_ratio_parity` take the same
`list[RecommendationEvaluation]` input and return the same `MetricsResult`
shape (`status`/`value`/`sample_size`/`reason`), so
`tests/unit/test_analytics_parity.py` can call both implementations on the
same fixture and assert status equality always, value equality (via
`math.isclose`) whenever both are `OK`. `empyrical-reloaded` is the
primitive authority used (`import empyrical`, guarded by a `try`/`except
ImportError` with an actionable `pip install -e ".[analytics]"` message,
matching `scripts/indicators.py`'s TA-Lib convention); `quantstats-lumi` is
exercised only by a separate `presentation_summary()` function, explicitly
never compared against `evaluation/metrics.py` or the `*_parity` functions
for parity (`DEPENDENCY_MATRIX.md` Section 6: "two independent authorities
over the same metric is a defect, not a feature"). Out of this scope, by
`REMOVAL_MANIFEST.md`'s own row: `hit_rate`, `average_return`,
`median_return`, `gain_loss_ratio`, `recommendation_to_fill_rate`, and
`group_by` have no library equivalent and are untouched.

**Numeric parity findings** (captured in
`docs/library-migration/pr11/comparison_output.txt`, reproducible via
`docs/library-migration/pr11/boundary_comparison_scratch.py`;
`empyrical-reloaded 0.5.12`, `quantstats-lumi 1.1.5`):

* `cumulative_return`: `empyrical.cum_returns_final(returns,
  starting_value=0)` matches the custom compounding formula bit-for-bit
  across every fixture tested.
* `sharpe_ratio`/`sortino_ratio`: `empyrical.sharpe_ratio`/
  `sortino_ratio` with `annualization=ANNUALIZATION_TRADING_DAYS` (252,
  equivalent to `period="daily"`) match the custom annualized formula
  bit-for-bit; `annualization=1` matches the custom unannualized (`annualize
  =False`) formula bit-for-bit. Confirms `evaluation/metrics.py`'s 252
  -trading-day annualization convention is exactly `empyrical`'s own
  `"daily"` period convention, not a coincidence requiring a scaling
  correction.
* `max_drawdown`: matches to ~1e-16 floating-point noise across every
  fixture (e.g. `-0.020000000000000122` vs. `-0.02000000000000008`) — the
  same class of acceptable float noise `scripts/indicators.py` documented
  for TA-Lib's Bollinger Bands in PR 4.
* **Zero-variance/zero-downside Sharpe and Sortino do *not* match without a
  boundary correction.** `empyrical.sharpe_ratio`/`sortino_ratio` do not
  special-case a zero (or floating-point-noise-near-zero) standard
  deviation: a flat 6-value 0.05 fixture returned Sharpe `1.04e17` (a huge
  finite float, not `NaN`/`inf`) from raw `empyrical.sharpe_ratio`, and a
  monotonically-increasing 5-value fixture returned Sortino `inf` from raw
  `empyrical.sortino_ratio`, where `evaluation/metrics.py`'s
  `math.isclose(std, 0.0, abs_tol=1e-12)` check reports `UNDEFINED`.
  `analytics_parity.py`'s `sharpe_ratio_parity`/`sortino_ratio_parity`
  compute the same variance/downside-deviation check independently, before
  calling either `empyrical` function, and return `UNDEFINED` without
  calling the library function at all in that case — proven by
  `test_sharpe_ratio_undefined_on_flat_returns_not_a_huge_finite_number`
  and `test_sortino_ratio_undefined_with_no_downside_not_inf`.
* **Calmar ratio does not match under any `empyrical.calmar_ratio()`
  annualization setting.** For the 6-bar oscillating fixture: composed
  Calmar (custom formula) is `5.108`; `empyrical.calmar_ratio(period=
  "daily")` gives `2922.7`; `empyrical.calmar_ratio(annualization=1)` gives
  `0.817` — neither recovers `5.108`. Root cause: `empyrical.calmar_ratio`
  divides a CAGR-style annualized-return numerator (compounding across
  `len(returns)` periods) by max drawdown, while `evaluation/metrics.py`
  divides the raw (non-annualized) cumulative return by max drawdown — a
  convention mismatch, not a units/scaling difference, and one that does not
  fit this repository's data anyway (independent per-recommendation
  returns, not a fixed-frequency daily bar series a CAGR annualization
  assumes). `calmar_ratio_parity` is therefore composed from
  `cumulative_return_parity`/`max_drawdown_parity` directly and never calls
  `empyrical.calmar_ratio()` — the same "adapter composed from primitives,
  not the library's single-shot function" pattern PR 4 used for `macd`/
  `trix` over `talib.MACD()`/`talib.TRIX()`.
  `test_calmar_ratio_parity_diverges_from_raw_empyrical_calmar_ratio`
  documents this divergence behaviorally, not just in prose.
  `quantstats_lumi.stats.calmar` was evaluated for `presentation_summary()`
  and excluded for a sharper reason than a convention mismatch: it requires
  a real `DatetimeIndex` to compute elapsed wall-clock time (its `cagr()`
  helper calls `.total_seconds()` on the index range) and raises
  `AttributeError` on the plain integer-indexed `Series` every other
  function in this module receives.
* Zero max drawdown: both `evaluation/metrics.py` and
  `calmar_ratio_parity` report `UNDEFINED` (division by zero), never `inf`
  — verified against the monotonically-increasing fixture, where raw
  `empyrical.calmar_ratio` itself returns `NaN` (its own internal guard,
  not one this module relies on for the parity claim).

**Not authoritative, enforced structurally, not by convention.**
`tests/unit/test_analytics_parity_import_boundary.py` AST-parses every file
under `src/trading_research/` (analogous to
`tests/unit/test_vector_research_import_boundary.py`) and asserts: (1)
`empyrical`/`quantstats_lumi` are imported nowhere except
`evaluation/analytics_parity.py`; (2) no other production module imports
`analytics_parity`. Both run unconditionally — pure `ast` source parsing,
no import of either library — so they hold even without the `analytics`
extra installed, unlike the parity tests themselves.

**Dependency behavior:** `evaluation/analytics_parity.py` does `import
empyrical` and `import quantstats_lumi.stats` at module scope, each inside
its own `try`/`except ImportError` that re-raises with an actionable `pip
install -e ".[analytics]"` message — both dependencies were already
declared in the `analytics` extra by PR 1; PR 11 adds no new dependency
declaration.

**CI:** a new blocking `analytics-tests` job
(`.github/workflows/ci.yml`), matrixed over Python 3.10/3.11 matching
`indicators-tests`' pattern (neither `empyrical-reloaded` `>=3.9` nor
`quantstats-lumi` `>=3.6` narrows this project's `>=3.10` floor), installs
`.[dev,analytics]` and runs `test_analytics_parity.py` +
`test_analytics_parity_import_boundary.py` for real — `main-tests` alone
(`.[dev]` only) would otherwise let the parity tests skip silently via
their module-level `pytest.importorskip` guards, masking a real regression.
The existing `dependency-extras-smoke` matrix already covered the bare
`import empyrical, quantstats_lumi` check for the `analytics` extra (added
PR 1) and needed no change.

**Tests run:**
- `pytest tests/unit/test_analytics_parity.py
  tests/unit/test_analytics_parity_import_boundary.py -q --tb=short`
  (analytics extra installed) — **58 passed**.
- The same two files, in a fresh venv with only `.[dev]` (no `analytics`
  extra) installed — the import-boundary file's 3 tests **passed** (they
  need neither library); `test_analytics_parity.py` **skipped** as a whole
  module (`pytest.importorskip`), never silently masking a failure as a
  pass.
- `pytest tests/unit/test_analytics_parity.py
  tests/unit/test_analytics_parity_import_boundary.py tests/unit/
  test_metrics.py tests/unit/test_vector_research_adapter.py tests/unit/
  test_vector_research_import_boundary.py
  tests/unit/test_research_comparison_extensions.py -q --tb=short` — **92
  passed, 40 skipped** (`vector_research`'s VectorBT-dependent tests skip,
  since only the `analytics` extra was installed — the "never combine
  extras" isolation convention `DEPENDENCY_MATRIX.md` Section 3 requires).
- `pytest tests/ -q --tb=short` (full offline suite, `analytics` extra
  installed) — **3245 passed, 57 skipped, 0 failed** — confirms adding
  `empyrical`/`quantstats_lumi` to the environment disturbs nothing
  elsewhere.
- `nox -s ci` — **all four blocking sessions passed**: `tests` (3119
  passed, 106 skipped, `.[dev]` only — the two new parity-dependent test
  files' module skips without the `analytics` extra), `paper_tests` (160
  passed), `safety_typecheck` (pyright, 0 errors — `analytics_parity.py` is
  outside `pyright-safety.json`'s scope, consistent with it not being a
  safety-critical module), `migration_smoke` (OK).
- `scripts/check_links.sh` — 186 links checked, 0 errors.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live or historical market data was
fetched — every fixture in the new tests is a small, hand-constructed
in-memory list of `RecommendationEvaluation` objects; the scheduler was not
enabled; no external paper order of any kind was submitted or referenced.

## Completed work (PR 12)

**Scope:** evaluation only — `docs/library-migration/pr12/EVALUATION.md`
(full evaluation and recommendation),
`docs/library-migration/pr12/scratch_smoke_test.py`, `scratch_output.txt`,
and `scratch_output_py311.txt` (scratch reproductions, not merged into
`src/`), plus this file, `MASTER_PLAN.md` row 12, `DEPENDENCY_MATRIX.md`,
`COMPONENT_MATRIX.md`, and `DECISIONS.md` (D10). No file under `src/`,
`scripts/`, `paper_runtime/src/`, or `backtest_runtime/` was modified. No
change to `pyproject.toml`. This PR's review fix rounds added
`tests/unit/test_pr12_evaluation_docs.py` — a documentation-consistency
regression test, not application code — and, to give that test's
`git merge-base --is-ancestor` check the commit history it needs in CI,
changed `.github/workflows/ci.yml` so the `main-tests`,
`python-3-10-floor`, and `research-tests` jobs' `actions/checkout@v4`
steps set `fetch-depth: 0` instead of the default single-commit shallow
checkout; see "Tests run" below.

**Outcome: defer, do not adopt.** Riskfolio-Lib 7.3.0 was re-verified live
against the PyPI JSON API: BSD-3-Clause, `License :: OSI Approved :: BSD
License` — conventional OSI-approved open source, unlike VectorBT's
Apache-2.0 + Commons Clause terms (`DECISIONS.md` D4), so no owner exception
is needed on licensing grounds. A wheel-only install into a clean scratch
virtualenv (macOS arm64, Python 3.14.5rc1) resolved 82 packages with `pip
check` clean and no source compilation; its `vectorbt>=0.28.0` hard
dependency resolved to **`vectorbt==1.1.0`**, the exact version already
pinned by the approved `research` extra (PR 5) — confirmed live, not only by
reading declared metadata, that Riskfolio-Lib does not conflict with the
already-adopted VectorBT pin at Python 3.11.15 and 3.14.5rc1 only — the two
interpreters actually installed and tested, both within VectorBT's declared
`>=3.11,<3.15` range; Python 3.12 and 3.13 were not installed or tested and
remain unverified (the same `vectorbt>=1.1.0,<1.2` range cannot resolve on
this repository's `>=3.10` project-wide floor without also raising it to
`>=3.11`, nor on Python 3.15+ without a future VectorBT upgrade) —
confirmed at both the 3.14.5rc1 development interpreter and, in a later
review fix round, an independent second install on Python 3.11.15 itself
(the actual floor boundary VectorBT's `>=3.11,<3.15` classifier declares),
which resolved the same 82-package closure with a clean `pip check` and
successful imports. The closure nonetheless includes several
packages with no other purpose in this repository (Jupyter widget support,
a second charting library alongside the existing `streamlit`, multiple QP
solver backends, `astropy`). A functional scratch smoke test confirmed
`rp.Portfolio(...).optimization(...)` returns a plain `pandas.DataFrame` of
per-asset weights with no order/share/authorization-shaped surface —
structurally advisory, matching `MASTER_PLAN.md` row 12's framing — but also
surfaced a `cvxpy` deprecation warning triggered by Riskfolio-Lib's own
internal code, worth re-checking before any future adoption.
`COMPONENT_MATRIX.md`'s "Portfolio optimization" row lists no existing
implementation this would replace, and no module under
`src/trading_research/` constructs a multi-position target allocation today
— there is no in-repo consumer this dependency would unblock. Per the same
"no concrete current need" bar `DECISIONS.md` already applies to Pandera/
PyArrow, the recommendation is **defer**, not adopt. See `DECISIONS.md` D10
and `pr12/EVALUATION.md` for the full record, including the binding
advisory-only constraint (ADR 0003 pattern) any future re-evaluation must
still satisfy.

**Custom code removed:** none. This PR adds no new production capability and
removes none — `pyproject.toml`, `src/`, and `scripts/` are byte-for-byte
unchanged from `main`; `tests/` gained only the documentation-consistency
regression coverage described above (`tests/unit/test_pr12_evaluation_docs.py`),
added during this PR's review fix rounds, not new application code.

**Tests run:**
- `tests/unit/test_pr12_evaluation_docs.py` was added during this PR's
  review fix rounds (not present in the original evaluation-only commit).
  It pins the Python 3.11.15/3.14.5rc1-only qualification into `EVALUATION.md`,
  `DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`, `COMPONENT_MATRIX.md`,
  `DECISIONS.md` D10, and this file, and pins the independent Python 3.11
  boundary verification recorded above. The scratch reproductions still run
  only inside disposable virtualenvs outside this repository's dependency
  graph, never against the project's own `.venv`.
- `.venv/bin/python -m pytest tests/ -q --tb=short` — **3275 passed, 57
  skipped, 0 failed**.
- `nox -s ci` — all four blocking sessions passed: `tests` (3147 passed, 106
  skipped, `.[dev]` only), `paper_tests` (160 passed), `safety_typecheck`
  (pyright, 0 errors — `pr12/scratch_smoke_test.py` is outside both
  `[tool.pyright]`'s `include` and `pyright-safety.json`'s scope, same as
  PR 2's scratch file), `migration_smoke` (OK).
- `scripts/check_links.sh` — 195 links checked, 193 OK, 0 errors, 2
  excluded.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, or real
market-data service was called; the only network access was two read-only,
wheel-only `pip install` runs (Python 3.14.5rc1 and, added in a later
review fix round, Python 3.11.15) plus a read-only PyPI JSON metadata
lookup, each into a disposable scratch virtualenv outside the repository —
none of which touched the project's own dependency graph, `.venv`, or any
application code path; the scheduler was not enabled; no external paper
order of any kind was submitted or referenced.

## Completed work (PR 13)

**Scope:** no implementation — documentation
(`docs/library-migration/pr13/EVALUATION.md`) and two scratch reproductions
(`docs/library-migration/pr13/scratch_trigger_orm_vs_core.py`,
`scratch_alembic_linearity.py`, with raw output in
`scratch_trigger_output.txt`/`scratch_alembic_output.txt` and resolved
package versions in `scratch_pip_freeze.txt`), plus this file,
`DEPENDENCY_MATRIX.md`, `COMPONENT_MATRIX.md`, and `DECISIONS.md` (D11).
No file under `src/`, `scripts/`, `paper_runtime/src/`, or
`backtest_runtime/` was modified. No change to `pyproject.toml`.
`tests/unit/test_pr13_evaluation_docs.py` is a documentation-consistency
regression test, not application code.

**Outcome: defer, do not adopt.** SQLAlchemy 2.0.52 and Alembic 1.19.1 (a
`alembic>=1.18,<1.19` scratch pin resolved and tested 1.18.5) were
re-verified live against the PyPI JSON API: both MIT, both OSI-approved,
Alembic's `Requires-Python` (`>=3.10`) matching this repository's floor
exactly. Row 13 required two questions to be explicitly tested, not just
reasoned about:

(a) whether trigger-protected tables can be restricted to SQLAlchemy Core
statements only. A scratch reproduction copy-pasted the exact production
trigger DDL for `real_orders` (fully reserved) and `paper_book_cash_ledger`
(append-only) and drove both via Core statements and an ORM `Session`,
against a file-backed SQLite database (matching `storage/database.py`) so
that independent visibility checks use a genuinely separate DBAPI
connection, across seven masking cases, including three adversarial ones
beyond the original hypothesis: a caller that forgets to roll back after a
rejected flush (SQLAlchemy raises `PendingRollbackError` on the next
operation rather than proceeding), an ORM relationship cascade deleting a
parent row (the cascade still issues a real `DELETE` the trigger still
rejects), and an ORM UPDATE of an already-loaded row through the identity
map (rejected identically; re-reading the mutated attribute before an
explicit rollback itself raises `PendingRollbackError`). Every case failed
closed; no object ever appeared "persistent" in memory before rollback, nor
did any attribute return a stale/masked value. `DEPENDENCY_MATRIX.md`
Section 5's PR 0 concern — "the ORM's unit-of-work flush ordering and
identity-map caching can mask a trigger-rejected write" — is **withdrawn as
unsubstantiated**. An eighth case then proved the Core-only boundary can be
*enforced*, not just followed: a `before_flush` guard on the ORM `Session`
class blocks every permitted session construction path tested, before any
SQL is emitted, while Core access is unaffected. A ninth case proved that
guard's table policy is complete: derived by scanning production's schema
modules for every write-rejecting trigger (50 tables, not the 2 a
hand-maintained allowlist previously covered), the guard rejects ORM writes
pre-SQL against every one of them, and cannot silently go stale as
production's schema grows. Core-only for trigger-protected tables remains
the recommendation for any future adoption regardless, as an auditability
preference with a proven, complete enforcement mechanism available, not a
correctness requirement.

(b) whether Alembic's branching revision graph can be constrained to
linear-only history matching `storage/schema_version.py`'s existing
monotonic ledger. A second scratch reproduction built a real, disposable
Alembic environment and found Alembic already resists *accidental*
branching (an un-spliced second child of an existing head is refused by
default; an ambiguous `upgrade head` with multiple heads present is
refused), but a deliberate `splice=True` still creates a real branch, and
`alembic merge` converges to one head while leaving a merge revision (a
tuple `down_revision`) that is not linear. A custom gate (one head, no
revision with more than one child, no tuple `down_revision`, no non-empty
`depends_on`) caught every case, including the merge case where "one head"
alone would have looked linear but was not, and two further cases showing
a single or multiple `depends_on` dependency edge — which Alembic counts
toward neither `get_heads()` nor down-revision fan-out — evades a
`down_revision`-only gate entirely unless checked explicitly.
**Conclusion: constrainable to linear-only history, but only via a new,
unbuilt, permanently-maintained CI gate that checks `depends_on` as well
as `down_revision`** — `schema_version.py`'s `dict[int, ...]` ledger has no
branch or dependency concept to guard against in the first place.

Neither finding is a correctness blocker, but neither identifies a current
capability gap either: `COMPONENT_MATRIX.md`'s "Persistence"/"Migrations"
rows describe the existing hand-written `storage/*_schema.py` DDL and
`storage/schema_version.py`'s ordered-migration ledger as available for
evaluation, not broken or unmaintained, and no module today has a problem
SQLAlchemy/Alembic would solve that pattern does not already solve. Applying
the same "no concrete current need exists" bar `DECISIONS.md` already uses
for Pandera/PyArrow/Riskfolio-Lib: **defer**, not adopt. See `DECISIONS.md`
D11 and `pr13/EVALUATION.md` for the full record. No ADR was produced —
none is required when adoption is not recommended, per `DECISIONS.md` D2's
single-ADR rule, reapplied in D10.

**Custom code removed:** none. This PR adds no new production capability and
removes none — `pyproject.toml`, `src/`, and `scripts/` are byte-for-byte
unchanged from `main`; `tests/` gained only the documentation-consistency
regression coverage described above
(`tests/unit/test_pr13_evaluation_docs.py`).

**Tests run:**
- `tests/unit/test_pr13_evaluation_docs.py` pins the "defer, do not adopt"
  outcome, the withdrawn masking-hypothesis finding, and the
  linear-only-gate finding into `EVALUATION.md`, `DEPENDENCY_MATRIX.md`,
  `COMPONENT_MATRIX.md`, `DECISIONS.md` D11, and this file, and asserts the
  two scratch reproductions' raw output contains no `FAIL`/`UNEXPECTED`
  marker. The scratch reproductions themselves ran only inside a disposable
  virtualenv (`/tmp/pr13_scratch_venv`, not committed) outside this
  repository's dependency graph, never against the project's own `.venv`.
- `.venv/bin/python -m pytest tests/ -q --tb=short` — **3289 passed, 57
  skipped, 0 failed**; no test outside the new file was modified.
- `nox -s ci` — all four blocking sessions passed: `tests` (3161 passed, 106
  skipped, `.[dev]` only), `paper_tests` (160 passed), `safety_typecheck`
  (pyright, 0 errors — `pr13/scratch_trigger_orm_vs_core.py` and
  `pr13/scratch_alembic_linearity.py` are outside both `[tool.pyright]`'s
  `include` and `pyright-safety.json`'s scope, same as PR 2/PR 12's scratch
  files), `migration_smoke` (OK).
- `scripts/check_links.sh` — 189 links checked, 187 OK, 0 errors, 2
  excluded.

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, or real
market-data service was called; the only network access was two read-only,
wheel-only `pip install` runs into a disposable scratch virtualenv (never
the project's own `.venv`) plus two read-only PyPI JSON metadata lookups;
the scheduler was not enabled; no external paper order of any kind was
submitted or referenced.
