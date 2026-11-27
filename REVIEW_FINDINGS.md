# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-gap-earnings-csv`
- Reviewed HEAD: `54e9b7a39da368cd6c40e3051f6e54f81ead7922`
- Subject: Record PR 45 incomplete-universe review fix pending re-review
- Claude commits reviewed: eca6a6462064bffcca45dc489f2090c670d21a8f,d6f099d76756c84a6a3b3840f5ba232ad2d5783e,a1be9f12090a8f4dddcd327fc76ffae4cabc1598,7f1c3bdac49635abbea85a47cc13ef7730d5a66d,1123d5d9ed7d8a49fb3f742373826addd0c69b86,415107a48bcf29c7be83a0862d514e6daa486437,54e9b7a39da368cd6c40e3051f6e54f81ead7922
- Review scope: INCREMENTAL
- Fix round: 3
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 3b4171d

## Findings

### [P1] Investigate: empty provider datasets can silently exclude requested symbols

Commit: eca6a6462064bffcca45dc489f2090c670d21a8f

Location: src/trading_research/evidence_providers/earnings_export.py:200-209; src/trading_research/cli.py:3171-3194

Concern: Verify whether an empty FMP earnings response should count as complete raw data. The current implementation can produce a successful earnings CSV while silently excluding a requested symbol whose FMP response was an empty list.

Evidence: `fetch_fmp_earnings()` accepts every list response, including `[]`, writes a current raw file, and reports the symbol as saved and complete. During the build, that file contributes zero rows and is not listed in `symbols_without_fmp_file`. The completeness gate added later checks only whether raw files exist, so if another symbol contributes a surviving row, the build writes the CSV, exits successfully, and records no accepted exclusion for the empty symbol. Existing tests cover non-list error responses and absent files but not empty list responses.

Potential impact if confirmed: A provider-side coverage gap, unsupported symbol, or anomalous empty response can silently narrow the requested universe. The frozen backtest input may therefore be biased toward symbols for which FMP returned data while presenting itself as a complete snapshot.

Investigation and conditional remediation: First verify this concern against the current code and intended provider contract, including whether an empty FMP list is an expected, explicitly acceptable outcome. Only if confirmed, classify empty or otherwise coverage-free responses as incomplete, or require an explicit recorded exclusion analogous to `--accept-missing`, and add regression coverage. If empty histories are intentionally accepted and another enforced mechanism records that choice, document the evidence and leave the code unchanged.

Validation: Fetch two requested symbols with the fake client: return a valid earnings row for one and `[]` for the other, and provide SEC files for both. Confirm whether the current fetch reports complete and the build writes a success-status CSV containing only the first symbol. If remediated, verify that the default path cannot silently succeed, an explicitly accepted exclusion is recorded deterministically, and an existing output remains untouched on failure.

Tests or diagnostics run:

- Inspected all seven specified Claude-authored commit diffs chronologically and verified surviving behavior at reviewed HEAD `54e9b7a39da368cd6c40e3051f6e54f81ead7922`.
- Inspected the exporter, CLI, unit tests, strict earnings loader, strategy plan, relevant current runbooks, and prior review resolutions.
- `git diff --check e7d8f05fefb0d256022384acac7c6f75de4b3d9c..54e9b7a39da368cd6c40e3051f6e54f81ead7922` — passed.
- `nox -s tests -- tests/unit/test_earnings_export.py` — could not start because the read-only environment had no usable temporary directory.
- Direct pytest with cache disabled encountered the same unavailable-temporary-directory restriction.
- No files were modified; no network, credentials, brokers, models, schedulers, GitHub operations, or order paths were accessed.

Resolution (fix commit 3b4171d): confirmed and fixed. Before the fix, `fetch_fmp_earnings()` saved
a `[]` response as a current file and reported the symbol complete, and the build skipped it
without listing it anywhere, so a CSV could succeed while silently excluding that symbol. Now
`fetch-fmp` records an empty list as `failed: empty_response`, writes nothing (a previous file is
kept), and leaves the symbol in `remaining`, so the fetch is not complete (exit 3). The build lists
any existing FMP file with no rows (e.g. one saved before this fix) under
`symbols_with_empty_fmp_history`, and the CLI gate treats it like a missing raw file:
`INCOMPLETE_RAW_DATA` (exit 2, existing output untouched) unless `--accept-missing` names it, in
which case it is recorded in `accepted_missing`. Regression tests in
`tests/unit/test_earnings_export.py`: `test_fetch_fmp_empty_history_is_a_failure_not_a_saved_file`
(initial fetch and refresh) and `test_cli_empty_fmp_history_cannot_silently_narrow_the_universe`
(the reviewer's two-symbol scenario end to end: fetch not complete, build refused with output
unchanged, explicit acceptance recorded). The runbook documents both behaviors. The local
`data/raw/fmp` holds no empty files, so the existing snapshot is unaffected. Validation:
`nox -s tests -- tests/unit/test_earnings_export.py` (42 passed), `nox -s ci` (tests, paper_tests,
safety_typecheck, migration_smoke all successful), and `scripts/check_links.sh` (0 errors).
