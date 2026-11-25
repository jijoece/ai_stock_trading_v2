# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-gap-earnings-csv`
- Reviewed HEAD: `1123d5d9ed7d8a49fb3f742373826addd0c69b86`
- Subject: Record PR 45 acceptance-clock review resolution pending re-review
- Claude commits reviewed: eca6a6462064bffcca45dc489f2090c670d21a8f,d6f099d76756c84a6a3b3840f5ba232ad2d5783e,a1be9f12090a8f4dddcd327fc76ffae4cabc1598,7f1c3bdac49635abbea85a47cc13ef7730d5a66d,1123d5d9ed7d8a49fb3f742373826addd0c69b86
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 415107a48bcf29c7be83a0862d514e6daa486437

## Findings

### [P1] Investigate: incomplete raw-data universes can produce successful backtest inputs

Commit: eca6a6462064bffcca45dc489f2090c670d21a8f

Location: src/trading_research/evidence_providers/earnings_export.py:373-398; src/trading_research/cli.py:3167-3175

Concern: Verify whether the offline build should succeed when requested symbols lack FMP or SEC raw files. Current behavior can silently turn an incomplete fetch into a valid-looking earnings CSV.

Evidence: `build_earnings_rows()` records missing FMP and SEC files in summary fields but continues processing other symbols. `build_earnings_csv_cli()` returns success and writes the CSV whenever at least one row survives, without checking `symbols_without_fmp_file` or `symbols_without_sec_file`. The test at `tests/unit/test_earnings_export.py:306-314` explicitly preserves this partial-build behavior. This bypasses the later fetch-stage safeguard introduced in d6f099d76756c84a6a3b3840f5ba232ad2d5783e, where missing requested files make `complete` false and the CLI exit nonzero. The runbook says not to build until both fetches are complete, but the build command does not enforce or require an explicit acknowledgement of exclusions.

Potential impact if confirmed: An operator or automation can accidentally produce and freeze a success-status earnings snapshot that excludes requested companies. The resulting backtest universe may be systematically biased toward providers’ successful responses, weakening sample integrity and reproducibility.

Investigation and conditional remediation: First verify the concern against the current code and intended workflow. If confirmed, make missing requested raw files fail the build by default, or require an explicit operator option identifying accepted exclusions, and add regression coverage showing that an incomplete universe cannot silently produce a successful CSV. If partial builds are intentionally permitted and another enforced mechanism proves the exclusions were explicitly accepted, document that evidence and leave the code unchanged.

Validation: Construct a symbols file containing one complete symbol and one symbol missing either its FMP or SEC file. Confirm whether the build currently writes a CSV and exits zero. If remediated, verify the default fails without replacing an existing output, while any explicit partial-build path reports the excluded symbols and records the acknowledgement deterministically.

Tests or diagnostics run:

- `git diff --check e7d8f05fefb0d256022384acac7c6f75de4b3d9c..1123d5d9ed7d8a49fb3f742373826addd0c69b86` — passed.
- `nox -s tests -- tests/unit/test_earnings_export.py` — could not start because the read-only environment had no usable temporary directory.
- Inspected all five requested commit diffs chronologically, the final exporter and CLI, unit tests, strict earnings loader, relevant ADR references, and current runbooks.
- No network, credentials, brokers, models, schedulers, GitHub operations, or order paths were accessed.

Resolution (fix commit 415107a48bcf29c7be83a0862d514e6daa486437): confirmed and fixed. Before the
fix, a build over AAA (complete), BBB (no SEC file) and CCC (no files) wrote the CSV and exited 0.
`build_earnings_csv_cli()` now returns `INCOMPLETE_RAW_DATA` (exit 2) before writing, so an
existing output is left untouched, whenever a requested symbol lacks an FMP or SEC raw file. The
only partial path is `--accept-missing`, which must name exactly the missing symbols. A list that
omits a missing symbol or names one that is not missing is rejected. A successful build records the
sorted, normalized exclusions as `accepted_missing`. Regression tests in
`tests/unit/test_earnings_export.py`: `test_cli_build_refuses_an_incomplete_universe` (six
acknowledgement variants; output and temp files unchanged),
`test_cli_build_records_exactly_accepted_exclusions` (deterministic SHA and recorded exclusions),
and `test_cli_main_accept_missing_flag_end_to_end` (argument parsing and exit codes). The runbook
documents the gate. Validation: `nox -s tests -- tests/unit/test_earnings_export.py` (40 passed),
`nox -s ci` (tests, paper_tests, safety_typecheck, migration_smoke all successful), and
`scripts/check_links.sh` (0 errors).
