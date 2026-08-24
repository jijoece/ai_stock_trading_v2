# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/13-sqlalchemy-alembic-feasibility`
- Reviewed HEAD: `e303ed03b42b5623fb15447ba42e47f99f7cc87b`
- Subject: Record PR 13 fix round: both findings resolved
- Claude commits reviewed: 4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e,967fe9eca670129298362c786b1ab2b227359154,e303ed03b42b5623fb15447ba42e47f99f7cc87b
- Review scope: FULL_PR
- Reviewed base: `641f5daee8d5ec629578f371555556a4a24b849e`
- GitHub PR: #30
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: `6869de867825c785c0b01823de0f300e63dca9ab`

## Findings

### [P2] Enforce Core-only access for every trigger-protected table

Commit: `967fe9eca670129298362c786b1ab2b227359154`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr13/scratch_trigger_orm_vs_core.py:420-430`

Problem: The new guard claims to enforce the required Core-only boundary for trigger-protected tables, but its allowlist contains only `real_orders` and `paper_book_cash_ledger`.

Evidence: Production defines many other append-only or immutable trigger-protected tables, including `paper_book_fills`, `research_attempts`, `research_attempt_failures`, `research_cycle_provider_provenance_links`, and numerous paper-execution audit tables. ORM flushes targeting any omitted table pass this guard. Nevertheless, `EVALUATION.md:128-134` records question (a) as answered and describes the mechanism generally as constraining trigger-protected tables. The regression test at `tests/unit/test_pr13_evaluation_docs.py:70-80` only searches previously generated output for two successful examples and cannot detect incomplete table coverage.

Impact: A future SQLAlchemy adoption following D11 could treat this reproduction as a proven safety boundary while ORM unit-of-work writes remain permitted against most append-only audit, accounting, and execution-safety tables.

Required fix: Define the complete trigger-protected-table policy from a centralized authoritative registry or derive and validate it against the production schema. The guard must reject ORM writes to every table governed by the Core-only policy, including subsequently added tables.

Validation: Map and attempt ORM insert/update/delete operations against every protected table category, confirm the guard rejects them before SQL execution, and add a regression check that fails whenever production gains a protected table absent from the guard policy.

Resolution: Fixed by `6869de867825c785c0b01823de0f300e63dca9ab`.
`TRIGGER_PROTECTED_TABLES` is no longer a hardcoded 2-table set; it is now
computed by `discover_trigger_protected_tables_from_production_schema()`,
which scans every `src/trading_research/storage/*_schema.py` module for a
`CREATE TRIGGER ... BEFORE {INSERT,UPDATE,DELETE} ON <table> ...
RAISE(ABORT ... END;` block (unconditional or `WHEN`-conditional) and
collects `<table>` — currently 50 tables, not 2. Added case 9 to
`scratch_trigger_orm_vs_core.py`: builds a disposable synthetic
single-column table for every one of the 50 discovered names, maps each
imperatively, and confirms `TriggerProtectedTableORMGuard` rejects an ORM
flush pre-SQL against every one, explicitly including the tables the
finding cited as omitted (`paper_book_fills`, `research_attempts`,
`research_attempt_failures`, `research_cycle_provider_provenance_links`).
Re-ran the scratch script against the pinned scratch venv (sqlalchemy
2.0.52) and committed the regenerated `scratch_trigger_output.txt`. Added
`test_guard_policy_matches_current_production_trigger_protected_tables` to
`tests/unit/test_pr13_evaluation_docs.py`, which independently re-derives
the protected-table set from the *current* production schema (a duplicated,
not imported, copy of the discovery regex) and fails if that count diverges
from the pinned scratch output — so a future table gaining a
write-rejecting trigger cannot silently fall outside guard coverage without
failing CI. Updated `EVALUATION.md` Section 2 (new case 9) and
`DECISIONS.md` D11 to record the guard's coverage as complete and
self-updating, not a hand-maintained 2-table allowlist.
Validation: `test_trigger_scratch_output_shows_guard_covers_every_discovered_table`
and `test_guard_policy_matches_current_production_trigger_protected_tables`
added to `tests/unit/test_pr13_evaluation_docs.py`.

Tests or diagnostics run:

- Inspected all three complete commit diffs chronologically with `git show` and `git diff`.
- Compared the scratch DDL and guard coverage with current production schema and trigger definitions.
- Reviewed `MASTER_PLAN.md`, D11, dependency/component matrices, status records, evaluation, scratch scripts, outputs, and regression tests.
- Attempted `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_pr13_evaluation_docs.py`; collection could not start because the read-only environment has no usable temporary directory.
- No broker, scheduler, model, credentialed, or external-order operations were performed.

### [P2] Exercise ORM updates before withdrawing the masking risk

Commit: `4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr13/EVALUATION.md:103`

Problem: An active GitHub review thread remains unresolved:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Exercise ORM updates before withdrawing the masking risk**
> 
> When a future mapper loads an existing `paper_book_cash_ledger` row, changes a protected field, and flushes, the trigger-rejected UPDATE and the already-persistent object's state are precisely the identity-map scenario under evaluation. Case 5 tests UPDATE only through Core, while the ORM cases cover a new-object INSERT and a cascade DELETE, so these six cases do not support withdrawing the ORM masking concern or downgrading Core-only access from a correctness constraint; add an ORM UPDATE case that inspects the object and database before and after rollback.
> 
> AGENTS.md reference: [AGENTS.md:L63-L69](https://github.com/jijoece/ai_stock_trading_v2/blob/4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e/AGENTS.md#L63-L69)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/30#discussion_r3840952291) is current, unresolved, and not outdated.

Impact: Merging would knowingly carry unresolved review feedback into main.

Required fix: Verify and address the review comment in code and add the requested regression coverage.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

Resolution: Fixed by `6869de867825c785c0b01823de0f300e63dca9ab`. Added
case 7 to `scratch_trigger_orm_vs_core.py`: loads the `paper_book_cash_ledger`
row case 5 inserted via `session.get()` (identity-map load of an existing
row, not a new object), mutates the protected `amount_usd` field, and
flushes — rejected identically (`IntegrityError`). Re-reading the mutated
attribute before calling `session.rollback()` was itself attempted and
raises `PendingRollbackError`, since SQLAlchemy expires an object's
attributes after a failed UPDATE flush and a dirty session refuses to serve
even a read of its own expired attribute — the same fail-closed behavior
case 3 already pins for unrelated work. A read from a genuinely independent
connection during that window shows the original, unmutated value; after
`session.rollback()` and `session.expire()`, re-reading the attribute
issues a fresh `SELECT` that returns the same original value, proving the
identity map does not retain or later resurface the rejected mutation.
Re-ran the scratch script against the pinned scratch venv (sqlalchemy
2.0.52) and committed the regenerated `scratch_trigger_output.txt`. Updated
`EVALUATION.md` Section 2 (new case 7) and `DECISIONS.md` D11 to record the
masking withdrawal as covering INSERT, UPDATE, and cascade DELETE, not just
INSERT and DELETE.
Validation: `test_trigger_scratch_output_shows_orm_update_masking_case`
added to `tests/unit/test_pr13_evaluation_docs.py`.

### [P2] Use a separate DB connection for the visibility check

Commit: `4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/docs/library-migration/pr13/scratch_trigger_orm_vs_core.py:313`

Problem: An active GitHub review thread remains unresolved:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Use a separate DB connection for the visibility check**
> 
> In Case 4, `make_engine()` combines an in-memory SQLite URL with `StaticPool`, which deliberately reuses one DBAPI connection, so this `engine.connect()` does not provide the independent observer claimed by the evaluation. Moreover, the failed flush has already caused SQLAlchemy to roll back its DB transaction before control reaches this handler, making a zero count unable to prove that the legal write was never externally visible while the rejection was being handled; use a file-backed database with a genuinely separate connection or narrow the documented conclusion to post-failure rollback state.
> 
> AGENTS.md reference: [AGENTS.md:L63-L69](https://github.com/jijoece/ai_stock_trading_v2/blob/4f81ba9087d39fa8024e36fe43446e8d5e2d0d1e/AGENTS.md#L63-L69)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/30#discussion_r3840952295) is current, unresolved, and not outdated.

Impact: Merging would knowingly carry unresolved review feedback into main.

Required fix: Verify and address the review comment in code and add the requested regression coverage.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

Resolution: Fixed by `6869de867825c785c0b01823de0f300e63dca9ab`.
`make_engine()` now opens a file-backed SQLite database (a temp file, not
`sqlite:///:memory:`), matching `storage/database.py`'s own
`sqlite3.connect(str(db_path))`, with the default `QueuePool` (no
`StaticPool`). `engine.connect()` in case 4 (and the new case 7) is
therefore a genuinely independent DBAPI connection from the ORM session's
own, not the same connection a `StaticPool`-pinned in-memory engine would
silently reuse. Also narrowed the documented conclusion: per case 3's
`PendingRollbackError`, SQLAlchemy has already rolled back the failed
flush's transaction internally by the time the `except` block runs, before
this test's own `session.rollback()` — so the check proves the
**post-failure end-state** is clean via true cross-connection visibility,
not that a still-pending, not-yet-rolled-back write was momentarily
invisible mid-transaction, a window that is not claimed or observable given
SQLAlchemy's fail-closed behavior. Re-ran the scratch script against the
pinned scratch venv (sqlalchemy 2.0.52) and committed the regenerated
`scratch_trigger_output.txt`. Updated `EVALUATION.md` case 4's description
and `DECISIONS.md` D11 to match the corrected connection and the narrowed
claim.
Validation: `test_evaluation_records_independent_connection_fix_and_narrowed_claim`
added to `tests/unit/test_pr13_evaluation_docs.py`.

