Set up an autonomous, quota-aware development orchestration system for the **existing** `ai_stock_trading_v2` repository.

This is NOT a greenfield project and you must NOT recreate the roadmap, migration plan, architecture decisions, or milestone structure.

Start by inspecting the repository's current state and continue from it.

Repository:

```text
https://github.com/jijoece/ai_stock_trading_v2
```

Current active PR when this prompt was prepared:

```text
PR #22
branch: migration/09-lumibot-normalization-contract
MASTER_PLAN phase: PR 9
head at preparation time:
3193b0bcc97ca9b1e9878b6eb036f42fb21bce36
```

Do NOT assume those values are still current. Verify GitHub and the repository before doing anything.

---

# Existing source of truth

The repository already contains the migration control plane.

Use these documents instead of creating a competing roadmap:

```text
docs/library-migration/MASTER_PLAN.md
docs/library-migration/STATUS.md
docs/library-migration/DECISIONS.md
docs/library-migration/COMPONENT_MATRIX.md
docs/library-migration/REMOVAL_MANIFEST.md
docs/library-migration/PRESERVATION_MANIFEST.md
docs/library-migration/DEPENDENCY_MATRIX.md

docs/milestones/rebuild/plan.md
```

Important precedence:

```text
MASTER_PLAN.md
    ↓
STATUS.md
    ↓
DECISIONS.md
    ↓
phase-specific records / ADRs
    ↓
docs/milestones/rebuild/plan.md
```

`MASTER_PLAN.md` explicitly supersedes the literal PR sequence in the older
`docs/milestones/rebuild/plan.md`.

Do NOT execute the old PR sequence where it conflicts with `MASTER_PLAN.md`.

The older plan remains authoritative for universal execution rules such as:

* bounded repository reading
* fresh Claude session for each PR
* targeted implementation
* incremental testing
* review
* migration-document updates
* one PR per phase
* stop after the PR
* no automatic merge unless explicitly enabled

---

# Current migration position

At the time this automation is being introduced:

```text
PR 6  — merged
PR 7  — merged
PR 8  — merged
PR 8a — tracked follow-up, NOT started
PR 9  — implemented in GitHub PR #22, not yet merged
PR 10 — explicitly documented as the next phase after PR 9
```

Do not decide that PR 8a should run before PR 10 merely because its numeric
identifier is `8a`.

`STATUS.md` and `MASTER_PLAN.md` currently identify PR 10 as the next phase.

The automation must always discover the current/next phase from repository
state rather than sorting milestone names numerically.

---

# Current PR 9

PR #22 implements:

```text
MASTER_PLAN.md row 9
"Strengthen LumiBot runtime normalization contract"
```

The implementation already includes multiple review/fix rounds recorded in
`DECISIONS.md` D8 and the PR history.

Do NOT reimplement PR 9.

Do NOT open another implementation PR for PR 9.

When bootstrapping the automation:

1. inspect PR #22
2. inspect its current HEAD
3. inspect CI
4. inspect unresolved review comments
5. inspect `STATUS.md`
6. determine whether PR 9 is:

   * awaiting review
   * awaiting fixes
   * ready to merge
   * already merged

If PR #22 has already merged by the time you execute this prompt, initialize
the orchestrator at the next state defined by `STATUS.md`.

If it remains open, initialize state around the existing PR rather than
creating a duplicate.

---

# Primary objective

Automate the workflow I currently perform manually:

```text
repository state
      ↓
ChatGPT/OpenAI plans/reviews
      ↓
Claude Code implements
      ↓
tests
      ↓
GitHub PR
      ↓
ChatGPT/OpenAI reviews PR
      ↓
Claude fixes findings
      ↓
tests
      ↓
re-review
      ↓
PR clean
      ↓
human merge or configured auto-merge
      ↓
next MASTER_PLAN phase
      ↓
repeat
```

I should no longer need to manually:

* copy implementation prompts into Claude Code
* copy PR URLs into ChatGPT
* copy review findings back into Claude Code
* tell Claude which milestone is next
* restart work after Claude quota resets

The repository and GitHub must carry all durable state.

---

# Critical Claude quota requirement

Claude Code is subscription-backed and may stop because of:

```text
5-hour usage window
weekly usage limit
other Claude usage exhaustion
```

This is expected behavior.

Quota exhaustion must PAUSE the workflow rather than break it.

The automation must preserve enough state that a fresh Claude execution can
continue later without needing the original Claude conversation.

When Claude quota is unavailable:

```text
WAITING_FOR_CLAUDE_QUOTA
```

Persist:

```text
active MASTER_PLAN row
active branch
GitHub PR number
current HEAD SHA
last reviewed SHA
CI state
outstanding review findings
review round
next required action
```

Then stop cleanly.

A scheduled recovery process should periodically attempt unfinished Claude
work again.

Once usage becomes available:

```text
read current repository state
read current PR
read unresolved findings
read current milestone documents
continue existing branch
```

Never restart the milestone from scratch.

---

# Preserve the repository's existing token-efficiency strategy

The existing migration plan already requires:

```text
fresh Claude Code session for every PR
bounded context reads
targeted rg/git grep
no repeated full repository scans
no huge test logs in context
no agent team
at most one narrow support subagent when justified
```

Keep those rules.

Do NOT replace them with a large persistent Claude conversation.

Repository artifacts are the memory.

---

# Do not introduce a second migration state system unnecessarily

Before creating `.agent/STATE.json` or a new milestone hierarchy, evaluate
whether the existing migration files plus GitHub metadata are sufficient.

Prefer extending the existing system.

For example, a small automation state file is acceptable for machine-only
information such as:

```json
{
  "active_phase": "PR_9",
  "github_pr": 22,
  "branch": "migration/09-lumibot-normalization-contract",
  "head_sha": "...",
  "last_reviewed_sha": "...",
  "review_round": 3,
  "state": "WAITING_FOR_REVIEW",
  "next_action": "review"
}
```

But do NOT duplicate the substantive roadmap into:

```text
.agent/milestones/M001.md
.agent/milestones/M002.md
...
```

because the repository already has `MASTER_PLAN.md`, `STATUS.md`,
`DECISIONS.md`, ADRs, and phase records.

The automation layer should reference those documents.

---

# Suggested new automation structure

Keep it small.

Prefer something similar to:

```text
.agent/
    config.yaml
    state.json
    README.md

scripts/
    automation/
        orchestrator.py
        state.py
        github.py
        claude.py
        openai.py
        review.py
        quota.py

.github/workflows/
    agent-orchestrator.yml
    agent-recovery.yml
```

Adjust names and locations to existing repository conventions if needed.

Do not create unnecessary abstractions.

---

# State machine

Use explicit states such as:

```text
DISCOVER
WAITING_FOR_IMPLEMENTATION
IMPLEMENTING
WAITING_FOR_CI
WAITING_FOR_REVIEW
FIX_REQUIRED
WAITING_FOR_CLAUDE_QUOTA
READY_TO_MERGE
WAITING_FOR_MERGE
ADVANCE_PHASE
DONE
HUMAN_REQUIRED
```

The state machine must reconcile itself with real GitHub/repository state.

GitHub is authoritative over cached state.

For example, if state says:

```text
WAITING_FOR_MERGE
```

but GitHub says the PR was already merged:

```text
do not fail
→ reconcile
→ ADVANCE_PHASE
```

---

# Bootstrap behavior

The first execution is special.

Do NOT start by creating a milestone.

Run discovery.

Determine:

```text
current branch
default branch
open migration PRs
PR #22 status
PR #22 HEAD
CI status
review status
MASTER_PLAN current row
STATUS current phase
STATUS next phase
DECISIONS relevant to current phase
```

Then initialize machine state from reality.

Conceptually:

```python
repo = inspect_repository()
migration = read_migration_state()
github = inspect_active_pr()

state = reconcile(repo, migration, github)
```

Do not hard-code PR #22 as permanent state.

It is only the bootstrap reference.

---

# Existing CI is authoritative

The repository already has substantial offline CI.

Do not replace it.

The existing workflow includes gates such as:

```text
main-tests
paper-runtime-tests
python-3-10-floor
indicators-tests
research-tests
dependency-extras-smoke
backtest-runtime-tests
type-check-safety
migration-smoke
links
```

There is also a known non-blocking whole-project pyright baseline.

Do not mistakenly treat the existing non-blocking global typecheck baseline
as a new regression.

Use the CI behavior already encoded in `.github/workflows/ci.yml`.

The standard local migration verification already uses:

```bash
nox -s ci
scripts/check_links.sh
```

plus phase-specific focused tests.

Do not invent a separate generic test standard unless required.

---

# AI review behavior

The OpenAI/ChatGPT side should review the actual PR rather than a copied diff.

Preferred flow:

```text
PR HEAD changes
      ↓
CI passes
      ↓
OpenAI/Codex PR review
      ↓
structured findings
```

Use the best supported OpenAI GitHub/Codex review mechanism available at
implementation time.

If native Codex GitHub review can perform the review directly, prefer it.

If a custom OpenAI API integration is required, implement the minimum
necessary wrapper.

Do not build an elaborate custom reviewer if GitHub/Codex already provides
the required behavior.

---

# Review only new SHAs

Store:

```text
last_reviewed_sha
```

Before requesting another OpenAI review:

```python
if current_head_sha == last_reviewed_sha:
    do_not_review_again()
```

This is critical for token/cost conservation.

A Claude fix that creates a new commit permits another review.

An unchanged commit must never trigger repeated AI reviews.

---

# Review findings

Review should prioritize:

```text
correctness
regressions
acceptance criteria
repository architecture
DECISIONS.md / ADR compliance
safety boundaries
broker ambiguity
point-in-time correctness
accounting correctness
persistence correctness
concurrency
retry semantics
fail-open behavior
unsafe defaults
insufficient regression tests
scope violations
```

Classify findings:

```text
P0
P1
P2
P3
```

P0-P2 are normally actionable.

P3 should not indefinitely block a PR unless explicitly promoted.

---

# Claude fix behavior

When review findings exist, Claude must use the existing PR branch.

It must read:

```text
STATUS.md
MASTER_PLAN current row
relevant DECISIONS.md section
relevant ADR
PR review findings
current diff
focused source/test files
```

It must NOT reread the entire repository.

Its job is:

```text
fix findings
run focused tests
run required full verification
update migration documentation if the decision changed
commit
push to same PR
stop
```

Do not open a replacement PR.

---

# Review-loop protection

Prevent:

```text
OpenAI review
→ Claude fix
→ OpenAI review
→ Claude fix
→ ...
```

from running forever.

Configurable default:

```yaml
review:
  max_rounds: 3
```

Important:

Existing PRs may already have undergone human/ChatGPT review rounds before
the orchestrator is installed.

Do not assume those historical rounds are represented in machine state.

For bootstrap, begin counting automated rounds from installation unless a
reliable marker already exists.

If the same substantive defect persists across multiple automated rounds,
escalate:

```text
HUMAN_REQUIRED
```

Include:

```text
PR
SHA
finding
previous attempted fixes
why the loop was stopped
```

---

# Model allocation

Respect the migration's existing model guidance.

Conceptually:

```yaml
claude:
  implementation_model: sonnet
  escalation_model: opus
```

Use Sonnet for normal implementation/fixes.

Use stronger architectural reasoning only where the existing MASTER_PLAN
specifies it or when escalation is necessary.

Do not spend Opus quota on mechanical fixes.

Also do not introduce unnecessary parallel Claude agents.

One implementation stream at a time.

---

# Current migration model guidance

`MASTER_PLAN.md` already specifies risk and recommended model by PR.

Use that rather than assigning models independently.

Examples include:

```text
PR 9  — High — Opus plan + Sonnet
PR 10 — High — Opus plan + Sonnet
PR 11 — Low-Medium — Sonnet
PR 13 — High decision — Opus review
PR 18 — High — Opus review
```

Read the current MASTER_PLAN rather than relying on these examples if it has
changed.

---

# Next-phase generation

Do NOT ask OpenAI to invent a new milestone from scratch.

When a PR completes, OpenAI planning should convert the existing next
`MASTER_PLAN.md` row into a bounded implementation prompt.

Inputs should include only:

```text
next MASTER_PLAN row
STATUS.md
relevant DECISIONS.md sections
relevant ADRs
dependency rows named by the phase
results/decisions from previous prerequisite PRs
targeted repository evidence
```

Output:

```text
phase objective
exact scope
files likely involved
acceptance criteria
focused tests
required full validation
risks
out-of-scope items
```

The planner may refine the execution plan based on current repository
evidence.

It must NOT silently rewrite the master roadmap.

If repository evidence proves the MASTER_PLAN is wrong or unsafe:

```text
HUMAN_REQUIRED
```

or create a proposed planning change for review.

Do not autonomously rewrite architectural decisions.

---

# Critical next-phase rule

At bootstrap, current documentation says:

```text
PR 9 → PR 10
```

Therefore, after PR #22 merges, the automation should prepare PR 10:

```text
Broker-to-paper_books reconciliation parity tests
```

Do NOT automatically choose row 8a unless `STATUS.md` / `MASTER_PLAN.md`
later makes it the active next phase.

Row 8a must remain tracked.

Do not lose it.

---

# One PR at a time

This repository's migration protocol requires one PR per phase.

Enforce:

```text
one active implementation milestone
one implementation branch
one migration PR
```

Do not automatically start PR 11 while PR 10 remains open.

Do not use spare Claude quota to preimplement future milestones.

---

# Merge behavior

Initially:

```yaml
merge:
  automatic: false
```

A clean PR should enter:

```text
READY_TO_MERGE
```

and remain there until merged.

When it is merged, the scheduled orchestrator detects the merge and advances.

Support optional auto-merge later, but do not enable it by default.

---

# Claude quota recovery

Add scheduled recovery.

Use a conservative interval such as:

```yaml
claude:
  quota_retry_hours: 3
```

When state is:

```text
WAITING_FOR_CLAUDE_QUOTA
```

the recovery run should make at most one Claude attempt.

If still unavailable:

```text
exit successfully
```

Do not:

* spin
* retry every few minutes
* call OpenAI again
* regenerate implementation prompts
* duplicate review comments

This protects both Claude and OpenAI usage.

---

# Other transient failures

Distinguish:

```text
CLAUDE_QUOTA
CLAUDE_EXECUTION_ERROR
OPENAI_RATE_LIMIT
OPENAI_ERROR
GITHUB_ERROR
CI_FAILURE
CONFIGURATION_ERROR
STATE_RECONCILIATION_ERROR
IMPLEMENTATION_BLOCKED
```

Use bounded retries only for true transient service failures.

Never convert quota exhaustion into an implementation failure.

---

# Cost controls

Add configuration similar to:

```yaml
enabled: false

claude:
  implementation_model: sonnet
  escalation_model: opus
  quota_retry_hours: 3
  max_attempts_per_run: 1

review:
  provider: codex
  max_rounds: 3
  max_reviews_per_sha: 1
  blocking_severity: P2

planner:
  provider: openai
  max_calls_per_phase: 2

merge:
  automatic: false
```

Important:

```text
enabled: false
```

must be the initial committed default.

Automation must not begin autonomously just because the infrastructure PR is
merged.

I must explicitly enable it.

---

# Secrets

Do not commit credentials.

Use GitHub Secrets or secure runtime configuration.

Potential secrets may include:

```text
OPENAI_API_KEY
Claude/Anthropic credentials if required by chosen execution mechanism
GitHub credentials if GITHUB_TOKEN is insufficient
```

Determine which are actually necessary before documenting them.

Do not require a secret merely because one could theoretically be used.

Use minimum GitHub permissions.

---

# Safety requirements specific to this trading repository

The automation itself must not weaken any existing trading restriction.

During migration automation:

```text
real model calls from trading application code: 0
real provider calls: 0
real broker calls: 0
paper orders: 0
live orders: 0
```

The automation's calls to Claude/OpenAI for software-development purposes are
separate from the trading application's runtime and must not enable trading
capabilities.

Never:

```text
enable paper submission
enable schedulers
enable live execution
inject trading credentials
contact Alpaca as part of tests
contact market-data providers
change authorization phrases
weaken account fingerprint checks
weaken ambiguous-side-effect handling
```

Tests remain credential-free/offline unless the existing migration explicitly
defines otherwise.

---

# Concurrency

Use GitHub Actions concurrency so two orchestrator runs cannot drive the same
PR simultaneously.

Example concept:

```text
ai-stock-trading-migration-orchestrator
```

Only one Claude mutation session may operate on an active migration branch at
a time.

Duplicate webhook/scheduled events must be safe.

---

# GitHub Actions triggers

Keep event handling minimal.

Likely triggers:

```text
workflow_dispatch
schedule
pull_request / workflow_run where needed
```

Do not subscribe to every GitHub event merely because it exists.

The orchestrator should reconcile state instead of assuming each event is
delivered exactly once.

---

# Manual controls

Support at least:

```text
status
resume
retry
request-review
reconcile
pause
```

Potentially:

```text
mark-human-required
```

Avoid dangerous manual operations unless necessary.

---

# Dry-run first

Before enabling real AI calls:

```bash
python scripts/automation/orchestrator.py --dry-run
```

It should report something like:

```text
Migration phase: PR 9
GitHub PR: #22
Branch: migration/09-lumibot-normalization-contract
HEAD: <current sha>
CI: PASS
Review state: <state>
Next documented phase: PR 10
Proposed action: <action>
External AI call: none (dry-run)
```

No GitHub mutation.
No Claude execution.
No OpenAI call.

---

# Do not mix automation setup into PR 9 behavior

The automation infrastructure is a separate engineering concern.

Do not alter the functional scope of PR #22 merely to install orchestration.

Before creating a branch, determine the safest base given PR #22's current
status.

If PR #22 is still open, account for the dependency cleanly rather than
mixing unrelated automation implementation into the PR 9 diff.

The resulting automation setup should have its own bounded PR.

---

# Implementation strategy

Do NOT implement the entire autonomous system in one giant coding pass.

Use these implementation stages.

## Automation Phase A — discovery and state reconciliation

Implement:

```text
read migration documents
discover active GitHub PR
derive current phase
derive next phase
reconcile cached state
dry-run status
tests
```

No Claude invocation.
No OpenAI invocation.

Prove the system correctly identifies the current PR #22 / PR 9 state.

## Automation Phase B — CI/review controller

Implement:

```text
CI state detection
SHA tracking
review-needed decision
max review rounds
GitHub review metadata handling
```

OpenAI integration may initially be mocked.

## Automation Phase C — Claude execution/resume

Implement:

```text
bounded Claude prompt construction
one Claude call per attempt
existing-branch continuation
quota detection
WAITING_FOR_CLAUDE_QUOTA
scheduled recovery
```

## Automation Phase D — OpenAI/Codex integration

Implement:

```text
PR review
structured findings
SHA deduplication
review/fix/review loop
```

## Automation Phase E — milestone advancement

Implement:

```text
merged PR detection
STATUS reconciliation
next MASTER_PLAN row
bounded implementation-plan generation
next branch/PR workflow
```

## Automation Phase F — hardening

Add:

```text
failure recovery
concurrency
security
cost limits
repeat-finding detection
documentation
manual controls
```

---

# Tests required

Test at minimum:

1. bootstrap with PR #22 open
2. bootstrap with PR #22 already merged
3. STATUS and GitHub disagree
4. current PR exists
5. no duplicate PR created
6. CI pending
7. CI failure
8. CI success
9. unchanged SHA does not trigger review
10. new SHA triggers review
11. OpenAI review contains blocking findings
12. Claude fix creates new SHA
13. review becomes clean
14. Claude quota exhaustion
15. quota recovery
16. process restart while waiting for Claude
17. process restart while waiting for review
18. duplicate GitHub event
19. concurrent orchestrator attempt
20. max review rounds
21. repeated finding
22. PR merge detected
23. next phase derived from STATUS/MASTER_PLAN
24. row 8a is not accidentally selected merely by sorting
25. automation disabled by default
26. dry-run makes no external mutations
27. no trading credentials/capabilities are activated

Mock external AI calls in normal unit tests.

---

# Documentation

Create one focused automation document, preferably:

```text
docs/library-migration/AUTOMATION.md
```

unless repository structure strongly suggests another location.

Document:

```text
architecture
state machine
existing migration-document precedence
bootstrap behavior
Claude quota handling
OpenAI review flow
SHA deduplication
cost controls
GitHub Actions
required secrets
dry-run
enable/disable
pause/resume
manual recovery
HUMAN_REQUIRED
troubleshooting
```

Do not duplicate all of `MASTER_PLAN.md` into this document.

Reference it.

---

# First task

Before writing code:

1. inspect the current branch and GitHub PR #22
2. read `STATUS.md`
3. read the relevant rows of `MASTER_PLAN.md`
4. read `DECISIONS.md` D8 and any automation-relevant decisions
5. read the Per-PR execution protocol in `docs/milestones/rebuild/plan.md`
6. inspect `.github/workflows/ci.yml`
7. inspect existing `.claude/` configuration/skills if present
8. inspect existing GitHub Actions and utility scripts
9. determine whether any existing automation can be reused
10. determine the cleanest branch/base strategy for implementing this system without contaminating PR 9

Then create a concise implementation plan for the automation.

Do NOT create a new migration roadmap.

Do NOT redesign the trading system.

Do NOT implement future trading migration PRs as part of this task.

The plan should specifically state:

```text
current detected migration state
current PR
current SHA
CI state
next documented migration phase
new files
modified files
state representation
GitHub integration
Claude invocation mechanism
quota detection/recovery
OpenAI/Codex review mechanism
testing
security
rollout
```

Critically review the plan for unnecessary complexity.

Then implement **Automation Phase A only**.

Run focused tests.

Run the repository-required validation relevant to the changed files.

Commit the work on a dedicated automation branch and stop.

Do not proceed to Automation Phase B in the same Claude session.

At the end report:

```text
current migration state detected
automation files added/changed
state-machine behavior implemented
dry-run output against the current repo
tests run and results
CI implications
remaining Automation Phases B-F
configuration/secrets that will eventually be required
exact prompt for the next fresh Claude Code session
```

The final line must provide the exact next-session prompt so the automation
setup itself can continue using the repository's existing fresh-session
migration discipline.
