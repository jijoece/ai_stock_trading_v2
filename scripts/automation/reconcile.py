"""Reconcile migration documents, GitHub, and cached state into one decision.

GitHub is authoritative over the cached state file, and over a merge claim in
`STATUS.md`: a status document written inside a PR branch still says "NOT
MERGED" after that PR merges, so the document's *phase sequence* is trusted
while its *merge status* is not.

This module is pure. It performs no I/O, so every branch of the state machine
is testable without a network, a checkout, or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .config import AutomationConfig
from .github import (
    CI_FAIL,
    CI_NONE,
    CI_PASS,
    CI_PENDING,
    PullRequestSnapshot,
    phase_id_for_branch,
)
from .migration_docs import MigrationDocuments, PlanRow
from . import state as state_module
from .state import AutomationState


@dataclass(frozen=True)
class Reconciliation:
    """The reconciled view, plus why it was reached."""

    state: AutomationState
    documents: MigrationDocuments
    active_row: PlanRow | None
    next_phase_id: str | None
    pull_request: PullRequestSnapshot | None
    reasons: tuple[str, ...]

    @property
    def ci_state(self) -> str:
        return self.pull_request.ci_state if self.pull_request else CI_NONE


def index_pull_requests_by_phase(
    pull_requests: Sequence[PullRequestSnapshot], branch_prefix: str
) -> dict[str, PullRequestSnapshot]:
    """Map each migration phase to its single representative pull request.

    A phase can accumulate several branches over time (an abandoned attempt, a
    reopened branch). An open PR always represents the phase; otherwise the
    most recent merged PR does; otherwise the most recent PR of any kind.
    """
    by_phase: dict[str, PullRequestSnapshot] = {}
    for pull_request in pull_requests:
        phase_id = phase_id_for_branch(pull_request.branch, branch_prefix)
        if phase_id is None:
            continue
        incumbent = by_phase.get(phase_id)
        if incumbent is None or _outranks(pull_request, incumbent):
            by_phase[phase_id] = pull_request
    return by_phase


def _rank(pull_request: PullRequestSnapshot) -> int:
    if pull_request.is_open:
        return 2
    if pull_request.merged:
        return 1
    return 0


def _outranks(candidate: PullRequestSnapshot, incumbent: PullRequestSnapshot) -> bool:
    candidate_rank, incumbent_rank = _rank(candidate), _rank(incumbent)
    if candidate_rank != incumbent_rank:
        return candidate_rank > incumbent_rank
    return candidate.number > incumbent.number


def _resolve_active_phase(
    documents: MigrationDocuments,
    by_phase: dict[str, PullRequestSnapshot],
    reasons: list[str],
) -> tuple[str | None, bool]:
    """Return `(active_phase_id, escalate)` for the documented position.

    The roadmap is walked at most one step. `STATUS.md` is updated inside each
    migration PR, so at most one documented phase can be merged-but-unadvanced
    at any time; two in a row means the documents are stale in a way this
    process must not paper over.
    """
    documented = documents.current_phase_id
    current_pr = by_phase.get(documented) if documented else None

    if documented is None:
        reasons.append("STATUS.md declares no current phase")
        return None, True

    if current_pr is None or not current_pr.merged:
        return documented, False

    reasons.append(
        f"STATUS.md names PR {documented} current, but GitHub shows "
        f"#{current_pr.number} merged; advancing to the documented next phase"
    )
    successor = documents.successor_of(documented)
    if successor is None:
        final_row = documents.rows[-1] if documents.rows else None
        if final_row is not None and final_row.phase_id == documented:
            reasons.append("the merged phase is the final MASTER_PLAN row")
            return None, False
        reasons.append(
            f"STATUS.md declares no next phase after PR {documented}; "
            "the successor cannot be guessed by sorting phase identifiers"
        )
        return None, True

    successor_pr = by_phase.get(successor)
    if successor_pr is not None and successor_pr.merged:
        reasons.append(
            f"PR {documented} and its documented successor PR {successor} are both "
            f"merged on GitHub while STATUS.md still names PR {documented} current; "
            "the migration documents are further behind than one phase"
        )
        return successor, True
    return successor, False


def _phase_continues(
    cached: AutomationState, phase_id: str | None, pull_request_number: int | None
) -> bool:
    """Whether cached review bookkeeping still describes this phase and PR."""
    return cached.active_phase == phase_id and cached.github_pr == pull_request_number


def reconcile(
    *,
    documents: MigrationDocuments,
    pull_requests: Sequence[PullRequestSnapshot],
    cached: AutomationState,
    config: AutomationConfig,
    now: datetime | None = None,
) -> Reconciliation:
    """Derive current automation state from reality, not from the cache."""
    reasons: list[str] = []
    by_phase = index_pull_requests_by_phase(pull_requests, config.branch_prefix)
    active_phase, escalate = _resolve_active_phase(documents, by_phase, reasons)
    pull_request = by_phase.get(active_phase) if active_phase else None

    continues = _phase_continues(
        cached, active_phase, pull_request.number if pull_request else None
    )
    last_reviewed_sha = cached.last_reviewed_sha if continues else None
    review_round = cached.review_round if continues else 0
    if not continues and cached.active_phase is not None and cached.active_phase != active_phase:
        reasons.append(
            f"active phase moved from PR {cached.active_phase} to "
            f"PR {active_phase}; review bookkeeping reset"
        )

    if escalate:
        resolved_state = state_module.HUMAN_REQUIRED
        action = state_module.ACTION_ESCALATE
    elif active_phase is None:
        resolved_state = state_module.DONE
        action = state_module.ACTION_NONE
        reasons.append("no further MASTER_PLAN phase remains")
    else:
        resolved_state, action, verdict_reasons = _classify(
            pull_request=pull_request,
            cached=cached,
            continues=continues,
            last_reviewed_sha=last_reviewed_sha,
            review_round=review_round,
            config=config,
        )
        reasons.extend(verdict_reasons)

    if not config.enabled:
        reasons.append("automation is disabled (`enabled: false`); no action will be taken")

    row = documents.row(active_phase)
    resolved = AutomationState(
        active_phase=active_phase,
        github_pr=pull_request.number if pull_request else None,
        branch=pull_request.branch if pull_request else None,
        head_sha=pull_request.head_sha if pull_request else None,
        last_reviewed_sha=last_reviewed_sha,
        review_round=review_round,
        state=resolved_state,
        next_action=action,
        updated_at=(now or datetime.now(timezone.utc)).isoformat(),
        notes=tuple(reasons),
    )
    return Reconciliation(
        state=resolved,
        documents=documents,
        active_row=row,
        next_phase_id=documents.successor_of(active_phase),
        pull_request=pull_request,
        reasons=tuple(reasons),
    )


def _classify(
    *,
    pull_request: PullRequestSnapshot | None,
    cached: AutomationState,
    continues: bool,
    last_reviewed_sha: str | None,
    review_round: int,
    config: AutomationConfig,
) -> tuple[str, str, list[str]]:
    """Classify an active phase into a state and the action it implies."""
    reasons: list[str] = []

    if pull_request is None:
        reasons.append("no pull request exists for the active phase")
        return state_module.WAITING_FOR_IMPLEMENTATION, state_module.ACTION_IMPLEMENT, reasons

    if pull_request.merged:
        reasons.append(f"#{pull_request.number} is merged")
        return state_module.ADVANCE_PHASE, state_module.ACTION_ADVANCE_PHASE, reasons

    if pull_request.is_closed_unmerged:
        reasons.append(
            f"#{pull_request.number} was closed without merging; a human must decide "
            "whether the phase is abandoned or should be reopened"
        )
        return state_module.HUMAN_REQUIRED, state_module.ACTION_ESCALATE, reasons

    if pull_request.is_draft:
        reasons.append(f"#{pull_request.number} is still a draft")
        return state_module.WAITING_FOR_IMPLEMENTATION, state_module.ACTION_IMPLEMENT, reasons

    if pull_request.ci_state == CI_FAIL:
        reasons.append(f"CI is failing on {pull_request.head_sha[:7]}")
        return state_module.FIX_REQUIRED, state_module.ACTION_FIX_CI, reasons

    if pull_request.ci_state in (CI_PENDING, CI_NONE):
        reasons.append(
            "CI has not reported a complete result yet"
            if pull_request.ci_state == CI_PENDING
            else "no CI checks are reported for this head commit"
        )
        return state_module.WAITING_FOR_CI, state_module.ACTION_WAIT_FOR_CI, reasons

    # CI_PASS from here on.
    if continues and cached.state == state_module.HUMAN_REQUIRED and cached.head_sha == pull_request.head_sha:
        reasons.append("a human escalation is still open against this head commit")
        return state_module.HUMAN_REQUIRED, state_module.ACTION_ESCALATE, reasons

    if last_reviewed_sha != pull_request.head_sha:
        if review_round >= config.review.max_rounds:
            reasons.append(
                f"review round {review_round} has reached the configured maximum "
                f"of {config.review.max_rounds}"
            )
            return state_module.HUMAN_REQUIRED, state_module.ACTION_ESCALATE, reasons
        reasons.append(
            f"CI passes and {pull_request.head_sha[:7]} has not been reviewed yet"
        )
        return state_module.WAITING_FOR_REVIEW, state_module.ACTION_REVIEW, reasons

    reasons.append(f"{pull_request.head_sha[:7]} has already been reviewed; no re-review")

    if continues and cached.state == state_module.FIX_REQUIRED:
        reasons.append("review findings against this head commit are still outstanding")
        return state_module.FIX_REQUIRED, state_module.ACTION_FIX_FINDINGS, reasons

    if pull_request.review_decision.upper() == "CHANGES_REQUESTED":
        reasons.append("GitHub reports changes requested on this pull request")
        return state_module.FIX_REQUIRED, state_module.ACTION_FIX_FINDINGS, reasons

    if config.merge.automatic:
        return state_module.READY_TO_MERGE, state_module.ACTION_MERGE, reasons
    reasons.append("automatic merge is off; waiting for a human merge")
    return state_module.READY_TO_MERGE, state_module.ACTION_WAIT_FOR_HUMAN_MERGE, reasons
