# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `01a5038369aaa774a2561167f3d3e2aec49bfe19`
- Subject: Record review fixes applied for ee69c28 (pending re-review)
- Claude commits reviewed: ee69c28fa17c3199e9a0aa70116b5e804d16afba,56c59f91b334af130a7f68a6833dcb84b2b601a2,01a5038369aaa774a2561167f3d3e2aec49bfe19
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: b2c3d6fed49067f8cb50e1ca071152b0444f40e2

## Findings

### [P2] Investigate: price-adjustment changes may bypass signal conflict detection

Commit: 56c59f91b334af130a7f68a6833dcb84b2b601a2

Location: src/trading_research/forward_testing/candidates.py:96-102

Concern: Investigate whether rerunning an evaluation under a different price-adjustment regime can be accepted as identical when the resulting OHLCV rows happen to match.

Evidence: `evaluation_input_hash()` fingerprints only each bar’s date and OHLCV values through `bars_fingerprint()`. The snapshot’s `adjustment` value is not included. `build_signal_record()` excludes `snapshot_hash`, and `StrategySignalRecord` has no separate adjustment field. Consequently, changing from `split` to `raw` or `all` can produce the same `record_hash` when no relevant corporate action changes the returned rows. The persisted row then retains only the first snapshot hash, while the later run is reported as already recorded. Existing tests cover revised bars and wider universes, but not changed adjustment semantics with identical rows.

Potential impact if confirmed: Append-only candidate history may treat evaluations performed under materially different market-data semantics as identical, weakening reproducibility and provenance auditing. Later corporate-action corrections could make it unclear which adjustment policy governed the recorded evaluation.

Investigation and conditional remediation: Verify the concern against the current code first by running the same evaluation twice with identical returned OHLCV rows but different snapshot adjustment values. If it is accepted as already recorded, bind the adjustment regime—along with any other required per-evaluation data semantics—to the input fingerprint or persisted record identity, while retaining wider-universe idempotency, and add regression coverage. If another current invariant rejects or distinctly records the second run, document that evidence and leave the code unchanged.

Validation: Add a case proving that identical OHLCV rows under different adjustment regimes conflict or receive distinct identities according to the intended persistence policy. Retain the existing wider-universe, revised-symbol-bars, and revised-benchmark-bars cases.

Tests or diagnostics run:

- Inspected all three specified commit diffs chronologically and traced the affected candidate persistence, historical-data, paper-risk, lifecycle, and test paths through HEAD `01a5038369aaa774a2561167f3d3e2aec49bfe19`.
- `git diff --check b15a1d382f03db90ee4858e951fff011a6aca29c..01a5038369aaa774a2561167f3d3e2aec49bfe19` — passed.
- Read-only AST parsing of the three modified Python modules — passed.
- No test suite was executed because the environment is read-only and cannot provide pytest/Nox temporary or cache storage.
- No broker, credentialed, network, model, scheduler, order, or write operations were performed.
