"""`scripts/run_dashboard.sh` loads an allowlist of keys from `.env`; absent optional keys must not abort it.

Each test copies the script into a throwaway repo root whose `.venv/bin/python` is a stub, so nothing real
(Streamlit, the database, the network) is started. The stub records the environment it was launched with.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_dashboard.sh"

OPTIONAL_KEYS = (
    "AI_STOCK_TRADING_ENABLE_SCORING",
    "AI_STOCK_TRADING_ENABLE_ROBINHOOD_PORTFOLIO",
    "ROBINHOOD_MCP_TOKEN_PATH",
    "ALPACA_MARKET_DATA_API_KEY",
    "ALPACA_MARKET_DATA_API_SECRET",
    "ALPACA_MARKET_DATA_BASE_URL",
    "ROBINHOOD_MCP_URL",
)

STUB_PYTHON = """#!/bin/sh
# Stub interpreter: `-c import streamlit` succeeds; `-m streamlit run ...` records its environment.
if [ "$1" = "-m" ]; then
  env > "$(dirname "$0")/../../launched.env.tmp"
  mv "$(dirname "$0")/../../launched.env.tmp" "$(dirname "$0")/../../launched.env"
fi
exit 0
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "run_dashboard.sh")
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text(STUB_PYTHON)
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "research.sqlite").touch()
    return tmp_path


def _run(repo: Path, env_lines: list[str] | None, extra_env: dict[str, str] | None = None):
    if env_lines is not None:
        (repo / ".env").write_text("\n".join(env_lines) + "\n")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **(extra_env or {})}
    return subprocess.run(["bash", str(repo / "scripts" / "run_dashboard.sh")], env=env,
                          capture_output=True, text=True, timeout=30)


def _launched_env(repo: Path) -> dict[str, str]:
    marker = repo / "launched.env"
    deadline = time.monotonic() + 10
    while not marker.exists():  # the launch is backgrounded with nohup
        assert time.monotonic() < deadline, "the startup command never ran"
        time.sleep(0.05)
    return dict(line.split("=", 1) for line in marker.read_text().splitlines() if "=" in line)


def test_a_minimal_env_with_only_the_database_path_reaches_startup(repo):
    db = repo / "research.sqlite"
    result = _run(repo, [f"AI_STOCK_TRADING_DB_PATH={db}"])
    assert result.returncode == 0, result.stderr
    assert "Dashboard starting" in result.stdout
    launched = _launched_env(repo)
    assert launched["AI_STOCK_TRADING_DB_PATH"] == str(db)
    assert not any(key in launched for key in OPTIONAL_KEYS)


def test_a_partial_env_exports_only_the_keys_it_has(repo):
    db = repo / "research.sqlite"
    result = _run(repo, [f'AI_STOCK_TRADING_DB_PATH="{db}"', "AI_STOCK_TRADING_ENABLE_SCORING=1"])
    assert result.returncode == 0, result.stderr
    launched = _launched_env(repo)
    assert launched["AI_STOCK_TRADING_DB_PATH"] == str(db)  # quotes stripped
    assert launched["AI_STOCK_TRADING_ENABLE_SCORING"] == "1"
    assert "ROBINHOOD_MCP_URL" not in launched


def test_a_complete_env_exports_the_allowlist_but_never_unrelated_secrets(repo):
    db = repo / "research.sqlite"
    lines = [f"AI_STOCK_TRADING_DB_PATH={db}", "ANTHROPIC_API_KEY=do-not-export"]
    lines += [f"{key}=value-{index}" for index, key in enumerate(OPTIONAL_KEYS)]
    lines.append("AI_STOCK_TRADING_ENABLE_SCORING=last-wins")
    result = _run(repo, lines)
    assert result.returncode == 0, result.stderr
    launched = _launched_env(repo)
    assert "ANTHROPIC_API_KEY" not in launched
    assert launched["AI_STOCK_TRADING_ENABLE_SCORING"] == "last-wins"
    for index, key in enumerate(OPTIONAL_KEYS[1:], start=1):
        assert launched[key] == f"value-{index}"


def test_real_environment_variables_take_precedence_over_env(repo):
    db = repo / "research.sqlite"
    result = _run(repo, [f"AI_STOCK_TRADING_DB_PATH={db}", "ROBINHOOD_MCP_URL=from-dotenv"],
                  extra_env={"ROBINHOOD_MCP_URL": "from-environment"})
    assert result.returncode == 0, result.stderr
    assert _launched_env(repo)["ROBINHOOD_MCP_URL"] == "from-environment"


def test_an_env_without_the_database_path_still_fails_closed(repo):
    result = _run(repo, ["AI_STOCK_TRADING_ENABLE_SCORING=1"])
    assert result.returncode == 1
    assert "AI_STOCK_TRADING_DB_PATH is not set" in result.stderr
    assert not (repo / "launched.env").exists()


def test_a_missing_database_file_still_fails_closed(repo):
    result = _run(repo, [f"AI_STOCK_TRADING_DB_PATH={repo / 'absent.sqlite'}"])
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert not (repo / "launched.env").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can read a mode-000 file")
def test_an_unreadable_env_fails_closed_instead_of_being_treated_as_empty(repo):
    env_file = repo / ".env"
    env_file.write_text(f"AI_STOCK_TRADING_DB_PATH={repo / 'research.sqlite'}\n")
    env_file.chmod(0)
    try:
        result = _run(repo, None, extra_env={"AI_STOCK_TRADING_DB_PATH": str(repo / "research.sqlite")})
    finally:
        env_file.chmod(0o600)
    assert result.returncode == 1
    assert "Could not read" in result.stderr
    assert not (repo / "launched.env").exists()
