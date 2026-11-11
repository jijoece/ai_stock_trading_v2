# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `0b570bf65a68738db7c48631068de82651bd1ac4`
- Subject: Add automated-live-trading handoff scratchpad and record Phase 1 progress
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 0e6f286d3911366bead442cb36be07607dc68282
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: approved decisions omit their strategy-control audit record

Commit: 0b570bf65a68738db7c48631068de82651bd1ac4

Location: src/trading_research/paper_books/strategy_bridge.py:448-465

Concern: Approved or resized risk decisions may remain untraceable to the strategy controls that governed them.

Evidence: This commit adds `save_decision_controls` for rejected decisions at lines 405–414, but the transaction persisting approved decisions and their order intents at lines 448–465 never calls it. The new table explicitly promises one row per accepted, resized, or rejected decision, while repository-wide search shows no other approved-path writer. Existing tests also contain no assertions for these audit records.

Potential impact if confirmed: The decisions that actually create exposure cannot establish which mode, activation stage, allowlist, capital budget, risk budget, and validity window authorized them. This weakens reproducibility, incident investigation, and the audit evidence required by the proposed live-trading migration.

Investigation and conditional remediation: Verify the concern against the current code first by creating one approved and one rejected strategy decision, then query `paper_book_decision_strategy_controls` for both risk-decision IDs. Only if the approved row is absent should the fixer persist the controls inside the same transaction as the approved decision, intent, reservation, and strategy link, and add regression coverage for approved, resized, rejected, rollback, and retry paths. If the concern is disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Confirm every persisted strategy risk decision has exactly one immutable controls record, and that failure to record it rolls back the associated approval and order intent.

Tests or diagnostics run:

- Read-only inspection of both full commit diffs and relevant code, tests, configuration, ADR, implementation plan, requirements matrix, schema, and current persistence paths.
- Repository-wide search for all readers and writers of `paper_book_decision_strategy_controls`.
- `git diff --check 8c53e26035f5e1228861f9798ff8104e2b322a0d..65fc53c5b87d3af37ef7e67672cf299361b7b8a7` — one trailing blank line in the ADR; excluded as non-consequential.
- `git diff --check 65fc53c5b87d3af37ef7e67672cf299361b7b8a7..0b570bf65a68738db7c48631068de82651bd1ac4` — passed.
- Test execution was not attempted because the filesystem is read-only and the worktree contains unrelated local modifications, including overlapping test and implementation files.
