from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import noxfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SESSIONS = {
    "tests",
    "paper_tests",
    "typecheck",
    "paper_typecheck",
    "safety_typecheck",
    "migration_smoke",
    "ci",
}
OPERATIONAL_COMMAND_TERMS = {
    "submit",
    "cancel",
    "broker",
    "scheduler",
    "shadow-cycle",
}


class RecordingSession:
    def __init__(self, posargs: list[str] | None = None) -> None:
        self.posargs = posargs or []
        self.env = {name: "true" for name in noxfile.OPT_IN_RUN_FLAGS}
        self.installs: list[tuple[str, ...]] = []
        self.runs: list[tuple[str, ...]] = []
        self.directories: list[str] = []
        self.notifications: list[str] = []

    def install(self, *args: str) -> None:
        self.installs.append(args)

    def run(self, *args: str) -> None:
        self.runs.append(args)

    def chdir(self, path: str) -> None:
        self.directories.append(path)

    def notify(self, session_name: str) -> None:
        self.notifications.append(session_name)


def test_required_nox_sessions_are_discoverable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "nox", "--list"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for session_name in REQUIRED_SESSIONS:
        assert f"* {session_name}" in result.stdout


def test_test_sessions_disable_opt_in_flags_and_pass_through_arguments() -> None:
    main = RecordingSession(["tests/unit/test_scorer.py", "-q"])
    noxfile.tests(main)  # type: ignore[arg-type]
    assert main.runs == [("pytest", "tests/unit/test_scorer.py", "-q")]
    assert set(main.env) == set(noxfile.OPT_IN_RUN_FLAGS)
    assert not any(main.env.values())

    paper = RecordingSession(["tests/test_protocol.py", "-q"])
    noxfile.paper_tests(paper)  # type: ignore[arg-type]
    assert paper.directories == ["paper_runtime"]
    assert paper.runs == [("pytest", "tests/test_protocol.py", "-q")]
    assert not any(paper.env.values())


def test_required_sessions_only_run_validation_commands() -> None:
    for session_function in (
        noxfile.tests,
        noxfile.paper_tests,
        noxfile.typecheck,
        noxfile.paper_typecheck,
        noxfile.safety_typecheck,
        noxfile.migration_smoke,
    ):
        session = RecordingSession()
        session_function(session)  # type: ignore[arg-type]
        assert {command[0] for command in session.runs} <= {
            "pytest",
            "pyright",
            "python",
        }
        command_text = " ".join(part for command in session.runs for part in command)
        assert not any(term in command_text.lower() for term in OPERATIONAL_COMMAND_TERMS)


def test_ci_notifies_safe_blocking_sessions_in_order() -> None:
    session = RecordingSession()
    noxfile.ci(session)  # type: ignore[arg-type]
    assert session.notifications == [
        "tests",
        "paper_tests",
        "safety_typecheck",
        "migration_smoke",
    ]


def test_migration_script_executes_directly() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_migrations.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "migration schema smoke checks OK"
