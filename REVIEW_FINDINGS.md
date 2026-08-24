# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/12-riskfolio-lib-evaluation`
- Reviewed HEAD: `cf47e05869fd04cccb25f55ddf1f5070451953bc`
- Subject: Record PR 12 review finding as fixed
- Claude commits reviewed: dc4e71b6a8497af26d027a8b446fbb9088cfcce0
- Review scope: FULL_PR
- Reviewed base: `611b3dfeb0d485d00461ee2a5c3f15e13c0b153f`
- GitHub PR: #29
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `b04690d9f661c601e800aa6db08a509627cdf35b`

## Findings

### [P2] Qualify compatibility across every canonical migration record

Commit: `dc4e71b6a8497af26d027a8b446fbb9088cfcce0`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/DEPENDENCY_MATRIX.md:52`, `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/DEPENDENCY_MATRIX.md:193`, `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/MASTER_PLAN.md:31`

Problem: The later fix qualifies Riskfolio-Lib/VectorBT compatibility only in `pr12/EVALUATION.md`. Canonical planning and decision documents still call the pairing “conflict-free” without stating that it cannot resolve on the repository’s Python 3.10 floor.

Evidence: `pyproject.toml` declares Python `>=3.10`, while VectorBT 1.1.0 requires `>=3.11`. The dependency matrix, master plan, component matrix line 41, decision record lines 1195–1200 and 1229, and status lines 10–12 and 2700–2704 retain the original unconditional conclusion. The new regression test checks only `EVALUATION.md`, allowing these contradictions to persist.

Impact: A future migration can rely on the canonical matrices or master plan and incorrectly treat Riskfolio-Lib as installable across the supported project range, producing an unresolvable Python 3.10 dependency set.

Required fix: Qualify every compatibility conclusion with the Python ≥3.11 constraint and explicitly record that adoption requires a suitably restricted extra or a project-wide floor increase. Keep the evaluation, matrices, master plan, D10 decision, and status synchronized.

Validation: Extend `test_pr12_evaluation_docs.py` to assert the qualification in each canonical record, then run the focused test and `nox -s ci`.

Tests or diagnostics run: Inspected the complete introducing diff and all subsequent changes through reviewed HEAD; inspected relevant dependency declarations, migration records, and regression test. `git diff --check 611b3dfeb0d485d00461ee2a5c3f15e13c0b153f..cf47e05869fd04cccb25f55ddf1f5070451953bc` passed. Focused pytest could not start because the read-only sandbox provided no writable temporary directory.

Resolution: Fixed in `b04690d9f661c601e800aa6db08a509627cdf35b`. `DEPENDENCY_MATRIX.md` (Riskfolio-Lib row and the Section 4 rejected/deferred summary row), `MASTER_PLAN.md` row 12, `COMPONENT_MATRIX.md`'s "Portfolio optimization" row, `DECISIONS.md` D10 (dependency-weight finding and the ruling), and `STATUS.md` (current-phase summary and the "Completed work (PR 12)" section) now all qualify the no-conflict conclusion to Python >=3.11 and state it cannot resolve on this repository's `>=3.10` project-wide floor without also raising it. Regression coverage pins the qualification into each of the six canonical locations.

Tests or diagnostics run: `.venv/bin/python -m pytest tests/unit/test_pr12_evaluation_docs.py -q` (13 passed); `.venv/bin/python -m nox -s ci` (all five sessions passed: tests 3132 passed/106 skipped, paper tests 160 passed, safety typecheck 0 errors, migration smoke passed).

### [P2] Account for the repository's Python 3.10 floor

Commit: `dc4e71b6a8497af26d027a8b446fbb9088cfcce0`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr12/EVALUATION.md:55`

Problem: An active GitHub review thread remains unresolved:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Account for the repository's Python 3.10 floor**
> 
> The compatibility conclusion is only established on Python 3.14. On the repository's declared Python 3.10 minimum, the adopted `vectorbt>=1.1.0,<1.2` range cannot resolve because VectorBT 1.1 requires Python >=3.11 (as the `research`-extra comment and `DEPENDENCY_MATRIX.md` already document). Therefore a future installation combining Riskfolio-Lib with the adopted VectorBT constraint would still require either a Python-floor increase or an explicitly narrower optional extra; record that limitation instead of describing the pairing as unconditionally conflict-free.
> 
> AGENTS.md reference: [AGENTS.md:L68-L68](https://github.com/jijoece/ai_stock_trading_v2/blob/dc4e71b6a8497af26d027a8b446fbb9088cfcce0/AGENTS.md#L68-L68)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/29#discussion_r3839089593) is current, unresolved, and not outdated.

Impact: Merging would knowingly carry unresolved review feedback into main.

Required fix: Verify and address the review comment in code and add the requested regression coverage.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

Resolution: Fixed in `faa4a9bcb7aa3fef15230a1909c1c5be8908e842` (EVALUATION.md) and `b04690d9f661c601e800aa6db08a509627cdf35b` (all remaining canonical records, per the preceding finding). The compatibility conclusion is now consistently scoped to Python >=3.11 across `EVALUATION.md`, `DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`, `COMPONENT_MATRIX.md`, `DECISIONS.md` D10, and `STATUS.md`, and each explicitly records that the adopted `vectorbt>=1.1.0,<1.2` range cannot resolve on this repository's declared `>=3.10` project-wide floor without also raising it.

Tests or diagnostics run: `.venv/bin/python -m pytest tests/unit/test_pr12_evaluation_docs.py -q` (13 passed); `.venv/bin/python -m nox -s ci` (all five sessions passed: tests 3132 passed/106 skipped, paper tests 160 passed, safety typecheck 0 errors, migration smoke passed).

