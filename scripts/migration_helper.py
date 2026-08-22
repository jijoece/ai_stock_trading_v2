#!/usr/bin/env python3
"""Report where the library migration stands, and print a prompt to continue it.

This is a read-only reporting tool, not an orchestrator. It holds no state of
its own: every answer is derived from `STATUS.md`, `MASTER_PLAN.md`, and
GitHub on each run. It never mutates the repository or GitHub, and never calls
a model provider or a broker.

Two facts drive the whole file:

* `STATUS.md` is authoritative for *which* phase is current and which is next.
  Phase order is never derived by sorting identifiers -- `MASTER_PLAN.md` lists
  row `8a` between rows `8` and `9`, but the phase after PR 9 is PR 10. A sort
  would wrongly select `8a`.
* GitHub is authoritative for *merge status*. `STATUS.md` describes a phase as
  "NOT MERGED" because that sentence is written inside that phase's own branch;
  it stays stale until the next phase's PR rewrites it.

Usage:

    python scripts/migration_helper.py status
    python scripts/migration_helper.py continue-prompt
    python scripts/migration_helper.py run-claude --fix-current-pr-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_RELATIVE_PATH = Path("docs/library-migration/STATUS.md")
MASTER_PLAN_RELATIVE_PATH = Path("docs/library-migration/MASTER_PLAN.md")
BRANCH_PREFIX = "migration/"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_HUMAN_ATTENTION = 2
# `run-claude` only: Claude quota looks exhausted. Resumable -- re-run later,
# never converted into an ordinary execution failure.
EXIT_CLAUDE_QUOTA = 3

# Descriptive results, not workflow states: each says what was found, not what
# some future automation would do about it.
CURRENT_PR_IN_PROGRESS = "CURRENT_PR_IN_PROGRESS"
CURRENT_PR_CI_FAILING = "CURRENT_PR_CI_FAILING"
CURRENT_PR_CI_PENDING = "CURRENT_PR_CI_PENDING"
CURRENT_PR_READY_FOR_REVIEW = "CURRENT_PR_READY_FOR_REVIEW"
CURRENT_PR_MERGED = "CURRENT_PR_MERGED"
NEXT_PHASE_READY = "NEXT_PHASE_READY"
HUMAN_ATTENTION_REQUIRED = "HUMAN_ATTENTION_REQUIRED"
# Not an anomaly: `--offline` was asked for, so PR state was never looked up.
# Distinct from "no PR exists", which is a verified answer.
PR_STATE_UNVERIFIED = "PR_STATE_UNVERIFIED"

CI_PASSING = "PASSING"
CI_FAILING = "FAILING"
CI_PENDING = "PENDING"
CI_NONE = "NONE"
CI_UNKNOWN = "UNKNOWN"

# `REVIEW_FINDINGS.md` "Review status:" values the helper recognises. Any
# other text paired with `Finding count: 0` is a malformed/inconsistent file
# (fails closed); any other text paired with a positive count is treated as
# an ordinary unresolved-findings status, since the exact vocabulary an
# external reviewer uses for "not clean" is not this file's contract to fix.
REVIEW_STATUS_CLEAN = "CLEAN"
REVIEW_STATUS_FIXES_APPLIED = "FIXES_APPLIED_PENDING_REVIEW"

REVIEW_FINDINGS_RELATIVE_PATH = Path("REVIEW_FINDINGS.md")

CLAUDE_BINARY = "claude"
CLAUDE_TIMEOUT_SECONDS = 3600
CLAUDE_ALLOWED_TOOLS = (
    "Bash(.venv/bin/nox *)",
    "Bash(nox *)",
    "Bash(.venv/bin/python -m pytest *)",
    "Bash(pytest *)",
    "Bash(scripts/check_links.sh)",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git add *)",
    "Bash(git commit *)",
    "Bash(git rev-parse *)",
    "Bash(git push)",
    "Bash(git push *)",
)

# Heuristic only -- `claude`'s exact wording for quota exhaustion is not a
# stable contract, so this scans combined stdout/stderr rather than trusting
# a specific exit code.
_CLAUDE_QUOTA_MARKERS = (
    "usage limit",
    "quota",
    "rate limit",
    "weekly limit",
    "5-hour limit",
    "resets at",
)


class HelperError(RuntimeError):
    """Raised when the migration position cannot be determined."""


# --------------------------------------------------------------------------
# Migration documents
# --------------------------------------------------------------------------

# "PR 9", "PR 8a", "PR 10" -- an integer with an optional lowercase suffix.
_PHASE_ID = r"([0-9]+[a-z]?)"
_CURRENT_PHASE = re.compile(rf"Current phase:\s*PR\s+{_PHASE_ID}", re.IGNORECASE)
_NEXT_PHASE = re.compile(rf"Next phase:\s*PR\s+{_PHASE_ID}", re.IGNORECASE)
_ROW_PHASE_ID = re.compile(rf"^\**\s*(?:PR\s+)?{_PHASE_ID}\s*\**$", re.IGNORECASE)


@dataclass(frozen=True)
class PlanRow:
    """One `MASTER_PLAN.md` table row."""

    phase_id: str
    title: str
    dependency: str
    risk: str
    model: str
    order: int

    @property
    def label(self) -> str:
        return f"PR {self.phase_id}"


@dataclass(frozen=True)
class MigrationDocuments:
    """What `STATUS.md` and `MASTER_PLAN.md` say, with nothing inferred."""

    current_phase_id: str | None
    next_phase_id: str | None
    rows: tuple[PlanRow, ...]

    def row(self, phase_id: str | None) -> PlanRow | None:
        return next((row for row in self.rows if row.phase_id == phase_id), None)

    def successor_of(self, phase_id: str | None) -> str | None:
        """The phase after `phase_id` per `STATUS.md`, never by sorting.

        Only the documented current -> next edge is known. Asking about any
        other phase returns `None` rather than guessing, so no caller can walk
        the roadmap on its own authority.
        """
        if phase_id is None or phase_id != self.current_phase_id:
            return None
        return self.next_phase_id


def parse_status(text: str) -> tuple[str | None, str | None]:
    """Return `(current_phase_id, next_phase_id)` declared by `STATUS.md`.

    Only the header block is consulted; the "Completed work" sections below it
    are history. Whitespace is flattened first because those declarations wrap
    across lines.
    """
    flat = re.sub(r"\s+", " ", text)
    current = _CURRENT_PHASE.search(flat)
    following = _NEXT_PHASE.search(flat)
    return (
        current.group(1).lower() if current else None,
        following.group(1).lower() if following else None,
    )


def parse_master_plan(text: str) -> tuple[PlanRow, ...]:
    """Parse the `MASTER_PLAN.md` PR table, preserving document order.

    Rows whose first cell is not a phase identifier -- the header, the
    separator, and the un-numbered pre-step row -- are skipped.
    """
    rows: list[PlanRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 6:
            continue
        match = _ROW_PHASE_ID.match(cells[0])
        if match is None:
            continue
        rows.append(
            PlanRow(
                phase_id=match.group(1).lower(),
                title=cells[1],
                dependency=cells[3],
                risk=cells[4],
                model=cells[5],
                order=len(rows),
            )
        )
    if not rows:
        raise HelperError("MASTER_PLAN.md contains no parseable PR rows")
    return tuple(rows)


def read_migration_documents(repo_root: Path) -> MigrationDocuments:
    """Read `STATUS.md` and `MASTER_PLAN.md` from a repository checkout."""
    status_path = repo_root / STATUS_RELATIVE_PATH
    plan_path = repo_root / MASTER_PLAN_RELATIVE_PATH
    for path in (status_path, plan_path):
        if not path.exists():
            raise HelperError(f"missing migration document: {path}")

    current, following = parse_status(status_path.read_text(encoding="utf-8"))
    if current is None:
        raise HelperError(f"{status_path} declares no `Current phase:`")
    return MigrationDocuments(
        current_phase_id=current,
        next_phase_id=following,
        rows=parse_master_plan(plan_path.read_text(encoding="utf-8")),
    )


# --------------------------------------------------------------------------
# GitHub (read-only)
# --------------------------------------------------------------------------

_BRANCH_PHASE = re.compile(rf"^{re.escape(BRANCH_PREFIX)}0*{_PHASE_ID}(?:[-/]|$)")

_FAILING_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILING_STATES = {"FAILURE", "ERROR"}
_PASSING_STATES = {"SUCCESS"}


@dataclass(frozen=True)
class PullRequest:
    """A migration pull request as GitHub currently reports it."""

    number: int
    phase_id: str
    branch: str
    is_open: bool
    is_merged: bool
    is_draft: bool = False
    head_sha: str | None = None
    ci_state: str = CI_UNKNOWN
    failing_checks: tuple[str, ...] = ()


def expected_branch_prefix(phase_id: str) -> str:
    """The branch prefix `phase_id_for_branch` will recognise for this phase.

    Discovery finds a phase's PR by its branch name, so a prompt that says only
    "create a branch" invites a name this helper cannot see -- and an invisible
    PR is one the next run would offer to duplicate.
    """
    match = re.fullmatch(r"([0-9]+)([a-z]*)", phase_id)
    if match is None:  # pragma: no cover - phase ids come from the two regexes
        return f"{BRANCH_PREFIX}{phase_id}-"
    return f"{BRANCH_PREFIX}{match.group(1).zfill(2)}{match.group(2)}-"


def phase_id_for_branch(branch: str) -> str | None:
    """Map `migration/09-...` to `9` and `migration/08a-...` to `8a`.

    Leading zeros are stripped so branch spelling never becomes a second
    phase-identifier convention. Non-migration branches return `None`.
    """
    match = _BRANCH_PHASE.match(branch)
    return match.group(1).lower() if match else None


def aggregate_ci_state(rollup: list[dict] | None) -> tuple[str, tuple[str, ...]]:
    """Reduce a `statusCheckRollup` to one state plus the failing check names.

    Pessimistic by construction: anything unrecognised counts as pending, never
    as passing, so an unfamiliar check can never be reported as a green CI.
    """
    if not rollup:
        return CI_NONE, ()

    failing: list[str] = []
    pending = False
    passing = False
    for check in rollup:
        name = check.get("name") or check.get("context") or "unnamed check"
        if "conclusion" in check or "status" in check:
            conclusion = (check.get("conclusion") or "").upper()
            status = (check.get("status") or "").upper()
            if status and status != "COMPLETED":
                pending = True
            elif conclusion in _FAILING_CONCLUSIONS:
                failing.append(name)
            elif conclusion in _PASSING_CONCLUSIONS:
                passing = True
            else:
                pending = True
            continue
        state = (check.get("state") or "").upper()
        if state in _FAILING_STATES:
            failing.append(name)
        elif state in _PASSING_STATES:
            passing = True
        else:
            pending = True

    if failing:
        return CI_FAILING, tuple(failing)
    if pending:
        return CI_PENDING, ()
    return (CI_PASSING, ()) if passing else (CI_NONE, ())


def _run_subprocess(
    argv: list[str], repo_root: Path, *, timeout: int, stream_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """The one place this file shells out.

    `_run_gh`, `_run_git`, and `_run_claude` are the only callers, and each is
    itself audited (read-only `gh`/`git` subcommands; `claude` only from the
    explicit `run-claude` command). Nothing else in this file may call
    `subprocess.run` directly.
    """
    if stream_output:
        process_factory = subprocess.Popen
        process = process_factory(
            argv,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        output: list[str] = []

        def pump_output() -> None:
            for line in process.stdout:
                output.append(line)
                print(line, end="", flush=True)

        pump = threading.Thread(target=pump_output, daemon=True)
        pump.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pump.join()
            raise
        pump.join()
        return subprocess.CompletedProcess(
            argv,
            returncode,
            stdout="".join(output),
            stderr="",
        )

    return subprocess.run(
        argv,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _run_gh(args: list[str], repo_root: Path) -> str:
    try:
        completed = _run_subprocess(["gh", *args], repo_root, timeout=90)
    except FileNotFoundError as error:  # pragma: no cover - environment dependent
        raise HelperError("the `gh` CLI is required; re-run with --offline") from error
    except subprocess.TimeoutExpired as error:  # pragma: no cover - environment dependent
        raise HelperError("`gh` timed out") from error
    if completed.returncode != 0:
        raise HelperError(f"`gh {' '.join(args)}` failed: {completed.stderr.strip()}")
    return completed.stdout


def list_migration_pull_requests(repo_root: Path) -> tuple[PullRequest, ...]:
    """Every migration PR in the repository's history, oldest first.

    This paginates the REST endpoint to completion rather than taking the first
    page. A bounded query would let an older PR fall outside the window, and
    the helper would then wrongly report that a phase has no PR at all -- the
    one failure mode that would send a fresh session off to open a duplicate.
    """
    raw = _run_gh(
        [
            "api",
            "--paginate",
            "repos/{owner}/{repo}/pulls?state=all&per_page=100",
            "--jq",
            "[.[] | {number, state, draft, merged_at, head: .head.ref, sha: .head.sha}]",
        ],
        repo_root,
    )
    # `--paginate` with `--jq` emits one JSON array per page.
    payload: list[dict] = []
    for chunk in raw.splitlines():
        chunk = chunk.strip()
        if chunk:
            payload.extend(json.loads(chunk))

    pull_requests: list[PullRequest] = []
    for item in payload:
        branch = item.get("head") or ""
        phase_id = phase_id_for_branch(branch)
        if phase_id is None:
            continue
        pull_requests.append(
            PullRequest(
                number=int(item["number"]),
                phase_id=phase_id,
                branch=branch,
                is_open=(item.get("state") or "").lower() == "open",
                is_merged=item.get("merged_at") is not None,
                is_draft=bool(item.get("draft")),
                head_sha=item.get("sha"),
            )
        )
    return tuple(sorted(pull_requests, key=lambda pr: pr.number))


def describe_pull_request(number: int, repo_root: Path) -> dict:
    """Fetch the CI rollup for one PR. Called only for the PR being reported."""
    raw = _run_gh(
        ["pr", "view", str(number), "--json", "headRefOid,isDraft,statusCheckRollup"],
        repo_root,
    )
    return json.loads(raw)


def current_pull_request(repo_root: Path) -> PullRequest:
    """Return the open PR for the checked-out branch, or fail closed.

    This deliberately uses `gh pr view`'s current-branch lookup instead of the
    migration branch parser. It exists for external-review fix sessions on
    non-migration PRs and can never select or start a migration phase.
    """
    raw = _run_gh(
        [
            "pr",
            "view",
            "--json",
            "number,headRefName,headRefOid,state,isDraft",
        ],
        repo_root,
    )
    payload = json.loads(raw)
    branch = payload.get("headRefName") or ""
    head_sha = payload.get("headRefOid") or ""
    state = (payload.get("state") or "").upper()
    if not branch or not head_sha or not payload.get("number"):
        raise HelperError("GitHub returned an incomplete current-PR description")
    if state != "OPEN":
        raise HelperError(
            f"the pull request for `{branch}` is {state or 'in an unknown state'}, not open"
        )
    return PullRequest(
        number=int(payload["number"]),
        phase_id=phase_id_for_branch(branch) or "current",
        branch=branch,
        is_open=True,
        is_merged=False,
        is_draft=bool(payload.get("isDraft")),
        head_sha=head_sha,
    )


# --------------------------------------------------------------------------
# Situation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Situation:
    """What the repository and GitHub say, together."""

    documents: MigrationDocuments
    active_phase_id: str | None
    active_row: PlanRow | None
    next_phase_id: str | None
    pull_request: PullRequest | None
    state: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_human(self) -> bool:
        return self.state == HUMAN_ATTENTION_REQUIRED

    def to_dict(self) -> dict:
        pull_request = self.pull_request
        return {
            "state": self.state,
            "documented_current_phase": self.documents.current_phase_id,
            "documented_next_phase": self.documents.next_phase_id,
            "active_phase": self.active_phase_id,
            "active_phase_title": self.active_row.title if self.active_row else None,
            "next_phase": self.next_phase_id,
            "pull_request": pull_request.number if pull_request else None,
            "branch": pull_request.branch if pull_request else None,
            "head_sha": pull_request.head_sha if pull_request else None,
            "ci_state": pull_request.ci_state if pull_request else None,
            "failing_checks": list(pull_request.failing_checks) if pull_request else [],
            "reasons": list(self.reasons),
        }


def _by_phase(pull_requests: tuple[PullRequest, ...]) -> dict[str, list[PullRequest]]:
    grouped: dict[str, list[PullRequest]] = {}
    for pull_request in pull_requests:
        grouped.setdefault(pull_request.phase_id, []).append(pull_request)
    return grouped


def _resolve_active_phase(
    documents: MigrationDocuments,
    grouped: dict[str, list[PullRequest]],
    reasons: list[str],
) -> tuple[str | None, bool]:
    """Reconcile `STATUS.md`'s claim against GitHub, advancing at most one step.

    `STATUS.md` is updated inside each phase's own PR, so at most one phase can
    be merged-but-still-described-as-current. Anything beyond that is a real
    inconsistency for a human, not something to resolve by walking the plan.
    Returns `(active_phase_id, needs_human)`; a `None` phase with no escalation
    means the final documented phase is merged.
    """
    current = documents.current_phase_id
    if documents.row(current) is None:
        reasons.append(f"STATUS.md names PR {current}, which has no MASTER_PLAN.md row")
        return None, True

    for_current = grouped.get(current or "", [])
    merged = [pr for pr in for_current if pr.is_merged]
    if not merged:
        return current, False

    numbers = ", ".join(f"#{pr.number}" for pr in merged)

    # A merged PR does not finish the phase if another PR for it is still open
    # -- a follow-up fix, or a duplicate. Advancing would start PR N+1 while
    # PR N is still being worked, breaking the one-phase-at-a-time rule.
    still_open = [pr for pr in for_current if pr.is_open]
    if still_open:
        open_numbers = ", ".join(f"#{pr.number}" for pr in still_open)
        reasons.append(
            f"PR {current} is merged ({numbers}) but also still has an open PR "
            f"({open_numbers}); resolve that before the next phase starts"
        )
        return None, True

    following = documents.successor_of(current)
    if following is None:
        if documents.rows[-1].phase_id == current:
            reasons.append(f"PR {current} is merged ({numbers}) and is the final phase")
            return None, False
        reasons.append(
            f"PR {current} is merged ({numbers}) but STATUS.md documents no next phase"
        )
        return None, True

    if documents.row(following) is None:
        reasons.append(
            f"PR {current} is merged ({numbers}), but STATUS.md names PR {following} "
            "next and MASTER_PLAN.md has no such row"
        )
        return None, True

    if any(pr.is_merged for pr in grouped.get(following, [])):
        reasons.append(
            f"PR {current} and PR {following} are both merged; "
            "STATUS.md is more than one phase behind GitHub"
        )
        return None, True

    reasons.append(
        f"STATUS.md still calls PR {current} current, but GitHub shows it merged "
        f"({numbers}); advancing to the documented next phase, PR {following}"
    )
    return following, False


def _classify(pull_requests: list[PullRequest], reasons: list[str]) -> tuple[str, PullRequest | None]:
    open_prs = [pr for pr in pull_requests if pr.is_open]
    if len(open_prs) > 1:
        numbers = ", ".join(f"#{pr.number}" for pr in open_prs)
        reasons.append(f"more than one open PR targets this phase ({numbers})")
        return HUMAN_ATTENTION_REQUIRED, None

    if open_prs:
        pull_request = open_prs[0]
        if pull_request.is_draft:
            reasons.append(f"#{pull_request.number} is still a draft")
            return CURRENT_PR_IN_PROGRESS, pull_request
        if pull_request.ci_state == CI_FAILING:
            failing = ", ".join(pull_request.failing_checks) or "unnamed check"
            reasons.append(f"CI is failing on #{pull_request.number}: {failing}")
            return CURRENT_PR_CI_FAILING, pull_request
        if pull_request.ci_state in (CI_PENDING, CI_NONE, CI_UNKNOWN):
            reasons.append(f"CI has not reported a complete result on #{pull_request.number}")
            return CURRENT_PR_CI_PENDING, pull_request
        reasons.append(f"CI is green on #{pull_request.number}; it is ready for review")
        return CURRENT_PR_READY_FOR_REVIEW, pull_request

    if not pull_requests:
        return NEXT_PHASE_READY, None

    merged = [pr for pr in pull_requests if pr.is_merged]
    if merged:
        return CURRENT_PR_MERGED, merged[-1]

    numbers = ", ".join(f"#{pr.number}" for pr in pull_requests)
    reasons.append(f"this phase's PR was closed without merging ({numbers})")
    return HUMAN_ATTENTION_REQUIRED, None


def build_situation(
    documents: MigrationDocuments,
    pull_requests: tuple[PullRequest, ...],
) -> Situation:
    """Combine the documents with GitHub into one descriptive result."""
    reasons: list[str] = []
    grouped = _by_phase(pull_requests)
    active, needs_human = _resolve_active_phase(documents, grouped, reasons)

    if needs_human:
        return Situation(
            documents=documents,
            active_phase_id=active,
            active_row=documents.row(active),
            next_phase_id=None,
            pull_request=None,
            state=HUMAN_ATTENTION_REQUIRED,
            reasons=tuple(reasons),
        )

    if active is None:
        return Situation(
            documents=documents,
            active_phase_id=None,
            active_row=None,
            next_phase_id=None,
            pull_request=None,
            state=CURRENT_PR_MERGED,
            reasons=tuple(reasons),
        )

    state, pull_request = _classify(grouped.get(active, []), reasons)
    if state == NEXT_PHASE_READY:
        reasons.append(f"no PR exists yet for PR {active}")
    return Situation(
        documents=documents,
        active_phase_id=active,
        active_row=documents.row(active),
        next_phase_id=documents.successor_of(active),
        pull_request=pull_request,
        state=state,
        reasons=tuple(reasons),
    )


def discover(repo_root: Path, *, offline: bool = False) -> Situation:
    """Read the documents and GitHub, then classify. Mutates nothing."""
    documents = read_migration_documents(repo_root)
    if offline:
        # `pull_request=None` here means "not looked up", never "none exists".
        # PR_STATE_UNVERIFIED keeps the two apart, so no caller can read this
        # as a confirmed absence and go start a phase that is already merged.
        return Situation(
            documents=documents,
            active_phase_id=documents.current_phase_id,
            active_row=documents.row(documents.current_phase_id),
            next_phase_id=documents.next_phase_id,
            pull_request=None,
            state=PR_STATE_UNVERIFIED,
            reasons=(
                "offline: GitHub was not consulted, so whether this phase is "
                "merged or already has a PR is unknown",
            ),
        )

    pull_requests = list_migration_pull_requests(repo_root)
    situation = build_situation(documents, pull_requests)
    if situation.pull_request is not None and situation.pull_request.is_open:
        detail = describe_pull_request(situation.pull_request.number, repo_root)
        ci_state, failing = aggregate_ci_state(detail.get("statusCheckRollup"))
        enriched = PullRequest(
            number=situation.pull_request.number,
            phase_id=situation.pull_request.phase_id,
            branch=situation.pull_request.branch,
            is_open=True,
            is_merged=False,
            is_draft=bool(detail.get("isDraft")),
            head_sha=detail.get("headRefOid") or situation.pull_request.head_sha,
            ci_state=ci_state,
            failing_checks=failing,
        )
        # Re-classify now that CI is known; the first pass only located the PR.
        situation = build_situation(
            documents,
            tuple(
                enriched if pr.number == enriched.number else pr for pr in pull_requests
            ),
        )
    return situation


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _next_phase_text(situation: Situation) -> str:
    """Describe what follows the active phase, distinguishing the two `None`s.

    A phase reached by advancing past a merged one has no documented successor
    yet -- that sentence is written by its own PR -- which is a different fact
    from the roadmap having ended.
    """
    if situation.next_phase_id:
        return f"PR {situation.next_phase_id}"
    if situation.active_phase_id != situation.documents.current_phase_id:
        return "not documented yet (this phase's own STATUS.md update declares it)"
    return "none documented"


def format_status(situation: Situation) -> str:
    row = situation.active_row
    pull_request = situation.pull_request
    lines = [
        f"Situation:        {situation.state}",
        f"Active phase:     {row.label} — {row.title}" if row else "Active phase:     none",
    ]
    if row:
        lines.append(f"Risk / model:     {row.risk} / {row.model}")
    if pull_request:
        lines += [
            f"Pull request:     #{pull_request.number}",
            f"Branch:           {pull_request.branch}",
            f"HEAD:             {pull_request.head_sha or 'unknown'}",
            f"CI:               {pull_request.ci_state}",
        ]
        if pull_request.failing_checks:
            lines.append(f"Failing checks:   {', '.join(pull_request.failing_checks)}")
    elif situation.state == PR_STATE_UNVERIFIED:
        lines.append("Pull request:     not checked (--offline)")
    else:
        lines.append("Pull request:     none")
    lines.append(f"Next phase:       {_next_phase_text(situation)}")
    if situation.reasons:
        lines.append("Reasons:")
        lines += [f"  - {reason}" for reason in situation.reasons]
    return "\n".join(lines)


_SHARED_RULES = """Never select a phase by numerically sorting identifiers.
STATUS.md's documented current/next relationship wins. In particular, do not
select row 8a unless STATUS.md explicitly makes it next.

Do not make external broker/provider/model calls from application code.
Do not enable trading capabilities.
Do not begin another migration phase in this session."""


def format_continue_prompt(situation: Situation) -> str:
    """The prompt to paste into a fresh Claude Code session."""
    if situation.state == PR_STATE_UNVERIFIED:
        row = situation.active_row
        documented = f"PR {row.phase_id} — {row.title}" if row else "unknown"
        return (
            "No continuation prompt: GitHub was not consulted, so it is unknown "
            "whether this phase is already merged or already has an open PR.\n\n"
            f"STATUS.md documents the current phase as {documented}, but that "
            "wording goes stale once the phase merges.\n\n"
            "Re-run without --offline to get an actionable prompt."
        )

    if situation.needs_human:
        return (
            "The migration position is ambiguous and needs a human decision "
            "before a fresh session is useful.\n\n"
            + "\n".join(f"- {reason}" for reason in situation.reasons)
        )

    row = situation.active_row
    if row is None:
        return "Every documented migration phase is merged. There is nothing to continue."

    header = [
        "Continue the library migration from the repository's current state.",
        "",
        f"Discovered state: active phase PR {row.phase_id} — {row.title} "
        f"(risk {row.risk}, model {row.model}).",
    ]
    pull_request = situation.pull_request
    if pull_request is not None and pull_request.is_open:
        header.append(
            f"An open PR already exists: #{pull_request.number} on branch "
            f"`{pull_request.branch}`, HEAD {pull_request.head_sha or 'unknown'}, "
            f"CI {pull_request.ci_state}."
        )
        if pull_request.failing_checks:
            header.append(f"Failing checks: {', '.join(pull_request.failing_checks)}.")
    else:
        header.append("No PR exists for this phase yet.")
    header.append(f"Phase after this one: {_next_phase_text(situation)}.")

    body = [
        "",
        "Read only the bounded context you need:",
        "",
        "1. docs/library-migration/STATUS.md",
        f"2. row {row.phase_id} of docs/library-migration/MASTER_PLAN.md",
        "3. the relevant DECISIONS.md / ADR sections",
    ]
    if pull_request is not None and pull_request.is_open:
        body += [
            f"4. PR #{pull_request.number}: its diff, CI failures, and review comments",
            "5. only the source and tests relevant to this phase",
            "",
            f"Continue that exact branch and PR — `{pull_request.branch}`, "
            f"PR #{pull_request.number}. Do not open another PR for this phase.",
            "",
            "- address CI failures first",
            "- then address actionable review findings left on the PR by "
            "Codex/ChatGPT or a human reviewer",
            "- run focused tests first, then `nox -s ci` and `scripts/check_links.sh`",
            "- update the migration documentation if implementation decisions changed",
            "- commit and push to the same branch",
            "- do not start another phase",
        ]
    else:
        body += [
            "4. only the source and tests relevant to this phase",
            "",
            f"No PR exists for PR {row.phase_id}, so prepare exactly that phase:",
            "",
            f"- create a fresh branch named `{expected_branch_prefix(row.phase_id)}"
            "<short-description>` for MASTER_PLAN.md row "
            f"{row.phase_id} only; this prefix is required, because the migration"
            " helper finds a phase's PR by its branch name",
            f"  (this phase depends on: {row.dependency})",
            "- verify the issue still exists before editing anything",
            f"- implement only PR {row.phase_id}",
            "- run focused tests first, then `nox -s ci` and `scripts/check_links.sh`",
            "- update STATUS.md and the relevant migration records",
            "- open one PR and stop",
        ]
    return "\n".join([*header, *body, "", _SHARED_RULES])


# --------------------------------------------------------------------------
# git (read-only verification) and Claude (the one command that mutates)
# --------------------------------------------------------------------------

# Only ever used to verify what Claude already did in its own session --
# never to commit, push, or reset from this file.
_ALLOWED_GIT_SUBCOMMANDS = {"rev-parse", "status", "ls-remote", "merge-base"}


def _run_git(args: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    if not args or args[0] not in _ALLOWED_GIT_SUBCOMMANDS:
        raise HelperError(f"refusing to run an unaudited git subcommand: {args}")
    try:
        return _run_subprocess(["git", *args], repo_root, timeout=30)
    except FileNotFoundError as error:  # pragma: no cover - environment dependent
        raise HelperError("`git` is required") from error
    except subprocess.TimeoutExpired as error:  # pragma: no cover - environment dependent
        raise HelperError("`git` timed out") from error


def _sha_equal(left: str, right: str) -> bool:
    """Compare two SHAs where either side may be abbreviated."""
    left, right = left.lower(), right.lower()
    return bool(left) and bool(right) and (left == right or left.startswith(right) or right.startswith(left))


def _git_current_branch(repo_root: Path) -> str:
    completed = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if completed.returncode != 0:
        raise HelperError(f"`git rev-parse --abbrev-ref HEAD` failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _git_head_sha(repo_root: Path) -> str:
    completed = _run_git(["rev-parse", "HEAD"], repo_root)
    if completed.returncode != 0:
        raise HelperError(f"`git rev-parse HEAD` failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _git_tracked_worktree_is_clean(repo_root: Path) -> bool:
    completed = _run_git(["status", "--porcelain", "--untracked-files=no"], repo_root)
    if completed.returncode != 0:
        raise HelperError(f"`git status` failed: {completed.stderr.strip()}")
    return completed.stdout.strip() == ""


def _git_remote_branch_sha(branch: str, repo_root: Path) -> str | None:
    completed = _run_git(["ls-remote", "origin", f"refs/heads/{branch}"], repo_root)
    if completed.returncode != 0:
        raise HelperError(f"`git ls-remote origin {branch}` failed: {completed.stderr.strip()}")
    line = completed.stdout.strip()
    return line.split()[0] if line else None


def _git_is_ancestor(candidate_sha: str, of_sha: str, repo_root: Path) -> bool:
    completed = _run_git(["merge-base", "--is-ancestor", candidate_sha, of_sha], repo_root)
    return completed.returncode == 0


@dataclass(frozen=True)
class ClaudeResult:
    """The outcome of one `claude` CLI attempt."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


def build_claude_argv(prompt: str) -> list[str]:
    """The exact, fixed `claude` invocation. Never bypasses permission checks."""
    argv = [
        CLAUDE_BINARY,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        " ".join(CLAUDE_ALLOWED_TOOLS),
    ]
    if os.environ.get("MIGRATION_HELPER_STREAM_CLAUDE_OUTPUT") == "1":
        argv[4] = "stream-json"
        argv.append("--verbose")
    return argv


def _run_claude(argv: list[str], repo_root: Path, *, timeout: int) -> ClaudeResult:
    stream_output = os.environ.get("MIGRATION_HELPER_STREAM_CLAUDE_OUTPUT") == "1"
    try:
        completed = _run_subprocess(
            argv, repo_root, timeout=timeout, stream_output=stream_output
        )
    except FileNotFoundError as error:
        raise HelperError("the `claude` CLI is required") from error
    except subprocess.TimeoutExpired as error:
        raise HelperError("`claude` timed out") from error
    return ClaudeResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def looks_like_quota_exhaustion(result: ClaudeResult) -> bool:
    """Best-effort detection, kept separate from an ordinary execution failure."""
    lowered = result.combined_output.lower()
    return any(marker in lowered for marker in _CLAUDE_QUOTA_MARKERS)


# --------------------------------------------------------------------------
# REVIEW_FINDINGS.md -- the handoff artifact between a reviewer and Claude
# --------------------------------------------------------------------------

_REVIEWED_HEAD = re.compile(r"Reviewed HEAD:\s*`?([0-9a-fA-F]{7,40})`?")
_REVIEW_STATUS = re.compile(r"Review status:\s*([^\n]+)")
_FINDING_COUNT = re.compile(r"Finding count:\s*([0-9]+)")
_FIX_COMMIT = re.compile(r"Fix commit:\s*`?([0-9a-fA-F]{7,40})`?")


@dataclass(frozen=True)
class ReviewFindings:
    """What `REVIEW_FINDINGS.md` currently says, parsed and self-consistent.

    The helper never writes to this file -- it is the durable handoff
    artifact between an external reviewer and Claude's fix session.
    """

    reviewed_head: str
    status: str
    finding_count: int
    fix_commit: str | None

    @property
    def is_actionable(self) -> bool:
        return self.finding_count > 0


def parse_review_findings(text: str) -> ReviewFindings:
    """Parse and internally validate the metadata block.

    Fails closed: a missing field, an unparseable count, or a status/count
    combination that contradicts itself all raise `HelperError` rather than
    guessing which side to trust.
    """
    reviewed_head_match = _REVIEWED_HEAD.search(text)
    status_match = _REVIEW_STATUS.search(text)
    count_match = _FINDING_COUNT.search(text)
    if reviewed_head_match is None:
        raise HelperError("REVIEW_FINDINGS.md declares no `Reviewed HEAD:`")
    if status_match is None:
        raise HelperError("REVIEW_FINDINGS.md declares no `Review status:`")
    if count_match is None:
        raise HelperError("REVIEW_FINDINGS.md declares no `Finding count:`")

    status = status_match.group(1).strip()
    finding_count = int(count_match.group(1))
    fix_commit_match = _FIX_COMMIT.search(text)
    fix_commit = fix_commit_match.group(1) if fix_commit_match else None

    status_upper = status.upper()
    zero_count_statuses = {REVIEW_STATUS_CLEAN, REVIEW_STATUS_FIXES_APPLIED}
    if finding_count == 0 and status_upper not in zero_count_statuses:
        raise HelperError(
            "REVIEW_FINDINGS.md is internally inconsistent: `Finding count: 0` "
            f"but `Review status: {status}`"
        )
    if finding_count > 0 and status_upper in zero_count_statuses:
        raise HelperError(
            f"REVIEW_FINDINGS.md is internally inconsistent: `Finding count: {finding_count}` "
            f"but `Review status: {status}`"
        )
    if status_upper == REVIEW_STATUS_FIXES_APPLIED and fix_commit is None:
        raise HelperError(
            f"REVIEW_FINDINGS.md declares `Review status: {REVIEW_STATUS_FIXES_APPLIED}` "
            "but no `Fix commit:`"
        )

    return ReviewFindings(
        reviewed_head=reviewed_head_match.group(1),
        status=status,
        finding_count=finding_count,
        fix_commit=fix_commit,
    )


def read_review_findings(repo_root: Path) -> ReviewFindings:
    """Read and validate `REVIEW_FINDINGS.md`. Fails closed if it is missing."""
    path = repo_root / REVIEW_FINDINGS_RELATIVE_PATH
    if not path.exists():
        raise HelperError(f"missing review artifact: {path}")
    return parse_review_findings(path.read_text(encoding="utf-8"))


def check_review_findings_not_stale(findings: ReviewFindings, expected_head: str) -> None:
    """Fail closed if the review was not run against the PR's current HEAD."""
    if not expected_head or not _sha_equal(expected_head, findings.reviewed_head):
        raise HelperError(
            f"REVIEW_FINDINGS.md is stale: it reviewed {findings.reviewed_head}, but the "
            f"PR's current HEAD is {expected_head or 'unknown'}. Re-review at the current "
            "HEAD before fixing."
        )


def check_pending_review_fix_landed(
    findings: ReviewFindings, expected_head: str, repo_root: Path
) -> None:
    """Fail closed unless the recorded fixes really landed on the PR's current HEAD.

    `FIXES_APPLIED_PENDING_REVIEW` deliberately keeps the pre-fix
    `Reviewed HEAD` as the historical record of what was reviewed, so this
    state is *expected* to lag the PR's current HEAD -- the staleness rule
    cannot apply to it. The invariant that must hold instead is that the
    recorded `Fix commit` is a genuine post-review commit in the history the
    PR currently points at.
    """
    if not expected_head:
        raise HelperError(
            "REVIEW_FINDINGS.md records applied fixes, but the PR's current HEAD is "
            "unknown; refusing to judge whether those fixes are the ones on the PR."
        )
    fix_commit = findings.fix_commit
    if not fix_commit:
        raise HelperError(
            f"REVIEW_FINDINGS.md declares `Review status: {REVIEW_STATUS_FIXES_APPLIED}` "
            "but no `Fix commit:`"
        )
    if _sha_equal(fix_commit, findings.reviewed_head):
        raise HelperError(
            f"REVIEW_FINDINGS.md's `Fix commit: {fix_commit}` is the reviewed commit "
            f"({findings.reviewed_head}) itself, so no fix was committed after the review."
        )
    if not _git_is_ancestor(findings.reviewed_head, fix_commit, repo_root):
        raise HelperError(
            f"REVIEW_FINDINGS.md's `Fix commit: {fix_commit}` does not descend from the "
            f"reviewed commit {findings.reviewed_head}; the recorded fix does not belong "
            "to that review."
        )
    if not _git_is_ancestor(fix_commit, expected_head, repo_root):
        raise HelperError(
            f"REVIEW_FINDINGS.md's `Fix commit: {fix_commit}` is not in the history of "
            f"the PR's current HEAD ({expected_head}); the recorded fixes are not the "
            "ones this PR carries."
        )


def check_review_findings_apply_to_head(
    findings: ReviewFindings, expected_head: str, repo_root: Path
) -> None:
    """Hold the artifact to the invariant its own recorded status implies.

    Actionable findings and `CLEAN` describe the current HEAD, so they must
    have been produced at it. Pending review describes a HEAD that has since
    moved on by construction, so it is checked by ancestry instead.
    """
    if findings.status.upper() == REVIEW_STATUS_FIXES_APPLIED:
        check_pending_review_fix_landed(findings, expected_head, repo_root)
        return
    check_review_findings_not_stale(findings, expected_head)


# --------------------------------------------------------------------------
# `run-claude` -- the one command that may invoke Claude for real
# --------------------------------------------------------------------------


def build_fix_prompt(situation: Situation, findings: ReviewFindings) -> str:
    """A bounded prompt: only the active phase, PR, branch, SHA, review
    artifact, required documents, validation commands, and safety rules."""
    row = situation.active_row
    pull_request = situation.pull_request
    assert row is not None and pull_request is not None

    return "\n".join(
        [
            "Fix the review findings on the existing library-migration pull request. "
            "Do not open a new PR.",
            "",
            f"Active phase: PR {row.phase_id} — {row.title} "
            f"(risk {row.risk}, model {row.model}).",
            f"Continue this exact branch: `{pull_request.branch}` "
            f"(PR #{pull_request.number}), currently at {pull_request.head_sha}.",
            "",
            "Read only the bounded context you need:",
            "1. REVIEW_FINDINGS.md",
            "2. the current diff on this PR",
            f"3. row {row.phase_id} of docs/library-migration/MASTER_PLAN.md",
            "4. docs/library-migration/STATUS.md",
            "5. only the source and test files each finding names",
            "",
            "For every finding recorded in REVIEW_FINDINGS.md:",
            "- confirm it against the current code before changing anything",
            "- fix every finding that is still valid",
            "- add a regression test for each fix",
            "",
            "Then, in order:",
            "- run the focused tests for the files you changed",
            "- run `nox -s ci`",
            "- run `scripts/check_links.sh`",
            "- commit the fix and its tests",
            "- run `git rev-parse HEAD` to read that commit's SHA",
            "- update REVIEW_FINDINGS.md: set `Review status: "
            f"{REVIEW_STATUS_FIXES_APPLIED}`, `Finding count: 0`, and add a "
            "`Fix commit: <the SHA you just read>` line; keep the original "
            "`Reviewed HEAD:` line unchanged as the historical record of what was reviewed",
            "- commit that documentation update as a separate commit",
            f"- push both commits to `{pull_request.branch}`",
            "",
            _SHARED_RULES,
            "Do not open a replacement PR.",
            "Do not merge.",
            "Do not start another migration phase in this session.",
        ]
    )


def build_current_pr_fix_prompt(pull_request: PullRequest) -> str:
    """Build a bounded fix prompt for the open PR on the current branch."""
    return "\n".join(
        [
            "Fix the review findings on the existing current pull request. "
            "Do not open a new PR.",
            "",
            f"Continue this exact branch: `{pull_request.branch}` "
            f"(PR #{pull_request.number}), currently at {pull_request.head_sha}.",
            "",
            "Read only the bounded context you need:",
            "1. REVIEW_FINDINGS.md",
            "2. the current diff on this PR",
            "3. the applicable AGENTS.md files",
            "4. only the source, tests, configuration, ADRs, and current runbooks "
            "needed to verify each finding",
            "",
            "For every finding recorded in REVIEW_FINDINGS.md:",
            "- confirm it against the current code before changing anything",
            "- fix every finding that is still valid",
            "- add a regression test for each fix",
            "",
            "Then, in order:",
            "- run the focused tests for the files you changed",
            "- run the repository's canonical validation required by AGENTS.md",
            "- commit the fix and its tests",
            "- run `git rev-parse HEAD` to read that commit's SHA",
            "- update REVIEW_FINDINGS.md: set `Review status: "
            f"{REVIEW_STATUS_FIXES_APPLIED}`, `Finding count: 0`, and add a "
            "`Fix commit: <the SHA you just read>` line; keep the original "
            "`Reviewed HEAD:` line unchanged as the historical record of what was reviewed",
            "- commit that documentation update as a separate commit",
            f"- push both commits to `{pull_request.branch}`",
            "",
            _SHARED_RULES,
            "Do not open a replacement PR.",
            "Do not merge.",
        ]
    )


def _preflight_branch_and_head(repo_root: Path, pull_request: PullRequest) -> None:
    """Fail closed unless the local checkout is exactly the PR's branch and HEAD."""
    branch = _git_current_branch(repo_root)
    if branch != pull_request.branch:
        raise HelperError(
            f"local checkout is on `{branch}`, but PR #{pull_request.number} is on "
            f"`{pull_request.branch}`; check out that branch before running run-claude"
        )
    local_head = _git_head_sha(repo_root)
    if pull_request.head_sha and not _sha_equal(pull_request.head_sha, local_head):
        raise HelperError(
            f"local HEAD ({local_head}) does not match PR #{pull_request.number}'s HEAD "
            f"on GitHub ({pull_request.head_sha}); pull the latest branch state first"
        )


def _validate_fix_outcome(
    repo_root: Path, pull_request: PullRequest, *, pre_run_head: str
) -> tuple[bool, str]:
    """Independently re-verify what the Claude session claims to have done.

    Every check here is re-derived from git and the filesystem, never trusted
    from the Claude process's exit code or stdout.
    """
    branch = _git_current_branch(repo_root)
    if branch != pull_request.branch:
        return False, (
            f"error: the working tree ended up on `{branch}`, not `{pull_request.branch}`; "
            "the fix session must stay on the PR branch"
        )

    if not _git_tracked_worktree_is_clean(repo_root):
        return False, "error: tracked implementation changes remain uncommitted after the fix session"

    local_head = _git_head_sha(repo_root)
    if local_head == pre_run_head:
        return False, "error: no new commit was made; the fix session did not commit anything"

    try:
        findings = read_review_findings(repo_root)
    except HelperError as error:
        return False, f"error: REVIEW_FINDINGS.md is unreadable after the fix session: {error}"

    if findings.is_actionable:
        return False, (
            f"error: REVIEW_FINDINGS.md still reports {findings.finding_count} unresolved "
            "finding(s) after the fix session exited"
        )
    if findings.fix_commit is None:
        return False, "error: REVIEW_FINDINGS.md records no `Fix commit:` after the fix session"
    if _sha_equal(pre_run_head, findings.fix_commit):
        return False, (
            "error: REVIEW_FINDINGS.md's `Fix commit:` still points at the pre-fix HEAD, "
            "not a new commit"
        )
    if not _git_is_ancestor(findings.fix_commit, local_head, repo_root):
        return False, (
            f"error: REVIEW_FINDINGS.md's `Fix commit: {findings.fix_commit}` is not part "
            "of this branch's history"
        )

    remote_head = _git_remote_branch_sha(pull_request.branch, repo_root)
    if not remote_head or not _sha_equal(remote_head, local_head):
        return False, (
            f"error: local HEAD ({local_head}) has not been pushed to "
            f"`origin/{pull_request.branch}` (remote is at {remote_head or 'unknown'})"
        )

    return True, (
        f"Fix session validated: findings resolved and pushed as {local_head} on "
        f"`{pull_request.branch}` (PR #{pull_request.number})."
    )


def _run_prepared_fix_session(
    repo_root: Path,
    pull_request: PullRequest,
    findings: ReviewFindings,
    prompt: str,
    *,
    dry_run: bool,
) -> int:
    """Run one already-scoped review-fix session for an existing PR."""
    if not findings.is_actionable:
        status = findings.status.upper()
        if status == REVIEW_STATUS_CLEAN:
            print(
                f"REVIEW_FINDINGS.md for PR #{pull_request.number} is clean; "
                "waiting for a human to merge."
            )
            return EXIT_OK
        if status == REVIEW_STATUS_FIXES_APPLIED:
            print(
                f"REVIEW_FINDINGS.md for PR #{pull_request.number} records fixes applied "
                f"in {findings.fix_commit or 'an unrecorded commit'}; an external review of "
                "the current PR HEAD is still required before merge."
            )
            return EXIT_OK
        print(
            f"error: REVIEW_FINDINGS.md for PR #{pull_request.number} reports no findings "
            f"under the unrecognized status `{findings.status}`; refusing to judge merge "
            "readiness",
            file=sys.stderr,
        )
        return EXIT_HUMAN_ATTENTION

    _preflight_branch_and_head(repo_root, pull_request)
    argv = build_claude_argv(prompt)

    if dry_run:
        print("DRY RUN -- would run:")
        print(" ".join(shlex.quote(part) for part in argv))
        print()
        print("Prompt:")
        print(prompt)
        return EXIT_OK

    pre_run_head = _git_head_sha(repo_root)
    result = _run_claude(argv, repo_root, timeout=CLAUDE_TIMEOUT_SECONDS)

    if result.returncode != 0:
        if looks_like_quota_exhaustion(result):
            print(
                "Claude quota appears exhausted; this is resumable -- re-run `run-claude` "
                "once quota is available.",
                file=sys.stderr,
            )
            return EXIT_CLAUDE_QUOTA
        print(f"error: the Claude fix session failed (exit {result.returncode})", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return EXIT_ERROR

    ok, explanation = _validate_fix_outcome(repo_root, pull_request, pre_run_head=pre_run_head)
    print(explanation)
    return EXIT_OK if ok else EXIT_ERROR


def _run_fix_session(repo_root: Path, situation: Situation, *, dry_run: bool) -> int:
    pull_request = situation.pull_request
    assert pull_request is not None

    findings = read_review_findings(repo_root)
    check_review_findings_apply_to_head(findings, pull_request.head_sha or "", repo_root)
    return _run_prepared_fix_session(
        repo_root,
        pull_request,
        findings,
        build_fix_prompt(situation, findings),
        dry_run=dry_run,
    )


def _run_current_pr_fix_session(repo_root: Path, *, dry_run: bool) -> int:
    pull_request = current_pull_request(repo_root)
    findings = read_review_findings(repo_root)
    check_review_findings_apply_to_head(findings, pull_request.head_sha or "", repo_root)
    return _run_prepared_fix_session(
        repo_root,
        pull_request,
        findings,
        build_current_pr_fix_prompt(pull_request),
        dry_run=dry_run,
    )


def _run_new_phase_session(
    repo_root: Path,
    situation: Situation,
    all_pull_requests: tuple[PullRequest, ...],
    *,
    dry_run: bool,
) -> int:
    row = situation.active_row
    assert row is not None

    still_open = [pr for pr in all_pull_requests if pr.is_open]
    same_phase = [pr for pr in still_open if pr.phase_id == situation.active_phase_id]
    if same_phase:
        numbers = ", ".join(f"#{pr.number}" for pr in same_phase)
        print(
            f"error: refusing to start PR {row.phase_id}: a PR for that same phase "
            f"({numbers}) is already open -- another session created it after this one "
            "discovered the phase. Re-run discovery and continue that PR instead of "
            "starting a duplicate.",
            file=sys.stderr,
        )
        return EXIT_HUMAN_ATTENTION
    if still_open:
        numbers = ", ".join(f"#{pr.number} (PR {pr.phase_id})" for pr in still_open)
        print(
            f"error: refusing to start PR {row.phase_id}: another migration PR is still "
            f"open on a different phase ({numbers})",
            file=sys.stderr,
        )
        return EXIT_HUMAN_ATTENTION

    prompt = format_continue_prompt(situation)
    argv = build_claude_argv(prompt)

    if dry_run:
        print("DRY RUN -- would run:")
        print(" ".join(shlex.quote(part) for part in argv))
        print()
        print("Prompt:")
        print(prompt)
        return EXIT_OK

    result = _run_claude(argv, repo_root, timeout=CLAUDE_TIMEOUT_SECONDS)

    if result.returncode != 0:
        if looks_like_quota_exhaustion(result):
            print(
                "Claude quota appears exhausted; this is resumable -- re-run `run-claude` "
                "once quota is available.",
                file=sys.stderr,
            )
            return EXIT_CLAUDE_QUOTA
        print(
            f"error: the Claude session for PR {row.phase_id} failed (exit {result.returncode})",
            file=sys.stderr,
        )
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return EXIT_ERROR

    print(f"Claude session for PR {row.phase_id} exited cleanly; re-run `status` to see the result.")
    return EXIT_OK


def run_claude(
    repo_root: Path, *, dry_run: bool = False, fix_current_pr_only: bool = False
) -> int:
    """The one command that may invoke `claude` for real.

    By default, discovery uses the same read-only logic `status` uses. From
    there this either fixes an open migration PR's recorded findings or starts
    exactly the documented next phase -- never both, and never a phase
    `STATUS.md` did not name. `fix_current_pr_only` instead resolves only the
    PR for the checked-out branch and can never start a migration phase.
    """
    if fix_current_pr_only:
        try:
            return _run_current_pr_fix_session(repo_root, dry_run=dry_run)
        except (HelperError, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_ERROR

    try:
        situation = discover(repo_root, offline=False)
    except HelperError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if situation.needs_human:
        print(format_status(situation))
        return EXIT_HUMAN_ATTENTION

    pull_request = situation.pull_request
    if pull_request is not None and pull_request.is_open:
        try:
            return _run_fix_session(repo_root, situation, dry_run=dry_run)
        except HelperError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_ERROR

    if situation.state == NEXT_PHASE_READY:
        try:
            all_pull_requests = list_migration_pull_requests(repo_root)
            return _run_new_phase_session(repo_root, situation, all_pull_requests, dry_run=dry_run)
        except HelperError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_ERROR

    print(format_status(situation))
    print("Nothing for run-claude to do.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report where the library migration stands, and optionally continue it."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("status", "continue-prompt", "run-claude"),
        help=(
            "`status` reports the position; `continue-prompt` prints a prompt for a fresh "
            "session; `run-claude` -- the only command that invokes Claude for real -- fixes "
            "recorded review findings or starts the documented next phase"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="status/continue-prompt only: read only the migration documents; do not consult GitHub",
    )
    parser.add_argument(
        "--json", action="store_true", help="status only: emit the situation as JSON"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run-claude only: print the proposed Claude command and prompt without invoking it",
    )
    parser.add_argument(
        "--fix-current-pr-only",
        action="store_true",
        help=(
            "run-claude only: fix REVIEW_FINDINGS.md on the open PR for the checked-out "
            "branch; never discover or start a migration phase"
        ),
    )
    args = parser.parse_args(argv)

    if args.command == "run-claude":
        if args.offline:
            print("error: run-claude requires GitHub; --offline is not supported", file=sys.stderr)
            return EXIT_ERROR
        return run_claude(
            args.repo_root.resolve(),
            dry_run=args.dry_run,
            fix_current_pr_only=args.fix_current_pr_only,
        )

    if args.fix_current_pr_only:
        print("error: --fix-current-pr-only requires run-claude", file=sys.stderr)
        return EXIT_ERROR

    try:
        situation = discover(args.repo_root.resolve(), offline=args.offline)
    except HelperError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(situation.to_dict(), indent=2, sort_keys=True))
    elif args.command == "continue-prompt":
        print(format_continue_prompt(situation))
    else:
        print(format_status(situation))
    return EXIT_HUMAN_ATTENTION if situation.needs_human else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
