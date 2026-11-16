# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `534bfa1ad58025cc6adab4a334f0639a74c52c47`
- Subject: Fix PR 43 round-4 review findings
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50,14c77d1881f2dd61bfbe03d713e376e310d1f15a,5c8062a4a18ca2ea579db379cbfd3d10fcb0168c,3c2b311c56757ec0e0ba1f4ddcd4f8b9edebaf83,3dc8d4d4912e4aa6f2150760183187d277fdc185,a5515c9aef3a6a80f278cf6e8d5ce563e4456233,534bfa1ad58025cc6adab4a334f0639a74c52c47
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: b0afdd6d0388e161277d9baa435b12c157e78cae

## Findings

### [P1] Investigate: truncated intraday data is treated as an executable exit

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/backtesting/intraday_engine.py:631-650

Concern: The simulator may silently convert an incomplete session into a completed trade by selling at the final cached bar, even when that bar occurs well before the required flat time or belongs to incomplete extended-hours data.

Evidence: When the timeline ends with shares remaining, regular positions are closed at `last.close` as `flat_time_close`, while extended-hours positions are closed at `last.close` as `end_of_data`. No check establishes that `last` is the scheduled session-ending bar or that the timeline reached the configured deadline. The repository already provides `missing_regular_slots()` for detecting incomplete regular sessions, but the engine and CLI do not consult it. Existing coverage removes only the expected 15:55 bar while retaining the 15:50 bar; it does not exercise a cache truncated much earlier in the day or an overnight timeline missing reaction-session bars.

Potential impact if confirmed: A partial or interrupted vendor response can manufacture an exit at an arbitrary earlier price, changing P&L, R multiples, drawdown, and the acceptance verdict instead of failing closed. For overnight or post-market variants, the engine can similarly report a completed trade at the last available extended-hours price despite the intended exit window never being observed.

Investigation and conditional remediation: Verify the concern against the current completeness contract and D5’s narrowly intended “session’s last bar close” fallback first, particularly whether that phrase requires a bar ending at the scheduled session close or immediately preceding flat deadline. If confirmed, distinguish a genuinely missing flat-time slot from a truncated timeline and fail or explicitly reject/mark the affected event unless sufficient coverage reaches the required exit horizon. Add regression coverage for a regular session truncated shortly after entry and for an overnight timeline missing the reaction-session exit window. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Run otherwise identical fixtures with complete data, only the flat-time bar missing, and the session truncated several hours early. Verify that the first two retain the documented behavior while the truncated case cannot produce a normal completed trade or contribute to the acceptance gate.

Tests or diagnostics run:

- Inspected all nine specified commits chronologically using their full diffs and relevant surrounding code, tests, configuration, plan, and runbook.
- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..534bfa1ad58025cc6adab4a334f0639a74c52c47` — reported one blank line at EOF in `config/strategies.yaml`; not a review finding.
- Targeted pytest invocation covering the intraday engine, history, metrics, candidate selection, CSV loader, and CLI could not start because the read-only environment had no usable temporary directory.
- No files were modified, and no network, model, broker, scheduler, credentialed test, or order path was invoked.
