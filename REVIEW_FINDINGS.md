# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/12-riskfolio-lib-evaluation`
- Reviewed HEAD: `ca7cd534037ab868b7bc2aa3eae5be8f42dc76ba`
- Subject: Record PR 29 STATUS-transient-phase finding as fixed
- Claude commits reviewed: dc4e71b6a8497af26d027a8b446fbb9088cfcce0,b04690d9f661c601e800aa6db08a509627cdf35b,35fcd35e1a246856a9f39b56d80abd9e4764c2a2,36bfd84cf2bda0fd6a1b842baa369e71c4e47a33,897ba7114ea753113ee3ab56ac250fd185da100d,15f7184157b5313243f3e3b96c22e8269fd86005,042aa9841f6e13f41a10a323cf6fea2a79e8b97a,2fc01d2037895c9a50cd5663fbaf932e0cf87994,711876314c030c61c10952a04ad5e490aa22c751,fda7367f9a333c8fe5c3d08dcb2bb052df626018,d7493a445c02ec4e62051bdf95280d951a0ceb32,2c503be01bbb263d1c0767c0265d3501a856b9eb,97bf12298240d70a6989135b80d24c5e836fa961,cb63b8f7618d510d989ccba789f31feb78940202,c351be0e537d4b9a8b09b52c3c53c9a6433c7be7,e2c4811bd7395fdc0a2074e40e547d2c4b8669a7,04e23c915cfef6edcdd3a448d67dcbe20c2fa2d1,751c888fb7bd43968d14d93b5b575e821253629e,336282a7852718114c0202e5d4f2fbfab36b79f4
- Review scope: FULL_PR
- Reviewed base: `611b3dfeb0d485d00461ee2a5c3f15e13c0b153f`
- GitHub PR: #29
- Fix round: 15
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `303128970be01980412467297dc0469ba64c6625`

## Findings

### [P2] Stop pinning the transient PR 12 current-phase heading

Commit: `b04690d9f661c601e800aa6db08a509627cdf35b`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_pr12_evaluation_docs.py:206-208`

Problem: `test_status_current_phase_scopes_no_conflict_claim` permanently requires `STATUS.md` to contain the heading `Current phase: PR 12`. Commit `20b431fc52f721282c78aa2e75ab4ac727964635` removed another test with this exact defect but left this assertion unchanged.

Evidence: The documented migration workflow requires PR 13 to rewrite `STATUS.md` so PR 13 becomes current and PR 12 becomes completed/merged. Replacing the heading with `Current phase: PR 13` makes the exact lookup at line 208 fail, even though PR 12’s enduring compatibility qualifications remain in its completed-work section.

Impact: Normal migration advancement to PR 13 will break the canonical `nox -s tests` and `nox -s ci` suites, forcing an unrelated test repair before the next phase can pass validation.

Required fix: Validate the compatibility wording within `## Completed work (PR 12)` instead of searching for the transient current-phase heading. Also audit the nearby current-phase-oriented assertions and retain only enduring PR 12 facts.

Validation: Update `STATUS.md` in a test fixture to represent PR 13 as current and PR 12 as completed/merged; the PR 12 documentation-consistency tests must still pass. Then run `nox -s tests -- tests/unit/test_pr12_evaluation_docs.py` and `nox -s ci`.

Tests or diagnostics run: Inspected every specified Claude-authored commit’s full diff chronologically and traced intervening fixes through HEAD `ca7cd534037ab868b7bc2aa3eae5be8f42dc76ba`; inspected the final documentation, CI workflow, dependency configuration, regression tests, and migration records. A read-only simulation confirmed the exact PR 12 heading disappears during PR 13 advancement. Focused pytest could not initialize because the sandbox provides no writable temporary directory. `git diff --check` reported only trailing whitespace and a final blank line in `REVIEW_FINDINGS.md`, excluded as non-consequential formatting. No files were modified.

Resolution: Fixed by `303128970be01980412467297dc0469ba64c6625`. Renamed
`test_status_current_phase_scopes_no_conflict_claim` to
`test_status_completed_work_scopes_untested_python_versions` and rescoped
its lookup from the transient `**Current phase: PR 12` heading to the
enduring `## Completed work (PR 12)` section (the same anchor
`test_status_completed_work_section_scopes_no_conflict_claim` already
uses), so it keeps passing once PR 13 rewrites STATUS.md's current phase.
Added
`test_status_untested_python_versions_check_survives_pr13_current_phase_rewrite`,
which simulates that PR 13 rewrite and proves the new anchor still
resolves where the old transient-heading lookup would have raised
`ValueError`.
Validation: 27 focused tests passed
(`tests/unit/test_pr12_evaluation_docs.py`);
`.venv/bin/python -m nox -s ci` passed all five sessions with 3,146 main
tests and 106 skipped, 160 paper tests, clean safety typecheck, and clean
migration smoke.
