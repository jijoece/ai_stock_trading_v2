"""Documentation-consistency regression test for PR 13 (SQLAlchemy/Alembic
feasibility and ADR — `MASTER_PLAN.md` row 13, `DECISIONS.md` D11).

PR 13 is evaluation-only: no dependency is added to `pyproject.toml`, and no
file under `src/`, `scripts/`, `paper_runtime/src/`, or `backtest_runtime/`
is touched. This file pins the facts the evaluation's "defer, not adopt"
outcome depends on into the checked-in records
(`pr13/EVALUATION.md`, `DECISIONS.md` D11, `DEPENDENCY_MATRIX.md`,
`COMPONENT_MATRIX.md`, `STATUS.md`) so a future edit that silently changes
the outcome, drops a tested claim, or reintroduces `sqlalchemy`/`alembic`
as a dependency without updating this record fails here — the same pattern
`tests/unit/test_pr12_evaluation_docs.py` established for PR 12.

Anchors on the enduring "## Completed work (PR 13)" section, not on
STATUS.md's mutable "Current phase" heading, which PR 14 will overwrite —
the same defect class PR 12's review-fix rounds already found and fixed
(see that file's docstrings).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "docs" / "library-migration" / "pr13" / "EVALUATION.md"
DEPENDENCY_MATRIX = ROOT / "docs" / "library-migration" / "DEPENDENCY_MATRIX.md"
COMPONENT_MATRIX = ROOT / "docs" / "library-migration" / "COMPONENT_MATRIX.md"
DECISIONS = ROOT / "docs" / "library-migration" / "DECISIONS.md"
STATUS = ROOT / "docs" / "library-migration" / "STATUS.md"
PYPROJECT = ROOT / "pyproject.toml"
TRIGGER_SCRATCH = ROOT / "docs" / "library-migration" / "pr13" / "scratch_trigger_orm_vs_core.py"
ALEMBIC_SCRATCH = ROOT / "docs" / "library-migration" / "pr13" / "scratch_alembic_linearity.py"
TRIGGER_OUTPUT = ROOT / "docs" / "library-migration" / "pr13" / "scratch_trigger_output.txt"
ALEMBIC_OUTPUT = ROOT / "docs" / "library-migration" / "pr13" / "scratch_alembic_output.txt"


def test_no_sqlalchemy_or_alembic_dependency_was_added():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "sqlalchemy" not in text.lower()
    assert "alembic" not in text.lower()


def test_scratch_reproductions_exist():
    assert TRIGGER_SCRATCH.exists()
    assert ALEMBIC_SCRATCH.exists()
    assert TRIGGER_OUTPUT.exists()
    assert ALEMBIC_OUTPUT.exists()


def test_trigger_scratch_output_shows_no_masking_across_all_cases():
    """Pins the empirical result of Section 2's nine cases: every one must
    report PASS, not FAIL — a FAIL anywhere means the masking hypothesis
    was substantiated and the "defer" outcome's Section 2 finding is stale."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "FAIL" not in text
    assert text.count("PASS") >= 9
    assert "real_orders is reserved" in text
    assert "paper_book_cash_ledger is append-only" in text
    assert "PendingRollbackError" in text


def test_alembic_scratch_output_shows_linear_gate_catches_branch():
    text = ALEMBIC_OUTPUT.read_text(encoding="utf-8")
    assert "FAIL" not in text
    assert "UNEXPECTED" not in text
    assert "please specify --splice" in text
    assert "Multiple head revisions are present" in text
    assert "a branch, not a linear chain" in text


def test_trigger_scratch_output_shows_core_only_guard_blocks_all_orm_paths():
    """Regression for review finding: question (a) requires proving
    trigger-protected tables can be constrained to Core-only statements, not
    just that the trigger still fires under ORM usage. Pins case 8's guard
    result: both permitted session construction paths are blocked before any
    SQL is emitted, and Core access still works with the guard installed."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "TriggerProtectedTableORMGuard" in text
    assert "sessionmaker() session blocked pre-SQL" in text
    assert "Session(bind=...) session blocked pre-SQL" in text
    assert "Core statements still work with the guard installed" in text


def test_trigger_scratch_output_shows_orm_update_masking_case():
    """Regression for review finding "Exercise ORM updates before
    withdrawing the masking risk": cases 1-6 (before this fix round) covered
    a new-object INSERT (case 2) and a cascade DELETE (case 6) but never an
    UPDATE reached through the identity map on an already-loaded row — the
    exact scenario the unresolved review thread described. Pins case 7's
    result: the ORM UPDATE is rejected, re-reading the mutated attribute
    before an explicit rollback itself raises PendingRollbackError (proving
    the session refuses to serve a possibly-stale value), and the row's
    value after rollback+refresh matches the database exactly — no masking."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "Case 7: ORM UPDATE" in text
    assert "loaded existing row via ORM identity map" in text
    assert "PASS: ORM update rejected" in text
    assert "PASS: the rejected UPDATE's in-memory mutation never became durable" in text


def test_trigger_scratch_output_shows_pre_rollback_read_actually_executed():
    """Regression for review finding "Actually execute the pre-rollback
    attribute read": an earlier fix round's case 7 explicitly avoided
    reading `row.amount_usd` after the rejected UPDATE flush, merely
    asserting in prose that doing so "would raise" — a claim EVALUATION.md
    and D11 repeated but the code never actually exercised, so a future
    identity-map regression that silently returned a stale value instead of
    raising could pass unnoticed. Pins that the expired attribute is now
    actually read pre-rollback and that the read itself raised
    `PendingRollbackError`, not merely a claim that it would."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert (
        "PASS: reading the expired attribute pre-rollback itself raised "
        "PendingRollbackError" in text
    )
    assert "FAIL: reading the expired attribute" not in text


def test_trigger_scratch_output_shows_guard_covers_relationship_secondary_and_multi_table_mapper():
    """Regression for review findings "Cover unit-of-work writes without
    mapped objects" and "Inspect relationship-generated writes in the ORM
    guard": the guard originally inspected only each changed object's own
    `__table__`, missing (a) a `relationship(..., secondary=protected_table)`
    collection write, where the flush emits association-table INSERT/DELETE
    with no directly mapped object for the secondary table itself, and (b)
    a joined-table-inheritance mapper, whose flush also writes an ancestor
    table beyond the object's own local table. Pins case 10's result: both
    adversarial paths — using the real `research_cycle_provider_provenance_links`
    association table cited by the finding's own evidence — are now rejected
    pre-SQL by `TriggerProtectedTableORMGuard`, not left to reach a trigger
    (or, for a synthetic table with no trigger, to pass through undetected)."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "Case 10:" in text
    assert "PASS: relationship secondary write blocked pre-SQL" in text
    assert "research_cycle_provider_provenance_links" in text.split("Case 10:", 1)[1]
    assert "PASS: multi-table mapper write blocked pre-SQL" in text
    assert "FAIL:" not in text.split("Case 10:", 1)[1]


def test_trigger_scratch_output_shows_guard_covers_unloaded_delete_cascade():
    """Regression for review finding "Block cascades through unloaded
    secondary relationships": the guard's relationship check only inspected
    `get_history(..., passive=PASSIVE_NO_FETCH)` for `added`/`deleted`
    entries, which stays empty when a relationship's owning object is
    deleted without its many-to-many collection ever loaded (or loaded but
    untouched) — SQLAlchemy still emits a DELETE against the secondary table
    for every existing association row once the parent is deleted, driven
    by current DB membership rather than any local Python mutation.
    Reproducing that flow with `research_cycle_provider_provenance_links`
    let the flush complete and removed the association row with no
    `TriggerProtectedTableORMGuard` raised. Pins case 10's third adversarial
    path: an object in `session.deleted` is force-fetched via
    `passive=PASSIVE_OFF` and its `unchanged` membership is now also
    treated as relevant, so the unloaded-delete cascade is rejected pre-SQL."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    case_10_text = text.split("Case 10:", 1)[1]
    assert "PASS: unloaded-delete cascade blocked pre-SQL" in case_10_text
    assert "FAIL:" not in case_10_text


def test_evaluation_records_unloaded_delete_cascade_fix():
    """Regression: EVALUATION.md must record the case-10(c) unloaded-delete
    fix — the guard now force-fetches (`PASSIVE_OFF`) and checks `unchanged`
    membership for objects being deleted, not just `added`/`deleted`
    history — not just the earlier relationship-secondary and multi-table
    mapper fixes."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "PASSIVE_OFF" in text
    assert "unchanged" in text


def test_evaluation_records_relationship_secondary_and_multi_table_mapper_fix():
    """Regression: EVALUATION.md must record the case-10 fix, not just claim
    universal enforcement — the guard's proven boundary is unit-of-work
    flush writes reachable through a mapper's tables or relationships, and
    the record must say so rather than overclaim."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "class_mapper(type(obj)).tables" in text
    assert "get_history" in text
    assert "research_cycle_provider_provenance_links" in text
    assert "bulk `update()`/`delete()` statements issued via" in text


def test_trigger_scratch_output_shows_guard_covers_every_discovered_table():
    """Regression for review finding "Enforce Core-only access for every
    trigger-protected table": the guard's allowlist previously covered only
    2 tables (`real_orders`, `paper_book_cash_ledger`) even though
    production defines many more append-only/reserved tables. Pins case 9's
    result: the guard's policy is derived from the production schema (not
    hardcoded) and blocks ORM writes pre-SQL against every discovered table,
    explicitly including the tables the original finding cited as omitted."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "Case 9:" in text
    assert "discovered 50 trigger-protected tables" in text
    assert "PASS: guard blocked ORM writes pre-SQL for all 50 discovered" in text
    for table in (
        "paper_book_fills",
        "research_attempts",
        "research_attempt_failures",
        "research_cycle_provider_provenance_links",
    ):
        assert table in text


def test_guard_policy_matches_current_production_trigger_protected_tables():
    """Regression for review finding "Enforce Core-only access for every
    trigger-protected table" validation requirement: "add a regression check
    that fails whenever production gains a protected table absent from the
    guard policy." Independently re-derives the trigger-protected table set
    directly from the *current* production schema files (duplicating, not
    importing, `scratch_trigger_orm_vs_core.py`'s discovery regex, so a bug
    in that regex cannot hide from this check) and compares the count
    against the committed scratch output. If production gains (or loses) a
    write-rejecting trigger, this count diverges from the pinned output and
    this test fails until the scratch script is re-run and the output/docs
    are regenerated — the guard's policy can no longer go silently stale."""
    import re as _re

    schema_dir = ROOT / "src" / "trading_research" / "storage"
    trigger_block = _re.compile(
        r"CREATE TRIGGER.*?BEFORE\s+(?:INSERT|UPDATE|DELETE)\s+ON\s+(\w+).*?END;",
        _re.DOTALL,
    )
    current_tables: set[str] = set()
    for schema_file in sorted(schema_dir.glob("*_schema.py")):
        source = schema_file.read_text(encoding="utf-8")
        for match in trigger_block.finditer(source):
            if "RAISE(ABORT" in match.group(0):
                current_tables.add(match.group(1))

    assert {"real_orders", "paper_book_cash_ledger"} <= current_tables
    output_text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert f"discovered {len(current_tables)} trigger-protected tables" in output_text, (
        f"production schema now defines {len(current_tables)} trigger-protected "
        f"table(s), but the pinned scratch output/EVALUATION.md record a "
        f"different count — re-run scratch_trigger_orm_vs_core.py and update "
        f"the committed output and docs"
    )


def test_alembic_scratch_output_shows_depends_on_edges_are_caught():
    """Regression for review finding: linear_only_gate() originally checked
    only down_revision, silently missing depends_on dependency edges. Pins
    cases 7-8's result: single and multiple depends_on targets are both
    flagged as violations even though get_heads() alone would miss them."""
    text = ALEMBIC_OUTPUT.read_text(encoding="utf-8")
    assert "depends_on dependency edge" in text
    assert "a single `depends_on` edge" in text
    assert "multiple depends_on targets are also caught" in text


def test_alembic_scratch_output_shows_concurrent_checkout_branch_case():
    """Regression for review finding "Test concurrent revisions before
    claiming default resistance": Case 2 only demonstrated Alembic's
    un-spliced-branch refusal when the second `alembic revision` call could
    already see the first developer's file in the same script directory —
    it never showed what happens when two developers create revisions
    independently from separate checkouts of the same parent and combine
    the files afterward, the common accidental-branch scenario. Pins case
    9's result: both checkouts succeed without `--splice` or any
    CommandError, and the combined script directory reports two heads."""
    text = ALEMBIC_OUTPUT.read_text(encoding="utf-8")
    assert "Case 2b:" in text
    assert "two independent checkouts" in text
    assert (
        "neither developer's local `alembic revision` call raised "
        "CommandError or needed --splice" in text
    )
    assert "heads after combining both checkouts' revision files: " in text
    assert "['0004concurrentA', '0004concurrentB']" in text


def test_evaluation_narrows_accidental_branch_resistance_claim():
    """Regression for review finding "Test concurrent revisions before
    claiming default resistance": EVALUATION.md, DECISIONS.md, and
    STATUS.md each repeated the claim that Alembic "resists accidental
    branching" without qualifying that the built-in guards only see state
    already visible within a single script directory. Pins that all three
    records now scope the claim to a sequential branch and separately
    record that the concurrent-checkout branch (case 9) is not caught."""
    for doc in (EVALUATION, DECISIONS, STATUS):
        text = doc.read_text(encoding="utf-8")
        assert "sequential" in text
        assert "concurrent" in text


def test_evaluation_states_defer_outcome():
    text = EVALUATION.read_text(encoding="utf-8")
    assert "Recommendation: defer" in text
    assert "do not add `sqlalchemy` or `alembic`" in text.lower() or (
        "do not add" in text and "sqlalchemy" in text.lower()
    )
    assert "No ADR is required" in text


def test_evaluation_withdraws_the_masking_hypothesis():
    text = EVALUATION.read_text(encoding="utf-8")
    assert "not substantiated" in text
    assert "withdrawn" in text


def test_evaluation_confirms_linear_only_gate_is_needed_not_default():
    text = EVALUATION.read_text(encoding="utf-8")
    assert "linear_only_gate" in text
    assert "not the library's default end-state" in text or "only its default resistance" in text


def test_evaluation_proves_core_only_boundary_not_just_recommends_it():
    """Regression: EVALUATION.md must record that question (a) was answered
    by a proven enforcement mechanism, not carried forward only as an
    unenforced recommendation."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "Question (a) is answered, not just recommended" in text
    assert "TriggerProtectedTableORMGuard" in text
    assert "before_flush" in text


def test_evaluation_records_independent_connection_fix_and_narrowed_claim():
    """Regression for review finding "Use a separate DB connection for the
    visibility check": case 4's `engine.connect()` used to reuse the same
    single StaticPool-pinned in-memory connection as the ORM session,
    defeating the "independent observer" claim, and the check ran after
    SQLAlchemy's automatic post-failure rollback, not during a still-pending
    transaction. Pins that EVALUATION.md now (a) records the engine as
    file-backed so `engine.connect()` is a genuinely separate DBAPI
    connection, and (b) narrows the documented conclusion to post-failure
    rollback state rather than a pending-write visibility window."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "file-backed SQLite database" in text
    assert "genuinely independent DBAPI connection" in text
    assert "post-failure end-state" in text
    assert "not that a" in text and "pending" in text


def test_evaluation_records_depends_on_gap_and_fix():
    """Regression: EVALUATION.md must record that the linear-only gate
    initially missed depends_on edges and was corrected."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "depends_on" in text
    assert "real gap, not a hypothetical one" in text


def test_dependency_matrix_records_defer_outcome_for_both_packages():
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| SQLAlchemy |")
    row = text[idx : idx + 400]
    assert "Defer" in row
    idx = text.index("| Alembic |")
    row = text[idx : idx + 400]
    assert "Defer" in row


def test_dependency_matrix_rejected_deferred_table_has_sqlalchemy_alembic_row():
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    assert "| SQLAlchemy / Alembic | Defer (PR 13" in text


def test_component_matrix_persistence_and_migrations_rows_record_defer():
    text = COMPONENT_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Persistence (repository/DAO layer) |")
    row = text[idx : idx + 400]
    assert "Defer (PR 13" in row
    idx = text.index("| Migrations |")
    row = text[idx : idx + 400]
    assert "Defer (PR 13" in row


def test_decisions_d11_exists_and_records_ruling():
    text = DECISIONS.read_text(encoding="utf-8")
    assert "## D11 — PR 13" in text
    idx = text.index("## D11 — PR 13")
    section = text[idx : idx + 7000]
    assert "**Ruling: defer, do not adopt.**" in section
    assert "withdrawn as unsubstantiated" in section
    assert "linear-only" in section


def test_status_records_pr13_evaluation_outcome():
    """Anchors on the enduring 'Completed work (PR 13)' heading, not the
    mutable current-phase summary PR 14 will later overwrite."""
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 13)", 1)[1]
    assert "**Scope:** no implementation" in section or "**Scope:**" in section
    assert "Outcome: defer, do not adopt" in section
    assert "test_pr13_evaluation_docs.py" in section


def test_status_pr13_section_survives_pr14_current_phase_rewrite():
    """Regression for the defect class PR 12's review-fix rounds found:
    simulates PR 14 rewriting STATUS.md's current-phase heading and proves
    the enduring PR 13 record still resolves independently of it."""
    text = STATUS.read_text(encoding="utf-8")
    simulated = text.replace(
        "**Current phase: PR 13 — SQLAlchemy/Alembic feasibility and ADR — "
        "EVALUATED, NOT MERGED**",
        "**Current phase: PR 14 — APScheduler/Tenacity feasibility**",
    )
    assert "**Current phase: PR 13" not in simulated
    section = simulated.split("## Completed work (PR 13)", 1)[1]
    assert "Outcome: defer, do not adopt" in section


def test_status_remaining_blockers_does_not_contradict_pr13_evaluated():
    """Regression for review finding "Resolve PR 13 in the remaining-blockers
    list": STATUS.md's "Current phase" heading declared PR 13 evaluated and
    the next phase advanced to PR 14, while the same file's "Remaining
    blockers" section still listed "PR 13/14 feasibility outcomes" as
    unknown until those PRs run — self-contradictory, and it continued to
    present the completed SQLAlchemy/Alembic evaluation as an unresolved
    blocker. Pins that the stale combined item is gone and PR 13's blocker
    entry is explicitly marked resolved, leaving only PR 14 genuinely
    unknown."""
    text = STATUS.read_text(encoding="utf-8")
    assert "PR 13/14 feasibility outcomes" not in text
    blockers_section = text.split("## Remaining blockers", 1)[1].split(
        "## Completed work (PR 2)", 1
    )[0]
    assert "PR 13 feasibility outcome" in blockers_section
    assert "Resolved 2026-08-23" in blockers_section
    assert "PR 14 feasibility outcome" in blockers_section
    assert "is unknown" in blockers_section
    assert "until that PR runs" in blockers_section


def test_status_no_file_under_src_scripts_paper_runtime_was_modified():
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 13)", 1)[1]
    idx = section.index("No file under")
    clause = section[idx : idx + 150]
    assert "was modified" in clause
