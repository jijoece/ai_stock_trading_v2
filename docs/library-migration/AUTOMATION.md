# Migration Automation

This describes the orchestration layer that drives the library-first migration
between phases. It is **not** a roadmap. It decides nothing about what a phase
contains; it only discovers where the migration currently stands and reports
the next action.

**Implementation status: Automation Phase A (discovery and state
reconciliation) only.** Nothing here calls Claude, calls OpenAI, mutates
GitHub, or touches the trading application. Automation Phases B-F are listed at
the end and are not implemented.

## Precedence — the automation is subordinate

```text
MASTER_PLAN.md
    ↓
STATUS.md
    ↓
DECISIONS.md
    ↓
phase records / ADRs
    ↓
docs/milestones/rebuild/plan.md   (per-PR execution protocol)
    ↓
.agent/config.yaml, .agent/state.json   (this layer)
```

[`MASTER_PLAN.md`](MASTER_PLAN.md) supersedes the literal PR sequence in
[`../milestones/rebuild/plan.md`](../milestones/rebuild/plan.md), which remains
authoritative for *how* a session runs: bounded context reads, a fresh session
per PR, targeted implementation, incremental testing, review, migration-document
updates, one PR per phase, and stopping after the PR. The automation preserves
those rules rather than replacing them with one long-running conversation. The
repository is the memory.

## Architecture

```text
scripts/automation/
    migration_docs.py   read STATUS.md + MASTER_PLAN.md
    github.py           read-only pull-request inspection (`gh` CLI)
    state.py            .agent/state.json load/save, state constants
    config.py           .agent/config.yaml
    reconcile.py        pure state machine: documents + GitHub + cache -> decision
    orchestrator.py     CLI entry point
```

`reconcile.py` performs no I/O, so every branch of the state machine is
exercised in tests without a network, a checkout, or a clock.

## How the current phase is derived

1. `STATUS.md`'s header block declares the current phase and the next phase.
2. `MASTER_PLAN.md`'s table supplies each row's title, scope, dependency, risk,
   and recommended model, **in document order**.
3. GitHub supplies the pull request for each phase, matched by branch name
   (`migration/<row>-<slug>`).

**Phase order is read, never sorted.** `MASTER_PLAN.md` lists row `8a` between
rows 8 and 9, but `STATUS.md` names PR 10 as the phase after PR 9. Any
sort-based or "next row in the table" rule would wrongly select `8a`. Only the
documented `Current phase -> Next phase` edge is trusted; asking for the
successor of any other phase returns nothing rather than a guess. Row 8a stays
tracked in `MASTER_PLAN.md` and is selected only when `STATUS.md` makes it the
active next phase.

## GitHub is authoritative over the cache and over merge claims

A `STATUS.md` sentence like "IMPLEMENTED, NOT MERGED" is written *inside* the
phase's own branch, so it still says "NOT MERGED" after that PR merges. The
reconciler therefore trusts the document's **phase sequence** but not its
**merge status**: if GitHub shows the documented current phase merged, it
advances one step to the documented next phase.

It advances at most one step. Because `STATUS.md` is updated inside each
migration PR, at most one documented phase can be merged-but-unadvanced at a
time. Two in a row means the documents are further behind than the protocol
allows, and the run escalates instead of walking the roadmap on its own
authority.

The same rule covers a stale cache: if `state.json` says `WAITING_FOR_MERGE`
and GitHub says merged, the run reconciles and advances rather than failing.

## State machine

| State | Meaning | Next action |
|---|---|---|
| `DISCOVER` | Cold start, nothing reconciled yet | — |
| `WAITING_FOR_IMPLEMENTATION` | Active phase has no open PR (or a draft) | `implement` |
| `IMPLEMENTING` | A Claude implementation attempt is in flight | — |
| `WAITING_FOR_CI` | CI incomplete, or no checks reported | `wait_for_ci` |
| `FIX_REQUIRED` | CI failing, or findings outstanding on the reviewed head | `fix_ci` / `fix_findings` |
| `WAITING_FOR_REVIEW` | CI passes and this head has not been reviewed | `review` |
| `WAITING_FOR_CLAUDE_QUOTA` | Claude usage exhausted; work paused, not lost | — |
| `READY_TO_MERGE` | CI passes, review clean | `wait_for_human_merge` / `merge` |
| `WAITING_FOR_MERGE` | Merge requested, not yet complete | — |
| `ADVANCE_PHASE` | The active PR merged | `advance_phase` |
| `DONE` | The final `MASTER_PLAN` row merged | — |
| `HUMAN_REQUIRED` | Escalation; see below | `escalate_to_human` |

Phase A computes every one of these. It performs none of them.

CI aggregation is deliberately pessimistic: anything unrecognised in GitHub's
status-check rollup counts as **pending**, never as passing. The known
non-blocking whole-project pyright baseline is simply one more reported check
and is not treated as a new regression.

### `HUMAN_REQUIRED`

Reached when the documents declare no current phase; when the documents are
more than one phase behind GitHub; when a merged phase has no documented
successor; when the active PR was closed without merging; when the configured
review-round maximum is reached; or when an escalation is already open against
the current head commit. An escalation is **sticky** until the head commit
changes, and the process exits non-zero so a scheduled run surfaces it.

## Review deduplication

`state.json` stores `last_reviewed_sha`. A review is requested only when the
PR's head differs from it. An unchanged commit never triggers a second review;
a Claude fix that creates a new commit does. `review.max_rounds` (default 3)
bounds the review/fix loop. Historical human or ChatGPT rounds that predate
this layer are not represented in machine state — automated rounds are counted
from installation.

## Claude quota handling

Quota exhaustion is expected behaviour, not a failure. It pauses the workflow:
the run persists the active phase, branch, PR number, head SHA, last reviewed
SHA, review round, and next required action, enters
`WAITING_FOR_CLAUDE_QUOTA`, and stops cleanly. A scheduled recovery run makes
at most one further Claude attempt (`claude.quota_retry_hours`, default 3); if
quota is still unavailable it exits successfully without calling OpenAI,
regenerating prompts, or duplicating review comments. A resumed run continues
the existing branch and PR — the milestone never restarts. *(The pause is
represented and preserved in Phase A; performing it is Automation Phase C.)*

## Configuration

[`.agent/config.yaml`](../../.agent/config.yaml). The committed default is
`enabled: false`, and `merge.automatic: false`. Merging this infrastructure
must not start the automation; the owner enables it deliberately.

`MASTER_PLAN.md` already assigns a risk and a recommended model per row
(PR 9 "Opus plan + Sonnet", PR 11 "Sonnet", PR 13 "Opus review", …). That
assignment wins; `claude.implementation_model` / `escalation_model` are only
the defaults for a row that names none. One implementation stream at a time:
one active phase, one branch, one PR. Spare quota is never spent
pre-implementing a future milestone.

## Usage

```bash
# Report only. No state written, no external mutation.
python scripts/automation/orchestrator.py --dry-run

# Report and persist the reconciled state cache.
python scripts/automation/orchestrator.py reconcile

# Machine-readable, and without contacting GitHub at all.
python scripts/automation/orchestrator.py status --json --offline
```

Exit codes: `0` normal, `1` configuration/document/GitHub error, `2`
`HUMAN_REQUIRED`.

`resume`, `retry`, `request-review`, `pause`, and `mark-human-required` are
Automation Phase F controls. They are **rejected** today rather than accepted
and silently ignored.

## Secrets

Phase A requires **no secret**. It reads public repository state through an
already-authenticated `gh` CLI, or `GITHUB_TOKEN` when run in GitHub Actions.

`OPENAI_API_KEY` becomes necessary only if Automation Phase D needs a custom
review integration rather than a native Codex GitHub review; whether any
Claude credential is needed depends on the execution mechanism Phase C selects.
Neither is documented as required until the phase that needs it lands.

## Safety boundary

The automation develops software. It must never extend the trading
application's authority. Enforced by test
([`tests/unit/test_automation_orchestrator.py`](../../tests/unit/test_automation_orchestrator.py)):

- no module in `scripts/automation/` imports `trading_research`,
  `paper_runtime`, `backtest_runtime`, `lumibot`, or `alpaca`
- no module and no `.agent/` file names an opt-in trading gate
  (`RUN_PAPER_BROKER_TESTS`, `RUN_EXTERNAL_PAPER_BROKER_TESTS`,
  `RUN_CLAUDE_RESEARCH_TESTS`, …) or a broker/model credential variable
- no AI SDK or HTTP client is importable from Phase A
- exactly one subprocess call exists, and it is a read-only `gh` subcommand

Real model calls, provider calls, broker calls, paper orders, and live orders
from application code all remain at zero. Tests stay credential-free and
offline.

## Validation

```bash
nox -s tests -- tests/unit/test_automation_migration_docs.py \
                tests/unit/test_automation_github.py \
                tests/unit/test_automation_state.py \
                tests/unit/test_automation_reconcile.py \
                tests/unit/test_automation_orchestrator.py
nox -s ci
scripts/check_links.sh
```

Phase A adds no CI job. The existing gates in
[`../../.github/workflows/ci.yml`](../../.github/workflows/ci.yml) —
`main-tests`, `paper-runtime-tests`, `python-3-10-floor`, `indicators-tests`,
`research-tests`, `dependency-extras-smoke`, `backtest-runtime-tests`,
`type-check-safety`, `migration-smoke`, `links` — remain authoritative, and the
new tests run inside `main-tests`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `missing migration document` | `--repo-root` is not a repository checkout | point it at the checkout root |
| `` `gh pr list` could not be executed`` | `gh` absent or unauthenticated | install/authenticate `gh`, or use `--offline` |
| `unknown state` | `state.json` hand-edited or written by a newer version | delete it; a cold start is safe |
| Exit code 2 | `HUMAN_REQUIRED` | read the `Reasons:` block in the report |
| `Next documented phase: not documented in this checkout` | the active phase's PR is open, so its `STATUS.md` update has not merged | expected; it lands with the PR |

## Remaining automation phases

| Phase | Scope | Status |
|---|---|---|
| A | Discovery and state reconciliation | **implemented** |
| B | CI/review controller, GitHub review metadata | not started |
| C | Claude execution, resume, quota detection, scheduled recovery | not started |
| D | OpenAI/Codex PR review, structured findings, review/fix loop | not started |
| E | Milestone advancement, bounded next-phase plan generation | not started |
| F | Hardening: concurrency, cost limits, repeat-finding detection, manual controls | not started |

GitHub Actions workflows (`agent-orchestrator.yml`, `agent-recovery.yml`),
including the single-concurrency group that keeps two runs off one migration
branch, arrive with Phases B-C. Phase A ships no workflow, because a discovery
run that cannot act has nothing to schedule.
