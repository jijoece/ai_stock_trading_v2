# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/score-alpaca-data`
- Reviewed HEAD: `13e180af320aecccd60d99d781fa84345abb22da`
- Subject: Update score.py, add yield_spread helper, refresh docs
- Claude commits reviewed: cbbcb4d8265a0e18f18e9b31c481dfa7d99ca299,13e180af320aecccd60d99d781fa84345abb22da
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 45977a4d052dc9b15c64f984f03c0351b2c66556

## Findings

### [P1] Investigate: Optional `.env` keys can prevent the dashboard from starting

Commit: cbbcb4d8265a0e18f18e9b31c481dfa7d99ca299

Location: scripts/run_dashboard.sh:28-36

Concern: Investigate whether a `.env` file that omits any allowlisted optional variable causes `run_dashboard.sh` to exit before starting Streamlit.

Evidence: The script enables both `set -e` and `pipefail`. For each unset variable, line 34 runs `grep` inside an assignment. When the key is absent, `grep` returns 1; because of `pipefail`, the pipeline and assignment return 1, triggering `set -e`. A read-only reproduction using `/dev/null` exited with status 1 before reaching the following command. The documented `.env` example does not include every variable iterated by this loop, and no shell regression test covers partial `.env` files.

Potential impact if confirmed: Normal documented dashboard configurations can fail to start merely because an optional scoring, Robinhood, or endpoint variable is omitted.

Investigation and conditional remediation: Verify the concern against the current script first using a disposable partial `.env` containing only `AI_STOCK_TRADING_DB_PATH`. If confirmed, make absence of each optional key an expected branch—while preserving the key allowlist and environment-precedence behavior—and add regression coverage for minimal, partial, and complete `.env` files. If disproved or already fixed, document the shell behavior demonstrating that and leave the code unchanged.

Validation: Run `scripts/run_dashboard.sh` under a stubbed Python/Streamlit environment with a minimal `.env`; verify it reaches the startup command, while malformed or missing required database configuration still fails closed.

### [P2] Investigate: Historical FRED scoring may use data unavailable at the scored close

Commit: 13e180af320aecccd60d99d781fa84345abb22da

Location: scripts/yield_spread.py:56-91; scripts/score.py:598-607

Concern: Investigate whether `--as-of ... --macro-score auto --yield-spread fred` introduces point-in-time bias by treating an observation’s date as proof that its current value was available at that session’s close.

Evidence: The parser excludes only rows whose observation date is after `last_session`. It records no retrieval vintage, release timestamp, or `available_at`, and the ordinary FRED graph CSV request retrieves current series contents. Consequently, a historical run can consume later revisions, and a same-date observation can be accepted without establishing that it had been published by the scored close. The resulting series supplies a 20%-weighted macro component. Existing tests verify observation-date filtering but not vintage or publication-time availability.

Potential impact if confirmed: Historical or fallback scorecards can incorporate information unavailable at decision time, changing the macro pillar and potentially the resulting trading action while being reported as look-ahead-safe.

Investigation and conditional remediation: Verify the concern against current FRED publication timing and revision behavior first, including a historical session and a run immediately after market close. If confirmed, use a point-in-time/vintage-aware source or constrain observations using verified availability timestamps, persist the vintage/retrieval provenance, and add regression coverage for revised and same-day-not-yet-available observations. If disproved or already fixed, document evidence that the endpoint’s returned values and publication timing are point-in-time-safe and leave the code unchanged.

Validation: Compare the scorer’s historical input with a vintage snapshot known to have existed at that session’s close, and verify that observations published or revised later are rejected.

Tests or diagnostics run: `bash -n scripts/run_dashboard.sh` passed. A minimal `set -euo pipefail` reproduction of the `.env` lookup exited 1 when the optional key was absent. `git diff --check 4be8c139498dc6b88eddab98d4cf5024fc062ed3..13e180af320aecccd60d99d781fa84345abb22da` passed. `nox -s tests -- tests/unit/test_robinhood_portfolio.py tests/unit/test_score_alpaca.py tests/unit/test_yield_spread.py tests/dashboard/test_robinhood_portfolio_page.py` was attempted, but no tests executed because Nox could not find a usable writable temporary directory. Static full-diff inspection, current-HEAD tracing, targeted test/configuration/runbook review, and blame attribution were completed.

## Fix Notes

- [P1] Confirmed: a disposable repo copy with a `.env` containing only `AI_STOCK_TRADING_DB_PATH` exited 1 with no message before the startup command. Fixed in `scripts/run_dashboard.sh`: grep status 1 (key absent) is an expected `continue`; any other non-zero status (for example an unreadable `.env`) fails closed. Allowlist and environment precedence are unchanged. Regression: `tests/unit/test_run_dashboard_env.py` (minimal, partial, complete `.env`; precedence; unrelated secrets never exported; missing DB path, missing DB file and unreadable `.env` fail closed). It fails 6 of 7 against the reviewed script and passes 7 of 7 against the fix.
- [P2] Confirmed: the session's own T10Y2Y value is published after its close (Treasury publishes the par yield curve after the close; FRED posts it the next business day), and the public CSV is always the current vintage. Fixed: `parse_fred_csv` uses only observations dated strictly before the scored session. `score.py` refuses `--yield-spread fred` for an `--as-of` session older than the latest completed session. `macro.yield_spread_source` now records `vintage`, `retrieved_at` and the availability rule. Regression tests are in `tests/unit/test_yield_spread.py` and `tests/unit/test_score_alpaca.py`.
- Validation: the focused tests passed (140 passed, 1 skipped). Under `nox -s ci`, `safety_typecheck` and `migration_smoke` passed. `tests` had 9 SQLite `disk I/O error` failures and `paper_tests` failed during install, both because the disk was full (`[Errno 28] No space left on device`, about 470 MiB free). The 9 `tests/unit/test_transaction_discipline.py` tests pass on their own in the nox venv. `paper_tests` did not run.
