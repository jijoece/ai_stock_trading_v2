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
    """Pins the empirical result of Section 2's six cases: every one must
    report PASS, not FAIL — a FAIL anywhere means the masking hypothesis
    was substantiated and the "defer" outcome's Section 2 finding is stale."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "FAIL" not in text
    assert text.count("PASS") >= 6
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
    just that the trigger still fires under ORM usage. Pins case 7's guard
    result: both permitted session construction paths are blocked before any
    SQL is emitted, and Core access still works with the guard installed."""
    text = TRIGGER_OUTPUT.read_text(encoding="utf-8")
    assert "TriggerProtectedTableORMGuard" in text
    assert "sessionmaker() session blocked pre-SQL" in text
    assert "Session(bind=...) session blocked pre-SQL" in text
    assert "Core statements still work with the guard installed" in text


def test_alembic_scratch_output_shows_depends_on_edges_are_caught():
    """Regression for review finding: linear_only_gate() originally checked
    only down_revision, silently missing depends_on dependency edges. Pins
    cases 7-8's result: single and multiple depends_on targets are both
    flagged as violations even though get_heads() alone would miss them."""
    text = ALEMBIC_OUTPUT.read_text(encoding="utf-8")
    assert "depends_on dependency edge" in text
    assert "a single `depends_on` edge" in text
    assert "multiple depends_on targets are also caught" in text


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
    section = text[idx : idx + 6000]
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


def test_status_no_file_under_src_scripts_paper_runtime_was_modified():
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 13)", 1)[1]
    idx = section.index("No file under")
    clause = section[idx : idx + 150]
    assert "was modified" in clause
