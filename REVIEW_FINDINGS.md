# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `32dd766ce8f292aebbb5cbab8b304904398b3d50`
- Subject: Record PR 43 review fixes pending re-review
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 14c77d1881f2dd61bfbe03d713e376e310d1f15a

## Findings

### [P1] Investigate: report parameters can collide on one run ID and overwrite artifacts

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/strategies/intraday_metrics.py:648-652

Concern: The persisted report identity may omit report-defining parameters, allowing distinct reports to share a run ID and overwrite each other.

Evidence: `build_report` derives `run_id` only from the engine run ID and input-manifest hash. It does not include `split` or the report `parameters`. Consequently, two requests selecting identical events and bars but using different requested date ranges—or the same evaluation rendered under a different split label—produce different report hashes and contents but the same run ID. `write_report` names both artifacts from that run ID. Existing `test_report_changes_with_inputs` confirms that changing `split` changes `report_hash`, but it does not assert that the run ID or output filename changes.

Potential impact if confirmed: A later run can silently replace a prior JSON/Markdown research artifact while retaining the same identity, leaving an ambiguous or misleading audit trail for parameter freezing and the one-time out-of-sample evaluation.

Investigation and conditional remediation: Verify the concern against the current report-building and persistence path first, including whether report identity is intentionally content-addressed over the complete persisted report or only computational results. If confirmed, incorporate every report-defining parameter—at minimum split, requested start/end, and other persisted parameters—into the run identity, and add regression coverage proving distinct report contents cannot resolve to the same artifact path. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Build otherwise identical reports while independently changing `split`, `start`, and `end`; assert that each differing report receives a distinct run ID and output filename, while byte-identical inputs remain deterministic.

Tests or diagnostics run:

- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..32dd766ce8f292aebbb5cbab8b304904398b3d50` — completed; reported only the existing blank line at the end of `config/strategies.yaml`.
- `nox -s tests -- tests/unit/test_intraday_metrics.py tests/unit/test_backtest_intraday_gap_cli.py tests/unit/test_intraday_engine.py tests/unit/test_earnings_gap_intraday_candidates.py` — could not start because the read-only environment had no writable temporary directory.
- Direct targeted pytest invocation with bytecode and cache writing disabled — also could not start because Python’s capture subsystem required a writable temporary directory.
