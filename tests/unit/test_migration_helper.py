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
    assert "create a fresh branch for MASTER_PLAN.md row 10 only" in prompt
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


def test_subprocess_is_only_ever_used_through_the_audited_gh_wrapper() -> None:
    """One choke point keeps the read-only audit above meaningful."""
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
