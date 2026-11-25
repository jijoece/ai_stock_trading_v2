# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-gap-earnings-csv`
- Reviewed HEAD: `eca6a6462064bffcca45dc489f2090c670d21a8f`
- Subject: Build the intraday-gap earnings CSV from FMP free plan plus SEC 8-K Item 2.02 times
- Claude commits reviewed: eca6a6462064bffcca45dc489f2090c670d21a8f
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: d6f099d76756c84a6a3b3840f5ba232ad2d5783e

## Findings

### [P1] Investigate: cached raw files permanently suppress later earnings and filing updates

Commit: eca6a6462064bffcca45dc489f2090c670d21a8f

Location: src/trading_research/evidence_providers/earnings_export.py:143-147, 182-190

Concern: Existing per-symbol raw files may be treated as permanently complete, causing later builds to omit newly published quarterly earnings or SEC filings.

Evidence: Both fetch paths skip a symbol whenever its raw file exists, without checking its capture time, requested date range, or whether newer reports are available. Because FMP’s endpoint is rolling and SEC submissions accumulate new filings, rerunning the documented workflow after another quarter will reuse stale snapshots. The runbook explicitly says saved symbols are skipped but provides no refresh or invalidation procedure.

Potential impact if confirmed: Backtests built from a reused raw directory can silently omit recent events or combine snapshots captured at materially different dates, producing incomplete and non-reproducible samples.

Investigation and conditional remediation: Verify the concern against current provider behavior and the intended snapshot lifecycle first. If confirmed, add an explicit refresh mechanism or bounded freshness/completeness contract for both providers, document it, and add regression coverage proving a later fetch can incorporate newly available reports and filings. If snapshots are intentionally immutable and already rotated through an external documented process, record that evidence and leave the code unchanged.

Validation: Reproduce with a saved symbol, then make the fake provider expose an additional earnings row or Item 2.02 filing and rerun the fetch; verify whether the new data can enter the build without manually deleting files.

### [P1] Investigate: operator-selectable SEC clock can reinterpret an explicit UTC timestamp

Commit: eca6a6462064bffcca45dc489f2090c670d21a8f

Location: src/trading_research/evidence_providers/earnings_export.py:259-265

Concern: The `eastern` mode may discard the explicit UTC offset encoded by SEC’s trailing `Z` and reinterpret the same wall clock as New York time.

Evidence: `_acceptance_local` first parses `Z` as `+00:00`, but `clock == "eastern"` then replaces that timezone instead of converting it. Elsewhere, the repository’s existing SEC provider treats the same field as UTC (`src/trading_research/evidence_providers/sec_provider.py:39-41`). The new runbook delegates this contract to manual histogram calibration, allowing identical source data to produce release times four or five hours apart.

Potential impact if confirmed: A wrong selection can misclassify premarket, regular-session, and after-close events, drop valid events, or assign fabricated release times that affect candidate eligibility and backtest results.

Investigation and conditional remediation: First verify the SEC field’s authoritative timezone contract and reconcile it with the existing SEC provider. If confirmed as UTC, remove or fail closed on the reinterpretation mode and add regression coverage using known SEC timestamps across daylight-saving boundaries. If SEC truly publishes ambiguous or historically inconsistent values, document concrete evidence and implement deterministic validation rather than operator inference. If the current behavior is correct, preserve it and record the supporting evidence.

Validation: Compare known filing acceptance timestamps against the SEC filing detail page and assert that the exporter and existing SEC provider produce the same instant and New York wall-clock time.

### [P2] Investigate: partial fetch failures can return success with `remaining: 0`

Commit: eca6a6462064bffcca45dc489f2090c670d21a8f

Location: src/trading_research/evidence_providers/earnings_export.py:160-172, 205-230

Concern: Fetch commands may report successful completion and zero remaining work even when requested symbols failed and no raw file was saved.

Evidence: Per-symbol provider failures and missing CIKs are accumulated in `failed`, but normal loop completion returns `remaining=0`. The CLI then exits successfully because the result has no top-level `error`. Only rate-limit and request-cap exits calculate remaining files.

Potential impact if confirmed: Automation or an operator relying on exit status and `remaining` can proceed to build an incomplete earnings universe, silently biasing the sample toward symbols whose fetches succeeded.

Investigation and conditional remediation: Verify the intended meanings of command success and `remaining` against the current runbook first. If confirmed, make partial completion unambiguous—such as counting all unsaved requested symbols as remaining and/or returning a non-success status—and add regression tests for provider errors, malformed responses, and missing CIKs. If `failed` is intentionally the sole retry signal and callers already enforce it, document that evidence and leave the code unchanged.

Validation: Run the existing fake-client failure scenarios and assert that the command cannot be mistaken for complete while requested raw files remain absent.

Tests or diagnostics run: Inspected the full commit diff and relevant earnings loader, strategy timing contract, existing SEC timestamp parser, tests, and runbooks. Attempted `tests/unit/test_earnings_export.py` with pytest and cache disabled; pytest could not start because the read-only environment had no writable temporary directory. No network, credentials, brokers, models, schedulers, or order paths were accessed.
