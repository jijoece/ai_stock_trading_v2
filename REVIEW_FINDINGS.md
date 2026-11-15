# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `5c8062a4a18ca2ea579db379cbfd3d10fcb0168c`
- Subject: Record PR 43 round-1 fixes pending re-review
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50,14c77d1881f2dd61bfbe03d713e376e310d1f15a,5c8062a4a18ca2ea579db379cbfd3d10fcb0168c
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 3c2b311c56757ec0e0ba1f4ddcd4f8b9edebaf83

## Findings

### [P1] Investigate: earnings snapshot provenance is absent from report identity

Commit: 4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0

Location: src/trading_research/cli.py:2880-2887

Concern: The input manifest presented as covering complete earnings rows may omit the required `captured_at` field, allowing materially different point-in-time provenance to produce the same report identity.

Evidence: `load_earnings_csv` requires and parses `captured_at`, and the strategy plan describes the estimate as the last pre-report consensus “as captured.” The manifest added by this commit includes symbol, fiscal period, report date, timing, release time, EPS values, and source, but excludes `e.captured_at`. Changing only that timestamp therefore leaves `input_manifest_hash`, `run_id`, and the artifact path unchanged. Existing identity regression coverage mutates `eps_value`, but not `captured_at`.

Potential impact if confirmed: Two snapshots with different collection histories—including a snapshot whose provenance raises a point-in-time or look-ahead concern—can be indistinguishable in the persisted research audit trail. A later run may overwrite the earlier artifact under the same run ID.

Investigation and conditional remediation: Verify the concern against the current CSV provenance requirements and report-identity contract first. If confirmed, include normalized `captured_at` in the earnings manifest, consider persisting the relevant provenance in the report, and add regression coverage demonstrating that changing it changes the manifest, run ID, and artifact filename. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Run otherwise identical reports from CSVs differing only in `captured_at`; verify that their input-manifest hashes and run IDs differ while byte-identical inputs remain deterministic.

### [P2] Investigate: fetch mode may accept an invalid cache solely because its pathname exists

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/cli.py:2857-2866

Concern: The explicit fetch workflow may report an invalid or corrupted cache entry as already cached without validating it or replacing it.

Evidence: Fetch mode calls `Path.exists()` and skips the entry immediately. Cache schema, symbol/date, adjustment/feed, parsing, and `data_hash` are validated only later by `get_bars`. Consequently, a truncated JSON file, stale-schema document, hash mismatch, or otherwise invalid entry causes fetch to report success with `already_cached += 1`, after which the offline backtest still fails. Tests cover skipping a valid cache and rejecting tampered data during reading, but do not cover fetch behavior when the existing file is invalid.

Potential impact if confirmed: The documented cache-population command cannot repair interrupted or corrupted cache writes and can misleadingly report that all requested data is available, blocking reproducible offline runs until the operator manually diagnoses and removes files.

Investigation and conditional remediation: Verify the concern against the current fetch and cache-validation paths first. If confirmed, validate an existing entry through the provider before counting it as cached, then either fail with an explicit repair procedure or safely refetch and atomically replace it. Add regression coverage for malformed, stale-schema, and hash-mismatched existing entries. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Place an invalid document at the expected cache path, run fetch with an injected non-network test client, and verify that the command does not count the entry as successfully cached without either repairing it or returning a clear failure.

Tests or diagnostics run:

- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..5c8062a4a18ca2ea579db379cbfd3d10fcb0168c` — reported one blank line at EOF in `config/strategies.yaml`; not a review finding.
- Targeted direct pytest run for `test_intraday_history.py`, `test_intraday_metrics.py`, and `test_backtest_intraday_gap_cli.py` — 43 passed; 39 setup errors because the read-only environment provided no writable temporary directory.
- No files were modified and no credentialed, broker, scheduler, model, or external-order paths were invoked.
