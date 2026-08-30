# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `4f261aa81b77117c711bc77c4a4045f58863d444`
- Subject: Record PR 14 fix round 1: indirect Tenacity wrapper guard closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `4a375c374124112a48c564cb8a0500076d7381df`

## Resolution

- Confirmed the P1 concern: `_find_tenacity_import_offenders()` only rejected literal `import tenacity` / `from tenacity import ...` statements, and would pass an indirect project-local wrapper (e.g. `from retry_utils import broker_retry` then `@broker_retry` on `retry_external_paper_order`) or an indirectly-imported `Retrying(...)` context manager used inside the protected functions.
- Added `_find_protected_function_offenders()`, which structurally forbids any decorator and any call named `retry`/`Retrying` (the two exact Tenacity API forms `MASTER_PLAN.md` row 14 names) on the three functions making up the ambiguous-broker-retry path: `retry_external_paper_order`, `_prepare_external_retry_attempt`, `refresh_retry_preview`. This closes the bypass without needing to trace transitive imports.
- Added regression coverage: proof tests for the indirect-decorator bypass and the indirect-`Retrying`-context-manager bypass, plus a control test confirming the guard does not overreach onto decorators on unrelated functions.
- Validation passed: 7 focused tests in `tests/unit/test_external_broker_no_tenacity_import_boundary.py` and `nox -s ci` (`ci`, `tests`: 3190 passed/106 skipped, `paper_tests`: 160 passed, `safety_typecheck`: 0 errors, `migration_smoke`: OK).

## Findings
