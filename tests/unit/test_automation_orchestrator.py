"""The orchestrator entry point, its dry-run guarantee, and its safety boundary.

Automation Phase A is discovery only. These tests pin that it stays that way:
no GitHub mutation, no Claude call, no OpenAI call, and no path by which the
automation could reach the trading application's broker, market-data, model, or
scheduler surfaces.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Sequence

import pytest

from scripts.automation.config import AutomationConfig, load_config
from scripts.automation.github import CI_PASS, PullRequestSnapshot
from scripts.automation import state as state_module
from scripts.automation.orchestrator import (
    EXIT_ERROR,
    EXIT_HUMAN_REQUIRED,
    EXIT_OK,
    exit_code_for,
    main,
    run_discovery,
)
from scripts.automation.state import load_state


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOMATION_PACKAGE = REPO_ROOT / "scripts" / "automation"
AGENT_DIRECTORY = REPO_ROOT / ".agent"

PLAN = """# Master Plan

| PR | Title | Scope | Dependency | Risk | Model |
|---|---|---|---|---|---|
| 9 | Normalization contract | **IMPLEMENTED** | PR 1 | High | Opus plan + Sonnet |
| 10 | Reconciliation parity tests | Prove parity | PR 9 | High | Opus plan + Sonnet |
"""

STATUS = """# Migration Status

**Current phase: PR 9 — the LumiBot runtime normalization contract —
IMPLEMENTED, NOT MERGED** (branch `migration/09-lumibot-normalization-contract`).

**Next phase: PR 10 — broker-to-`paper_books` reconciliation parity tests**
(`MASTER_PLAN.md` row 10), which depends on PR 9.
"""


class RecordingGitHub:
    """A reader that records every call, so mutation attempts are visible."""

    def __init__(self, pull_requests: Sequence[PullRequestSnapshot] = ()) -> None:
        self.pull_requests = tuple(pull_requests)
        self.calls: list[str] = []

    def list_pull_requests(self) -> Sequence[PullRequestSnapshot]:
        self.calls.append("list_pull_requests")
        return self.pull_requests


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    migration = tmp_path / "docs" / "library-migration"
    migration.mkdir(parents=True)
    (migration / "MASTER_PLAN.md").write_text(PLAN, encoding="utf-8")
    (migration / "STATUS.md").write_text(STATUS, encoding="utf-8")
    return tmp_path


def open_pull_request() -> PullRequestSnapshot:
    return PullRequestSnapshot(
        number=22,
        state="OPEN",
        branch="migration/09-lumibot-normalization-contract",
        head_sha="3193b0bcc97ca9b1e9878b6eb036f42fb21bce36",
        base="main",
        merged=False,
        ci_state=CI_PASS,
        review_decision="",
    )


def test_discovery_reads_only(checkout: Path) -> None:
    github = RecordingGitHub([open_pull_request()])
    result = run_discovery(
        repo_root=checkout,
        config=AutomationConfig(),
        github=github,
        state_path=checkout / ".agent" / "state.json",
    )
    assert github.calls == ["list_pull_requests"]
    assert result.state.active_phase == "9"
    assert result.next_phase_id == "10"


def test_dry_run_writes_no_state_file(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_path = checkout / ".agent" / "state.json"
    exit_code = main(
        [
            "reconcile",
            "--dry-run",
            "--offline",
            "--repo-root",
            str(checkout),
            "--state",
            str(state_path),
        ]
    )
    assert exit_code == 0
    assert not state_path.exists(), "a dry run must not persist state"
    assert "External AI call:       none" in capsys.readouterr().out


def test_status_never_writes_state_even_without_dry_run(checkout: Path) -> None:
    state_path = checkout / ".agent" / "state.json"
    assert main(["status", "--offline", "--repo-root", str(checkout), "--state", str(state_path)]) == 0
    assert not state_path.exists()


def test_reconcile_persists_state_for_the_next_process(checkout: Path) -> None:
    state_path = checkout / ".agent" / "state.json"
    assert (
        main(["reconcile", "--offline", "--repo-root", str(checkout), "--state", str(state_path)])
        == 0
    )
    persisted = load_state(state_path)
    assert persisted.active_phase == "9"
    assert persisted.updated_at is not None


def test_json_output_is_machine_readable(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["status", "--offline", "--json", "--repo-root", str(checkout)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["active_phase"] == "9"
    assert payload["state"] == "WAITING_FOR_IMPLEMENTATION"


def test_a_status_document_without_a_current_phase_is_an_error(checkout: Path) -> None:
    (checkout / "docs" / "library-migration" / "STATUS.md").write_text(
        "# Migration Status\n\nNo phase declared here.\n", encoding="utf-8"
    )
    assert main(["status", "--offline", "--repo-root", str(checkout)]) == EXIT_ERROR


def test_an_escalation_exits_non_zero_so_a_scheduled_run_surfaces_it() -> None:
    assert exit_code_for(state_module.HUMAN_REQUIRED) == EXIT_HUMAN_REQUIRED
    for state in state_module.ALL_STATES:
        if state != state_module.HUMAN_REQUIRED:
            assert exit_code_for(state) == EXIT_OK


def test_phase_a_rejects_commands_it_cannot_perform(checkout: Path) -> None:
    # `resume`, `retry`, `pause` and friends arrive in Automation Phase F. They
    # must be refused, not accepted and silently ignored.
    with pytest.raises(SystemExit):
        main(["resume", "--offline", "--repo-root", str(checkout)])


# --- committed defaults ---------------------------------------------------


def test_the_committed_configuration_is_disabled() -> None:
    config = load_config(AGENT_DIRECTORY / "config.yaml")
    assert config.enabled is False, "merging the infrastructure must not start the automation"
    assert config.merge.automatic is False, "auto-merge must be opt-in"


def test_the_default_configuration_is_disabled_even_when_the_file_is_absent(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.yaml").enabled is False


# --- safety boundary ------------------------------------------------------


def _automation_sources() -> list[Path]:
    return sorted(AUTOMATION_PACKAGE.glob("*.py"))


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_the_automation_never_imports_the_trading_application() -> None:
    forbidden_roots = ("trading_research", "paper_runtime", "backtest_runtime", "lumibot", "alpaca")
    for source in _automation_sources():
        modules = _imported_modules(ast.parse(source.read_text(encoding="utf-8")))
        offending = {
            module
            for module in modules
            if module.split(".")[0] in forbidden_roots
        }
        assert not offending, f"{source.name} imports trading application code: {offending}"


def test_the_automation_never_enables_an_opt_in_trading_gate() -> None:
    # These are the environment gates that turn on credentialed, networked,
    # broker, model, and scheduler behaviour. The automation must not name them.
    forbidden = (
        "RUN_PAPER_BROKER_TESTS",
        "RUN_EXTERNAL_PAPER_BROKER_TESTS",
        "RUN_CLAUDE_RESEARCH_TESTS",
        "RUN_MARKET_DATA_TESTS",
        "RUN_NEWS_API_TESTS",
        "RUN_SEC_API_TESTS",
        "RUN_REAL_RESEARCH_CYCLE",
        "RUN_REAL_SHADOW_CYCLE",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ANTHROPIC_API_KEY",
    )
    surfaces = _automation_sources() + [
        path for path in AGENT_DIRECTORY.rglob("*") if path.is_file()
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        for gate in forbidden:
            assert gate not in text, f"{path.name} references the trading gate {gate}"


def test_every_subprocess_call_is_a_read_only_gh_command() -> None:
    # The only external process Phase A may start is a `gh` read. A mutating
    # subcommand here would be a capability the state machine cannot undo.
    read_only_gh_subcommands = {("pr", "list"), ("pr", "view")}
    found = 0
    for source in _automation_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"run", "call", "check_call", "check_output", "Popen"}
            ):
                continue
            found += 1
            command = _literal_command(source, node)
            assert command[0] == "gh", f"{source.name} starts {command[0]!r}, not `gh`"
            assert (
                tuple(command[1:3]) in read_only_gh_subcommands
            ), f"{source.name} runs a non-read-only gh subcommand: {command[1:3]}"
    assert found == 1, "exactly one subprocess call is expected in Phase A"


def _literal_command(source: Path, node: ast.Call) -> list[str]:
    """Resolve a subprocess call's command list to string literals."""
    argument = node.args[0] if node.args else None
    if isinstance(argument, ast.Name):
        for candidate in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (
                isinstance(candidate, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == argument.id
                    for target in candidate.targets
                )
                and isinstance(candidate.value, ast.List)
            ):
                argument = candidate.value
                break
    assert isinstance(argument, ast.List), f"{source.name} builds its command dynamically"
    return [
        element.value if isinstance(element, ast.Constant) and isinstance(element.value, str)
        else "<dynamic>"
        for element in argument.elts
    ]


def test_no_ai_or_http_client_is_reachable_from_phase_a() -> None:
    # Claude and OpenAI invocation arrive in Automation Phases C and D. Phase A
    # must not be able to spend either quota.
    forbidden = {"anthropic", "openai", "httpx", "requests", "urllib", "http"}
    for source in _automation_sources():
        modules = _imported_modules(ast.parse(source.read_text(encoding="utf-8")))
        offending = {module for module in modules if module.split(".")[0] in forbidden}
        assert not offending, f"{source.name} can reach an external service: {offending}"
