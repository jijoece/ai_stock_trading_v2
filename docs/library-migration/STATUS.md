# Migration Status

**Current phase: PR 1 — complete.**
**Next phase: PR 2 — Boundary validation evaluation.**

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

**Scope:** `pyproject.toml`, `paper_runtime/pyproject.toml`, and the
`docs/library-migration/*` documentation set — not `pyproject.toml` only, and
not limited to dependency declarations; PR 1 also corrected inaccurate
planning guidance recorded during PR 0.

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

`paper` extra (both `pyproject.toml` and `paper_runtime/pyproject.toml`):

```text
lumibot==4.5.78   (bumped from 4.5.74, identical in both distributions)
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
installation only as an explicit fallback (PR 4 scope).

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
- **Environment E1 (root `.[paper]` extra, standalone):** `pip install -e
  ".[paper]"` fails with `ResolutionImpossible` — lumibot's
  `google-genai<2.0.0,>=1.72.0` constraint conflicts with newer `google-adk`
  releases (`>=2.2.0`) that require `google-genai>=2.4`/`2.8`/`2.9`. **This
  is a pre-existing condition, not introduced by this PR**: it reproduces
  identically with the previous `lumibot==4.5.74` pin on unmodified `main`.
  Recorded as a remaining blocker below.
- **Environment E2 (isolated `paper_runtime[dev]`):** installed cleanly;
  `pip check` clean; resolved `lumibot==4.5.78`; paper_runtime suite —
  **59 passed, 0 failed**.
- **LumiBot version assertion:** programmatically confirmed both
  `pyproject.toml` and `paper_runtime/pyproject.toml` pin the identical
  exact version, `lumibot==4.5.78`.
- Indicators/analytics/observability/paper were never installed together in
  one combined environment, consistent with the isolation requirement.

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

## Custom code removed

None. PR 1 changed no file under `src/`, `scripts/`, `paper_runtime/src/`,
`tests/`, or `config/`.

## Library authority established

None yet beyond dependency declaration. No production code imports any PR 1
dependency; PR 3/4/11/15/16 wire the respective libraries into code.

## Tests run

- Main offline suite (Environment A): **2746 passed, 18 skipped, 0 failed.**
- `paper_runtime` suite (Environment E2): **59 passed, 0 failed.**
- No test file under `tests/` or `paper_runtime/tests/` was modified.

## Remaining blockers

1. **Root `.[paper]` extra dependency resolution (`pip install -e
   ".[paper]"`) is currently `ResolutionImpossible`** due to a conflict
   between lumibot's `google-genai<2.0.0,>=1.72.0` pin and newer
   `google-adk` releases' higher `google-genai` floors. Confirmed
   pre-existing (reproduces at `lumibot==4.5.74` on unmodified `main`), not
   introduced by this PR's version bump. The isolated `paper_runtime`
   distribution (the actual production install path) is unaffected and
   installs cleanly with `lumibot==4.5.78`. Not fixed in PR 1 — resolving
   it would require touching base dependency floors (`anthropic`, `mcp`,
   `jsonschema`) outside this PR's approved corrections, or an upstream
   lumibot fix. Flagged for owner decision; does not block PR 1 or PR 2.
2. **LumiBot-backtest-mode import-boundary question** (`DECISIONS.md` D4,
   open item 1) must be resolved before PR 6 starts. Not a blocker for
   PR 1 or PR 2.
3. **PR 13/14 feasibility outcomes** (SQLAlchemy/Alembic trigger-safety,
   APScheduler lease-coexistence) are unknown until those PRs run — PR 1
   does not depend on them.

## Next PR

**PR 2 — Boundary validation evaluation.**

Scope, per `DECISIONS.md` D2 and `MASTER_PLAN.md`:

```text
inventory untrusted-input boundaries (YAML/env/CLI/JSONL)
compare current hand-written validation against a Pydantic v2 boundary
  implementation: dependency/performance impact, error-message behavior,
  unknown-field rejection, secret-field handling
make no broad domain-model replacement — frozen dataclasses stay
  authoritative for internal domain code
add no Pydantic dependency by default
prepare an ADR only if adoption at a boundary is recommended, before adding
  pydantic to any dependency declaration
```

## Exact next-session prompt

```text
Implement PR 2 for the library-first migration of ai_stock_trading_v2:
Boundary validation evaluation.

Read first (bounded context only):
  docs/library-migration/STATUS.md
  docs/library-migration/DECISIONS.md (D2, and the VectorBT/Pydantic
    corrections recorded in PR 1)
  docs/library-migration/MASTER_PLAN.md (PR 2 row)
  docs/library-migration/COMPONENT_MATRIX.md (the boundary-parsing row)

Scope: evaluation only.

1. Inventory every untrusted-input boundary currently validated by hand:
   YAML/environment configuration loading, CLI request validation, external
   provider response parsing, broker/runtime JSONL message validation, and
   API/serialized DTO validation. Cite exact files and functions.
2. For each boundary, compare current hand-written validation against a
   Pydantic v2 boundary-model implementation on: dependency/performance
   impact, error-message behavior, unknown-field rejection, secret-field
   handling. A throwaway/scratch comparison implementation is acceptable;
   it does not need to be merged into src/.
3. Do not replace any frozen dataclass domain model
   (models/trading_models.py, analysis/screener.py::ScreeningConfig,
   analysis/scorer.py::ScoringConfig,
   recommendations/builder.py::FrozenRecommendation,
   paper_books/models.py, or any other @dataclass(frozen=True) domain type).
4. Do not add pydantic to pyproject.toml unless the comparison recommends
   adoption at one or more boundaries.
5. If adoption is recommended, draft (do not necessarily finalize) an ADR
   that explicitly supplements/narrows ADR 0001 before any pydantic
   dependency is added, per the single rule recorded in DECISIONS.md D2.
6. Do not modify any file under src/, scripts/, paper_runtime/src/, or
   tests/ unless the recommendation is adoption and the ADR is approved
   within the same session — if so, keep the change scoped to one boundary
   as a proof of concept, not a broad rollout.
7. Update docs/library-migration/STATUS.md at the end: record the
   evaluation outcome (adopt-at-boundary / do not adopt), and set the next
   PR to PR 3 (exchange_calendars migration) regardless of the Pydantic
   outcome, since PR 3 does not depend on PR 2.
8. Open one PR. Do not begin PR 3 in the same session. Do not merge
   automatically.
```
