"""The automation reads the existing migration control plane correctly.

These tests pin the parsing rules that keep the automation subordinate to
`STATUS.md` and `MASTER_PLAN.md` — in particular that phase order is read, not
sorted, so row `8a` is never selected merely because it sorts between 8 and 9.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.automation.migration_docs import (
    MigrationDocumentError,
    MigrationDocuments,
    parse_master_plan,
    parse_status,
    read_migration_documents,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

PLAN_FIXTURE = """# Master Plan

| PR | Title | Scope | Dependency on earlier PRs | Risk | Model |
|---|---|---|---|---|---|
| 8 | Removal decision | **MERGED** decision gate | PR 7 | High | Opus review |
| 8a | Legacy backtest run identity | **Follow-up created by PR 8, not started.** | PR 8 | Medium | Sonnet |
| 9 | Normalization contract | **IMPLEMENTED** on its branch | PR 1 | High | Opus plan + Sonnet |
| 10 | Reconciliation parity tests | Prove reconciliation correctness | PR 9 | High | Opus plan + Sonnet |
"""


def test_status_header_declares_current_and_next_phase() -> None:
    current, following = parse_status(
        "**Current phase: PR 9 — the LumiBot runtime normalization contract —\n"
        "IMPLEMENTED, NOT MERGED** (branch `migration/09-...`).\n\n"
        "**Next phase: PR 10 — broker-to-`paper_books` reconciliation parity tests**\n"
        "(`MASTER_PLAN.md` row 10), which depends on PR 9.\n"
    )
    assert (current, following) == ("9", "10")


def test_status_phase_markers_survive_line_wrapping() -> None:
    # The real STATUS.md wraps mid-sentence; a line-oriented parser would miss this.
    current, following = parse_status("**Current phase:\nPR 8a** and later\n**Next\nphase: PR 9**")
    assert (current, following) == ("8a", "9")


def test_status_without_a_next_phase_reports_none() -> None:
    assert parse_status("**Current phase: PR 18 — final audit**") == ("18", None)


def test_master_plan_rows_keep_document_order_and_metadata() -> None:
    rows = parse_master_plan(PLAN_FIXTURE)
    assert [row.phase_id for row in rows] == ["8", "8a", "9", "10"]
    assert [row.order for row in rows] == [0, 1, 2, 3]
    assert rows[2].title == "Normalization contract"
    assert rows[2].risk == "High"
    assert rows[2].model == "Opus plan + Sonnet"
    assert rows[0].is_merged and rows[0].is_implemented
    assert rows[2].is_implemented and not rows[2].is_merged
    assert not rows[1].is_implemented


def test_master_plan_skips_the_unnumbered_pre_step_row() -> None:
    plan = PLAN_FIXTURE + (
        "| — | **Pre-step before PR 6** | Resolve the boundary question | PR 5 | — | Opus review |\n"
    )
    assert [row.phase_id for row in parse_master_plan(plan)] == ["8", "8a", "9", "10"]


def test_master_plan_without_rows_is_an_error() -> None:
    with pytest.raises(MigrationDocumentError):
        parse_master_plan("# Master Plan\n\nNo table here.\n")


def test_successor_comes_from_status_not_from_sorting() -> None:
    # Requirement: row 8a must never be selected merely because "8a" sorts
    # between "8" and "9" and appears between them in the table.
    documents = MigrationDocuments(
        current_phase_id="9", next_phase_id="10", rows=parse_master_plan(PLAN_FIXTURE)
    )
    assert documents.successor_of("9") == "10"
    assert documents.row("8a") is not None, "row 8a must stay tracked, just not selected"


def test_successor_of_an_undocumented_edge_is_not_guessed() -> None:
    documents = MigrationDocuments(
        current_phase_id="9", next_phase_id="10", rows=parse_master_plan(PLAN_FIXTURE)
    )
    assert documents.successor_of("8") is None
    assert documents.successor_of("10") is None
    assert documents.successor_of(None) is None


def test_missing_documents_are_reported_not_defaulted(tmp_path: Path) -> None:
    with pytest.raises(MigrationDocumentError, match="missing migration document"):
        read_migration_documents(tmp_path)


def test_repository_documents_parse(tmp_path: Path) -> None:
    documents = read_migration_documents(REPO_ROOT)
    assert documents.current_phase_id is not None
    phase_ids = [row.phase_id for row in documents.rows]
    assert "8a" in phase_ids and "9" in phase_ids and "10" in phase_ids
    # Document order, which is what the automation relies on for reporting.
    assert phase_ids.index("8a") < phase_ids.index("9") < phase_ids.index("10")
