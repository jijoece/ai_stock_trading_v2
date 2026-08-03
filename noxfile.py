"""Canonical, credential-free repository validation tasks."""

from __future__ import annotations

from collections.abc import Sequence

import nox


MAIN_TEST_ARGS = ("tests/", "-q", "--tb=short")
PAPER_TEST_ARGS = ("tests/", "-q", "--tb=short")

# Never inherit opt-in gates for credentialed, networked, model, research-cycle,
# shadow-cycle, or broker tests into either canonical test session.
OPT_IN_RUN_FLAGS = (
    "RUN_CLAUDE_BEAR_TESTS",
    "RUN_CLAUDE_RESEARCH_TESTS",
    "RUN_CORPORATE_STATUS_TESTS",
    "RUN_EXTERNAL_PAPER_BROKER_TESTS",
    "RUN_MARKET_DATA_TESTS",
    "RUN_NEWS_API_TESTS",
    "RUN_PAPER_BROKER_TESTS",
    "RUN_REAL_CLAUDE_SHADOW_CYCLE",
    "RUN_REAL_RESEARCH_CYCLE",
    "RUN_REAL_SHADOW_CYCLE",
    "RUN_REDDIT_FREE_TESTS",
    "RUN_REDDIT_SENTIMENT_TESTS",
    "RUN_SEC_API_TESTS",
)


def _install_main(session: nox.Session, *packages: str) -> None:
    session.install("-e", ".[dev]", *packages)


def _install_paper_runtime(session: nox.Session, *packages: str) -> None:
    session.chdir("paper_runtime")
    session.install("-e", ".[dev]", *packages)


def _disable_opt_in_tests(session: nox.Session) -> None:
    session.env.update({name: "" for name in OPT_IN_RUN_FLAGS})


def _pytest_args(posargs: Sequence[str], defaults: Sequence[str]) -> tuple[str, ...]:
    return tuple(posargs) if posargs else tuple(defaults)


@nox.session
def tests(session: nox.Session) -> None:
    """Run the main credential-free test suite."""
    _install_main(session)
    _disable_opt_in_tests(session)
    session.run("pytest", *_pytest_args(session.posargs, MAIN_TEST_ARGS))


@nox.session
def paper_tests(session: nox.Session) -> None:
    """Run the isolated paper_runtime test suite."""
    _install_paper_runtime(session)
    _disable_opt_in_tests(session)
    session.run("pytest", *_pytest_args(session.posargs, PAPER_TEST_ARGS))


@nox.session
def typecheck(session: nox.Session) -> None:
    """Run the whole-project main-package Pyright check."""
    _install_main(session, "pyright")
    session.run("pyright")


@nox.session
def paper_typecheck(session: nox.Session) -> None:
    """Run the isolated paper_runtime Pyright check."""
    _install_paper_runtime(session, "pyright")
    session.run("pyright")


@nox.session
def safety_typecheck(session: nox.Session) -> None:
    """Run the blocking safety-critical Pyright subset."""
    _install_main(session, "pyright")
    session.run("pyright", "--project", "pyright-safety.json")


@nox.session
def migration_smoke(session: nox.Session) -> None:
    """Validate fresh and additive SQLite schema migrations."""
    _install_main(session)
    session.run("python", "scripts/check_migrations.py")


@nox.session(venv_backend="none")
def ci(session: nox.Session) -> None:
    """Run the safe blocking pre-PR validation sessions in order."""
    for session_name in (
        "tests",
        "paper_tests",
        "safety_typecheck",
        "migration_smoke",
    ):
        session.notify(session_name)
