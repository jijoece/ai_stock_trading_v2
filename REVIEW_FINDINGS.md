# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `automation/phase-b-claude-runner`
- Reviewed HEAD: `635e5ce606f2c33521927a32d4917dcceaa68f09`
- Subject: Add run-claude: a bounded, gated command to fix review findings and advance phases
- Claude commits reviewed: 635e5ce606f2c33521927a32d4917dcceaa68f09
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: `fc11795770636e318de71aee7360bdbae154c523`

## Findings

### [P1] Preserve the pending-review gate after fixes

Commit: `635e5ce606f2c33521927a32d4917dcceaa68f09`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/scripts/migration_helper.py:1103-1108`

Problem: `_run_fix_session` treats every non-actionable findings state as externally clean and tells the operator the PR is waiting to merge. This includes `FIXES_APPLIED_PENDING_REVIEW`, which explicitly means the fixes have not received their required follow-up review.

Evidence: `parse_review_findings` accepts both `CLEAN/0` and `FIXES_APPLIED_PENDING_REVIEW/0`. Both make `is_actionable` false, and this branch does not inspect `findings.status`. The canonical runbook at `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/AUTOMATION.md:223-228` distinguishes these states and says the latter is awaiting the next external review. No test exercises rerunning `run-claude` with that state.

Impact: After Claude pushes fixes, rerunning the command produces an affirmative “waiting for a human to merge” message even though the revised HEAD has not been reviewed. This can bypass the intended review gate and allow consequential migration or trading-safety regressions to merge unchecked.

Required fix: Branch explicitly on normalized status. Only `CLEAN/0` should report merge readiness. `FIXES_APPLIED_PENDING_REVIEW/0` must report that another external review of the current PR HEAD is required and must not describe the PR as clean.

Validation: Add a regression test with `Review status: FIXES_APPLIED_PENDING_REVIEW`, `Finding count: 0`, and a fix SHA, asserting that Claude is not invoked and the output requires re-review rather than merge.

### [P2] Block a concurrently created PR for the active phase

Commit: `635e5ce606f2c33521927a32d4917dcceaa68f09`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/scripts/migration_helper.py:1154-1167`

Problem: The second GitHub listing before starting a phase blocks only open PRs whose `phase_id` differs from the selected active phase. If another operator or session creates the active phase’s PR between initial discovery and this listing, `other_open` remains empty and this invocation still launches Claude with the stale “No PR exists” prompt.

Evidence: `run_claude` discovers `NEXT_PHASE_READY` at lines 1208-1229, lists PRs again, and passes them here. The filter expressly excludes `pr.phase_id == situation.active_phase_id`; the prompt at line 1166 is still built from the earlier no-PR `Situation`. The existing concurrency test covers only a different-phase PR, not a newly appearing same-phase PR.

Impact: Two sessions can independently implement the same phase, create competing branches or PRs, and split migration state. This defeats the stated one-phase/one-PR boundary and can lead to duplicated or conflicting changes.

Required fix: Before launching a new-phase session, refuse to proceed if the refreshed listing contains any open migration PR. If it is for the selected phase, rebuild discovery or direct the operator to the existing PR; if it is for another phase, retain the human-attention outcome.

Validation: Add a regression test where initial discovery returns `NEXT_PHASE_READY` but the refreshed listing contains an open PR for that same phase, asserting `_run_claude` is never called and the command exits nonzero or requests human attention.

Tests or diagnostics run: Inspected the complete commit diff and relevant current implementation, unit tests, `AUTOMATION.md`, `STATUS.md`, and `MASTER_PLAN.md`. `nox -s tests -- tests/unit/test_migration_helper.py` could not run because `nox` is unavailable. Direct pytest startup also failed because the read-only environment provides no writable temporary directory. No files were modified.
