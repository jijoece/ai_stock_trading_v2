"""Migration orchestrator entry point — Automation Phase A (discovery only).

    python scripts/automation/orchestrator.py --dry-run

Phase A discovers the migration position, reconciles it against GitHub, and
reports the action a later automation phase would take. It never mutates
GitHub, never runs Claude, and never calls OpenAI. Those capabilities arrive in
Automation Phases B-E; see `docs/library-migration/AUTOMATION.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # executed as a script, not imported as a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.automation.config import (
    DEFAULT_CONFIG_RELATIVE_PATH,
    DEFAULT_STATE_RELATIVE_PATH,
    AutomationConfig,
    ConfigurationError,
    load_config,
)
from scripts.automation.github import (
    GhCliGitHub,
    GitHubError,
    GitHubReader,
    StaticGitHub,
)
from scripts.automation.migration_docs import (
    MigrationDocumentError,
    read_migration_documents,
)
from scripts.automation.reconcile import Reconciliation, reconcile
from scripts.automation import state as state_module
from scripts.automation.state import StateError, load_state, save_state


EXIT_OK = 0
EXIT_HUMAN_REQUIRED = 2
EXIT_ERROR = 1

# Phase A recognises exactly one command. `resume`, `retry`, `request-review`,
# `pause`, and `mark-human-required` are Automation Phase F work and are not
# accepted yet rather than accepted and silently ignored.
COMMAND_STATUS = "status"
COMMAND_RECONCILE = "reconcile"


def exit_code_for(state: str) -> int:
    """Map a reconciled state to a process exit code.

    An escalation exits non-zero so a scheduled run surfaces it instead of
    reporting success and being ignored.
    """
    return EXIT_HUMAN_REQUIRED if state == state_module.HUMAN_REQUIRED else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Discover and reconcile the library-migration automation state.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=COMMAND_STATUS,
        choices=(COMMAND_STATUS, COMMAND_RECONCILE),
        help="`status` reports without writing; `reconcile` also persists state.json.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository checkout to inspect (default: this checkout).",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    parser.add_argument("--state", type=Path, default=None, help="Path to state.json.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only. Makes no external mutation and writes no state file.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not call GitHub; reconcile against the migration documents alone.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def run_discovery(
    *,
    repo_root: Path,
    config: AutomationConfig,
    github: GitHubReader,
    state_path: Path,
) -> Reconciliation:
    """Read documents, GitHub, and cached state, and reconcile the three."""
    documents = read_migration_documents(repo_root)
    cached = load_state(state_path)
    pull_requests = github.list_pull_requests()
    return reconcile(
        documents=documents,
        pull_requests=pull_requests,
        cached=cached,
        config=config,
    )


def _next_phase_label(result: Reconciliation) -> str:
    """Describe the successor phase, distinguishing "none" from "not here yet".

    `STATUS.md` is updated inside each migration PR, so while a PR is open the
    default branch does not yet name the phase that follows it. That is not a
    missing successor, and reporting it as one would be misleading.
    """
    if result.next_phase_id:
        return f"PR {result.next_phase_id}"
    if result.state.active_phase is None:
        return "none — migration complete"
    if result.state.active_phase != result.documents.current_phase_id:
        return (
            "not documented in this checkout (this phase's own STATUS.md update "
            "declares it, and lands when its PR merges)"
        )
    return "none documented"


def format_report(result: Reconciliation, *, dry_run: bool) -> str:
    """Render the human-readable status block."""
    state = result.state
    row = result.active_row
    pull_request = result.pull_request

    phase = f"PR {state.active_phase}" if state.active_phase else "none (migration complete)"
    if row is not None:
        phase = f"{phase} — {row.title}"

    lines = [
        f"Migration phase:        {phase}",
        f"GitHub PR:              {'#' + str(pull_request.number) if pull_request else 'none'}",
        f"Branch:                 {state.branch or 'none'}",
        f"HEAD:                   {state.head_sha or 'none'}",
        f"CI:                     {result.ci_state}",
        f"Review state:           {state.state}",
        f"Last reviewed SHA:      {state.last_reviewed_sha or 'none'}",
        f"Review round:           {state.review_round}",
        f"Next documented phase:  {_next_phase_label(result)}",
        f"Proposed action:        {state.next_action}",
        f"External AI call:       none ({'dry-run' if dry_run else 'Automation Phase A performs none'})",
    ]
    if row is not None:
        lines.append(f"MASTER_PLAN risk/model: {row.risk} / {row.model}")
    if result.reasons:
        lines.append("Reasons:")
        lines.extend(f"  - {reason}" for reason in result.reasons)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root: Path = args.repo_root.resolve()
    config_path = args.config or repo_root / DEFAULT_CONFIG_RELATIVE_PATH
    state_path = args.state or repo_root / DEFAULT_STATE_RELATIVE_PATH

    try:
        config = load_config(config_path)
        github: GitHubReader = (
            StaticGitHub() if args.offline else GhCliGitHub(repo_root=str(repo_root))
        )
        result = run_discovery(
            repo_root=repo_root, config=config, github=github, state_path=state_path
        )
    except (ConfigurationError, MigrationDocumentError, StateError, GitHubError) as error:
        print(f"orchestrator: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR

    if args.command == COMMAND_RECONCILE and not args.dry_run:
        save_state(state_path, result.state)

    if args.json:
        print(json.dumps(result.state.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report(result, dry_run=args.dry_run))

    return exit_code_for(result.state.state)


if __name__ == "__main__":
    raise SystemExit(main())
