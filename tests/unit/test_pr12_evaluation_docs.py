"""Regression test for the PR 12 review finding on the Python 3.10 floor.

`docs/library-migration/pr12/EVALUATION.md` originally described the
Riskfolio-Lib / VectorBT compatibility check as unconditionally conflict-free.
It was only verified on Python 3.14; VectorBT 1.1.0 requires Python >=3.11
(`DEPENDENCY_MATRIX.md`), which cannot resolve on this repository's declared
`>=3.10` project-wide floor. This pins the caveat into the checked-in
document and pins the underlying facts it depends on, so either regressing
back to the unqualified claim or silently changing the floors it cites would
fail here.

A follow-up review round (PR 29 fix round 1) found that only
`EVALUATION.md` carried the qualification — the canonical planning and
decision records (`DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`,
`COMPONENT_MATRIX.md`, `DECISIONS.md` D10, `STATUS.md`) still called the
Riskfolio-Lib/VectorBT pairing unconditionally conflict-free. The tests
below pin the same `>=3.11` qualification into each of those records.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "docs" / "library-migration" / "pr12" / "EVALUATION.md"
DEPENDENCY_MATRIX = ROOT / "docs" / "library-migration" / "DEPENDENCY_MATRIX.md"
MASTER_PLAN = ROOT / "docs" / "library-migration" / "MASTER_PLAN.md"
COMPONENT_MATRIX = ROOT / "docs" / "library-migration" / "COMPONENT_MATRIX.md"
DECISIONS = ROOT / "docs" / "library-migration" / "DECISIONS.md"
STATUS = ROOT / "docs" / "library-migration" / "STATUS.md"
PYPROJECT = ROOT / "pyproject.toml"


def _section(text: str, heading: str, next_heading: str) -> str:
    return text.split(heading, 1)[1].split(next_heading, 1)[0]


def test_repository_python_floor_is_still_3_10():
    """The caveat's premise: the project floor Riskfolio-Lib would inherit."""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in text


def test_dependency_matrix_still_documents_vectorbt_requiring_3_11():
    """The other half of the premise: VectorBT's own floor is narrower."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    match = re.search(r"\| VectorBT[^\n]*?\|\s*`(>=3\.11[^`]*)`\s*\|", text)
    assert match, "VectorBT row's Python-requirement column not found"
    assert match.group(1).startswith(">=3.11")


def test_evaluation_states_the_3_10_floor_caveat():
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    assert "requires-python = \">=3.10\"" in section2 or ">=3.10" in section2
    assert ">=3.11" in section2
    assert "cannot resolve" in section2
    assert "vectorbt>=1.1.0,<1.2" in section2


def test_evaluation_compatibility_claim_is_scoped_to_python_3_11():
    """The 'do not conflict' claim must be qualified, not floor-independent."""
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    idx = section2.index("do not conflict")
    scoped = section2[idx : idx + 200]
    assert ">=3.11" in scoped


def test_recommendation_also_scopes_the_conflict_free_claim():
    text = EVALUATION.read_text(encoding="utf-8")
    section5 = _section(text, "## 5.", "**Decision:")
    idx = section5.index("technically installable")
    scoped = section5[idx : idx + 250]
    assert ">=3.11" in scoped
    assert "3.10" in scoped


def test_dependency_matrix_riskfolio_row_scopes_no_conflict_claim():
    """DEPENDENCY_MATRIX.md's Riskfolio-Lib row must not read as unconditional."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Riskfolio-Lib | 7.3.0 |")
    row = text[idx : idx + 600]
    assert ">=3.11" in row
    assert "3.10" in row


def test_dependency_matrix_rejected_deferred_table_scopes_no_conflict_claim():
    """Section 4's rejected/deferred summary row must carry the same caveat."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Riskfolio-Lib | Defer (PR 12, evaluated 2026-08-23)")
    row = text[idx : idx + 400]
    assert ">=3.11" in row
    assert "3.10" in row


def test_master_plan_row_12_scopes_no_conflict_claim():
    text = MASTER_PLAN.read_text(encoding="utf-8")
    idx = text.index("| 12 | Riskfolio-Lib evaluation only |")
    row = text[idx : idx + 800]
    assert ">=3.11" in row
    assert "3.10" in row


def test_component_matrix_portfolio_optimization_row_scopes_conflict_free_claim():
    text = COMPONENT_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Portfolio optimization |")
    row = text[idx : idx + 500]
    assert ">=3.11" in row
    assert "3.10" in row


def test_decisions_d10_scopes_no_conflict_claim():
    text = DECISIONS.read_text(encoding="utf-8")
    idx = text.index("## D10")
    section = text[idx : idx + 4000]
    assert ">=3.11" in section
    assert "3.10" in section


def test_decisions_d10_ruling_scopes_technically_unblocked_claim():
    text = DECISIONS.read_text(encoding="utf-8")
    idx = text.index("**Ruling: defer, do not adopt.**")
    ruling = text[idx : idx + 500]
    assert "technically installable without" in ruling
    assert ">=3.11" in ruling
    assert "3.10" in ruling


def test_status_current_phase_scopes_no_conflict_claim():
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("**Current phase: PR 12")
    section = text[idx : idx + 1000]
    assert ">=3.11" in section
    assert "3.10" in section


def test_status_completed_work_section_scopes_no_conflict_claim():
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("that Riskfolio-Lib does not conflict with the")
    scoped = text[idx : idx + 250]
    assert ">=3.11" in scoped
    assert "3.10" in scoped
