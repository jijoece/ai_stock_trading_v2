"""The reconciliation state machine.

Every case here is derived from a real hazard: GitHub disagreeing with a status
document written before a merge, an unchanged SHA that must not be paid to
review twice, a review/fix loop that must not run forever, and phase ordering
that must never be produced by sorting identifiers.
"""

from __future__ import annotations

import pytest

from scripts.automation.config import AutomationConfig, MergeConfig, ReviewConfig
from scripts.automation.github import CI_FAIL, CI_NONE, CI_PASS, CI_PENDING, PullRequestSnapshot
from scripts.automation.migration_docs import MigrationDocuments, parse_master_plan
from scripts.automation.reconcile import index_pull_requests_by_phase, reconcile
from scripts.automation import state as state_module
from scripts.automation.state import AutomationState


PLAN = parse_master_plan(
    """
| PR | Title | Scope | Dependency | Risk | Model |
|---|---|---|---|---|---|
| 8 | Removal decision | **MERGED** | PR 7 | High | Opus review |
| 8a | Legacy backtest identity | **not started** | PR 8 | Medium | Sonnet |
| 9 | Normalization contract | **IMPLEMENTED** | PR 1 | High | Opus plan + Sonnet |
| 10 | Reconciliation parity tests | Prove parity | PR 9 | High | Opus plan + Sonnet |
| 18 | Final authority and safety audit | Confirm completion | All prior | High | Opus review |
"""
)

HEAD = "3193b0bcc97ca9b1e9878b6eb036f42fb21bce36"
OTHER_HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def documents(current: str | None, following: str | None) -> MigrationDocuments:
    return MigrationDocuments(current_phase_id=current, next_phase_id=following, rows=PLAN)


def pull_request(
    *,
    number: int = 22,
    phase: str = "09",
    state: str = "OPEN",
    head: str = HEAD,
    merged: bool = False,
    ci: str = CI_PASS,
    review_decision: str = "",
    is_draft: bool = False,
) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        number=number,
        state=state,
        branch=f"migration/{phase}-slug",
        head_sha=head,
        base="main",
        merged=merged,
        ci_state=ci,
        review_decision=review_decision,
        is_draft=is_draft,
    )


def run(
    *,
    docs: MigrationDocuments,
    pull_requests=(),
    cached: AutomationState | None = None,
    config: AutomationConfig | None = None,
):
    return reconcile(
        documents=docs,
        pull_requests=pull_requests,
        cached=cached or AutomationState(),
        config=config or AutomationConfig(),
    )


# --- bootstrap ------------------------------------------------------------


def test_bootstrap_with_the_active_pull_request_open() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()])
    assert result.state.active_phase == "9"
    assert result.state.github_pr == 22
    assert result.state.state == state_module.WAITING_FOR_REVIEW
    assert result.next_phase_id == "10"


def test_bootstrap_reuses_the_existing_pull_request_instead_of_proposing_a_new_one() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()])
    assert result.state.next_action != state_module.ACTION_IMPLEMENT
    assert result.state.branch == "migration/09-slug"


def test_bootstrap_with_the_active_pull_request_already_merged_advances() -> None:
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(state="MERGED", merged=True)],
    )
    assert result.state.active_phase == "10"
    assert result.state.state == state_module.WAITING_FOR_IMPLEMENTATION
    assert result.state.next_action == state_module.ACTION_IMPLEMENT


def test_status_and_github_disagree_about_the_merge_and_github_wins() -> None:
    # The real case at installation: main's STATUS.md still called PR 8 current
    # ("NOT MERGED") because that claim was written inside PR 8's own branch.
    result = run(
        docs=documents("8", "9"),
        pull_requests=[
            pull_request(number=20, phase="08", state="MERGED", merged=True, head=OTHER_HEAD),
            pull_request(),
        ],
    )
    assert result.state.active_phase == "9"
    assert result.state.github_pr == 22
    assert any("merged" in reason for reason in result.reasons)


def test_a_phase_after_the_final_row_is_done_not_an_error() -> None:
    result = run(
        docs=documents("18", None),
        pull_requests=[pull_request(number=40, phase="18", state="MERGED", merged=True)],
    )
    assert result.state.state == state_module.DONE
    assert result.state.active_phase is None


def test_documents_more_than_one_phase_behind_escalate() -> None:
    result = run(
        docs=documents("8", "9"),
        pull_requests=[
            pull_request(number=20, phase="08", state="MERGED", merged=True),
            pull_request(number=22, phase="09", state="MERGED", merged=True),
        ],
    )
    assert result.state.state == state_module.HUMAN_REQUIRED
    assert result.state.next_action == state_module.ACTION_ESCALATE


def test_row_8a_is_never_selected_merely_because_it_sorts_between_8_and_9() -> None:
    # 8a sits between 8 and 9 in the table and would win any sort-based choice.
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(state="MERGED", merged=True)],
    )
    assert result.state.active_phase == "10"
    assert result.documents.row("8a") is not None, "8a must remain tracked in the plan"


# --- CI -------------------------------------------------------------------


@pytest.mark.parametrize("ci_state", [CI_PENDING, CI_NONE])
def test_incomplete_ci_waits(ci_state: str) -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request(ci=ci_state)])
    assert result.state.state == state_module.WAITING_FOR_CI
    assert result.state.next_action == state_module.ACTION_WAIT_FOR_CI


def test_failing_ci_requires_a_fix_and_never_a_review() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request(ci=CI_FAIL)])
    assert result.state.state == state_module.FIX_REQUIRED
    assert result.state.next_action == state_module.ACTION_FIX_CI


def test_passing_ci_on_an_unreviewed_head_requests_a_review() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request(ci=CI_PASS)])
    assert result.state.state == state_module.WAITING_FOR_REVIEW
    assert result.state.next_action == state_module.ACTION_REVIEW


# --- review deduplication -------------------------------------------------


def test_an_unchanged_head_is_never_reviewed_twice() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=1,
        state=state_module.READY_TO_MERGE,
    )
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()], cached=cached)
    assert result.state.next_action != state_module.ACTION_REVIEW
    assert result.state.state == state_module.READY_TO_MERGE


def test_a_new_head_from_a_fix_permits_another_review() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=1,
        state=state_module.FIX_REQUIRED,
    )
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(head=OTHER_HEAD)],
        cached=cached,
    )
    assert result.state.state == state_module.WAITING_FOR_REVIEW
    assert result.state.next_action == state_module.ACTION_REVIEW
    assert result.state.review_round == 1


def test_outstanding_findings_on_a_reviewed_head_stay_outstanding() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=1,
        state=state_module.FIX_REQUIRED,
    )
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()], cached=cached)
    assert result.state.state == state_module.FIX_REQUIRED
    assert result.state.next_action == state_module.ACTION_FIX_FINDINGS


def test_changes_requested_on_github_requires_a_fix() -> None:
    cached = AutomationState(
        active_phase="9", github_pr=22, head_sha=HEAD, last_reviewed_sha=HEAD, review_round=1
    )
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(review_decision="CHANGES_REQUESTED")],
        cached=cached,
    )
    assert result.state.state == state_module.FIX_REQUIRED


def test_review_bookkeeping_resets_when_the_phase_advances() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=3,
        state=state_module.READY_TO_MERGE,
    )
    result = run(
        docs=documents("9", "10"),
        pull_requests=[
            pull_request(state="MERGED", merged=True),
            pull_request(number=23, phase="10", head=OTHER_HEAD),
        ],
        cached=cached,
    )
    assert result.state.active_phase == "10"
    assert result.state.review_round == 0
    assert result.state.last_reviewed_sha is None


def test_max_review_rounds_escalates_instead_of_looping() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=3,
        state=state_module.FIX_REQUIRED,
    )
    config = AutomationConfig(review=ReviewConfig(max_rounds=3))
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(head=OTHER_HEAD)],
        cached=cached,
        config=config,
    )
    assert result.state.state == state_module.HUMAN_REQUIRED
    assert result.state.next_action == state_module.ACTION_ESCALATE
    assert any("maximum" in reason for reason in result.reasons)


def test_an_open_escalation_is_sticky_until_the_head_changes() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=3,
        state=state_module.HUMAN_REQUIRED,
    )
    unchanged = run(docs=documents("9", "10"), pull_requests=[pull_request()], cached=cached)
    assert unchanged.state.state == state_module.HUMAN_REQUIRED

    moved = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(head=OTHER_HEAD)],
        cached=cached,
        config=AutomationConfig(review=ReviewConfig(max_rounds=5)),
    )
    assert moved.state.state == state_module.WAITING_FOR_REVIEW


# --- merge ----------------------------------------------------------------


def test_a_clean_pull_request_waits_for_a_human_merge_by_default() -> None:
    cached = AutomationState(
        active_phase="9", github_pr=22, head_sha=HEAD, last_reviewed_sha=HEAD, review_round=1
    )
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()], cached=cached)
    assert result.state.state == state_module.READY_TO_MERGE
    assert result.state.next_action == state_module.ACTION_WAIT_FOR_HUMAN_MERGE


def test_auto_merge_is_opt_in() -> None:
    cached = AutomationState(
        active_phase="9", github_pr=22, head_sha=HEAD, last_reviewed_sha=HEAD, review_round=1
    )
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request()],
        cached=cached,
        config=AutomationConfig(merge=MergeConfig(automatic=True)),
    )
    assert result.state.next_action == state_module.ACTION_MERGE


def test_a_merge_that_happened_while_waiting_is_reconciled_not_failed() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        state=state_module.WAITING_FOR_MERGE,
    )
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(state="MERGED", merged=True)],
        cached=cached,
    )
    assert result.state.active_phase == "10"
    assert result.state.state == state_module.WAITING_FOR_IMPLEMENTATION


# --- restarts, duplicates, and anomalies ----------------------------------


def test_repeating_a_run_over_identical_inputs_is_idempotent() -> None:
    # A duplicate scheduled trigger or a redelivered webhook must not change
    # anything: the state machine reconciles, it does not count events.
    docs = documents("9", "10")
    first = run(docs=docs, pull_requests=[pull_request()])
    second = run(docs=docs, pull_requests=[pull_request()], cached=first.state)
    assert first.state.state == second.state.state
    assert first.state.next_action == second.state.next_action
    assert first.state.review_round == second.state.review_round


def test_a_restart_while_waiting_for_claude_quota_resumes_the_same_phase() -> None:
    cached = AutomationState(
        active_phase="9",
        github_pr=22,
        branch="migration/09-slug",
        head_sha=HEAD,
        last_reviewed_sha=HEAD,
        review_round=2,
        state=state_module.WAITING_FOR_CLAUDE_QUOTA,
        next_action=state_module.ACTION_FIX_FINDINGS,
    )
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()], cached=cached)
    assert result.state.active_phase == "9"
    assert result.state.github_pr == 22
    assert result.state.review_round == 2
    assert result.state.last_reviewed_sha == HEAD
    assert result.state.state != state_module.WAITING_FOR_IMPLEMENTATION


def test_a_pull_request_closed_without_merging_escalates() -> None:
    result = run(
        docs=documents("9", "10"),
        pull_requests=[pull_request(state="CLOSED")],
    )
    assert result.state.state == state_module.HUMAN_REQUIRED


def test_a_draft_pull_request_is_not_reviewed() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request(is_draft=True)])
    assert result.state.state == state_module.WAITING_FOR_IMPLEMENTATION


def test_an_open_pull_request_outranks_an_older_abandoned_one() -> None:
    result = run(
        docs=documents("9", "10"),
        pull_requests=[
            pull_request(number=25, state="CLOSED", head=OTHER_HEAD),
            pull_request(number=22),
        ],
    )
    assert result.state.github_pr == 22
    assert result.state.state == state_module.WAITING_FOR_REVIEW


def test_unrelated_branches_are_not_mistaken_for_migration_phases() -> None:
    unrelated = PullRequestSnapshot(
        number=21,
        state="MERGED",
        branch="task/nox-task-runner",
        head_sha=OTHER_HEAD,
        base="main",
        merged=True,
        ci_state=CI_PASS,
        review_decision="",
    )
    assert index_pull_requests_by_phase([unrelated], "migration/") == {}


def test_a_status_document_without_a_current_phase_escalates() -> None:
    result = run(docs=documents(None, None), pull_requests=[pull_request()])
    assert result.state.state == state_module.HUMAN_REQUIRED


def test_a_merged_phase_with_no_documented_successor_escalates() -> None:
    result = run(
        docs=documents("9", None),
        pull_requests=[pull_request(state="MERGED", merged=True)],
    )
    assert result.state.state == state_module.HUMAN_REQUIRED
    assert any("sorting" in reason for reason in result.reasons)


def test_disabling_the_automation_is_reported_on_every_state() -> None:
    result = run(docs=documents("9", "10"), pull_requests=[pull_request()])
    assert any("disabled" in reason for reason in result.reasons)
