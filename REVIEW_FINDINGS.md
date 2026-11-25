# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-gap-earnings-csv`
- Reviewed HEAD: `a1be9f12090a8f4dddcd327fc76ffae4cabc1598`
- Subject: Record PR 45 review fixes pending re-review
- Claude commits reviewed: eca6a6462064bffcca45dc489f2090c670d21a8f,d6f099d76756c84a6a3b3840f5ba232ad2d5783e,a1be9f12090a8f4dddcd327fc76ffae4cabc1598
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 7f1c3bdac49635abbea85a47cc13ef7730d5a66d

## Findings

### [P1] Investigate: SEC acceptance times may be shifted by treating Eastern timestamps as UTC

Commit: d6f099d76756c84a6a3b3840f5ba232ad2d5783e

Location: src/trading_research/evidence_providers/earnings_export.py:298-313

Concern: Verify whether `acceptanceDateTime` from the SEC submissions feed represents Eastern time despite its trailing `Z`. The current implementation unconditionally interprets it as UTC and converts it to New York time.

Evidence: `_acceptance_local()` delegates to `_parse_accepted_at()`, which parses `Z` as UTC. The fix commit made this interpretation mandatory and removed the operator calibration path. However, the SEC’s timestamp guidance defines EDGAR “Acceptance Time” as Eastern Standard Time, while the submissions API documentation does not document a UTC conversion contract. The newly added tests only compare the exporter with the repository’s existing parser, so both can agree while sharing the same incorrect assumption. SEC timestamp guidance (`https://www.sec.gov/about/webmaster-frequently-asked-questions`), SEC submissions API documentation (`https://www.sec.gov/search-filings/edgar-application-programming-interfaces`).

Potential impact if confirmed: Acceptance times can shift by four or five hours. After-close releases may be classified as regular-session events and dropped, while some morning filings may fail the EDGAR-hours check. This can materially bias the earnings sample and alter backtest eligibility and results.

Investigation and conditional remediation: First verify the concern against the current code and authoritative, known filing examples by comparing submissions-feed values with the filing’s EDGAR acceptance time. If confirmed, parse the field according to the verified SEC clock contract, update the shared SEC parser consistently, and add regression coverage across standard- and daylight-saving periods. If disproved or already fixed, document the timestamp evidence and leave the code unchanged.

Validation: Select known premarket and after-close Item 2.02 filings across EST and EDT dates. Assert that the exporter reproduces the EDGAR-displayed acceptance wall time, assigns the correct `am`/`pm` classification, and does not incorrectly drop the events as regular-session or outside filing hours.

Tests or diagnostics run:

- `git diff --check e7d8f05fefb0d256022384acac7c6f75de4b3d9c..a1be9f12090a8f4dddcd327fc76ffae4cabc1598` — passed.
- `nox -s tests -- tests/unit/test_earnings_export.py` — could not start because the read-only environment had no usable temporary directory.
- CodeRabbit CLI review — not run because its required host-authenticated execution was unavailable under the no-approval sandbox policy.
- Inspected all three requested commit diffs chronologically, the current exporter and CLI, unit tests, strict earnings loader, shared SEC parser, relevant ADR references, and current runbooks. No credentials, brokers, models, schedulers, GitHub operations, or order paths were accessed.

Resolution (fix commit 7f1c3bdac49635abbea85a47cc13ef7730d5a66d): disproved. The parser is unchanged.
Each submissions-feed `acceptanceDateTime` was compared with the "Accepted" Eastern time on the
filing's EDGAR index page, both read from sec.gov on 2026-11-24. AAPL 0000320193-26-000005
`2026-01-29T21:30:33.000Z` showed 16:30:33 (EST). AAPL 0000320193-26-000018
`2026-07-30T20:30:28.000Z` showed 16:30:28 (EDT). JPM 0001628280-26-001902
`2026-01-13T11:41:09.000Z` showed 06:41:09 (EST). JPM 0001628280-26-048078
`2026-07-14T10:30:38.000Z` showed 06:30:38 (EDT). The `Z` hour moves with daylight saving
while the Eastern time stays fixed, so the field is genuine UTC. Apple's Item 2.02 history back
to 2015 shows the same 20:30Z/21:30Z pattern. Regression tests in
`tests/unit/test_earnings_export.py` (`test_acceptance_reproduces_edgar_displayed_eastern_time`,
`test_reading_the_z_as_eastern_would_contradict_edgar`) pin these filings. The runbook records
the evidence. The SEC reference links above are kept as code spans because sec.gov returns 403 to the
repository link checker.
