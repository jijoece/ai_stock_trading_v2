# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `9578517a701754a82f7148a4d44be53d43171adc`
- Subject: Add intraday earnings-gap backtest (research only, steps 1-6)
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0

## Findings

### [P1] Investigate: Backtest fetches raw rather than split-adjusted bars

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/cli.py:2839-2841

Concern: The CLI may violate the strategy’s required split-adjusted data policy by constructing the provider with its default `adjustment="raw"`.

Evidence: The accepted implementation plan specifies “Alpaca 5-minute bars, split-adjusted” in D1. `backtest_intraday_gap_cli` does not pass an adjustment, while `CachedAlpacaIntradayProvider` defaults to raw bars. No CLI option or strategy configuration changes that default. Historical splits can therefore appear as artificial gaps and alter price, liquidity, sizing, stop distance, returns, and eligibility.

Potential impact if confirmed: Research results spanning stock splits can contain false earnings gaps or distorted returns and may incorrectly pass or fail the acceptance gate.

Investigation and conditional remediation: Verify against the current provider behavior and the intended D1 policy first. If confirmed, make split-adjusted data the explicit CLI default or require an explicit adjustment, include it in report/run provenance, and add regression coverage proving the requested adjustment reaches the vendor and cache. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Run a fixture with a split inside the history window and verify that the CLI requests `adjustment=split`, produces split-consistent eligibility and returns, and records the adjustment in reproducibility metadata.

### [P1] Investigate: Chronological split counts rows instead of event dates

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/cli.py:2757-2766

Concern: The 60/40 split may not implement the documented requirement to divide by event date.

Evidence: `_intraday_gap_split` selects its boundary using `len(ordered) * 3 // 5`, where `ordered` contains individual company events. It then prevents a date from straddling the boundary. A date containing many symbols can therefore dominate the row index: for example, 100 events on the first date followed by nine dates with one event each chooses the first date as the boundary, leaving the in-sample set empty rather than the earliest 60% of event dates. Existing coverage uses five events on five distinct dates and cannot detect this case.

Potential impact if confirmed: The tuning and holdout periods can differ materially from the declared chronological 60/40 design, undermining the one-time out-of-sample evaluation and its acceptance verdict.

Investigation and conditional remediation: Verify the concern against the current code and clarify whether D7 means 60% of unique event dates, as stated. If confirmed, calculate the boundary from sorted unique report dates and add regression coverage with multiple symbols on the same dates and strongly uneven per-date event counts. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Test both balanced and highly clustered event dates, asserting disjoint date sets and that the earliest 60% of unique dates are in-sample.

### [P1] Investigate: Run identity omits eligibility and control input bars

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/backtesting/intraday_engine.py:699-710

Concern: The reported `data_hash` and derived run ID may not identify all data consumed by the evaluation.

Evidence: Candidate selection reads prior sessions for average dollar volume, pre-market relative volume, prior close, and benchmark trend. Baselines and the ordinary-day control read additional dates. The engine creates a fresh cache and hashes only report/reaction timelines loaded for eligible candidates; rejected candidates do not load any bars here. The run-ID candidate material records status, reasons, and decision time, but omits EPS inputs, candidate signals, prior close, assumed release, and the historical bars that produced them. Consequently, materially different input datasets can retain the same data hash and run ID. Reports are named by this run ID, increasing the risk of silently replacing a report with different underlying evidence.

Potential impact if confirmed: Results cannot be reliably reproduced or audited from their declared hashes, and different research inputs may be treated as the same run.

Investigation and conditional remediation: Verify the concern against the complete current evaluation path first. If confirmed, compute run provenance from every consumed earnings record and bar—including candidate history, benchmark history, baselines, and control dates—or construct a single audited data manifest shared by all evaluation stages. Add regression tests that mutate each input class and require the data hash/run ID to change. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Mutate one prior-volume bar, one benchmark bar, one ordinary-control bar, and one EPS value independently while preserving candidate status; each mutation should change the run identity.

### [P2] Investigate: Cache identity does not distinguish Alpaca feeds

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/backtesting/intraday_history.py:192-212,229-236

Concern: IEX and SIP cache entries may be silently reused across feeds.

Evidence: The provider accepts `feed="iex"` or `"sip"` and persists the feed in the cache document, but `cache_key` omits the feed and `get_bars` does not validate the document’s `feed`. Parsing then labels bars with the currently configured feed rather than the feed stored in the document. Thus a SIP-configured provider can read an existing IEX cache at the same path and identify its bars as SIP.

Potential impact if confirmed: Volume, liquidity filters, fills, and capacity metrics can be calculated from the wrong market-data feed while provenance claims otherwise.

Investigation and conditional remediation: Verify by fetching one feed and reading the same cache directory through a provider configured for the other feed. If confirmed, include feed in cache identity and validation, preserve the stored feed in bar provenance, and add cross-feed rejection/isolation tests. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Assert that IEX and SIP resolve to distinct cache paths, and that a feed mismatch fails closed rather than relabeling cached bars.

Tests or diagnostics run:

- Inspected the full diff for 9578517a701754a82f7148a4d44be53d43171adc and relevant current code, tests, plan, and runbook.
- `nox -s tests -- tests/unit/test_intraday_history.py tests/unit/test_intraday_engine.py tests/unit/test_backtest_intraday_gap_cli.py tests/unit/test_earnings_gap_intraday_candidates.py tests/unit/test_intraday_metrics.py` — could not start because the read-only environment had no usable temporary directory.
- Direct pytest fallback with bytecode and cache writing disabled — same temporary-directory limitation; no tests executed.
- No files were modified.
