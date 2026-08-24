# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/13-sqlalchemy-alembic-feasibility`
- Reviewed HEAD: `4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e`
- Subject: PR 13: SQLAlchemy/Alembic feasibility and ADR — defer, not adopted
- Claude commits reviewed: 4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e
- Review scope: FULL_PR
- Reviewed base: `641f5daee8d5ec629578f371555556a4a24b849e`
- GitHub PR: #30
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `967fe9eca670129298362c786b1ab2b227359154`

## Findings

### [P2] Prove the required Core-only boundary before closing PR 13

Commit: `4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e`

Location: [EVALUATION.md](/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr13/EVALUATION.md:47)

Problem: The evaluation does not perform the Core-only enforcement test required by `MASTER_PLAN.md` row 13. It instead tests whether ORM operations propagate trigger failures and then withdraws the ORM-masking concern.

Evidence: The plan requires explicit testing that trigger-protected tables are “only ever touched via SQLAlchemy Core statements, never ORM-session flush/unit-of-work.” Cases 2, 4, and 6 deliberately map those tables into ORM sessions and demonstrate trigger behavior, but no metadata separation, mapping prohibition, import/static guard, or runtime enforcement prevents a future repository from using ORM sessions. Lines 109–112 consequently carry Core-only usage forward as a recommendation rather than a proven restriction.

Impact: PR 13 is recorded as complete even though one of its two high-decision acceptance conditions remains unverified. A future adoption could rely on this decision record while having no mechanism that prevents ORM access to safety-critical reserved and append-only tables.

Required fix: Mark question (a) incomplete or add a representative enforcement design and adversarial test proving ORM mappings/session operations cannot target any trigger-protected table while Core access remains available.

Validation: Add a test that attempts to map and flush representative reserved and append-only tables through every permitted SQLAlchemy session construction path and confirms the architectural guard rejects the operation before SQL execution; also verify Core statements still work as intended.

Resolution: Fixed by `967fe9eca670129298362c786b1ab2b227359154`. Added case
7 to `scratch_trigger_orm_vs_core.py`: a `before_flush` guard
(`TriggerProtectedTableORMGuard`) registered once on the ORM `Session`
class rejects a flush against `real_orders` or `paper_book_cash_ledger`
before any SQL is emitted, verified through two independently permitted
session construction paths (`sessionmaker()` and a directly constructed
`Session(bind=...)`) by asserting the guard's own exception type is raised
rather than the trigger's `IntegrityError`, which would mean the guard
fired too late. Re-verified Core statements against the same tables still
succeed with the guard installed. Re-ran the scratch script against the
pinned scratch venv (sqlalchemy 2.0.52) and committed the regenerated
`scratch_trigger_output.txt`. Updated `EVALUATION.md` question (a) and
`DECISIONS.md` D11 to record the boundary as proven, not just recommended.
Validation: `test_trigger_scratch_output_shows_core_only_guard_blocks_all_orm_paths`
and `test_evaluation_proves_core_only_boundary_not_just_recommends_it`
added to `tests/unit/test_pr13_evaluation_docs.py`.

### [P2] Include Alembic dependency edges in the linear-history gate

Commit: `4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e`

Location: [scratch_alembic_linearity.py](/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr13/scratch_alembic_linearity.py:138)

Problem: `linear_only_gate()` considers only `down_revision`. It ignores Alembic’s separate `depends_on` revision dependencies while claiming that an empty result proves the whole revision graph is a single strict chain isomorphic to the current integer migration ledger.

Evidence: Lines 149–163 inspect `rev.down_revision` and parent-child counts exclusively. Neither the scratch reproduction nor its documentation mentions or tests `depends_on`. Alembic revisions can therefore introduce additional graph edges that the proposed gate does not evaluate.

Impact: The decision record overstates that Alembic was proven constrainable using this gate. If adopted later as described, CI could accept a dependency graph that is not equivalent to the repository’s strictly linear monotonic ledger, undermining migration ordering assumptions.

Required fix: Reject every non-empty `depends_on`/dependency edge, or formally incorporate those edges into the linearity proof. Update the evaluation and D11 conclusion to describe the corrected gate.

Validation: Add adversarial revisions using single and multiple `depends_on` targets and demonstrate that the gate rejects them, alongside the existing splice, multiple-head, and merge cases.

Resolution: Fixed by `967fe9eca670129298362c786b1ab2b227359154`.
`linear_only_gate()` now also rejects any revision with a non-empty
`dependencies` attribute. Added cases 7 and 8 to
`scratch_alembic_linearity.py` using single (`depends_on="0002"`) and
multiple (`depends_on=["0001", "0002"]`) dependency targets; both are
confirmed as gate violations. Also fixed `build_env()`'s
`script.py.mako` template, which never wrote `depends_on` into the
generated revision file — without that fix, the corrected gate reported
zero violations against a real `depends_on` edge because the attribute was
lost on reload from disk, even though `linear_only_gate()`'s own logic was
already correct; this was caught by re-running the script against the
pinned scratch venv (alembic 1.18.5) rather than by reasoning alone. Also
confirmed `get_heads()` still reports exactly one head with a `depends_on`
edge present, so a head-count-only gate would miss it entirely. Committed
the regenerated `scratch_alembic_output.txt`. Updated `EVALUATION.md`
Section 3 and `DECISIONS.md` D11 to describe the corrected gate.
Validation: `test_alembic_scratch_output_shows_depends_on_edges_are_caught`
and `test_evaluation_records_depends_on_gap_and_fix` added to
`tests/unit/test_pr13_evaluation_docs.py`.

Tests or diagnostics run:

- Inspected the complete range and commit diff with `git show`/`git diff`.
- Compared scratch trigger DDL with current production trigger definitions.
- Inspected `MASTER_PLAN.md`, D11, current architecture documentation, tests, and migration implementation.
- `git diff --check 641f5daee8d5ec629578f371555556a4a24b849e..4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e` passed.
- Tests could not run: `nox` is unavailable, and the available `python3` environment has no `pytest` module.

Fix round 0 validation: `.venv/bin/python -m pytest
tests/unit/test_pr13_evaluation_docs.py tests/unit/test_pr12_evaluation_docs.py`
— 46 passed. `.venv/bin/python -m nox -s ci` — all five sessions
succeeded: `ci`, `tests` (3,165 passed, 106 skipped), `paper_tests` (160
passed), `safety_typecheck` (0 errors), `migration_smoke`.
