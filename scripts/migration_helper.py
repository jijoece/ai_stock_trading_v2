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
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_RELATIVE_PATH = Path("docs/library-migration/STATUS.md")
MASTER_PLAN_RELATIVE_PATH = Path("docs/library-migration/MASTER_PLAN.md")
BRANCH_PREFIX = "migration/"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_HUMAN_ATTENTION = 2

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


def _run_gh(args: list[str], repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["gh", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report where the library migration stands, read-only."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("status", "continue-prompt"),
        help="`status` reports the position; `continue-prompt` prints a prompt for a fresh session",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="read only the migration documents; do not consult GitHub",
    )
    parser.add_argument("--json", action="store_true", help="emit the situation as JSON")
    args = parser.parse_args(argv)

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
