# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `c98f9e4192a096b2488e8cde279696ccf8a2ea33`
- Subject: Record review fixes applied for 3139a4f (pending re-review)
- Claude commits reviewed: 2198c1332f4a07886a43092b55cf434f26259dab,d22067d6a98dadaa08f800439a06e94bb986706e,106bcc1e35d9f7785e8fb1d9d3e294d78d509538,04f43e796e3374ab99da70ea12ba55032456f65f,084fc1728571b27c6be3c7b0d28ff2893a27ebcd,74c69405c1c676738fa78a81c1c794ac8e52a6b2,f344d846443bd3913d818bf3d6d5fe4bce7a2376,3139a4f9bd95d868021a86c08a37a2ebdd45ed48,efe1ef763d23c5fd4d41c9e4698c74435ddc645c,c98f9e4192a096b2488e8cde279696ccf8a2ea33
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: 16715fdf21b8c377bdf1349429228f324e0f25bb

## Findings

### [P2] Investigate: broader snapshots may be rejected for shorter requested ranges

Commit: efe1ef763d23c5fd4d41c9e4698c74435ddc645c

Location: src/trading_research/backtesting/alpaca_history.py:370-377

Concern: Verify whether a valid snapshot extending beyond the requested end date is incorrectly rejected as missing the requested terminal-session bar.

Evidence: The coverage check at lines 401-405 explicitly accepts `snapshot.end > end`. However, `_require_complete_for()` derives the terminal session from the shorter requested `end`, then `_require_terminal_session()` compares it with the last bar of the complete, untrimmed snapshot. For example, a valid snapshot ending 2025-11-28 is rejected for a request ending 2025-11-26 because its last bar is 2025-11-28 rather than 2025-11-26. A read-only reproduction produced: `AAPL: history ends 2025-11-28, not on the last closed session 2025-11-26; the final session's bar is missing`. Existing tests cover exact-end reuse but not reuse for a covered subrange.

Potential impact if confirmed: Researchers cannot reuse a valid broader snapshot for shorter backtest windows, causing otherwise valid offline and reproducible runs to fail. Because snapshots are deliberately never overwritten, recovery may require managing additional snapshot files or refetching market data unnecessarily.

Investigation and conditional remediation: First verify the concern against the current code using a valid snapshot whose end and final bar are later than the requested end. If confirmed, validate that each requested symbol contains the requested terminal session—such as by checking for that date within the bars restricted through `end`—rather than requiring it to be the snapshot’s overall last bar, and add regression coverage. If the concern is disproved or already fixed through another current-code invariant, document that evidence and leave the code unchanged.

Validation: Add a case where a complete snapshot ending after the requested range is successfully reused and its requested terminal-session bar is present. Retain failure coverage for snapshots genuinely missing that bar, including exact-end, early-close, and per-symbol cases.

Tests or diagnostics run:

- Inspected all ten specified commit patches chronologically and traced their changes through reviewed HEAD `c98f9e4192a096b2488e8cde279696ccf8a2ea33`.
- `git diff --check ce09e01e79c00f93dd9e30264713b6e835da43fc..c98f9e4192a096b2488e8cde279696ccf8a2ea33` — passed.
- Read-only Python reproduction of broader-snapshot/shorter-range validation — reproduced the erroneous `BacktestError`.
- `.venv/bin/pytest -q tests/unit/test_alpaca_history.py tests/unit/test_backtest_risk_breaker_report.py` — could not start because the read-only environment had no usable temporary directory.
- No broker, credentialed, scheduler, model, order, network, or write operations were performed.
