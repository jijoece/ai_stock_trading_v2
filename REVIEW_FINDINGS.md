# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `3139a4f9bd95d868021a86c08a37a2ebdd45ed48`
- Subject: Refuse snapshots fetched before the final session closed; report resume-then-rebreach as a terminal halt
- Claude commits reviewed: 2198c1332f4a07886a43092b55cf434f26259dab,d22067d6a98dadaa08f800439a06e94bb986706e,106bcc1e35d9f7785e8fb1d9d3e294d78d509538,04f43e796e3374ab99da70ea12ba55032456f65f,084fc1728571b27c6be3c7b0d28ff2893a27ebcd,74c69405c1c676738fa78a81c1c794ac8e52a6b2,f344d846443bd3913d818bf3d6d5fe4bce7a2376,3139a4f9bd95d868021a86c08a37a2ebdd45ed48
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: efe1ef763d23c5fd4d41c9e4698c74435ddc645c

## Findings

### [P2] Investigate: unhashed metadata may bypass snapshot-completeness enforcement

Commit: 3139a4f9bd95d868021a86c08a37a2ebdd45ed48

Location: src/trading_research/backtesting/alpaca_history.py:235, src/trading_research/backtesting/alpaca_history.py:249-254, src/trading_research/backtesting/alpaca_history.py:335-345

Concern: Verify whether snapshot freshness can be trusted when `fetched_as_of`, the sole input used to decide temporal completeness, is excluded from integrity validation.

Evidence: `read_snapshot()` accepts `fetched_as_of` from the JSON document, while `_content_hash()` deliberately omits it. The new `_require_complete_for()` then compares only this unauthenticated timestamp with the requested `as_of`; it does not confirm that each requested symbol actually contains the latest session that should have been closed. Changing only `fetched_as_of` to a post-close timestamp therefore appears capable of preserving a valid content hash while making a snapshot created before the final close pass the new completeness check.

Potential impact if confirmed: An altered, incorrectly migrated, or otherwise inconsistent snapshot could silently omit final-session bars while the report names the complete requested range. Returns, signals, benchmark comparisons, and drawdown-breaker conclusions could consequently be based on incomplete data without an integrity error.

Investigation and conditional remediation: First verify the concern against current code by creating a pre-close snapshot, changing only its serialized `fetched_as_of` to after the final close without updating its hash, and checking whether `load_or_fetch_snapshot()` accepts it for a post-close `as_of`. If confirmed, bind the completeness evidence to snapshot integrity—such as hashing the timestamp and/or validating expected terminal-session coverage per requested symbol—and add regression coverage. If the concern is disproved or another current-code invariant already authenticates and validates completeness, document that evidence and leave the code unchanged.

Validation: Confirm that changing freshness metadata without updating the integrity value is rejected, and that a snapshot claiming post-close completeness but lacking the applicable terminal-session bars cannot be reused. Preserve successful reuse of genuinely complete snapshots and the existing early-close calendar behavior.

Tests or diagnostics run:

- `git diff --check ce09e01e79c00f93dd9e30264713b6e835da43fc..3139a4f9bd95d868021a86c08a37a2ebdd45ed48` — passed.
- Inspected every specified commit’s full patch chronologically and traced the resulting behavior through the reviewed HEAD.
- Inspected the current engine, snapshot provider, report schema, targeted tests, configuration, and canonical backtesting runbook.
- Targeted pytest command for `test_backtest_risk_breaker_report.py` and `test_alpaca_history.py` could not start because the read-only environment had no usable temporary directory.
- No credentialed, broker, scheduler, model, order, or write operations were performed.
