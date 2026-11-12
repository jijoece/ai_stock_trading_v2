# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/score-alpaca-data`
- Reviewed HEAD: `910aff771cf0bc3d9636ba7c5674410474b04675`
- Subject: Record review fixes applied for PR 41 findings (pending re-review)
- Claude commits reviewed: cbbcb4d8265a0e18f18e9b31c481dfa7d99ca299,13e180af320aecccd60d99d781fa84345abb22da,45977a4d052dc9b15c64f984f03c0351b2c66556,910aff771cf0bc3d9636ba7c5674410474b04675
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: 60ec95b5c88e34ad95a2e28a0a30f17300392eed

## Findings

### [P2] Investigate: FRED fallback scoring bypasses the historical-vintage guard

Commit: `13e180af320aecccd60d99d781fa84345abb22da`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/scripts/score.py:717-724`

Concern: Investigate whether `--yield-spread fred --fallback-previous-session` introduces point-in-time bias by downloading current-vintage FRED data for the historical fallback session.

Evidence: The explicit `--as-of` path rejects FRED when the requested session predates the latest completed session. The fallback path instead changes `as_of_session` to the prior session and reruns `_attempt`, which calls `fetch_fred_yield_spread(as_of_session)` without applying that guard. The repository explicitly acknowledges that this endpoint may contain revisions published after a historical close. The regression test at `tests/unit/test_score_alpaca.py:819-826` currently requires the downloader to be called for that prior session, and the README recommends this exact option combination.

Potential impact if confirmed: The 20%-weighted yield-spread component may use information unavailable at the fallback close, changing the macro pillar and resulting score or trading action while appearing point-in-time safe.

Investigation and conditional remediation: Verify the fallback output against a vintage snapshot available at the fallback close. If confirmed, reject current-vintage FRED whenever fallback selects an older session, or obtain vintage-aware data; allow a verified fixed yield value or omission as already supported. Replace the existing fallback test with coverage proving that current-vintage FRED cannot be applied to the historical session. If the endpoint can be shown to preserve the relevant historical vintage, record that evidence and leave the code unchanged.

Validation performed: Inspected all four specified commits chronologically and traced current HEAD code, tests, documentation, and attribution. `git diff --check` and `bash -n` passed. No tests were run because the workspace is read-only.

## Fix Notes

- [P2] Confirmed against the code: `_score_remote` refuses `--yield-spread fred` for an `--as-of` session older than the latest completed session, but the fallback branch set `as_of_session` to the prior session and reran `_attempt`, which called `fetch_fred_yield_spread(prior)` with no check. The repository's own rule (FRED's public CSV is the current vintage, which is only treated as safe for the latest completed session) therefore applies to the fallback session too. No vintage-aware source is available to show the endpoint keeps the historical vintage. Fixed in `scripts/score.py`: when the fallback would step back and `--yield-spread fred` is set, the run stops with a `ScoreConfigError` before any FRED request for the older session. A fixed `--yield-spread <PCT>` or omitting it still works. The `--fallback-previous-session` help text and the README example and notes are updated.
- Regression (`tests/unit/test_score_alpaca.py`): `test_fred_is_queried_for_the_fallback_session` is replaced by `test_current_vintage_fred_is_never_applied_to_the_fallback_session` (exit 1, no card, FRED never fetched for the prior session). Also added: `test_the_fallback_session_can_still_use_a_fixed_yield_level` and `test_fred_with_fallback_is_unaffected_when_the_latest_session_is_published`. The first test fails against the reviewed `score.py` and passes with the fix.
- Validation: the focused tests passed in the project `.venv` (`tests/unit/test_score_alpaca.py`: 100 passed, 1 skipped). The nox `tests` venv has no TA-Lib, so this module skips there. `nox -s ci` succeeded: `tests` 3971 passed and 208 skipped, `paper_tests` 160 passed, `safety_typecheck` reported 0 errors, and `migration_smoke` passed.
