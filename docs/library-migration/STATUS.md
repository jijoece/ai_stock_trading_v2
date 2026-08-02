# Migration Status

**Current phase: PR 7 — backtest parity report — IMPLEMENTED, NOT MERGED**
(branch `migration/07-backtest-parity-report`; report at
`docs/library-migration/pr7/PARITY_REPORT.md`).
**Next phase: PR 8 — the removal-decision gate, which PR 7's report is the
input to.**

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
## Completed work (PR 7) — IMPLEMENTED, NOT MERGED (PR #19)

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

## Next PR

**PR 8 — decide whether the custom backtest component can be safely removed.
Not started.** A decision gate only, not a pre-committed removal. Its input is
`docs/library-migration/pr7/PARITY_REPORT.md` and the raw data under
`pr7/results/`. The report establishes that on one genuinely identical
buy-and-hold the two engines agree on every economic number, and it explicitly
does **not** establish that `backtest_runtime` could replace
`backtesting/engine.py`: its *adapter capability defects* (D7 fees/slippage,
D8 realized P&L and exit support) plus D9 (mandatory risk exits) and D11 (no
order or position records) are the list of things that would have to be built
or accepted first, most of them on the `backtest_runtime` side — no sells, no
exits, no stops or targets, no maximum holding period, no fees, no slippage,
no realized P&L, no rejected entries, no multi-symbol, no risk-based sizing,
no daily-loss or drawdown limits.

**A general replacement adapter must also address peak-equity seeding and
state-series start (D13 + D1).** PR 7's exact case agrees on drawdown, but it
does so by *construction*, not because the two aggregations are equivalent:
`case_f_exact_entry_parity` is built so that `backtest_runtime`'s first reported
session is still flat and therefore marks at exactly the budget. Underneath
that:

- the legacy engine seeds its running peak equity with `initial_cash`, so a
  drawdown is measurable from the first session in the dataset;
- `backtest_runtime` seeds its peak with `0.0` and raises it on the first
  session it reports;
- by **D1** those are not the same session — LumiBot's first trading iteration
  is the second bar, so the runtime's series never contains the first.

On the five default-timing cases the entry is already booked in the runtime's
first reported session, so its seed carries the entry's mark and the legacy seed
does not, and the two peaks stay apart for the whole run (case A:
−0.0000499925… vs −0.00005). **Aligning entry timing does not fix this.** Any
adapter intended to replace `backtesting/engine.py` in general — rather than on
one hand-built fixture — has to decide what its peak is seeded with and which
session its state series starts on, or its drawdown and any limit derived from
it will disagree with the legacy engine on ordinary data. PR 7 classifies it as
a library semantic difference (neither seed contradicts its own run) and leaves
the decision to PR 8.

PR 8 must also weigh **D17** independently of any migration decision: the
legacy engine's run identity ignores the historical bar dataset, so two runs
over provably different bars share one `backtest_run_id` and
`configuration_hash`, and `_persist_result` treats the second as an idempotent
replay and discards it. That is a defect in the component being judged, found
by PR 7 but deliberately left unfixed there.

Must not begin in the same branch/session as PR 7.
