"""GitHub inspection: branch-to-phase mapping and CI verdict aggregation.

The orchestrator must never advance a pull request because it failed to
understand a check, so anything unrecognised aggregates to PENDING, not PASS.
"""

from __future__ import annotations

from scripts.automation.github import (
    CI_FAIL,
    CI_NONE,
    CI_PASS,
    CI_PENDING,
    aggregate_ci_state,
    phase_id_for_branch,
    snapshot_from_gh_json,
)


PREFIX = "migration/"


def test_branch_maps_to_the_master_plan_row_identifier() -> None:
    assert phase_id_for_branch("migration/09-lumibot-normalization-contract", PREFIX) == "9"
    assert phase_id_for_branch("migration/10-reconciliation-parity", PREFIX) == "10"
    assert phase_id_for_branch("migration/08a-legacy-backtest-identity", PREFIX) == "8a"


def test_non_migration_branches_are_ignored() -> None:
    assert phase_id_for_branch("task/nox-task-runner", PREFIX) is None
    assert phase_id_for_branch("main", PREFIX) is None
    assert phase_id_for_branch("automation/phase-a-discovery", PREFIX) is None
    assert phase_id_for_branch("migration/no-number-here", PREFIX) is None


def test_ci_passes_only_when_every_check_completed_successfully() -> None:
    rollup = [
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"},
        {"__typename": "StatusContext", "state": "SUCCESS"},
    ]
    assert aggregate_ci_state(rollup) == CI_PASS


def test_a_single_failure_fails_the_rollup() -> None:
    rollup = [
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]
    assert aggregate_ci_state(rollup) == CI_FAIL


def test_an_incomplete_check_is_pending() -> None:
    rollup = [
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None},
    ]
    assert aggregate_ci_state(rollup) == CI_PENDING


def test_a_failure_outranks_a_pending_check() -> None:
    rollup = [
        {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None},
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "TIMED_OUT"},
    ]
    assert aggregate_ci_state(rollup) == CI_FAIL


def test_an_unrecognised_conclusion_is_pending_not_passing() -> None:
    rollup = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SOMETHING_NEW"}]
    assert aggregate_ci_state(rollup) == CI_PENDING


def test_no_checks_reported() -> None:
    assert aggregate_ci_state([]) == CI_NONE
    assert aggregate_ci_state(None) == CI_NONE


def test_snapshot_reads_an_open_pull_request() -> None:
    snapshot = snapshot_from_gh_json(
        {
            "number": 22,
            "state": "OPEN",
            "headRefName": "migration/09-lumibot-normalization-contract",
            "headRefOid": "3193b0bcc97ca9b1e9878b6eb036f42fb21bce36",
            "baseRefName": "main",
            "mergedAt": None,
            "isDraft": False,
            "reviewDecision": "",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
        }
    )
    assert snapshot.number == 22
    assert snapshot.is_open and not snapshot.merged
    assert snapshot.ci_state == CI_PASS
    assert not snapshot.is_closed_unmerged


def test_snapshot_reads_a_merged_pull_request() -> None:
    snapshot = snapshot_from_gh_json(
        {
            "number": 20,
            "state": "MERGED",
            "headRefName": "migration/08-backtest-removal-decision",
            "headRefOid": "5b9e1e3",
            "baseRefName": "main",
            "mergedAt": "2026-08-02T23:03:19Z",
            "statusCheckRollup": [],
        }
    )
    assert snapshot.merged and not snapshot.is_open


def test_snapshot_reads_a_pull_request_closed_without_merging() -> None:
    snapshot = snapshot_from_gh_json(
        {
            "number": 14,
            "state": "CLOSED",
            "headRefName": "migration/09-abandoned-attempt",
            "headRefOid": "deadbee",
            "baseRefName": "main",
            "mergedAt": None,
            "statusCheckRollup": [],
        }
    )
    assert snapshot.is_closed_unmerged and not snapshot.merged
