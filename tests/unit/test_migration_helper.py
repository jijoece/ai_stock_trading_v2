"""Tests for the read-only migration continuation helper.

The helper reports where the migration stands and prints a prompt for a fresh
Claude Code session. It is stateless and must stay read-only, so these tests
cover both what it reports and what it must never do.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts import migration_helper as helper


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SOURCE = REPO_ROOT / "scripts" / "migration_helper.py"


STATUS_TEMPLATE = """# Migration Status

**Current phase: PR {current} — some title —
IMPLEMENTED, NOT MERGED** (branch `migration/0{current}-something`).

Row 8a is **not started**.

**Next phase: PR {following} — the phase after this one**
(`MASTER_PLAN.md` row {following}).
"""

MASTER_PLAN = """# Master plan

| PR | Title | Scope | Dependency on earlier PRs | Risk | Model |
|---|---|---|---|---|---|
| 8 | Removal decision | **MERGED** decision gate | PR 7 | High | Opus review |
| 8a | Legacy backtest run identity | Follow-up, not started | PR 8 | Medium | Sonnet |
| 9 | Normalization contract | **MERGED** contract work | PR 1 | High | Opus plan + Sonnet |
| 10 | Reconciliation parity tests | Prove reconciliation | PR 9 | High | Opus plan + Sonnet |
"""


def write_docs(root: Path, *, current: str = "9", following: str = "10") -> Path:
    docs = root / "docs" / "library-migration"
    docs.mkdir(parents=True)
    (docs / "STATUS.md").write_text(
        STATUS_TEMPLATE.format(current=current, following=following), encoding="utf-8"
    )
    (docs / "MASTER_PLAN.md").write_text(MASTER_PLAN, encoding="utf-8")
    return root


def pull_request(
    number: int,
    phase_id: str,
    *,
    is_open: bool = True,
    is_merged: bool = False,
    is_draft: bool = False,
    ci_state: str = helper.CI_PASSING,
    failing_checks: tuple[str, ...] = (),
) -> helper.PullRequest:
    return helper.PullRequest(
        number=number,
        phase_id=phase_id,
        branch=f"migration/{phase_id.zfill(2)}-branch",
        is_open=is_open,
        is_merged=is_merged,
        is_draft=is_draft,
        head_sha="a" * 40,
        ci_state=ci_state,
        failing_checks=failing_checks,
    )


# --------------------------------------------------------------------------
# 1-4. Document parsing and phase ordering
# --------------------------------------------------------------------------


def test_current_phase_is_parsed_across_a_wrapped_line() -> None:
    current, _ = helper.parse_status(STATUS_TEMPLATE.format(current="9", following="10"))
    assert current == "9"


def test_next_phase_is_parsed_across_a_wrapped_line() -> None:
    _, following = helper.parse_status(STATUS_TEMPLATE.format(current="9", following="10"))
    assert following == "10"


def test_master_plan_rows_are_looked_up_by_phase_id_in_document_order() -> None:
    rows = helper.parse_master_plan(MASTER_PLAN)
    assert [row.phase_id for row in rows] == ["8", "8a", "9", "10"]

    documents = helper.MigrationDocuments("9", "10", rows)
    row = documents.row("10")
    assert row is not None
    assert row.title == "Reconciliation parity tests"
    assert row.risk == "High"
    assert row.dependency == "PR 9"


def test_row_8a_is_never_selected_by_sorting_phase_identifiers() -> None:
    """`8a` sits between `8` and `9` in the table; the successor of 9 is 10."""
    documents = helper.MigrationDocuments("9", "10", helper.parse_master_plan(MASTER_PLAN))
    assert documents.successor_of("9") == "10"
    # 8a stays in the plan -- it is tracked, just not next.
    assert documents.row("8a") is not None
    # No edge other than the documented one is known.
    assert documents.successor_of("8") is None


def test_row_8a_is_selected_only_when_status_md_says_so(tmp_path: Path) -> None:
    write_docs(tmp_path, current="8", following="8a")
    documents = helper.read_migration_documents(tmp_path)
    # While PR 8 is open, PR 8 is the active phase and 8a is merely next.
    assert helper.build_situation(documents, ()).active_phase_id == "8"
    # Once PR 8 merges, STATUS.md's documented edge makes 8a active -- and this
    # is the only way 8a is ever selected.
    situation = helper.build_situation(
        documents, (pull_request(20, "8", is_open=False, is_merged=True),)
    )
    assert situation.active_phase_id == "8a"


def test_the_real_repository_documents_parse() -> None:
    documents = helper.read_migration_documents(REPO_ROOT)
    phase_ids = [row.phase_id for row in documents.rows]
    assert phase_ids.index("8a") < phase_ids.index("9") < phase_ids.index("10")
    assert documents.current_phase_id is not None


# --------------------------------------------------------------------------
# 5-8. GitHub reconciliation
# --------------------------------------------------------------------------


def test_an_existing_open_migration_pr_is_detected(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(documents, (pull_request(22, "9"),))
    assert situation.state == helper.CURRENT_PR_READY_FOR_REVIEW
    assert situation.pull_request is not None
    assert situation.pull_request.number == 22


def test_a_merged_current_phase_advances_to_the_documented_next_phase(tmp_path: Path) -> None:
    """STATUS.md says PR 9 is NOT MERGED because it was written in PR 9's branch."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", is_open=False, is_merged=True),)
    )
    assert situation.state == helper.NEXT_PHASE_READY
    assert situation.active_phase_id == "10"
    assert any("merged" in reason for reason in situation.reasons)


def test_documents_more_than_one_phase_behind_github_need_a_human(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents,
        (
            pull_request(22, "9", is_open=False, is_merged=True),
            pull_request(24, "10", is_open=False, is_merged=True),
        ),
    )
    assert situation.state == helper.HUMAN_ATTENTION_REQUIRED


def test_the_final_merged_phase_reports_the_migration_as_complete(tmp_path: Path) -> None:
    write_docs(tmp_path, current="10", following="10")
    docs = tmp_path / "docs" / "library-migration"
    docs.joinpath("STATUS.md").write_text(
        "**Current phase: PR 10 — the last one**\n", encoding="utf-8"
    )
    documents = helper.read_migration_documents(tmp_path)
    situation = helper.build_situation(
        documents, (pull_request(24, "10", is_open=False, is_merged=True),)
    )
    assert situation.state == helper.CURRENT_PR_MERGED
    assert situation.active_phase_id is None


@pytest.mark.parametrize(
    ("rollup", "expected", "failing"),
    [
        (None, helper.CI_NONE, ()),
        ([], helper.CI_NONE, ()),
        ([{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}], helper.CI_PASSING, ()),
        (
            [{"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"}],
            helper.CI_FAILING,
            ("tests",),
        ),
        ([{"name": "ci", "status": "IN_PROGRESS", "conclusion": None}], helper.CI_PENDING, ()),
        ([{"context": "legacy", "state": "SUCCESS"}], helper.CI_PASSING, ()),
        ([{"context": "legacy", "state": "PENDING"}], helper.CI_PENDING, ()),
        ([{"context": "legacy", "state": "ERROR"}], helper.CI_FAILING, ("legacy",)),
    ],
)
def test_ci_states_are_aggregated_for_display(
    rollup: list[dict] | None, expected: str, failing: tuple[str, ...]
) -> None:
    assert helper.aggregate_ci_state(rollup) == (expected, failing)


def test_a_failure_outranks_a_pending_check() -> None:
    state, failing = helper.aggregate_ci_state(
        [
            {"name": "slow", "status": "IN_PROGRESS", "conclusion": None},
            {"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"},
        ]
    )
    assert (state, failing) == (helper.CI_FAILING, ("tests",))


def test_an_unrecognised_conclusion_is_pending_never_passing() -> None:
    """A green report must never be produced from a check nobody understood."""
    state, _ = helper.aggregate_ci_state(
        [{"name": "novel", "status": "COMPLETED", "conclusion": "SOMETHING_NEW"}]
    )
    assert state == helper.CI_PENDING


def test_failing_ci_is_reported_with_the_failing_check_names(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents,
        (pull_request(22, "9", ci_state=helper.CI_FAILING, failing_checks=("main-tests",)),),
    )
    assert situation.state == helper.CURRENT_PR_CI_FAILING
    assert "main-tests" in helper.format_status(situation)


def test_pending_ci_is_reported_as_pending(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", ci_state=helper.CI_PENDING),)
    )
    assert situation.state == helper.CURRENT_PR_CI_PENDING


def test_a_draft_pr_is_reported_as_in_progress(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(documents, (pull_request(22, "9", is_draft=True),))
    assert situation.state == helper.CURRENT_PR_IN_PROGRESS


def test_two_open_prs_for_one_phase_require_human_attention(tmp_path: Path) -> None:
    """Never silently pick one; a duplicate PR is a human's problem."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9"), pull_request(25, "9"))
    )
    assert situation.state == helper.HUMAN_ATTENTION_REQUIRED
    assert situation.pull_request is None
    assert any("#22" in reason and "#25" in reason for reason in situation.reasons)


def test_a_closed_unmerged_pr_requires_human_attention(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", is_open=False, is_merged=False),)
    )
    assert situation.state == helper.HUMAN_ATTENTION_REQUIRED


def test_branches_map_to_phases_without_sorting_or_zero_padding() -> None:
    assert helper.phase_id_for_branch("migration/09-lumibot-normalization-contract") == "9"
    assert helper.phase_id_for_branch("migration/08a-legacy-run-identity") == "8a"
    assert helper.phase_id_for_branch("migration/10-reconciliation") == "10"
    assert helper.phase_id_for_branch("pre-step/06-import-boundary") is None
    assert helper.phase_id_for_branch("automation/phase-a-discovery") is None
    assert helper.phase_id_for_branch("main") is None


# --------------------------------------------------------------------------
# 9. Bounded history must not invent a missing PR
# --------------------------------------------------------------------------


def test_pull_request_history_is_paginated_to_completion() -> None:
    """A bounded query could drop an older PR and produce a false "no PR exists".

    That single error would send a fresh session off to open a duplicate PR, so
    the listing must page the REST endpoint to the end.
    """
    source = HELPER_SOURCE.read_text(encoding="utf-8")
    listing = source.split("def list_migration_pull_requests")[1].split("\ndef ")[0]
    assert "--paginate" in listing
    assert "--limit" not in listing


def test_every_page_of_a_paginated_listing_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        json.dumps([{"number": 18, "state": "closed", "merged_at": "x", "draft": False,
                     "head": "migration/06-adapter", "sha": "b" * 40}]),
        json.dumps([{"number": 22, "state": "open", "merged_at": None, "draft": False,
                     "head": "migration/09-contract", "sha": "c" * 40}]),
    ]
    monkeypatch.setattr(helper, "_run_gh", lambda args, root: "\n".join(pages))
    found = helper.list_migration_pull_requests(Path("/nowhere"))
    assert [pr.number for pr in found] == [18, 22]
    assert [pr.phase_id for pr in found] == ["6", "9"]
    assert found[0].is_merged and found[1].is_open


def test_a_github_failure_is_an_error_not_an_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting "no PR exists" because `gh` failed would be the worst outcome."""

    def explode(args: list[str], root: Path) -> str:
        raise helper.HelperError("gh exploded")

    monkeypatch.setattr(helper, "_run_gh", explode)
    with pytest.raises(helper.HelperError):
        helper.list_migration_pull_requests(Path("/nowhere"))


# --------------------------------------------------------------------------
# 10-13. The continuation prompt
# --------------------------------------------------------------------------


def test_the_prompt_names_the_active_phase(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    prompt = helper.format_continue_prompt(helper.build_situation(documents, ()))
    assert "PR 9" in prompt
    assert "Normalization contract" in prompt


def test_the_prompt_names_the_existing_pr_and_branch(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", ci_state=helper.CI_FAILING, failing_checks=("tests",)),)
    )
    prompt = helper.format_continue_prompt(situation)
    assert "#22" in prompt
    assert "migration/09-branch" in prompt
    assert "FAILING" in prompt
    assert "tests" in prompt


def test_the_prompt_says_to_fix_the_existing_pr_not_open_another(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    prompt = helper.format_continue_prompt(
        helper.build_situation(documents, (pull_request(22, "9"),))
    )
    assert "Do not open another PR for this phase." in prompt
    assert "create a fresh branch" not in prompt


def test_with_no_pr_the_prompt_prepares_exactly_the_documented_next_phase(
    tmp_path: Path,
) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", is_open=False, is_merged=True),)
    )
    prompt = helper.format_continue_prompt(situation)
    assert "MASTER_PLAN.md row 10 only" in prompt
    assert "implement only PR 10" in prompt
    assert "row 8a" in prompt  # still warned about, never selected


def test_the_prompt_always_carries_the_migration_and_safety_rules(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    for pull_requests in ((), (pull_request(22, "9"),)):
        prompt = helper.format_continue_prompt(
            helper.build_situation(documents, pull_requests)
        )
        assert "Never select a phase by numerically sorting identifiers." in prompt
        assert "Do not enable trading capabilities." in prompt
        assert "Do not begin another migration phase in this session." in prompt


def test_an_ambiguous_position_yields_no_actionable_prompt(tmp_path: Path) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9"), pull_request(25, "9"))
    )
    prompt = helper.format_continue_prompt(situation)
    assert "needs a human decision" in prompt
    assert "create a fresh branch" not in prompt


# --------------------------------------------------------------------------
# 14-15. The helper stays read-only and offline of any AI/broker
# --------------------------------------------------------------------------


def _helper_ast() -> ast.Module:
    return ast.parse(HELPER_SOURCE.read_text(encoding="utf-8"))


def test_every_github_invocation_is_a_read_only_subcommand() -> None:
    """No `gh` call may create, edit, merge, close, or comment on anything."""
    allowed_first_words = {"api", "pr"}
    allowed_pr_subcommands = {"view", "list", "diff"}
    forbidden = {"merge", "create", "edit", "close", "comment", "review", "ready", "reopen"}

    calls: list[list[str]] = []
    for node in ast.walk(_helper_ast()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_run_gh" or not node.args:
            continue
        argument = node.args[0]
        assert isinstance(argument, ast.List), "gh arguments must be a visible literal list"
        words = [
            element.value
            for element in argument.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        calls.append(words)

    assert calls, "expected at least one gh invocation to audit"
    for words in calls:
        assert words[0] in allowed_first_words, words
        if words[0] == "pr":
            assert words[1] in allowed_pr_subcommands, words
        assert not forbidden.intersection(words), words


def test_subprocess_is_only_ever_used_through_one_audited_low_level_wrapper() -> None:
    """One choke point (`_run_subprocess`) keeps the `gh`/`git`/`claude` audits meaningful."""
    runs = [
        node
        for node in ast.walk(_helper_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert len(runs) == 1


def test_every_git_invocation_is_a_read_only_verification_subcommand() -> None:
    """`_run_git` only ever verifies what Claude already did -- never commits, pushes,
    resets, or checks out anything itself."""
    allowed = {"rev-parse", "status", "ls-remote", "merge-base"}
    forbidden = {
        "commit", "push", "reset", "checkout", "clean", "branch",
        "rebase", "merge", "cherry-pick", "-D", "--force", "--hard",
    }

    calls: list[list[str]] = []
    for node in ast.walk(_helper_ast()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_run_git" or not node.args:
            continue
        argument = node.args[0]
        assert isinstance(argument, ast.List), "git arguments must be a visible literal list"
        words = [
            element.value
            for element in argument.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        calls.append(words)

    assert calls, "expected at least one git invocation to audit"
    for words in calls:
        assert words[0] in allowed, words
        assert not forbidden.intersection(words), words


def test_claude_is_only_invoked_through_the_audited_wrapper_without_bypassing_permissions() -> None:
    """The only command that may mutate anything must never skip permission checks."""
    source = HELPER_SOURCE.read_text(encoding="utf-8")
    assert "--dangerously-skip-permissions" not in source

    claude_argv_literals = [
        node
        for node in ast.walk(_helper_ast())
        if isinstance(node, ast.List)
        and node.elts
        and isinstance(node.elts[0], ast.Name)
        and node.elts[0].id == "CLAUDE_BINARY"
    ]
    assert len(claude_argv_literals) == 1, "expected exactly one literal argv naming the claude binary"


def test_the_helper_never_writes_to_disk() -> None:
    """Stateless by construction: nothing is cached between runs."""
    source = HELPER_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("write_text(", "open(", "mkdir(", "os.remove", "shutil."):
        assert forbidden not in source, forbidden


def test_no_ai_provider_broker_or_http_client_is_imported() -> None:
    forbidden = {
        "anthropic", "openai", "httpx", "requests", "urllib", "aiohttp",
        "alpaca", "lumibot", "trading_research", "paper_runtime", "backtest_runtime",
    }
    imported: set[str] = set()
    for node in ast.walk(_helper_ast()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not forbidden.intersection(imported), imported.intersection(forbidden)


def test_no_trading_gate_or_credential_name_appears_in_the_helper() -> None:
    """The helper must not touch any trading authorization surface."""
    source = HELPER_SOURCE.read_text(encoding="utf-8").upper()
    for phrase in (
        "ALPACA", "API_KEY", "SECRET_KEY", "ACCOUNT_FINGERPRINT",
        "SUBMIT_ORDER", "PAPER_TRADING_ENABLED", "LIVE",
    ):
        assert phrase not in source, phrase


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_offline_mode_reports_from_documents_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_docs(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline mode must not consult GitHub")

    monkeypatch.setattr(helper.subprocess, "run", fail)
    assert helper.main(["status", "--repo-root", str(tmp_path), "--offline"]) == helper.EXIT_OK
    assert "PR 9" in capsys.readouterr().out


def test_json_output_carries_the_discovered_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_docs(tmp_path)
    monkeypatch.setattr(
        helper, "list_migration_pull_requests", lambda root: (pull_request(22, "9"),)
    )
    monkeypatch.setattr(
        helper,
        "describe_pull_request",
        lambda number, root: {
            "headRefOid": "d" * 40,
            "isDraft": False,
            "statusCheckRollup": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        },
    )
    assert helper.main(["status", "--repo-root", str(tmp_path), "--json"]) == helper.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == helper.CURRENT_PR_READY_FOR_REVIEW
    assert payload["pull_request"] == 22
    assert payload["ci_state"] == helper.CI_PASSING
    assert payload["head_sha"] == "d" * 40


def test_an_unparseable_checkout_exits_with_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert helper.main(["status", "--repo-root", str(tmp_path)]) == helper.EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_an_ambiguous_position_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_docs(tmp_path)
    monkeypatch.setattr(
        helper,
        "list_migration_pull_requests",
        lambda root: (pull_request(22, "9"), pull_request(25, "9")),
    )
    assert (
        helper.main(["status", "--repo-root", str(tmp_path)]) == helper.EXIT_HUMAN_ATTENTION
    )
    assert helper.HUMAN_ATTENTION_REQUIRED in capsys.readouterr().out


def test_only_reporting_commands_are_accepted(tmp_path: Path) -> None:
    """There is no `implement`, `merge`, or `resume` -- the helper cannot act."""
    for command in ("implement", "merge", "resume", "advance"):
        with pytest.raises(SystemExit):
            helper.main([command, "--repo-root", str(tmp_path)])


# --------------------------------------------------------------------------
# Review follow-ups
# --------------------------------------------------------------------------


def test_offline_never_reports_a_pr_as_absent(tmp_path: Path) -> None:
    """`--offline` means "not looked up", which is not "none exists".

    Conflating the two told the operator to rebuild an already-merged phase.
    """
    write_docs(tmp_path)
    situation = helper.discover(tmp_path, offline=True)
    assert situation.state == helper.PR_STATE_UNVERIFIED
    assert "not checked" in helper.format_status(situation)


def test_offline_produces_no_actionable_continuation_prompt(tmp_path: Path) -> None:
    write_docs(tmp_path)
    prompt = helper.format_continue_prompt(helper.discover(tmp_path, offline=True))
    assert "Re-run without --offline" in prompt
    assert "create a fresh branch" not in prompt
    assert "No PR exists" not in prompt


def test_a_merged_phase_with_a_still_open_pr_does_not_advance(tmp_path: Path) -> None:
    """A merged PR plus an open follow-up means the phase is not finished."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents,
        (
            pull_request(22, "9", is_open=False, is_merged=True),
            pull_request(26, "9"),
        ),
    )
    assert situation.state == helper.HUMAN_ATTENTION_REQUIRED
    assert situation.active_phase_id != "10"
    assert any("#26" in reason for reason in situation.reasons)


def test_a_documented_successor_with_no_plan_row_needs_a_human(tmp_path: Path) -> None:
    """Advancing to a phase MASTER_PLAN.md never defined is not an advance."""
    write_docs(tmp_path, current="9", following="99")
    documents = helper.read_migration_documents(tmp_path)
    situation = helper.build_situation(
        documents, (pull_request(22, "9", is_open=False, is_merged=True),)
    )
    assert situation.state == helper.HUMAN_ATTENTION_REQUIRED
    assert "no such row" in " ".join(situation.reasons)
    # And it must not be mistaken for a finished migration.
    assert "every documented migration phase is merged" not in (
        helper.format_continue_prompt(situation).lower()
    )


@pytest.mark.parametrize(
    ("phase_id", "expected"),
    [("9", "migration/09-"), ("10", "migration/10-"), ("8a", "migration/08a-")],
)
def test_the_expected_branch_prefix_is_one_discovery_recognises(
    phase_id: str, expected: str
) -> None:
    assert helper.expected_branch_prefix(phase_id) == expected
    assert helper.phase_id_for_branch(f"{expected}some-description") == phase_id


def test_the_prompt_requires_a_branch_name_the_helper_can_find(tmp_path: Path) -> None:
    """A branch like `feature/10-x` would hide the PR from the next run."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9", is_open=False, is_merged=True),)
    )
    prompt = helper.format_continue_prompt(situation)
    assert "migration/10-" in prompt
    assert "required" in prompt


# --------------------------------------------------------------------------
# `run-claude` -- the one command that may invoke Claude for real
# --------------------------------------------------------------------------


def _open_pr_situation(tmp_path: Path, *, phase: str = "9") -> helper.Situation:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    pr = pull_request(22, phase, ci_state=helper.CI_PASSING)
    return helper.build_situation(documents, (pr,))


def _write_findings(
    tmp_path: Path,
    *,
    reviewed_head: str,
    status: str,
    finding_count: int,
    fix_commit: str | None = None,
) -> Path:
    lines = [
        f"Reviewed HEAD: {reviewed_head}",
        f"Review status: {status}",
        f"Finding count: {finding_count}",
    ]
    if fix_commit is not None:
        lines.append(f"Fix commit: {fix_commit}")
    path = tmp_path / "REVIEW_FINDINGS.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fail_if_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("must not be called in this scenario")


def test_run_claude_with_a_clean_findings_file_waits_for_human_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean open migration PR waits for merge; no Claude session is started."""
    situation = _open_pr_situation(tmp_path)
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(
        tmp_path,
        reviewed_head=situation.pull_request.head_sha,
        status=helper.REVIEW_STATUS_CLEAN,
        finding_count=0,
    )
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_OK


def test_run_claude_with_unresolved_findings_invokes_a_fix_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    findings_path = _write_findings(
        tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=2
    )
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    head_values = iter([pr.head_sha, pr.head_sha, "b" * 40])
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: next(head_values))
    monkeypatch.setattr(helper, "_git_tracked_worktree_is_clean", lambda root: True)
    monkeypatch.setattr(helper, "_git_remote_branch_sha", lambda branch, root: "b" * 40)
    monkeypatch.setattr(helper, "_git_is_ancestor", lambda candidate, of, root: True)

    invoked: list[list[str]] = []

    def fake_run_claude(argv: list[str], root: Path, *, timeout: int) -> helper.ClaudeResult:
        invoked.append(argv)
        findings_path.write_text(
            f"Reviewed HEAD: {pr.head_sha}\n"
            f"Review status: {helper.REVIEW_STATUS_FIXES_APPLIED}\n"
            "Finding count: 0\n"
            f"Fix commit: {'b' * 40}\n",
            encoding="utf-8",
        )
        return helper.ClaudeResult(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(helper, "_run_claude", fake_run_claude)

    assert helper.run_claude(tmp_path) == helper.EXIT_OK
    assert invoked, "expected exactly one Claude attempt"
    assert helper.CLAUDE_BINARY in invoked[0][0]


def test_run_claude_fix_session_uses_the_existing_pr_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails closed if the local checkout is not already on the PR's branch."""
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1)
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: "some-other-branch")
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_dry_run_invokes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1)
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: pr.head_sha)
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)
    monkeypatch.setattr(helper.subprocess, "run", _fail_if_called)

    assert helper.run_claude(tmp_path, dry_run=True) == helper.EXIT_OK
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert helper.CLAUDE_BINARY in out
    assert "Prompt:" in out


def test_run_claude_reports_a_failed_claude_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1)
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    head_values = iter([pr.head_sha, pr.head_sha])
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: next(head_values))
    monkeypatch.setattr(
        helper,
        "_run_claude",
        lambda argv, root, *, timeout: helper.ClaudeResult(
            returncode=1, stdout="", stderr="something went wrong"
        ),
    )

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_detects_quota_exhaustion_as_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1)
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    head_values = iter([pr.head_sha, pr.head_sha])
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: next(head_values))
    monkeypatch.setattr(
        helper,
        "_run_claude",
        lambda argv, root, *, timeout: helper.ClaudeResult(
            returncode=1, stdout="", stderr="Claude AI usage limit reached. Try again later."
        ),
    )

    assert helper.run_claude(tmp_path) == helper.EXIT_CLAUDE_QUOTA


def test_run_claude_fails_closed_when_findings_are_not_resolved_after_claude_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1)
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    head_values = iter([pr.head_sha, pr.head_sha, "b" * 40])
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: next(head_values))
    monkeypatch.setattr(helper, "_git_tracked_worktree_is_clean", lambda root: True)
    # Claude committed and pushed, but never updated REVIEW_FINDINGS.md.
    monkeypatch.setattr(
        helper,
        "_run_claude",
        lambda argv, root, *, timeout: helper.ClaudeResult(returncode=0, stdout="{}", stderr=""),
    )

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_fails_closed_when_fixes_are_committed_but_not_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    pr = situation.pull_request
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    findings_path = _write_findings(
        tmp_path, reviewed_head=pr.head_sha, status="NEEDS_FIXES", finding_count=1
    )
    monkeypatch.setattr(helper, "_git_current_branch", lambda root: pr.branch)
    head_values = iter([pr.head_sha, pr.head_sha, "b" * 40])
    monkeypatch.setattr(helper, "_git_head_sha", lambda root: next(head_values))
    monkeypatch.setattr(helper, "_git_tracked_worktree_is_clean", lambda root: True)
    monkeypatch.setattr(helper, "_git_is_ancestor", lambda candidate, of, root: True)
    # Remote is still at the old SHA -- local commits exist but were never pushed.
    monkeypatch.setattr(helper, "_git_remote_branch_sha", lambda branch, root: pr.head_sha)

    def fake_run_claude(argv: list[str], root: Path, *, timeout: int) -> helper.ClaudeResult:
        findings_path.write_text(
            f"Reviewed HEAD: {pr.head_sha}\n"
            f"Review status: {helper.REVIEW_STATUS_FIXES_APPLIED}\n"
            "Finding count: 0\n"
            f"Fix commit: {'b' * 40}\n",
            encoding="utf-8",
        )
        return helper.ClaudeResult(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(helper, "_run_claude", fake_run_claude)

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_fails_closed_on_a_malformed_findings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    (tmp_path / "REVIEW_FINDINGS.md").write_text(
        f"Reviewed HEAD: {situation.pull_request.head_sha}\nReview status: CLEAN\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_fails_closed_on_a_stale_findings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(
        tmp_path, reviewed_head="c" * 40, status="NEEDS_FIXES", finding_count=1
    )  # not the PR's actual HEAD ("a" * 40)
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_fails_closed_on_an_internally_inconsistent_findings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = _open_pr_situation(tmp_path)
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    _write_findings(
        tmp_path,
        reviewed_head=situation.pull_request.head_sha,
        status=helper.REVIEW_STATUS_CLEAN,
        finding_count=3,
    )
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_ERROR


def test_run_claude_advances_to_the_documented_next_phase_after_a_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A merged prerequisite advances run-claude to the documented next phase."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    merged = pull_request(22, "9", is_open=False, is_merged=True)
    situation = helper.build_situation(documents, (merged,))
    assert situation.state == helper.NEXT_PHASE_READY
    assert situation.active_phase_id == "10"

    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    monkeypatch.setattr(helper, "list_migration_pull_requests", lambda root: (merged,))
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path, dry_run=True) == helper.EXIT_OK
    out = capsys.readouterr().out
    assert "PR 10" in out
    assert "row 10" in out


def test_run_claude_never_selects_row_8a_by_sorting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """8a is only ever prepared when STATUS.md's documented edge names it next."""
    write_docs(tmp_path, current="8", following="8a")
    documents = helper.read_migration_documents(tmp_path)
    merged = pull_request(20, "8", is_open=False, is_merged=True)
    situation = helper.build_situation(documents, (merged,))
    assert situation.active_phase_id == "8a"

    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    monkeypatch.setattr(helper, "list_migration_pull_requests", lambda root: (merged,))
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path, dry_run=True) == helper.EXIT_OK
    assert "PR 8a" in capsys.readouterr().out


def test_run_claude_refuses_a_second_active_phase_when_another_pr_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No duplicate PR or second active migration phase: a stray open PR on a
    different phase must block starting a new one, not be silently ignored."""
    documents = helper.read_migration_documents(write_docs(tmp_path))
    merged = pull_request(22, "9", is_open=False, is_merged=True)
    situation = helper.build_situation(documents, (merged,))
    assert situation.state == helper.NEXT_PHASE_READY

    stray_open = pull_request(30, "8a")
    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    monkeypatch.setattr(
        helper, "list_migration_pull_requests", lambda root: (merged, stray_open)
    )
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path, dry_run=True) == helper.EXIT_HUMAN_ATTENTION


def test_run_claude_needing_human_attention_never_invokes_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = helper.read_migration_documents(write_docs(tmp_path))
    situation = helper.build_situation(
        documents, (pull_request(22, "9"), pull_request(25, "9"))
    )
    assert situation.needs_human

    monkeypatch.setattr(helper, "discover", lambda root, offline=False: situation)
    monkeypatch.setattr(helper, "_run_claude", _fail_if_called)

    assert helper.run_claude(tmp_path) == helper.EXIT_HUMAN_ATTENTION


def test_run_claude_never_shells_out_directly_bypassing_the_audited_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No real Claude, GitHub mutation, broker, or provider call happens in tests:
    even with `discover` left real, nothing reaches the raw subprocess boundary
    once `_run_gh`/`_run_git`/`_run_claude` are replaced."""
    write_docs(tmp_path)
    monkeypatch.setattr(helper.subprocess, "run", _fail_if_called)
    monkeypatch.setattr(
        helper, "list_migration_pull_requests", lambda root: (pull_request(22, "9"),)
    )
    monkeypatch.setattr(
        helper,
        "describe_pull_request",
        lambda number, root: {
            "headRefOid": "a" * 40,
            "isDraft": False,
            "statusCheckRollup": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        },
    )
    _write_findings(tmp_path, reviewed_head="a" * 40, status=helper.REVIEW_STATUS_CLEAN, finding_count=0)

    assert helper.run_claude(tmp_path) == helper.EXIT_OK


def test_run_claude_rejects_offline(tmp_path: Path) -> None:
    assert helper.main(["run-claude", "--repo-root", str(tmp_path), "--offline"]) == helper.EXIT_ERROR
