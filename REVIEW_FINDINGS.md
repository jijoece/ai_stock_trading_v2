# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `2198c1332f4a07886a43092b55cf434f26259dab`
- Subject: Flag drawdown-breaker halts in backtest reports and add --max-drawdown-fraction
- Claude commits reviewed: 2198c1332f4a07886a43092b55cf434f26259dab
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: d22067d6a98dadaa08f800439a06e94bb986706e

## Findings

### [P1] Investigate: end-of-session drawdown may be falsely reported as a trading halt

Commit: 2198c1332f4a07886a43092b55cf434f26259dab

Location: src/trading_research/backtesting/strategy_backtest_runner.py:116-137

Concern: The new report may classify a run as breaker-tripped and state that subsequent results reflect a halt even when the engine never blocked an entry.

Evidence: `risk_breaker_summary()` sets `tripped` whenever any end-of-session `daily_state.drawdown_fraction` reaches the limit. However, the engine evaluates the breaker earlier using opening `pre_entry_equity` (`engine.py:298-310`), before calculating and recording the closing drawdown (`engine.py:382-387`). A position can therefore cross the threshold at the close, recover by the next opening evaluation, and never produce a `DRAWDOWN_LIMIT` rejection. Crossing on the final session is an even simpler false-positive case. The added `test_limit_is_inclusive` validates the summary helper with a synthetic closing state but does not verify that the engine actually halted trading.

Potential impact if confirmed: Backtest reports can incorrectly warn that returns, exposure, and trade counts “reflect the halt, not the strategy.” Researchers may discard or reinterpret valid results, choose split windows unnecessarily, or loosen the breaker based on a halt that did not occur.

Investigation and conditional remediation: Verify the concern against the current engine with a deterministic scenario where closing equity reaches the threshold but the next pre-entry opening equity is above it—or where the threshold is reached only on the final session—and confirm that no `DRAWDOWN_LIMIT` rejection occurs. If confirmed, distinguish “drawdown observed at/over limit” from “entry breaker activated,” and reserve halt language for evidence matching the engine’s actual decision point; add regression coverage using an engine-produced result. If disproved or already fixed, document the engine trace showing when entry blocking occurred and leave the code unchanged.

Validation: Add assertions that `tripped` and halt warnings follow actual breaker evaluations or blocked entries, while closing-threshold-only runs are reported without claiming trading stopped. Also cover recovery above the threshold and a final-session breach.

Tests or diagnostics run:

- `git diff --check ce09e01e79c00f93dd9e30264713b6e835da43fc..2198c1332f4a07886a43092b55cf434f26259dab` — passed.
- Inspected the complete commit diff and current engine/configuration/reporting paths.
- Canonical Nox tests could not run because `nox` was not on `PATH`; `uv run nox` was blocked by the read-only cache.
- Direct `.venv/bin/pytest` was also blocked because the read-only environment provided no writable temporary directory.
