# Lightweight Migration Continuation Helper

`scripts/migration_helper.py` reports where the library migration stands and
prints a prompt you can paste into a fresh Claude Code session. Two of its
three commands (`status`, `continue-prompt`) are exactly that: read-only,
stateless, and hold no opinion the migration documents do not already record.

The third command, `run-claude`, is the one narrow, explicit exception: it may
invoke the `claude` CLI for real, to fix review findings on the existing PR or
to start the next documented phase. It is subordinate to the same rules as the
prompt the other two commands print -- it does not decide anything they would
not already have told a human to do next.

It is subordinate to [`MASTER_PLAN.md`](MASTER_PLAN.md) and
[`STATUS.md`](STATUS.md), which remain the only roadmap. The helper never
writes to them, and never becomes a second place where migration state lives.

## Why this is not an autonomous orchestrator

An earlier design proposed a full pipeline -- planner, autonomous
implementation, review/fix loop, quota-aware pause and resume, scheduled
recovery, and automatic milestone advancement. It was deliberately abandoned.

`run-claude` does not resurrect that design. There is no scheduler, no
OpenAI/Codex API integration, no persisted state file, and no automatic merge.
It makes **one** Claude attempt per invocation, only when explicitly run, and
only for one of two narrow, already-documented actions:

1. fix the findings recorded in `REVIEW_FINDINGS.md` on the PR that is
   already open for the active phase, or
2. start exactly the next phase `STATUS.md` and `MASTER_PLAN.md` document,
   when no PR exists yet for it.

Two operations still stay with the operator, by choice: **running
`run-claude` when Claude quota is available**, and **merging a PR when it is
clean**. Nothing in this file merges a PR or decides quota is available on its
own initiative -- it is invoked, it makes one attempt, and it stops.

## Workflow

1. Run `python scripts/migration_helper.py status` to see the current
   position.
2. If a PR is open and `REVIEW_FINDINGS.md` records unresolved findings, run
   `python scripts/migration_helper.py run-claude` to fix them on that same
   branch, or start a fresh Claude Code session yourself with
   `continue-prompt`'s output -- either path is supported.
3. CI runs. Codex/ChatGPT (or a human) reviews the PR on GitHub and writes
   `REVIEW_FINDINGS.md`.
4. Repeat step 2 until `REVIEW_FINDINGS.md` is `CLEAN`.
5. A human merges.
6. Run `run-claude` again (or `continue-prompt`) to pick up the next
   documented phase.
7. Repeat.

The GitHub PR is the handoff mechanism between reviewer and Claude.
`REVIEW_FINDINGS.md` is the handoff artifact carrying the reviewer's findings
into a Claude fix session. The helper does not invoke a reviewer, and there is
no OpenAI client anywhere in this repository.

## Usage

```bash
python scripts/migration_helper.py status                # where are we?
python scripts/migration_helper.py continue-prompt        # prompt for a fresh session
python scripts/migration_helper.py run-claude --dry-run   # what run-claude would do
python scripts/migration_helper.py run-claude             # actually invoke Claude once
./scripts/continue-migration.sh                            # status + continue-prompt, in one go
```

Useful flags: `--json` for machine-readable output (`status` only),
`--offline` to read the migration documents without consulting GitHub
(`status`/`continue-prompt` only -- `run-claude` requires GitHub and rejects
`--offline`), `--repo-root` to point at another checkout, `--dry-run` to make
`run-claude` print the proposed Claude command and prompt without invoking
Claude or mutating anything.

Exit codes: `0` reported normally (including "nothing actionable right now"),
`1` an error, or a `run-claude` fix/advancement attempt that failed
verification, `2` the position is ambiguous and needs a human, `3`
(`run-claude` only) Claude quota looks exhausted -- resumable, re-run later.

## What `status` and `continue-prompt` answer

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
therefore selected only when `STATUS.md` explicitly makes it next -- and this
applies equally to `run-claude`, which reuses the exact same discovery logic.

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

These are descriptions of what was found, not workflow states. `status` and
`continue-prompt` cannot act on any of them; `run-claude` only ever acts on
`CURRENT_PR_*` (to look for fixable findings) or `NEXT_PHASE_READY` (to start
the next phase) -- every other row below leaves `run-claude` with nothing to
do, same as the other two commands.

| Situation | Meaning |
|---|---|
| `CURRENT_PR_IN_PROGRESS` | An open draft PR exists for the active phase |
| `CURRENT_PR_CI_FAILING` | CI is failing; the failing check names are listed |
| `CURRENT_PR_CI_PENDING` | CI has not reported a complete result |
| `CURRENT_PR_READY_FOR_REVIEW` | CI is green; the PR is ready for review or merge |
| `CURRENT_PR_MERGED` | The final documented phase is merged |
| `NEXT_PHASE_READY` | No PR exists for the active phase yet |
| `PR_STATE_UNVERIFIED` | `--offline` was used, so PR state was never looked up |
| `HUMAN_ATTENTION_REQUIRED` | The position is ambiguous -- see below |

CI aggregation is pessimistic: anything unrecognised counts as pending, never
as passing, so an unfamiliar check can never be reported as a green CI.

A human is asked to intervene when more than one open PR targets the same
phase; when a phase has both a merged PR and a still-open one, since a
follow-up fix means the phase is not finished and the next phase must not
start; when this phase's only PR was closed without merging; when `STATUS.md`
is more than one phase behind GitHub; or when either the current phase or the
successor it names has no `MASTER_PLAN.md` row. The helper never silently
picks one of two candidate PRs, and `run-claude` never invokes Claude in any
of these cases.

`PR_STATE_UNVERIFIED` is not an anomaly -- it is what `--offline` reports.
Because `STATUS.md`'s wording goes stale the moment a phase merges, the
documents alone cannot say whether a phase is already done, so offline mode
deliberately produces no actionable continuation prompt. "Not looked up" is
never rendered as "no PR exists" -- which is also exactly why `run-claude`
refuses `--offline` outright rather than risk acting on an unverified position.

## GitHub access

The helper shells out to `gh` at least twice per `status`/`continue-prompt`
run, both read-only: a fully paginated listing of pull requests, and a
`gh pr view` for the one PR it reports on. The listing is paginated to
completion on purpose -- a bounded query could drop an older PR and produce a
false "no PR exists for this phase", which would send a fresh session off to
open a duplicate. A `gh` failure is reported as an error, never as an empty
result. `run-claude`'s `NEXT_PHASE_READY` path makes one further listing call
to check for a stray open PR on a different phase before starting anything.

No secret is required beyond whatever `gh` is already authenticated with. No
credential is read, written, or referenced.

## What `run-claude` does

`run-claude` runs the exact same discovery `status` uses, then takes at most
one of two actions:

**An open PR exists for the active phase.** It reads and validates
`REVIEW_FINDINGS.md` (see below). If the file is clean, it prints that the PR
is waiting for a human to merge and stops -- it never touches a clean open PR.
If the file records actionable findings, it builds a bounded prompt (the
active phase, the PR, its branch and SHA, `REVIEW_FINDINGS.md`, the required
validation commands, and the same safety rules `continue-prompt` always
carries), runs one `claude` attempt on the existing branch, and then
independently re-verifies -- from git and the filesystem, never from the
Claude process's own exit code -- that: a new commit was made; the tracked
working tree is clean; `REVIEW_FINDINGS.md` no longer reports any unresolved
finding; the fix commit it names is real and part of this branch's history,
not the stale pre-fix SHA; and the local `HEAD` has actually been pushed to
the PR's branch on `origin`. Any of those failing is reported and the command
exits non-zero -- it never claims success on Claude's word alone, and it never
opens a replacement PR.

**No PR exists yet for the active phase** (`NEXT_PHASE_READY`, exactly as
`status` would report it). This only happens when GitHub confirms the
prerequisite phase merged, or the active phase never had a PR to begin with --
the same reconciliation `status` already performs, including the rule that a
phase is never selected by sorting identifiers (row `8a` is only ever
prepared when `STATUS.md`'s documented edge names it next). Before starting
anything, `run-claude` also checks that no *other* migration PR is currently
open on a different phase; if one is, it stops and asks for a human rather
than risking a second active phase. Otherwise it sends Claude exactly the
prompt `continue-prompt` would have printed, as one attempt, and stops. It
does not itself validate the resulting PR beyond reporting whether the Claude
process exited cleanly -- that PR goes through the same
CI/review/`run-claude`-fix/merge cycle as any other.

`--dry-run` short-circuits either path right before the `claude` invocation:
it prints the exact command and prompt and returns without invoking Claude or
touching git.

## `REVIEW_FINDINGS.md` handling

`REVIEW_FINDINGS.md` is the durable handoff artifact between an external
reviewer (Codex/ChatGPT, or a human) and a Claude fix session. The helper
never writes to it -- only Claude does, as an explicit step in the prompt
`run-claude` gives it -- and never silently erases a finding.

Reading it fails closed: a missing file, a missing `Reviewed HEAD:` /
`Review status:` / `Finding count:` field, an unparseable count, or a
`Review status`/`Finding count` combination that contradicts itself (for
example `CLEAN` with a nonzero count) all stop `run-claude` with an
explanation rather than guessing which side to trust. It also fails closed if
the file is stale -- if `Reviewed HEAD:` does not match the PR's actual
current `HEAD` on GitHub, the recorded review does not cover what is on the
branch now, and `run-claude` refuses to act on it.

Three states are distinguished:

| `Review status:` | `Finding count:` | Meaning |
|---|---|---|
| `CLEAN` | `0` | Nothing to fix; a clean open PR waits for a human to merge |
| anything else | `> 0` | Actionable findings; `run-claude` starts a fix session |
| `FIXES_APPLIED_PENDING_REVIEW` | `0`, with a `Fix commit:` SHA | Claude has fixed and pushed; awaiting the *next* external review, not `run-claude` |

## Safety

The helper's `status` and `continue-prompt` commands stay exactly as
read-only as before, and tests enforce it:

- every `gh` invocation is a read-only subcommand, checked by an AST audit
- every `git` invocation `run-claude` makes is one of `rev-parse`, `status`,
  `ls-remote`, or `merge-base` -- verification only, also AST-audited; commits
  and pushes happen inside Claude's own session, never from this file
- `subprocess` is reachable through exactly one low-level wrapper, shared by
  the `gh`, `git`, and `claude` call sites so each can be audited in one place
- the helper itself writes nothing to disk -- there is no cache and no state
  file (Claude's child session writes to the working tree; this file does not)
- `run-claude` is the only command that can invoke `claude`, and does so
  without `--dangerously-skip-permissions`, in noninteractive mode, one
  attempt per invocation -- never inside `nox -s ci`/`nox -s tests`, and never
  more than once per `run-claude` call
- no trading gate, authorization phrase, or credential name appears in it;
  no broker, market-data provider, or trading application model provider is
  ever called; no trading capability is ever enabled
- Claude quota exhaustion is detected separately (a heuristic scan of
  `claude`'s combined output for known quota/rate-limit wording) from an
  ordinary execution failure, and reported as resumable (exit `3`) rather than
  as an error

No secret beyond `gh`'s and `claude`'s own existing authentication is
required. No credential is read, written, or referenced.

## Validation

```bash
nox -s tests -- tests/unit/test_migration_helper.py
nox -s ci
scripts/check_links.sh
```
