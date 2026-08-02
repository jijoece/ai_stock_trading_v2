"""docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 1:
`paper_runtime/pyproject.toml` and `backtest_runtime/pyproject.toml` are two
separately resolved environments that must not drift apart silently. This
reads both `pyproject.toml` files as plain text (no environment needs to be
installed, and no TOML-parsing dependency is required -- `tomllib` is
3.11-only and this distribution's own declared floor is `>=3.10`) and
asserts they pin the identical exact LumiBot version, which must also be
`backtest_runtime.LUMIBOT_PINNED_VERSION`."""
from __future__ import annotations

import pathlib
import re

from backtest_runtime import LUMIBOT_PINNED_VERSION

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKTEST_RUNTIME_PYPROJECT = REPO_ROOT / "backtest_runtime" / "pyproject.toml"
PAPER_RUNTIME_PYPROJECT = REPO_ROOT / "paper_runtime" / "pyproject.toml"
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"

_LUMIBOT_DEPENDENCY_LINE = re.compile(r'^\s*"(lumibot==[^"]+)"\s*,?\s*$', re.MULTILINE)


def _lumibot_pins(pyproject_path: pathlib.Path) -> list[str]:
    return _LUMIBOT_DEPENDENCY_LINE.findall(pyproject_path.read_text())


def _lumibot_pin(pyproject_path: pathlib.Path) -> str:
    pins = _lumibot_pins(pyproject_path)
    assert len(pins) == 1, f"{pyproject_path} must declare exactly one lumibot pin, found {pins}"
    return pins[0]


def test_backtest_runtime_and_paper_runtime_pin_the_same_exact_lumibot_version():
    backtest_runtime_pin = _lumibot_pin(BACKTEST_RUNTIME_PYPROJECT)
    paper_runtime_pin = _lumibot_pin(PAPER_RUNTIME_PYPROJECT)
    assert backtest_runtime_pin == paper_runtime_pin
    assert backtest_runtime_pin == f"lumibot=={LUMIBOT_PINNED_VERSION}"


def test_root_pyproject_declares_no_lumibot_dependency():
    assert _lumibot_pins(ROOT_PYPROJECT) == []
