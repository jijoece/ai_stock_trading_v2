# Code Review Findings

## Review Metadata

- Repository: `/private/tmp/claude-501/-Users-jijopaul-workspace-ai-stock-trading-v2/41187e8a-db2f-4f1b-a2be-f8645f8a453f/scratchpad/pr42`
- Branch: `chore/yield-spread-httpx-and-notes`
- Reviewed HEAD: `3a530d278245e47dc47c248a8cdb4470522a9c69`
- Subject: Address PR 42 re-review: point-in-time overnight baseline, post-submission fills, shared timeline
- Claude commits reviewed: 3a530d278245e47dc47c248a8cdb4470522a9c69
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `0c8f5c8550aba60de53543153db6e6ac6d4b45d8`

## Findings

### [P2] Investigate: overnight-baseline fixture does not vary the forbidden pre-market inputs

Commit: 3a530d278245e47dc47c248a8cdb4470522a9c69

Location: docs/intraday-earnings-gap-plan.md:370-378

Concern: The newly specified fixture may not detect whether the 16:30 baseline incorrectly uses next-morning pre-market filters.

Evidence: The plan requires bars to remain identical “through the baseline’s exit” and varies only “later pre-market bars.” Because the baseline exits at the next regular open, all next-morning pre-market bars occur before that exit and therefore remain identical. An implementation could filter the 16:30 candidate using `premarket_gap`, `premarket_hold`, or `premarket_rel_volume` and still pass this fixture. No current test checks the new baseline requirement.

Potential impact if confirmed: The baseline candidate set could contain look-ahead bias, producing invalid comparisons and potentially allowing the strategy to pass its acceptance gate against an understated or overstated baseline.

Investigation and conditional remediation: Verify against the current plan and eventual Step 5 implementation whether the fixture mutates next-morning pre-market bars while holding only information available through 16:30 constant. If confirmed, revise the fixture requirement and add regression coverage that varies each forbidden next-morning input before the exit while asserting candidate selection and entry remain unchanged; exit returns may legitimately differ. If the concern is disproved or already covered elsewhere, document that evidence and leave the code unchanged.

Validation: Confirm the regression fails when the baseline is intentionally selected using the 09:25 candidate filter and passes when selection uses only the `post_report_overnight` candidate set.

Tests or diagnostics run:

- Inspected the full commit diff and relevant plan/test context.
- `nox -s tests -- tests/unit/test_intraday_earnings_gap_plan_docs.py -q` could not create its virtual environment because no usable temporary directory was available.
- Direct pytest fallback was unavailable because `python` was not installed on `PATH`.
- Repository search found no existing test covering the new 16:30 baseline candidate-set requirement.
