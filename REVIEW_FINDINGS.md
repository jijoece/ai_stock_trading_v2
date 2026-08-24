# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/12-riskfolio-lib-evaluation`
- Reviewed HEAD: `c351be0e537d4b9a8b09b52c3c53c9a6433c7be7`
- Subject: Record PR 29 CI-workflow-scope finding as fixed
- Claude commits reviewed: dc4e71b6a8497af26d027a8b446fbb9088cfcce0,b04690d9f661c601e800aa6db08a509627cdf35b,35fcd35e1a246856a9f39b56d80abd9e4764c2a2,36bfd84cf2bda0fd6a1b842baa369e71c4e47a33,897ba7114ea753113ee3ab56ac250fd185da100d,15f7184157b5313243f3e3b96c22e8269fd86005,042aa9841f6e13f41a10a323cf6fea2a79e8b97a,2fc01d2037895c9a50cd5663fbaf932e0cf87994,711876314c030c61c10952a04ad5e490aa22c751,fda7367f9a333c8fe5c3d08dcb2bb052df626018,d7493a445c02ec4e62051bdf95280d951a0ceb32,2c503be01bbb263d1c0767c0265d3501a856b9eb,97bf12298240d70a6989135b80d24c5e836fa961,cb63b8f7618d510d989ccba789f31feb78940202,c351be0e537d4b9a8b09b52c3c53c9a6433c7be7
- Review scope: FULL_PR
- Reviewed base: `611b3dfeb0d485d00461ee2a5c3f15e13c0b153f`
- GitHub PR: #29
- Fix round: 10
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `e2c4811bd7395fdc0a2074e40e547d2c4b8669a7`

## Findings

### [P2] Limit the compatibility claim to the interpreters actually tested

Commit: `15f7184157b5313243f3e3b96c22e8269fd86005`

Location: `docs/library-migration/pr12/EVALUATION.md:74`

Problem: The evaluation claims Riskfolio-Lib and the adopted VectorBT pin “do not conflict on Python >=3.11,<3.15,” but the evidence covers only Python 3.11.15 and 3.14.5rc1. It does not establish dependency resolution, wheel availability, imports, or functional compatibility on Python 3.12 and 3.13.

Evidence: Lines 62–77 explicitly acknowledge that one interpreter cannot establish compatibility across a range, then make that same range-wide conclusion after testing only its lower endpoint and a 3.14 prerelease. The canonical component matrix, dependency matrix, master plan, decisions, and status records repeat the unsupported interval-wide claim. The regression test merely requires the literal bounds to appear; neither it nor CI tests Riskfolio-Lib on 3.12 or 3.13.

Impact: A future adoption decision may incorrectly treat all supported Python minors as verified, potentially discovering resolver, wheel, import, or runtime incompatibility only during implementation or deployment.

Required fix: Either describe compatibility precisely as verified only on Python 3.11.15 and 3.14.5rc1, or add wheel-only resolution, `pip check`, import, and representative optimization smoke tests on Python 3.12 and 3.13 before retaining the full-range claim. Update every canonical record and make the regression test validate the evidence rather than only the wording.

Validation: Verify all four minors in a clean interpreter matrix, or confirm every canonical document uses interpreter-specific wording and no longer asserts compatibility across the entire interval.

Tests or diagnostics run:

- Inspected every specified commit’s full diff chronologically and traced all later corrections through reviewed HEAD `c351be0e537d4b9a8b09b52c3c53c9a6433c7be7`.
- `git blame` confirmed the bounded range-wide claim was introduced by `15f7184`.
- Searched the evaluation evidence, regression test, and CI workflow for Python 3.12/3.13 validation; none exists.
- Targeted pytest was attempted read-only but could not start because the sandbox provides no writable temporary directory.
- `git diff --check` reported only whitespace issues in `REVIEW_FINDINGS.md`, excluded as non-consequential style.

Resolution: Fixed by `e2c4811bd7395fdc0a2074e40e547d2c4b8669a7`; took the
first `Required fix` option — `EVALUATION.md`, `STATUS.md`,
`DEPENDENCY_MATRIX.md`, `COMPONENT_MATRIX.md`, `MASTER_PLAN.md`, and
`DECISIONS.md` D10 now state the "do not conflict" conclusion as verified
only at Python 3.11.15 and 3.14.5rc1 — the two interpreters actually
installed and tested — and explicitly say Python 3.12 and 3.13 were not
installed or tested and remain unverified, rather than asserting
compatibility across the full declared `>=3.11,<3.15` range.
`tests/unit/test_pr12_evaluation_docs.py` now pins the interpreter-specific
wording (and the explicit 3.12/3.13 untested caveat) in every one of those
six records instead of only the range's literal bounds. Validation: 26
focused tests passed (`tests/unit/test_pr12_evaluation_docs.py`);
`.venv/bin/python -m pytest tests/ -q --tb=short` passed with 3,273 tests
and 57 skipped; `.venv/bin/python -m nox -s ci` passed all five sessions
with 3,145 main tests and 106 skipped, 160 paper tests, clean safety
typecheck, and clean migration smoke.
