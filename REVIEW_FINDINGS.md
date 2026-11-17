# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `0b4789ee4009c55081a91e931769a865469c8aa4`
- Subject: Record PR 43 round-6 fixes pending re-review
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50,14c77d1881f2dd61bfbe03d713e376e310d1f15a,5c8062a4a18ca2ea579db379cbfd3d10fcb0168c,3c2b311c56757ec0e0ba1f4ddcd4f8b9edebaf83,3dc8d4d4912e4aa6f2150760183187d277fdc185,a5515c9aef3a6a80f278cf6e8d5ce563e4456233,534bfa1ad58025cc6adab4a334f0639a74c52c47,b0afdd6d0388e161277d9baa435b12c157e78cae,0d053969f9b2315181b20f2c4d0f1f485d2b90bc,99d6457279d3c07f5244ba4f456ada0e3544c408,0b4789ee4009c55081a91e931769a865469c8aa4
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: NEEDS_FIXES
- Highest priority: P1
- Finding count: 1

## Findings

### [P1] Investigate: pre-entry session truncation can silently remove trades

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/backtesting/intraday_engine.py:339-385,740-747

Concern: A regular session truncated before a strategy signal may be accepted as a legitimate `no_signal` result rather than invalidating the backtest.

Evidence: `_regular_plan` examines only the available regular bars and returns `NO_SIGNAL` when no trigger appears. The caller records that as a submission rejection without checking whether the cached session reached the entry or exit horizon. The later truncation fixes only raise `INCOMPLETE_SESSION_DATA` after an entry has filled. Cache validation in `intraday_history.py` verifies schema, identity, parsing, and content hash, but not session completeness. Baselines similarly use the last available regular bar as the close. Consequently, removing all bars after an early cutoff can turn a later valid signal into `no_signal` while still producing an apparently valid report.

Potential impact if confirmed: Incomplete market-data sessions can selectively eliminate potential trades and alter trade count, expectancy, baseline comparisons, capacity, and acceptance-gate results. Because missingness may correlate with volatile sessions or vendor failures, this can materially bias the research sample.

Investigation and conditional remediation: Verify this concern against the current code first using an otherwise identical complete timeline containing a valid afternoon signal and a copy truncated before that signal. If confirmed, require sufficient coverage through each variant’s relevant decision and execution horizon, or explicitly invalidate affected runs when trailing session data is absent; preserve the documented tolerance for isolated missing bars where appropriate. Add regression coverage proving that truncation before a would-be signal cannot become an ordinary `no_signal` observation and that baselines do not treat an early final bar as the session close. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Compare complete, isolated-gap, and trailing-truncation fixtures. The complete session should trade, isolated missing bars should follow the documented sparse-data policy, and trailing truncation should fail closed rather than reduce the sample.

Tests or diagnostics run:

- Inspected all thirteen specified commits chronologically, including their full diffs and the relevant current code, tests, configuration, plan, and runbook.
- `nox -s tests -- …` could not create its virtual environment because the read-only environment exposed no writable temporary directory.
- Direct targeted pytest execution was also blocked before collection for the same temporary-directory restriction.
- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..0b4789ee4009c55081a91e931769a865469c8aa4` reported only a blank line at EOF in `config/strategies.yaml`; this is not a review finding.
- No files were modified. No network, model, broker, scheduler, credentialed test, or order path was invoked.
