# Migration Status

**Current phase: PR 4 — complete.**
**Next phase: PR 5 — vectorized research library selection and adapter.**

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
declaration in the repository. `runtime/lumibot/adapter.py` (ADR 0001) and
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

1. **LumiBot-backtest-mode import-boundary question** (`DECISIONS.md` D4,
   open item 1) must be resolved before PR 6 starts. Not a blocker for
   PR 1 or PR 2.
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

**Outcome: TA-Lib is now the sole calculation authority** for
`ema_series`, `rsi_wilder`, `macd`, `trix`, and `bollinger`. All public
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
differences and reconciliations" below for why. `tests/unit/
test_talib_is_sole_authority_no_custom_formulas_remain` guards against a
custom formula (or a `statistics.pstdev` import) being reintroduced.

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

**Tests run:**
- `pytest tests/unit/test_indicators.py -q --tb=short` (TA-Lib installed
  via `.[indicators]`, verified wheel resolution on this machine, macOS
  arm64, Python 3.14) — **25 passed**. Covers: increasing, decreasing,
  flat, and oscillating prices; short inputs below every warm-up
  threshold; inputs at the exact warm-up threshold for EMA20, RSI14,
  Bollinger-20, the MACD signal (34 bars), the TRIX line (44 bars), and
  the TRIX signal (52 bars); a 250-bar long realistic synthetic series
  (matching the CLI self-test's sin+drift formula); the missing-TA-Lib
  actionable-error path; and each intentional semantic difference above.
- `pytest tests/unit/test_indicators.py -q --tb=short` with TA-Lib
  *uninstalled* (simulating the `main-tests` CI job, which only installs
  `.[dev]`) — **1 passed** (the missing-dependency guard), **24 skipped**
  (the `I` fixture's `pytest.importorskip("talib")`), **0 failed** — proves
  the ordinary default suite stays green without masking a real TA-Lib
  regression, since the dedicated `indicators-tests` CI job (added to
  `ci.yml`, installs `.[dev,indicators]`) runs all 25 for real.
- `python3 .claude/skills/run-agentic-trading-desk/driver.py` (TA-Lib
  installed) — all checks passed, including the CLI self-test, file-input,
  short-series-warning, and direct-import (`compute()` callable) paths for
  `indicators.py`, plus the unaffected `macro_pillar.py`/`score.py` checks.
- `pytest tests/ -q --tb=short` (full offline suite, TA-Lib installed) —
  **2820 passed, 17 skipped, 0 failed** (2795 passed in PR 3's baseline +
  the 25 new tests in this file; the 17 skipped count is unchanged from
  PR 3, confirming no other test file was affected).

**Wheel installation result:** `pip install -e ".[indicators]"` resolved
`TA-Lib==0.7.1` from a prebuilt wheel (`ta_lib-0.7.1-cp314-cp314-
macosx_14_0_arm64.whl`) with no compilation, reconfirming PR 1's finding
on this machine (macOS arm64) against a newer local Python (3.14, ahead of
the declared `>=3.10` floor and CI's 3.11). No system package or source
install fallback was needed or added.

**No fallback authority remains:** the custom EMA/RSI/MACD/TRIX/Bollinger
formulas are fully deleted from `scripts/indicators.py`; TA-Lib is the
only calculation code path for these five indicators. No
`pandas-ta-classic` dependency was added — wheel resolution did not fail
on any platform this PR could verify (macOS arm64 locally; the new
`indicators-tests` CI job verifies Linux/`ubuntu-latest`).

**Safety:** no trading limit, authorization rule, `paper_books` accounting
code, or scheduling behavior was touched; no broker, provider, model, or
market-data service was called; no live data was fetched; no real
provider/broker/paper/live order calls were made; the scheduler was not
enabled.

## Next PR

**PR 5 — vectorized research library selection and adapter.**

Bounded prompt for the next session:

```text
Implement PR 5: evaluate an OSI-approved vectorized-research library, or
obtain explicit owner approval for VectorBT's Apache-2.0 + Commons Clause
terms (docs/library-migration/DECISIONS.md D4 — currently
BLOCKED_PENDING_LICENSE_DECISION), before adding any new dependency; then
build a new, additive `research` optional-dependency group and adapter for
signal matrices/parameter sweeps, per docs/library-migration/
MASTER_PLAN.md row 5 and COMPONENT_MATRIX.md's "Vectorized research/
parameter sweeps" row.

Read first: docs/library-migration/STATUS.md, MASTER_PLAN.md,
COMPONENT_MATRIX.md, DECISIONS.md (D4), DEPENDENCY_MATRIX.md, and
pyproject.toml's existing optional-dependency groups (`indicators`,
`analytics`, `observability`) for the pattern a new `research` group
should follow.

If VectorBT's licensing is not resolved (owner approval not obtained),
evaluate and select an OSI-approved alternative instead (e.g. a
vectorized backtesting/signal library without a Commons Clause or
similar non-OSI restriction) and document the comparison and choice in
this file.

This is new, additive capability — no existing file under `src/`,
`scripts/`, `paper_runtime/src/`, or `config/` is replaced or removed by
this PR. Add an import-boundary test (the selected library is never
imported from `paper_runtime` and `paper_runtime` is never imported from
the new adapter), analogous to the existing LumiBot AST import-boundary
test. The new adapter has no execution authority — it does not place
orders, does not touch `paper_books` accounting, and is not wired into any
scheduled or live code path.

Do not touch scripts/indicators.py, analysis/indicators.py, or any file
this PR (PR 4) modified. Do not begin the Riskfolio-Lib evaluation (PR
12) or any Category-B PR (13/14).

Update docs/library-migration/STATUS.md, COMPONENT_MATRIX.md, and
DECISIONS.md (D4's resolution) recording: the library selected (or the
approval obtained) and why, the new `research` group's dependencies and
verified wheel/install result, the import-boundary test result, and an
exact bounded PR 6 prompt (the LumiBot-backtest-mode import-boundary
pre-step in MASTER_PLAN.md must be resolved in DECISIONS.md D4 open item 1
before PR 6 implementation begins, per MASTER_PLAN.md's pre-step row).

Safety: do not change trading limits or authorization rules; do not touch
paper_books accounting; do not enable scheduling; do not call a broker,
provider, model, or market-data service; do not fetch live data; do not
begin PR 6; do not merge automatically.

Open one PR titled "PR 5: Vectorized research library selection and
adapter". Stop after opening the PR.
```
