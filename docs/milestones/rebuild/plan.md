# Library-First Migration Orchestrator

## Repository

You are working in a local fork of:

```text
jijoece/ai_stock_trading
```

Local path:

```text
/Users/jijopaul/workspace/ai_stock_trading_v2/
```

This fork will replace custom generic quantitative-trading infrastructure with mature open-source libraries.

The original repository must not be modified.

---

# Your role

Act as the migration architect and implementation coordinator.

Use the current model allocation as follows:

```text
Opus:
- architecture
- migration sequencing
- dependency decisions
- difficult design reviews
- resolving semantic conflicts

Sonnet:
- normal implementation
- refactoring
- adapter development
- tests
- documentation updates

Haiku subagents:
- repository inventory
- symbol and import discovery
- test-output summarization
- mechanical documentation work
```

Do not use Haiku as the primary implementation model for:

```text
backtesting
portfolio accounting
broker execution
database migrations
point-in-time semantics
risk controls
concurrency
reconciliation
safety boundaries
```

When running through `opusplan`, use Opus to complete and approve the plan, then switch to Sonnet for implementation.

---

# Primary objective

Rebuild the fork so established open-source libraries become authoritative for generic quantitative infrastructure.

Use one library per responsibility.

Target authorities:

| Responsibility                           | Authoritative library                       |
| ---------------------------------------- | ------------------------------------------- |
| Vectorized research and parameter sweeps | VectorBT                                    |
| Event-driven backtesting                 | LumiBot                                     |
| Alpaca paper execution                   | LumiBot isolated runtime                    |
| Exchange sessions and holidays           | exchange_calendars                          |
| Technical indicators                     | VectorBT indicators or TA-Lib               |
| Performance analytics                    | QuantStats and Empyrical-compatible package |
| Portfolio optimization                   | Riskfolio-Lib                               |
| Validation and configuration             | Pydantic v2 and Pandera                     |
| Historical dataset storage               | PyArrow and Parquet                         |
| Persistence                              | SQLAlchemy 2.x                              |
| Database migrations                      | Alembic                                     |
| Scheduling                               | APScheduler                                 |
| Generic transient retries                | Tenacity                                    |
| Structured logging                       | Structlog                                   |
| Tracing and metrics                      | OpenTelemetry                               |
| Testing invariants                       | pytest and Hypothesis                       |

Do not install or operate multiple frameworks that own the same responsibility.

In particular, do not make Backtrader, LEAN, NautilusTrader, CCXT, LumiBot, and VectorBT simultaneously authoritative for execution or portfolio accounting.

---

# Custom functionality that must remain

Preserve application-specific functionality that differentiates this project:

```text
AI evidence and research orchestration
bounded model prompts and output validation
point-in-time evidence provenance
candidate and activation policies
operator authorization
explicit paper-order approval
token and cost budgeting
project-specific strategy definitions
hard trading safety limits
audit metadata
ambiguous-side-effect reconciliation
account fingerprint verification
readiness and promotion evidence
```

A thin adapter may translate between project models and an external library.

A thin adapter must not reimplement:

```text
cash accounting
portfolio accounting
position accounting
lot accounting
fill simulation
event loops
exchange calendars
performance formulas
generic retries
scheduler internals
ORM behavior
migration versioning
```

---

# Non-negotiable safety requirements

Preserve these invariants:

```text
research-only by default
scheduler disabled by default
automated paper submission disabled by default
live trading unavailable
no options
no margin
no shorting
whole shares only
limit orders only
DAY time in force only
maximum single trade: $50
maximum submitted paper notional per UTC day: $150
maximum symbol allocation: 10%
```

Required paper authorization:

```text
Approved, submit this exact paper order.
```

Reserved live authorization phrase:

```text
Approved, place this exact order.
```

Do not implement live trading.

Credentials must provide authentication only and must never activate a capability.

---

# External-call restrictions

During migration:

```text
real model calls: 0
real provider calls: 0
real broker calls: 0
paper orders: 0
live orders: 0
```

Do not:

```text
contact Alpaca
contact Robinhood
call Anthropic/OpenAI from repository code
fetch real SEC/news/market data
use production credentials
enable schedulers
enable automated research
enable paper submission
enable live execution
```

Use deterministic fixtures, mocked adapters, temporary databases, and offline datasets only.

---

# Token-efficiency rules

This migration must not be attempted in one Claude Code session.

## Repository-reading discipline

Do not repeatedly scan the entire repository.

For each phase:

1. Read `docs/library-migration/STATUS.md`.
2. Read the phase-specific plan.
3. Read only the source and test files listed in that plan.
4. Use targeted `rg`, `git grep`, and file-range reads.
5. Do not reread unrelated architecture documents.
6. Do not dump entire large files when a relevant range is sufficient.
7. Do not load full test logs into the main context.
8. Do not launch an agent team.
9. Use at most one narrow Haiku support subagent at a time.
10. Start a fresh Claude Code session for every PR.

Keep `CLAUDE.md` limited to universal project constraints.

Store migration-specific instructions under:

```text
docs/library-migration/
.claude/skills/library-migration/
```

---

# Initial task: planning only

For this first session, do not modify production code.

Perform the following work using Opus.

## 1. Build a targeted repository inventory

Identify current implementations for:

```text
data normalization
technical indicators
strategy signals
exchange calendars
backtesting
fill simulation
cash accounting
position accounting
lot accounting
portfolio valuation
risk sizing
order construction
broker execution
reconciliation
analytics
scheduling
retry handling
configuration
persistence
migrations
logging
telemetry
```

Use Haiku only for narrow inventory tasks when helpful.

Do not ask a Haiku subagent to make architecture decisions.

## 2. Verify dependency compatibility

For each proposed library, determine:

```text
supported Python version
license
maintenance status
macOS compatibility
offline-test support
dependency weight
known conflicts
whether it actually replaces the intended custom responsibility
```

Do not install dependencies during this planning phase.

Identify incompatible or abandoned packages before recommending them.

## 3. Define one authority per responsibility

Create a clear authority matrix.

No responsibility may have both a custom and library implementation after migration completion.

Temporary dual operation is allowed only during parity validation.

## 4. Create migration documents

Create plans for the following files, but do not write them until I approve the plan:

```text
docs/library-migration/ARCHITECTURE.md
docs/library-migration/MASTER_PLAN.md
docs/library-migration/STATUS.md
docs/library-migration/COMPONENT_MATRIX.md
docs/library-migration/REMOVAL_MANIFEST.md
docs/library-migration/DEPENDENCY_MATRIX.md
docs/library-migration/DECISIONS.md
```

## 5. Break the work into small PRs

Each PR should:

```text
have one authoritative responsibility
touch a bounded set of files
have independent acceptance tests
avoid unrelated refactoring
remove custom code only after parity is proven
end with an updated STATUS.md
```

Prefer approximately:

```text
5–15 production files per PR
one library or one tightly related migration concern
one full offline test-suite run per PR
```

---

# Required migration sequence

Use this order unless repository evidence proves a dependency requires adjustment.

## PR 0 — Architecture and dependency decisions

Deliver:

```text
authority matrix
dependency matrix
license review
Python-version decision
migration sequence
removal manifest
```

No production behavior changes.

## PR 1 — Library foundation

Introduce dependency groups and version constraints.

Suggested groups:

```text
core
research
backtest
paper
analytics
dev
```

Keep heavyweight LumiBot dependencies isolated from the minimal offline install.

## PR 2 — Canonical data contracts

Introduce:

```text
Pydantic models
Pandera schemas
Arrow/Parquet dataset contracts
explicit temporal fields
```

Required temporal concepts:

```text
event_timestamp
published_timestamp
available_at
retrieved_at
effective_timestamp
evaluation_timestamp
```

## PR 3 — Exchange calendar replacement

Replace custom session arithmetic with `exchange_calendars`.

Cover:

```text
normal sessions
early closes
holidays
weekends
DST changes
pre-market
intraday
after-hours
previous session
next session
session counts
```

Delete custom calendar logic after parity passes.

## PR 4 — Indicator replacement

Replace custom indicators with VectorBT or TA-Lib equivalents.

For each indicator:

```text
compare fixture outputs
document warm-up semantics
document null semantics
document rounding differences
select intended library behavior
migrate callers
remove custom function
```

## PR 5 — VectorBT research adapter

Use VectorBT for:

```text
signal matrices
parameter sweeps
exploratory vectorized portfolio runs
candidate-ranking inputs
```

Keep execution and operator authorization outside VectorBT.

## PR 6 — LumiBot event-driven backtest adapter

Create LumiBot strategy adapters for:

```text
momentum breakout
mean reversion
event catalyst
baseline benchmark
```

Do not delete the existing backtester yet.

## PR 7 — Backtest parity harness

Run identical fixture signals through:

```text
old custom backtester
new LumiBot backtester
```

Compare:

```text
orders
fills
entry and exit timestamps
prices
quantities
cash
positions
fees
realized P&L
unrealized P&L
equity
drawdown
final portfolio value
```

Classify differences as:

```text
old-engine defect
adapter defect
intentional library semantic difference
unsupported requirement
```

## PR 8 — Remove custom backtester

Delete:

```text
custom event loop
custom fill simulation
custom trade reconstruction
custom position reconstruction
custom holding-session calculations
```

Do not retain a fallback switch.

## PR 9 — LumiBot portfolio and paper-accounting integration

Make LumiBot authoritative for:

```text
orders
fills
cash
positions
portfolio value
trade lifecycle
paper broker state
```

Retain a thin application safety gateway.

## PR 10 — Remove custom generic ledgers

After parity and safety validation, remove custom:

```text
cash ledger
position ledger
lot ledger
average-cost calculations
generic fill accounting
generic paper portfolio valuation
```

Retain only project-specific audit and approval records.

## PR 11 — Analytics replacement

Use QuantStats and Empyrical-compatible analytics for:

```text
returns
CAGR
Sharpe
Sortino
Calmar
volatility
maximum drawdown
benchmark comparisons
rolling returns
monthly returns
tear sheets
```

Delete duplicate custom formulas.

## PR 12 — Portfolio optimization

Use Riskfolio-Lib only for advisory allocation.

All output must pass the existing hard safety gateway.

## PR 13 — SQLAlchemy foundation

Introduce SQLAlchemy models and transactional boundaries.

Do not attempt to migrate every table in one PR.

## PR 14 — Alembic migrations

Replace the custom migration framework.

Because this is a fork, compatibility with every original operational database is not required unless separately requested.

## PR 15 — APScheduler and Tenacity

Replace generic scheduler and transient retry infrastructure.

Never automatically retry an ambiguous broker or provider transmission.

## PR 16 — Structlog and OpenTelemetry

Replace generic logging and tracing wrappers.

Do not log secrets, prompts, raw account IDs, or unredacted provider responses.

## PR 17 — Final custom-engine removal

Use `REMOVAL_MANIFEST.md` to remove all remaining duplicate generic authorities.

## PR 18 — Final parity and readiness audit

Run full offline validation and confirm no removed implementation remains reachable.

---

# Planning output required now

Before making any edits, return:

## A. Repository architecture map

Show:

```text
current components
custom authorities
external-library integrations already present
entry points
data flow
backtest flow
paper-execution flow
persistence boundaries
```

## B. Authority matrix

Use:

| Responsibility | Current authority | Target library | Adapter retained | Custom code removed |
| -------------- | ----------------- | -------------- | ---------------- | ------------------- |

## C. Dependency risks

Use:

| Library | Purpose | Python requirement | License | Risk | Decision |
| ------- | ------- | ------------------ | ------- | ---- | -------- |

## D. Proposed PR sequence

For every PR include:

```text
title
scope
files likely involved
tests
custom implementation removed
dependency on earlier PRs
expected risk
recommended model
```

Recommended model must be one of:

```text
Opus plan + Sonnet implementation
Sonnet only
Haiku support task only
Opus review
```

## E. First PR execution prompt

At the end, produce a compact implementation prompt for PR 0.

Do not implement PR 0 yet.

Wait for approval after presenting the plan.

---

# Per-PR execution protocol

After PR 0 is approved, each implementation session must follow this protocol.

## Step 1 — Read bounded context

Read only:

```text
docs/library-migration/STATUS.md
the current phase plan
files explicitly listed in the phase plan
related focused tests
```

Do not perform a full repository scan.

## Step 2 — Verify current state

Before editing:

```text
confirm the issue still exists
confirm earlier PRs did not already replace it
identify exact callers
identify exact deletion targets
```

## Step 3 — Present an implementation plan

Return:

```text
files to modify
files to add
files to delete
library APIs to use
semantic differences
focused tests
rollback risks
```

Do not edit until the plan is approved.

## Step 4 — Clear planning context

After approval, preserve the plan in:

```text
docs/library-migration/plans/<phase>.md
```

Then clear the large exploration context before implementation.

## Step 5 — Implement using Sonnet

Rules:

```text
no unrelated refactors
no dual permanent authorities
no custom fallback
no external calls
no credentials
no activation changes
```

## Step 6 — Test incrementally

Run focused tests first.

Use:

```bash
pytest <focused tests> -q --tb=short
```

Run the full credential-free suite once after focused tests pass.

Do not stream passing-test output into the main context.

## Step 7 — Review

Check:

```text
acceptance criteria
duplicate authorities
custom fallback paths
unremoved imports
missing parity tests
unsafe defaults
unrelated changes
```

Use Opus review only for high-risk PRs:

```text
LumiBot backtesting
portfolio accounting
broker execution
SQLAlchemy/Alembic
final removal
```

## Step 8 — Update migration status

Update:

```text
STATUS.md
COMPONENT_MATRIX.md
REMOVAL_MANIFEST.md
DECISIONS.md
```

Record:

```text
completed work
custom code removed
library authority established
tests run
remaining blockers
next PR
exact next-session prompt
```

## Step 9 — Commit and stop

Open one PR.

Do not begin the next phase in the same session.

Do not merge automatically.

---

# Haiku subagent policy

Create at most two reusable Haiku subagents.

## Migration inventory subagent

Responsibilities:

```text
find symbols
find imports
find callers
find tests
identify deletion candidates
```

Restrictions:

```text
read-only
no architecture decisions
no edits
no test execution
bounded directories only
short report
```

## Test-output summarizer

Responsibilities:

```text
run or inspect bounded test output
return failing tests
return shortest useful stack traces
group failures by root cause
```

Restrictions:

```text
no code edits
no architectural recommendations
no full passing-test logs
```

Do not create multiple parallel agents.

---

# Completion criteria

The fork is complete only when:

1. Each generic responsibility has one authoritative library.
2. Custom generic implementations have been deleted.
3. No fallback to the removed engine remains.
4. Parity tests pass or intentional differences are documented and accepted.
5. Safety rules remain enforced outside third-party framework defaults.
6. Offline workflows require no credentials.
7. Paper execution remains disabled and operator initiated.
8. Live trading remains unavailable.
9. Documentation matches current code.
10. `REMOVAL_MANIFEST.md` has no unresolved removal target.
11. CI prevents deleted custom implementations from being reintroduced.

---

# Final verification required for every PR

Report:

```text
branch
commit SHA
model used for planning
model used for implementation
Haiku support tasks used
files changed
files deleted
library authority established
custom authority removed
focused tests
full offline suite
type checks
dependency changes
license review
model calls from repository code
provider calls
broker calls
paper orders
live orders
scheduler status
remaining blockers
next-session prompt
PR URL
```

Explicitly confirm:

```text
real model calls: 0
real provider calls: 0
broker calls: 0
paper orders: 0
live orders: 0
scheduler enabled: no
live trading implemented: no
original repository modified: no
PR merged automatically: no
```
