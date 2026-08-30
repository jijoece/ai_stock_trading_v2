# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `2e561ffbf1e27c75cf92f4de6f758d5e47f3a60f`
- Subject: Record PR 14 fix round 1: indirect Tenacity wrapper guard closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `76b399d1a270388425fb28884962b8a4c852ddf6`

## Resolution

- Confirmed the P1 concern: `_PROTECTED_FUNCTIONS` covered only
  `retry_external_paper_order`, `_prepare_external_retry_attempt`, and
  `refresh_retry_preview`, but both the retry path
  (`retry_external_paper_order`) and the ordinary first-attempt path
  (`_submit_once`) delegate to `_submit_checkpointed_attempt` for the actual
  `runtime.submit_limit_order(...)` call. A project-local, potentially
  Tenacity-backed decorator applied directly to `_submit_checkpointed_attempt`
  contained no literal `tenacity` import and targeted a function outside
  `_PROTECTED_FUNCTIONS`, so it passed both `_find_tenacity_import_offenders`
  and `_find_protected_function_offenders` at reviewed HEAD.
- Added `_submit_checkpointed_attempt` to `_PROTECTED_FUNCTIONS` in
  `tests/unit/test_external_broker_no_tenacity_import_boundary.py`, closing
  the transitive bypass for both the retry path and the ordinary
  first-attempt path with a single, zero-cost addition (the helper currently
  carries no decorator and makes no `retry`/`Retrying` call).
- The P2 concern (GitHub review thread
  `https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3889721104`)
  raised the same underlying gap -- an indirect, non-import-based Tenacity
  wrapper on the ambiguous-broker-retry path. The decorator/attribute and
  `retry`/`Retrying` call-name checks in `_find_protected_function_offenders`
  already close this for the three originally named functions (including
  `@tenacity.retry`-style attribute decorators and dynamically imported
  Tenacity); the only residual gap was the transitive
  `_submit_checkpointed_attempt` helper addressed by the P1 fix above. No
  separate code change was needed for P2 beyond the P1 fix.
- Added regression coverage:
  `test_detector_flags_an_indirect_decorator_on_the_shared_submission_helper`,
  which proves a project-local decorator on `_submit_checkpointed_attempt` is
  now rejected by `_find_protected_function_offenders`.
- Validation passed: 8 focused tests in
  `tests/unit/test_external_broker_no_tenacity_import_boundary.py` and
  `nox -s ci` (`ci`, `tests`: 3191 passed/106 skipped, `paper_tests`: 160
  passed, `safety_typecheck`: 0 errors, `migration_smoke`: OK).

## Findings

None outstanding.
