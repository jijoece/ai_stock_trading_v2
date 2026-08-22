"""Read-only GitHub inspection for the orchestrator.

Phase A only *reads*. There is no method here that opens, updates, comments on,
reviews, or merges a pull request. The `gh` CLI is used rather than a new HTTP
client so no dependency is added and the same code path works locally and in
GitHub Actions (`GITHUB_TOKEN`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


# Aggregated CI verdicts.
CI_PASS = "PASS"
CI_FAIL = "FAIL"
CI_PENDING = "PENDING"
CI_NONE = "NONE"

_FAILING_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_PASSING_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}
_FAILING_STATES = {"FAILURE", "ERROR"}
_PASSING_STATES = {"SUCCESS", "EXPECTED"}

_GH_PR_FIELDS = "number,state,headRefName,headRefOid,baseRefName,mergedAt,isDraft,reviewDecision,statusCheckRollup"

# `migration/09-...` -> "9"; `migration/08a-...` -> "8a". Leading zeros are
# stripped so the branch name and the MASTER_PLAN row identifier agree.
_BRANCH_PHASE = re.compile(r"^0*([0-9]+[a-z]?)(?:[-/]|$)")


class GitHubError(RuntimeError):
    """Raised when GitHub could not be inspected."""


@dataclass(frozen=True)
class PullRequestSnapshot:
    """The subset of a pull request's state the orchestrator reasons about."""

    number: int
    state: str
    branch: str
    head_sha: str
    base: str
    merged: bool
    ci_state: str
    review_decision: str
    is_draft: bool = False

    @property
    def is_open(self) -> bool:
        return self.state.upper() == "OPEN"

    @property
    def is_closed_unmerged(self) -> bool:
        return self.state.upper() == "CLOSED" and not self.merged


class GitHubReader(Protocol):
    """Read-only pull-request access."""

    def list_pull_requests(self) -> Sequence[PullRequestSnapshot]:
        ...


def phase_id_for_branch(branch: str, branch_prefix: str) -> str | None:
    """Derive a MASTER_PLAN phase identifier from a migration branch name."""
    if not branch.startswith(branch_prefix):
        return None
    match = _BRANCH_PHASE.match(branch[len(branch_prefix) :])
    return match.group(1).lower() if match else None


def aggregate_ci_state(rollup: Sequence[dict[str, Any]] | None) -> str:
    """Reduce GitHub's status-check rollup to a single verdict.

    Unknown values are treated as pending rather than passing: the orchestrator
    must never advance a PR because it failed to understand a check.
    """
    if not rollup:
        return CI_NONE

    pending = False
    for check in rollup:
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        state = str(check.get("state") or "").upper()

        if conclusion in _FAILING_CONCLUSIONS or state in _FAILING_STATES:
            return CI_FAIL
        if state:  # a commit status context
            if state not in _PASSING_STATES:
                pending = True
            continue
        if status and status != "COMPLETED":
            pending = True
            continue
        if conclusion not in _PASSING_CONCLUSIONS:
            pending = True

    return CI_PENDING if pending else CI_PASS


def snapshot_from_gh_json(payload: dict[str, Any]) -> PullRequestSnapshot:
    """Build a snapshot from one `gh pr list --json ...` record."""
    merged_at = payload.get("mergedAt")
    return PullRequestSnapshot(
        number=int(payload["number"]),
        state=str(payload.get("state") or "").upper(),
        branch=str(payload.get("headRefName") or ""),
        head_sha=str(payload.get("headRefOid") or ""),
        base=str(payload.get("baseRefName") or ""),
        merged=bool(merged_at) or str(payload.get("state") or "").upper() == "MERGED",
        ci_state=aggregate_ci_state(payload.get("statusCheckRollup")),
        review_decision=str(payload.get("reviewDecision") or ""),
        is_draft=bool(payload.get("isDraft")),
    )


class GhCliGitHub:
    """`GitHubReader` backed by the `gh` CLI."""

    def __init__(self, repo_root: str, limit: int = 30, timeout: int = 60) -> None:
        self._repo_root = repo_root
        self._limit = limit
        self._timeout = timeout

    def list_pull_requests(self) -> Sequence[PullRequestSnapshot]:
        if shutil.which("gh") is None:
            raise GitHubError("the `gh` CLI is not installed; cannot inspect GitHub")
        command = [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            str(self._limit),
            "--json",
            _GH_PR_FIELDS,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise GitHubError(f"`gh pr list` could not be executed: {error}") from error
        if completed.returncode != 0:
            raise GitHubError(f"`gh pr list` failed: {completed.stderr.strip()}")
        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as error:
            raise GitHubError(f"`gh pr list` returned invalid JSON: {error}") from error
        return tuple(snapshot_from_gh_json(record) for record in payload)


class StaticGitHub:
    """`GitHubReader` over a fixed set of snapshots (tests and `--offline`)."""

    def __init__(self, pull_requests: Sequence[PullRequestSnapshot] = ()) -> None:
        self._pull_requests = tuple(pull_requests)

    def list_pull_requests(self) -> Sequence[PullRequestSnapshot]:
        return self._pull_requests
