# Dependency Matrix

Compatibility research performed 2026-07-26 via live PyPI/GitHub lookups
(current training data is stale for this purpose — every figure below was
verified against the package index, not recalled). See `DECISIONS.md` D4 for
the decisions this data produced.

## PR 1 re-verification (2026-07-26)

The following packages were re-verified directly against the PyPI JSON API
(`https://pypi.org/pypi/<name>/json`, live package-index source, not
recalled) before editing `pyproject.toml` for PR 1:

| Library | Verified version | Requires-Python | Wheel availability | License (PyPI classifier/metadata) | Source |
|---|---|---|---|---|---|
| exchange_calendars | 4.13.2 | `>=3.10,<4` | Yes | Apache-2.0 | PyPI JSON API, 2026-07-26 |
| TA-Lib | 0.7.1 | `>=3.9` | Yes — manylinux2014/musllinux (x86_64/aarch64), macOS 13/14 (x86_64/arm64), Windows, cp39-cp314 | BSD-2-Clause | PyPI JSON API, 2026-07-26 |
| quantstats-lumi | 1.1.5 | `>=3.6` | Yes | Apache Software License | PyPI JSON API, 2026-07-26 |
| empyrical-reloaded | 0.5.12 | `>=3.9` | Yes | Apache License 2.0 | PyPI JSON API, 2026-07-26 |
| structlog | 26.1.0 | `>=3.10` | Yes | MIT/Apache-2.0 dual | PyPI JSON API, 2026-07-26 |
| opentelemetry-api | 1.44.0 | `>=3.10` | Yes | Apache-2.0 | PyPI JSON API, 2026-07-26 |
| opentelemetry-sdk | 1.44.0 | `>=3.10` | Yes | Apache-2.0 | PyPI JSON API, 2026-07-26 |
| hypothesis | 6.161.5 | `>=3.10` | Yes | MPL-2.0 | PyPI JSON API, 2026-07-26 |
| time-machine | 3.2.0 | `>=3.10` | Yes | MIT | PyPI JSON API, 2026-07-26 |
| lumibot | 4.5.78 | `>=3.10` | Yes | MIT | PyPI JSON API, 2026-07-26 |

Every one of these packages declares `Requires-Python` compatible with the
project's existing `>=3.10` floor. No approved PR 1 dependency requires
raising the floor, so `requires-python = ">=3.10"` is unchanged in both
`pyproject.toml` and `paper_runtime/pyproject.toml`. The `>=3.11` floor
recorded below (Section 2) was contingent on adding VectorBT, which PR 1 does
not do — see `DECISIONS.md` D4 update and the PR 1 correction record at the
end of this file.

## 1. Compatibility matrix

| Library | Version | Python | License | Maintenance | macOS | Offline tests | Dependency weight | Conflicts | Decision |
|---|---|---|---|---|---|---|---|---|---|
| exchange_calendars | 4.13.2 (2026-03) | `>=3.10,<4` | Apache-2.0 | Active | No native deps | Yes | Light (~5 deps) | None | **Adopt** |
| VectorBT (OSS `vectorbt`) | 1.1.0 (2026-07, re-verified PR 5) | `>=3.11,<3.15` | Apache-2.0 + Commons Clause | Active, secondary to commercial vectorbt.pro | No native deps | Yes | Heavy (numpy/pandas/numba) | `pandas>=3.0.3,<4.0`, `numpy>=2.4.6` conflicts with LumiBot's `numpy<2.5.0` (mitigated: `paper_runtime` is a separately installed distribution, never resolved in the same environment as `research`) | **Adopted (PR 5)** — explicit owner-approved exception to the OSI-approved-only posture (`DECISIONS.md` D4); added to a new `research` extra, requiring Python >=3.11 (narrower than this project's own `>=3.10` floor, which PR 5 left unchanged) |
| TA-Lib | 0.7.1 (2026-07) | `>=3.9` | BSD-2-Clause | Active | Prebuilt wheels for manylinux2014/musllinux, macOS 13/14, Windows, cp39-cp314 — no native install required on any CI-relevant platform | Yes | Light wrapper, native footprint | numpy>=2 compatible | **Adopt** — wheel first; native/system installation is an explicit fallback only if a target platform lacks a compatible wheel |
| pandas-ta (original) | 0.4.71b0 (2025-09, never left beta) | — | MIT | Facing archival past its own 2026-07-01 deadline | — | — | — | — | **Reject** |
| pandas-ta-classic (fork) | 0.6.52 (2026-06) | `>=3.10` | MIT | Active fork | No native deps | Yes | Moderate | numpy>=2.0, pandas>=2.0 | **Evaluate as fallback only** |
| QuantStats (original) | 0.0.81 (2026-01, revived after long gap) | — | Apache-2.0 | Historically stagnant | — | — | — | — | **Reject as primary** |
| quantstats-lumi (fork) | 1.1.5 (2026-06) | — | Apache-2.0 | Active (Lumiwealth, same maintainer as LumiBot) | No native deps | Yes (avoid its yfinance download helpers) | Moderate | Loose pins, no conflicts | **Adopt** |
| Empyrical (original) | archived | — | Apache | Abandoned (Quantopian) | — | — | — | — | **Reject** |
| empyrical-reloaded (fork) | 0.5.12 (2025-06) | `>=3.9` (3.10-3.13 tested) | Apache-2.0 | Slowed, standard replacement | No native deps | Yes | Light (numpy/scipy) | numpy>=2 requires pandas>=2.2.2 | **Adopt** |
| Pydantic v2 | 2.13.4 (2026-05) | 3.9-3.14 | MIT | Very active | Prebuilt wheels (Rust core) | Yes | Light | None | **Evaluate** (boundary-only, PR 2) |
| Pandera | 0.32.1 (2026-06) | 3.10-3.14 | MIT | Active | No issues | Yes | Light with `[pandas]` extra only | None known | **Defer** |
| PyArrow | 25.0.0 (2026-07) | 3.10-3.14 | Apache-2.0 | Very active | Prebuilt wheels both arches | Yes | Heavy (28-53MB wheel) | Watch numpy pin vs. numba (VectorBT dep) | **Defer** |
| LumiBot | 4.5.78 (repository pins 4.5.78 in `paper_runtime/pyproject.toml`; no `paper` extra exists in the root `pyproject.toml` — see `DECISIONS.md` D5) | 3.10-3.12 declared | MIT | Active | No native deps | Yes | Heavy, already isolated | `numpy<2.5.0,>=1.20.0`, `pandas>=2.2.0` conflicts with VectorBT; `google-adk[extensions]`→`litellm` pins `jsonschema==4.23.0` exactly, unconditionally conflicting with this repo's `jsonschema>=4.26.0` base floor | **Bumped to 4.5.78** in `paper_runtime`; root `paper` extra removed, not "no change" |
| Riskfolio-Lib | 7.3.0 | `>=3.10` | BSD-3-Clause (OSI-approved, confirmed PR 12) | Active | No native issues, heavy deps | Yes | Very heavy (82-package closure verified live, PR 12: cvxpy, matplotlib, sklearn, statsmodels, astropy, Jupyter widgets, plotly, multiple QP solvers) + hard-depends on `vectorbt>=0.28.0` (confirmed resolves to the already-adopted `vectorbt==1.1.0` with no conflict at Python 3.11.15 and 3.14.5rc1 only — the two interpreters tested, both within VectorBT 1.1.0's declared `>=3.11,<3.15` range, 3.12/3.13 untested; the adopted `vectorbt>=1.1.0,<1.2` range cannot resolve on this repository's `>=3.10` project-wide floor without also raising it, nor on Python 3.15+ without a future VectorBT upgrade) | Transitively pulls VectorBT's pandas/numpy chain | **Defer** (PR 12, evaluated 2026-08-23 — not added; no existing consumer, see `pr12/EVALUATION.md`) |
| SQLAlchemy | 2.0.52 (re-verified live, PR 13, 2026-08-23) | `>=3.7` | MIT | Very active | No issues | Yes | Light core (`typing-extensions`, `greenlet`) | None with sqlite3 | **Defer** (PR 13, evaluated 2026-08-23 — not added; no existing capability gap, see `pr13/EVALUATION.md`) |
| Alembic | 1.19.1 (re-verified live, PR 13, 2026-08-23; scratch-tested at 1.18.5) | `>=3.10` | MIT | Active (SQLAlchemy team) | No issues | Yes | Light | None | **Defer** (PR 13, evaluated 2026-08-23 — not added; branching graph constrainable to linear-only history only via a new, unbuilt CI gate, see `pr13/EVALUATION.md`) |
| APScheduler | 3.11.3 stable (v4 alpha, not production-ready; re-verified live, PR 14, 2026-08-30) | 3.8-3.14 | MIT | Active | No issues | Yes | Light (SQLite jobstore reuses SQLAlchemy) | SQLite jobstore documented unsuitable for multiple concurrent schedulers (re-confirmed at the source level, PR 14: `BaseScheduler`'s locks are in-process only, not a distributed lease); no distributed lease/fencing | **Defer** (PR 14, evaluated 2026-08-30 — not added; conflicts with ADR 0005 Decision 1's no-daemon architecture, and its stateless trigger classes alone solve none of this repository's market-calendar/catch-up/idempotency complexity, see `pr14/EVALUATION.md`) |
| Tenacity | 9.1.4 (2026-02, re-verified live, PR 14, 2026-08-30) | `>=3.10` | Apache-2.0 | Active | No issues | Yes | Negligible | None | **Defer** (PR 14, evaluated 2026-08-30 — not added; no existing capability gap, see `pr14/EVALUATION.md`; a structural AST test guards `external_broker.py` against future accidental use regardless) |
| Structlog | 26.1.0 (2026-06) | `>=3.10` | MIT/Apache-2.0 | Active | No issues | Yes | Minimal | None | **Adopt** (PR 15) |
| OpenTelemetry SDK/API | 1.44.0 | `>=3.10` | Apache-2.0 | Active | No issues | Yes | Light core, exporters opt-in | None | **Adopt** (PR 16, additive) |
| Hypothesis | 6.161.5 (2026-07) | 3.10-3.14 | MPL-2.0 | Very active | No issues | Yes | Light | None | **Adopt** (dev group) |
| time-machine | 3.2.0 (2025-12) | `>=3.10` | MIT | Active | Prebuilt wheels | Yes | Light | None | **Adopt** (dev group, preferred over freezegun) |
| freezegun | 1.5.5 (2025-08) | 3.8-3.13 (no 3.14) | Apache-2.0 | Active, weaker for pandas C-extension time | No issues | Yes | Light | None | **Reject as primary**, fallback note only |

## 2. Recommended Python version

**Corrected 2026-07-26 (PR 1), re-confirmed with a narrower scope 2026-07-26
(PR 5):** the project's Python floor **remains `>=3.10`** project-wide.
PR 5 adopted VectorBT (see `DECISIONS.md` D4 — explicit owner-approved
exception), which does require `>=3.11`, but scoped that requirement to the
new `research` optional-dependency group alone rather than raising the
global floor — see `DECISIONS.md` D4 for why (raising the global floor
would require editing PR 4's `indicators-tests` CI matrix, which PR 5's
bounded scope excludes). Every package verified for PR 1 (Section "PR 1
re-verification" above) still supports `>=3.10`; only the `research` extra
now diverges.

The paragraph immediately below is retained as the historical PR 0 finding;
PR 5 evaluated it and deliberately did not follow it (see above and
`DECISIONS.md` D4):

> **Python 3.11**, raised from the repository's then-current `>=3.10` floor.
> Binding constraint: VectorBT 1.1.0 requires `>=3.11`. Nothing else in the
> Adopt/Evaluate set requires more than 3.10. If PR 5 (or a future owner
> decision) approves VectorBT under an OSI-compatible license path, raising
> the floor to `>=3.11` at that time — not before — remains the correct call.
> **PR 5 outcome:** approved VectorBT but chose not to raise the floor this
> PR, for the CI-scope reason above; a future PR without that constraint can
> still make this call.

`paper_runtime` is a **separately installable distribution** (existing
pattern, not new) and can independently target whatever Python/numpy/pandas
range LumiBot supports without sharing a resolved environment with VectorBT
or Riskfolio-Lib. No new isolation mechanism is required.

## 3. Dependency groups

Corrected 2026-07-26 (PR 1) to match what was actually approved and
installed. See the PR 1 correction record at the end of this file for the
full list of differences from the original PR 0 proposal below.

```text
base dependencies (not an extra):
  anthropic, mcp, jsonschema, PyYAML, python-dotenv, httpx, pandas,
  streamlit, vaderSentiment   (existing)
  + exchange_calendars        (PR 1 — base dependency, not an optional
                               "core" extra; required application
                               infrastructure once PR 3 removes the custom
                               calendar)

research:
  vectorbt>=1.1.0,<1.2         (PR 5 — added; explicit owner-approved
                               exception to the OSI-approved-only posture,
                               DECISIONS.md D4; requires Python >=3.11,
                               narrower than this project's own >=3.10
                               floor, which PR 5 left unchanged; wired into
                               src/trading_research/vector_research/)

indicators:
  TA-Lib                      (PR 1 — added as an optional extra;
                               PR 4 wires it into scripts/indicators.py)
  pandas-ta-classic           (evaluated only in PR 4, and only if TA-Lib's
                               wheel resolution actually fails for a
                               supported target environment)

backtest:
  WILL NOT BE POPULATED. The pre-step before PR 6 (Opus review + pinned
  feasibility spike, 2026-07-26; sentinel-.env suppression proof and owner
  acceptance, 2026-08-01) selected a separate distribution,
  `backtest_runtime/`, over any root extra — see DECISIONS.md D4 and
  docs/adr/0009-lumibot-backtest-distribution-boundary.md (Accepted).
  That distribution owns its own pyproject.toml; PR 6 creates it.
  A root `backtest` extra containing lumibot could not resolve anyway, for
  exactly the reason the `paper` extra was removed below (re-verified against
  lumibot==4.5.78, not just the older pin).

paper:
  removed (PR 1) — `pip install -e ".[paper]"` cannot resolve for any
  published lumibot==4.5.x release (litellm's exact jsonschema==4.23.0 pin,
  pulled in via google-adk[extensions], unconditionally conflicts with this
  repo's jsonschema>=4.26.0 floor). `paper_runtime/pyproject.toml` is
  TODAY the sole LumiBot dependency authority (lumibot==4.5.78). Under the
  accepted ADR 0009 that becomes two isolated authorities once PR 6 creates
  `backtest_runtime/pyproject.toml` — paper_runtime/ (credentialed, live)
  and backtest_runtime/ (uncredentialed, offline), each owning its own
  declaration in its own separately-installed environment. The invariant
  that actually governs is unchanged either way: the ROOT pyproject.toml
  declares no LumiBot dependency and no extra containing one, ever. See
  `DECISIONS.md` D5 (and its reconciliation section),
  `docs/adr/0002-isolated-lumibot-runtime.md` (Amendment), and
  `docs/adr/0009-lumibot-backtest-distribution-boundary.md`.

analytics:
  empyrical-reloaded            (PR 1 — added; authoritative primitive
                                 metrics: returns, annualization, Sharpe,
                                 Sortino, drawdown, alpha/beta)
  quantstats-lumi               (PR 1 — added; reporting/presentation only:
                                 tear sheets, tables, charts)
  riskfolio-lib                 not added — evaluated and deferred in PR 12
                                 (docs/library-migration/pr12/EVALUATION.md,
                                 DECISIONS.md D10); pulls vectorbt>=0.28.0
                                 transitively (confirmed via live scratch
                                 install against PyPI, resolves cleanly to
                                 the already-adopted vectorbt==1.1.0), so
                                 the dependency question is resolved as
                                 "defer, no current consumer" rather than
                                 outstanding

observability:
  structlog                    (PR 1 — added; PR 15 wires it in)
  opentelemetry-sdk, opentelemetry-api   (PR 1 — added; PR 16 wires them in)

dev:
  pytest, pytest-asyncio        (existing)
  hypothesis                    (PR 1 — added)
  time-machine                  (PR 1 — added)
```

`pydantic`, `tenacity`, `sqlalchemy`, `alembic`, `apscheduler`,
`riskfolio-lib`, `pandera`, `pyarrow`, `pandas-ta`, `pandas-ta-classic` are
intentionally not placed in any group by PR 1 — each remains evaluated in
its own later PR before any dependency addition. `vectorbt` was PR 1's
remaining item in this list; it was added in PR 5 (`research` group), not
by PR 1 itself — see the "PR 1 correction record" note below and
`DECISIONS.md` D4.

## 4. Rejected or deferred libraries

| Library | Status | Reason | What remains authoritative |
|---|---|---|---|
| pandas-ta (original) | Reject | Beta-only, never left `0.4.x`, facing archival past its own 2026-07-01 deadline | `scripts/indicators.py`, then TA-Lib after PR 4 |
| Empyrical (original) | Reject | Abandoned since Quantopian's shutdown | `evaluation/metrics.py`, then empyrical-reloaded after PR 11 |
| QuantStats (original) | Reject as primary | Long stagnation before a recent revival; quantstats-lumi shares a maintainer with the already-adopted LumiBot | `evaluation/metrics.py`, then quantstats-lumi after PR 11 |
| freezegun | Reject as primary, fallback note only | Pure-Python monkeypatching misses time-travel inside pandas' C-extension datetime internals; no 3.14 support | time-machine |
| Riskfolio-Lib | Defer (PR 12, evaluated 2026-08-23) | OSI-approved and technically conflict-free at Python 3.11.15 and 3.14.5rc1 only — the two interpreters live-verified against the adopted `vectorbt==1.1.0`, both within VectorBT's declared `>=3.11,<3.15` range (3.12/3.13 untested; the adopted `vectorbt>=1.1.0,<1.2` range cannot resolve on this repository's `>=3.10` project-wide floor without also raising it to `>=3.11`, nor on Python 3.15+ without a future VectorBT upgrade), but its 82-package closure has no current in-repo consumer — `COMPONENT_MATRIX.md`'s "Portfolio optimization" row lists no existing implementation to migrate off of | No portfolio-optimization capability exists; none is added |
| Pandera | Defer | No current DataFrame-shaped contract exists in the repo | Existing dataclass/YAML validation |
| PyArrow | Defer | No current bulk historical-dataset storage requirement; heaviest dependency evaluated | Existing fixtures/SQLite |
| pandas-ta-classic | Evaluate only if needed | Only relevant if TA-Lib's native C-library requirement proves infeasible in some CI/macOS target | TA-Lib as primary |
| SQLAlchemy / Alembic | Defer (PR 13, evaluated 2026-08-23) | Both OSI-approved MIT and technically de-risked by live testing — the PR 0 "ORM masks a trigger-rejected write" concern is withdrawn as unsubstantiated (`pr13/EVALUATION.md` Section 2), and Alembic's branching graph was confirmed constrainable to linear-only history (Section 3) — but adopting either would migrate ~20 existing schema/repository modules, including every trigger-protected table, for no current capability gap, while adding a linear-only CI gate that would need to be built and maintained | `storage/*_schema.py` hand-written DDL and `storage/schema_version.py`'s ordered-migration ledger |

## 5. Special findings called out by the migration plan

- **VectorBT / LumiBot / Riskfolio-Lib / TA-Lib / QuantStats sharing one
  Python version:** they cannot share one *resolved environment* (VectorBT's
  `pandas>=3.0.3`/`numpy>=2.4.6` conflicts with LumiBot's `numpy<2.5.0`), but
  they do not need to — LumiBot is already isolated to its own distribution.
  All other libraries here are mutually compatible under Python 3.11 with a
  shared numpy>=2.4.6/pandas>=3.0 floor. Riskfolio-Lib hard-depends on
  `vectorbt>=0.28.0`, coupling it to the same floor.
- **TA-Lib native system package:** corrected 2026-07-26 (PR 1) — TA-Lib
  0.7.1 ships prebuilt binary wheels (`bdist_wheel`) for manylinux2014/
  musllinux (x86_64/aarch64), macOS 13/14 (x86_64/arm64), and Windows across
  cp39–cp314 (confirmed via the PyPI JSON API). A `pip install` on any of
  these platform/interpreter combinations resolves a compatible wheel
  without an unconditional `brew install ta-lib` / `apt install` step. PR 4
  must: (1) require a supported wheel, (2) verify wheel resolution on Linux
  CI and supported macOS architectures, (3) document a system-package/source
  install only as an explicit fallback for a platform lacking a compatible
  wheel, and (4) fail the compatibility check rather than silently compiling
  an unexpected native dependency.
- **pandas-ta maintenance status:** confirmed abandoned in its original
  form; `pandas-ta-classic` is the actively maintained fork.
- **Empyrical maintenance status:** confirmed the original is unmaintained;
  `empyrical-reloaded` is the standard modern replacement, though its own
  release cadence has slowed (last release 2025-06).
- **LumiBot dependency-tree conflict:** confirmed — its `numpy<2.5.0` ceiling
  is incompatible with VectorBT's `numpy>=2.4.6` floor in anything but a
  razor-thin overlap window, and untested against VectorBT's `pandas>=3.0.3`
  floor at all. Isolation via `paper_runtime` (already existing) is the
  correct mitigation, not a new requirement.
- **SQLAlchemy/Alembic vs. trigger-heavy safety tables — corrected 2026-08-23
  (PR 13):** SQLite triggers fire at the SQL-statement level regardless of
  ORM/Core usage. This PR 0 entry originally also asserted "the ORM's
  unit-of-work flush ordering and identity-map caching can mask a
  trigger-rejected write" as a reason to restrict any future SQLAlchemy
  usage of trigger-protected tables (append-only tables, `real_orders`) to
  Core-only statements. PR 13 tested that claim directly (six cases against
  the real production trigger DDL, including an unhandled failed flush and
  an ORM relationship cascade) and found **no masking in any case** —
  SQLAlchemy 2.0.52's session fails closed every time (`pr13/EVALUATION.md`
  Section 2). The masking-risk justification is withdrawn; the PR 13/D11
  ruling was **defer, not adopt**, for unrelated reasons (no current
  capability gap), so the Core-only recommendation is retained anyway as an
  auditability preference for any future adoption, not as a correctness
  requirement.
- **APScheduler vs. database leases/generation fencing:** APScheduler v3's
  SQLite jobstore is explicitly documented upstream as unsuitable for
  multiple concurrent schedulers sharing a store, and provides only coarse
  scheduler-level locking, not a distributed lease with fencing tokens.
  Conclusion: APScheduler can coexist for simple due-time triggering: it
  cannot replace the existing lease/generation-fencing logic.
- **Tenacity retry scoping:** confirmed decorator/context-manager scoped
  only, no global interception. It can be structurally restricted away from
  the ambiguous-broker-retry path (`external_broker.py`) simply by never
  applying it there — recommend a structural test enforcing this, analogous
  to the existing LumiBot-import AST test.
- **Pandera/PyArrow deferral:** confirmed no concrete current need exists;
  both remain deferred until the VectorBT research adapter (PR 5)
  establishes one.

## 6. PR 1 correction record (2026-07-26)

PR 1 corrected several inaccuracies in the PR 0 proposal. The stale cells
in Section 1's compatibility table (VectorBT, TA-Lib, and LumiBot rows) were
edited in place to state the current decision directly, rather than left
to contradict the corrections below; this section remains the authoritative
summary of what changed and why:

| Item | PR 0 proposal | PR 1 correction | Reason |
|---|---|---|---|
| `exchange_calendars` | Optional `core` extra | Base application dependency | Becomes required infrastructure once PR 3 removes the custom calendar; must not require an extra for ordinary install |
| VectorBT | Adopt, isolated `research` group | **Not added.** `BLOCKED_PENDING_LICENSE_DECISION` (`DECISIONS.md` D4) | Apache-2.0 + Commons Clause is source-available/fair-code, not conventional OSI-approved open source, without an explicit owner-approved exception |
| Riskfolio-Lib | Added to `analytics` group | **Not added.** Remains PR 12 evaluation-only at the time of PR 1 | Evaluation (need, dependency weight, advisory-output bounding, OSI-compatible resolution) had not happened yet as of PR 1; PR 12 completed it 2026-08-23 with a **defer** outcome — see the Section 1/4 rows above and `pr12/EVALUATION.md` |
| Python floor | Raise to `>=3.11` | **Unchanged, `>=3.10`** | The `>=3.11` requirement was solely VectorBT's; VectorBT is not added in this PR |
| TA-Lib install guidance | Unconditional `brew install ta-lib` / apt package | Prebuilt wheel required first; system install is a documented fallback only if wheel resolution fails on a supported target | TA-Lib 0.7.1 ships prebuilt wheels for all CI-relevant platform/interpreter combinations |
| Analytics authority | Ambiguous overlap between empyrical-reloaded and quantstats-lumi | empyrical-reloaded = authoritative primitives; quantstats-lumi = reporting/presentation only | Two independent authorities over the same metrics is a defect, not a feature |
| PR 5 title | "VectorBT research adapter" | "Vectorized research library selection and adapter" | PR 5 may select VectorBT only after the license decision is explicitly approved and recorded |
| Root `paper` extra | Bump `lumibot` patch pin in place, keep the extra | **Removed entirely.** `paper_runtime/pyproject.toml` is the sole LumiBot dependency authority today; under the accepted ADR 0009 it becomes one of two isolated authorities once PR 6 adds `backtest_runtime/pyproject.toml`, with the root still declaring none (`DECISIONS.md` D5 and its reconciliation section) | `pip install -e ".[paper]"` cannot resolve for any published `lumibot==4.5.x` release — `litellm`'s exact `jsonschema==4.23.0` pin (pulled in via `google-adk[extensions]`) unconditionally conflicts with this repo's `jsonschema>=4.26.0` floor |
