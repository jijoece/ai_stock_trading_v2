# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/11-quantstats-analytics-parity`
- Reviewed HEAD: `5b2ea86e808691d5153bb7529763cb57f0b1c345`
- Subject: PR 11: QuantStats/analytics fixture-parity migration
- Claude commits reviewed: 5b2ea86e808691d5153bb7529763cb57f0b1c345
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `9f63d0c53ae7c2619311b42e55443dd43698acf5`

## Findings

### [P2] Preserve empty-input semantics before declaring parity

Commit: `5b2ea86e808691d5153bb7529763cb57f0b1c345`

Location: [analytics_parity.py](./src/trading_research/evaluation/analytics_parity.py) (lines 90, 142) and [test_analytics_parity.py](./tests/unit/test_analytics_parity.py) (line 84)

Problem: `cumulative_return_parity()` and `max_drawdown_parity()` do not preserve the authoritative functions' behavior for empty input when `min_sample_size=0`, despite the commit marking fixture parity.

Evidence: The existing public functions accept `min_sample_size=0`. For empty input, `metrics.cumulative_return([], min_sample_size=0)` returns `OK` with `Decimal("0")`, and `metrics.max_drawdown([], min_sample_size=0)` also returns `OK` with `Decimal("0")`.

Impact: PR 17 could rely on the recorded parity decision and replace the authoritative implementation, changing a valid public parameter combination from deterministic zero to `NaN`. That can contaminate downstream analytics and reports.

Required fix: Either explicitly handle empty returns in both candidate functions so they reproduce the current zero result, or validate and reject non-positive `min_sample_size` consistently in both old and new implementations.

Validation: Add parity tests for both functions using empty evaluations with `min_sample_size=0`, asserting matching status, sample size, and finite zero value. Also test any chosen rejection contract.

Tests or diagnostics run: Inspected the full commit diff and relevant metrics, model, test, dependency, migration, and CI contracts. `git diff --check` passed. Could not execute the canonical Nox suite locally.
