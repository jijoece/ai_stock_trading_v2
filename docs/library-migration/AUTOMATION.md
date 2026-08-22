# Lightweight Migration Continuation Helper

`scripts/migration_helper.py` reports where the library migration stands and
prints a prompt you can paste into a fresh Claude Code session. That is all it
does. It is read-only, stateless, and holds no opinion the migration documents
do not already record.

It is subordinate to [`MASTER_PLAN.md`](MASTER_PLAN.md) and
[`STATUS.md`](STATUS.md), which remain the only roadmap. The helper never
writes to them, and never becomes a second place where migration state lives.

## Why this is not an autonomous orchestrator

An earlier design proposed a full pipeline — planner, autonomous
implementation, review/fix loop, quota-aware pause and resume, scheduled
recovery, and automatic milestone advancement. It was deliberately abandoned.

The remaining migration is bounded, and GitHub plus the existing migration
documents already provide durable state. Building provider-specific quota,
persistence, review-loop, scheduling, and advancement infrastructure would cost
more engineering effort than the remaining manual coordination it would
eliminate.

Two operations stay with the operator, by choice: **starting Claude when quota
is available**, and **merging a PR when it is clean**.

## Workflow

1. Run the helper to see the current position.
2. Start a fresh Claude Code session with the generated prompt.
3. Claude implements or fixes exactly one PR, and pushes it.
4. CI runs.
5. Codex/ChatGPT reviews the PR on GitHub.
6. Run the helper again; a fresh Claude session reads the findings and fixes them.
7. A human merges.
8. Repeat.

The GitHub PR is the handoff mechanism between reviewer and Claude. The helper
does not invoke a reviewer, and there is no OpenAI client in this repository.

## Usage

```bash
python scripts/migration_helper.py status           # where are we?
python scripts/migration_helper.py continue-prompt  # prompt for a fresh session
./scripts/continue-migration.sh                     # both, in one go
```

Useful flags: `--json` for machine-readable output, `--offline` to read the
migration documents without consulting GitHub, `--repo-root` to point at
another checkout.

Exit codes: `0` reported normally, `1` the position could not be determined,
`2` the position is ambiguous and needs a human.

## What it answers

| Question | Source |
|---|---|
| What phase are we on? | `STATUS.md`, reconciled against GitHub |
| Is there an existing PR, and on what branch? | GitHub |
| What is its CI state, and which checks failed? | GitHub |
| Is the current phase already merged? | GitHub |
| What does `STATUS.md` say comes next? | `STATUS.md` |
| What should a fresh Claude session do? | the generated prompt |

## Three rules that matter

**Phase order is read, never sorted.** `MASTER_PLAN.md` lists row `8a` between
rows `8` and `9`, but the phase after PR 9 is PR 10. Only the documented
`Current phase:` → `Next phase:` edge in `STATUS.md` is trusted; the successor
of any other phase is reported as unknown rather than guessed. Row `8a` is
therefore selected only when `STATUS.md` explicitly makes it next.

**Branch names are the discovery key.** A phase's PR is found by its branch,
so the generated prompt requires the recognised `migration/<NN>-` prefix. A
branch outside that convention produces a PR this helper cannot see, and an
invisible PR is one the next run would offer to duplicate.

**GitHub is authoritative for merge status.** `STATUS.md` describes the current
phase as "NOT MERGED" because that sentence is written inside that phase's own
branch, and it stays stale until the next phase's PR rewrites it. When GitHub
shows the documented current phase merged, the helper advances one step to the
documented next phase and says so in its reasons. It advances **at most one
step**: two merged phases in a row is a real inconsistency for a human, not
something to resolve by walking the plan.

## Reported situations

These are descriptions of what was found, not workflow states. The helper
cannot act on any of them.

| Situation | Meaning |
|---|---|
| `CURRENT_PR_IN_PROGRESS` | An open draft PR exists for the active phase |
| `CURRENT_PR_CI_FAILING` | CI is failing; the failing check names are listed |
| `CURRENT_PR_CI_PENDING` | CI has not reported a complete result |
| `CURRENT_PR_READY_FOR_REVIEW` | CI is green; the PR is ready for review or merge |
| `CURRENT_PR_MERGED` | The final documented phase is merged |
| `NEXT_PHASE_READY` | No PR exists for the active phase yet |
| `PR_STATE_UNVERIFIED` | `--offline` was used, so PR state was never looked up |
| `HUMAN_ATTENTION_REQUIRED` | The position is ambiguous — see below |

CI aggregation is pessimistic: anything unrecognised counts as pending, never
as passing, so an unfamiliar check can never be reported as a green CI.

A human is asked to intervene when more than one open PR targets the same
phase; when a phase has both a merged PR and a still-open one, since a
follow-up fix means the phase is not finished and the next phase must not
start; when this phase's only PR was closed without merging; when `STATUS.md`
is more than one phase behind GitHub; or when either the current phase or the
successor it names has no `MASTER_PLAN.md` row. The helper never silently
picks one of two candidate PRs.

`PR_STATE_UNVERIFIED` is not an anomaly — it is what `--offline` reports.
Because `STATUS.md`'s wording goes stale the moment a phase merges, the
documents alone cannot say whether a phase is already done, so offline mode
deliberately produces no actionable continuation prompt. "Not looked up" is
never rendered as "no PR exists".

## GitHub access

The helper shells out to `gh` twice per run, both read-only: a fully paginated
listing of pull requests, and a `gh pr view` for the one PR it reports on. The
listing is paginated to completion on purpose — a bounded query could drop an
older PR and produce a false "no PR exists for this phase", which would send a
fresh session off to open a duplicate. A `gh` failure is reported as an error,
never as an empty result.

No secret is required beyond whatever `gh` is already authenticated with. No
credential is read, written, or referenced.

## Safety

The helper is outside the trading system entirely, and tests enforce it:

- it imports no trading distribution, model provider SDK, or HTTP client
- every `gh` invocation is a read-only subcommand, checked by an AST audit
- `subprocess` is reachable through exactly one audited wrapper
- it writes nothing to disk — there is no cache and no state file
- no trading gate, authorization phrase, or credential name appears in it

## Validation

```bash
nox -s tests -- tests/unit/test_migration_helper.py
nox -s ci
scripts/check_links.sh
```
