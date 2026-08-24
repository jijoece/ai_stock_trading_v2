# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/12-riskfolio-lib-evaluation`
- Reviewed HEAD: `897ba7114ea753113ee3ab56ac250fd185da100d`
- Subject: Record PR 29 fix round 2 findings as fixed
- Claude commits reviewed: dc4e71b6a8497af26d027a8b446fbb9088cfcce0,b04690d9f661c601e800aa6db08a509627cdf35b,35fcd35e1a246856a9f39b56d80abd9e4764c2a2,36bfd84cf2bda0fd6a1b842baa369e71c4e47a33,897ba7114ea753113ee3ab56ac250fd185da100d
- Review scope: FULL_PR
- Reviewed base: `611b3dfeb0d485d00461ee2a5c3f15e13c0b153f`
- GitHub PR: #29
- Fix round: 3
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `15f7184157b5313243f3e3b96c22e8269fd86005`

## Findings

### [P2] Bound the documented compatible Python range

Commit: `dc4e71b6a8497af26d027a8b446fbb9088cfcce0`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr12/EVALUATION.md:63-73`

Problem: The evaluation still concludes that Riskfolio-Lib and the adopted VectorBT pin are compatible on “Python >=3.11,” even though VectorBT 1.1.0 explicitly requires Python `<3.15`.

Evidence: Line 63 records VectorBT’s `Requires-Python: >=3.11,<3.15`, but lines 72-73 broaden the conclusion to every Python version `>=3.11`. The same unbounded wording remains in `COMPONENT_MATRIX.md:41`, `DEPENDENCY_MATRIX.md:52,193`, `MASTER_PLAN.md:31`, `DECISIONS.md:1200,1232`, and `STATUS.md:12,2713`. The added regression tests require only the substring `>=3.11`, so they preserve rather than detect the missing upper bound.

Impact: A future adoption following these canonical records can incorrectly declare or install the extra on Python 3.15+, where the adopted `vectorbt==1.1.0` cannot resolve. This is particularly likely because the root project itself has no Python upper bound.

Required fix: Qualify every compatibility and adoption statement as `Python >=3.11,<3.15`, including the requirement for either an appropriately bounded extra or a future VectorBT upgrade before supporting Python 3.15+. Update the regression tests to require both bounds in every canonical record.

Validation: Run `nox -s tests -- tests/unit/test_pr12_evaluation_docs.py`, then `nox -s ci`.

Tests or diagnostics run: Inspected every requested Claude-authored commit’s full diff chronologically and the relevant HEAD documentation, dependency declarations, scratch evidence, tests, and project instructions. `git diff --check 611b3dfeb0d485d00461ee2a5c3f15e13c0b153f..897ba7114ea753113ee3ab56ac250fd185da100d` reported only whitespace issues in `REVIEW_FINDINGS.md`, which are non-consequential and not findings. Focused pytest could not initialize because the read-only sandbox provides no writable temporary directory. No files were modified.

Resolution: Fixed in `15f7184157b5313243f3e3b96c22e8269fd86005`. Every canonical conclusion cited above — `EVALUATION.md` Section 2 ("Hard dependency on VectorBT", the "do not conflict" confirmation, and the Section 5 recommendation), `COMPONENT_MATRIX.md`'s "Portfolio optimization" row, `DEPENDENCY_MATRIX.md`'s Riskfolio-Lib row and its Section 4 rejected/deferred summary row, `MASTER_PLAN.md` row 12, `DECISIONS.md` D10's dependency-weight paragraph and ruling, and `STATUS.md`'s current-phase summary and "Completed work (PR 12)" section — now states the range as `Python >=3.11,<3.15` and adds that Python 3.15+ would require a future VectorBT upgrade, since VectorBT 1.1.0's own `Requires-Python` ceiling is `<3.15`. `test_pr12_evaluation_docs.py`'s existing scoped-window assertions were extended to require `<3.15` (not just `>=3.11`) in every one of those locations, so a regression that drops the upper bound fails the suite.

Tests or diagnostics run: `.venv/bin/python -m pytest tests/unit/test_pr12_evaluation_docs.py -q` (20 passed); `.venv/bin/python -m nox -s ci` (all five sessions passed: `tests` 3139 passed/106 skipped, `paper_tests` 160 passed, `safety_typecheck` 0 errors, `migration_smoke` OK); `scripts/check_links.sh` (189 checked, 187 OK, 0 errors, 2 excluded).

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
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/29#discussion_r3839089593) is current, unresolved, and not outdated.

Impact: Merging would knowingly carry unresolved review feedback into main.

Required fix: Verify and address the review comment in code and add the requested regression coverage.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

Resolution: No further code change required. Confirmed against current `HEAD` (through fix commit `15f7184157b5313243f3e3b96c22e8269fd86005`) that this substantive finding remains fixed exactly as established in the prior fix round: `EVALUATION.md`'s "Python-floor caveat" section, `DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`, `COMPONENT_MATRIX.md`, `DECISIONS.md` D10, and `STATUS.md` all state that the adopted `vectorbt>=1.1.0,<1.2` range cannot resolve on this repository's `>=3.10` project-wide floor without also raising it to `>=3.11`, and `test_pr12_evaluation_docs.py` (`test_evaluation_states_the_3_10_floor_caveat` and the row/section-scoped `3.10` assertions) pins this into every one of those records. This finding recurred in this round because the cited GitHub review thread had not been marked resolved on GitHub itself, not because the underlying code regressed; the thread should now be marked resolved.

Tests or diagnostics run: Read `EVALUATION.md`, `DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`, `COMPONENT_MATRIX.md`, `DECISIONS.md`, and `STATUS.md` at current `HEAD` and confirmed each still carries the required `>=3.10`-floor qualification after this round's `<3.15`-bounding edits; ran `test_pr12_evaluation_docs.py`'s existing tests for this qualification (all passed as part of the full suite recorded above).
