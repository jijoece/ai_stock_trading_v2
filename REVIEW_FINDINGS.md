# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-gap-volume-reconciliation`
- Reviewed HEAD: `27202e708a00f458c735ef3cf7a5f1f75b86a325`
- Subject: Wire Step 1 daily-volume reconciliation into the intraday-gap fetch and report
- Claude commits reviewed: 27202e708a00f458c735ef3cf7a5f1f75b86a325
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: e6523ad4644925081e9810ca5b36ccc92654de38

## Findings

### [P2] Investigate: daily-volume changes can leave the run identity unchanged

Commit: 27202e708a00f458c735ef3cf7a5f1f75b86a325

Location: src/trading_research/cli.py:2967-2972

Concern: Verify whether every cached daily-volume input is intended to affect the run ID. The implementation hashes only the aggregated reconciliation summary, which omits daily volumes for sessions that remain within tolerance.

Evidence: `volume_reconciliation_summary()` records actual volumes only for flagged sessions. For unflagged sessions it records only aggregate counts. Consequently, changing a session’s `daily_volume` from one within-tolerance value to another leaves `volume_check`, the manifest hash, and the run ID unchanged. The added test at tests/unit/test_backtest_intraday_gap_cli.py:476-494 changes a flagged session into an unflagged one, so it does not cover this case. This conflicts with the runbook’s statement that daily volumes are part of the run ID.

Potential impact if confirmed: Two runs consuming different cached vendor data could share an identity and be treated as identical by the out-of-sample run-once ledger, weakening reproducibility and auditability.

Investigation and conditional remediation: First verify against current HEAD that changing an unflagged session’s non-null daily volume to another still-unflagged value preserves the run ID. If confirmed, include a deterministic per-session daily-volume manifest—including `None`—in the hashed inputs and add regression coverage for within-tolerance-to-within-tolerance changes. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Add a test that changes one read session’s daily volume without crossing `volume_tolerance`, confirms trading results remain unchanged, and asserts that the run ID changes.

Tests or diagnostics run:

- `git diff --check 9809529aad4d8f552e8c22db105513ced132ada5 27202e708a00f458c735ef3cf7a5f1f75b86a325` — passed.
- Targeted `nox -s tests -- tests/unit/test_intraday_history.py tests/unit/test_backtest_intraday_gap_cli.py tests/unit/test_intraday_metrics.py` — could not start because the read-only environment had no usable temporary directory.
- CodeRabbit CLI review — not run because its required host-authenticated execution was unavailable under the no-approval sandbox policy.

## Fix Record

- [P2] daily-volume run identity — CONFIRMED and fixed in `e6523ad4644925081e9810ca5b36ccc92654de38`.
  `backtest_intraday_gap_cli` now hashes a sorted per-session `daily_volumes` list (including `None`)
  into the input manifest. Regression: `test_within_tolerance_daily_volume_change_changes_the_run_identity`
  (fails on `27202e7`, passes on the fix).
- Validation: `nox -s ci` passed (tests 4391 passed / 213 skipped; paper_tests 160 passed;
  safety_typecheck and migration_smoke successful).
