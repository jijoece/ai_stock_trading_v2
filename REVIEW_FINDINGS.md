# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `ee69c28fa17c3199e9a0aa70116b5e804d16afba`
- Subject: Close the remaining PR 38 review findings on exits, pending exposure and candidate provenance
- Claude commits reviewed: ee69c28fa17c3199e9a0aa70116b5e804d16afba
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: 56c59f91b334af130a7f68a6833dcb84b2b601a2

## Findings

### [P2] Investigate: revised market data can bypass append-only conflict detection

Commit: ee69c28fa17c3199e9a0aa70116b5e804d16afba

Location: src/trading_research/forward_testing/candidates.py:86-91

Concern: Investigate whether removing `snapshot_hash` from `record_hash` allows a rerun using genuinely revised market data to be accepted as an identical evaluation.

Evidence: `snapshot_hash` is the only persisted fingerprint of the complete market-data input. The commit excludes it unconditionally, while `insert_strategy_signal` determines equality solely from `record_hash`. Therefore, two snapshots with different underlying bars but coincidentally identical derived signal and screening fields produce the same `record_hash`; the second run is reported as already recorded, and its differing provenance is neither retained nor rejected. The added regression test covers a wider symbol universe, but not changed bars for an existing symbol. No per-symbol or per-evaluation source-data hash was found that distinguishes universe expansion from source-data revision.

Potential impact if confirmed: Historical candidate records may silently conceal corrected or drifting price inputs, weakening reproducibility, point-in-time auditing, and detection of data-provider revisions.

Investigation and conditional remediation: Verify this concern against the current code first by rerunning one evaluation with unchanged derived fields but a snapshot whose bars for that symbol differ. If the run is accepted as identical, preserve universe-expansion idempotency while incorporating a stable per-evaluation input fingerprint—such as the evaluated symbol’s bars and required benchmark inputs—into conflict detection, and add regression coverage for both wider-universe and revised-data cases. If another persisted invariant already detects the revision, document that evidence and leave the code unchanged.

Validation: Confirm that widening the universe with unchanged inputs remains idempotent, while changing an evaluated symbol’s or required benchmark’s input bars either produces a distinct evaluation record under the intended identity policy or fails closed with a provenance conflict.

Tests or diagnostics run:

- `git diff ... --check` — passed.
- Inspected the full commit diff, candidate persistence schema/repository, snapshot hashing implementation, relevant paper-risk and lifecycle paths, current plan, ADRs, and runbooks.
- `nox` was unavailable directly.
- `uv run nox -s tests -- tests/unit/test_strategy_candidates.py tests/unit/test_strategy_paper_cycle.py` could not start because the read-only environment prevented uv cache initialization; no tests executed.
