"""Structural import-boundary tests for the isolation
docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 1/Allowed
imports and Decision 4 require:

* `backtest_runtime/src` may import only the standard library, `pandas`, and
  `lumibot`.
* `backtest_runtime` must never import `trading_research` or
  `trading_paper_runtime`.
* The main project (`src/trading_research`) must never import
  `backtest_runtime`.
* `paper_runtime/src` must never import `backtest_runtime`.

AST-based, not a source-text substring check -- analogous to
`tests/unit/test_lumibot_adapter.py::test_no_lumibot_import_outside_runtime_package`.
"""
from __future__ import annotations

import ast
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKTEST_RUNTIME_SRC = REPO_ROOT / "backtest_runtime" / "src"
MAIN_PROJECT_SRC = REPO_ROOT / "src" / "trading_research"
PAPER_RUNTIME_SRC = REPO_ROOT / "paper_runtime" / "src"

ALLOWED_TOP_LEVEL_MODULES = {"lumibot", "pandas"} | set(sys.stdlib_module_names)
# `__future__` imports and the package's own internal modules are always fine.
ALLOWED_TOP_LEVEL_MODULES |= {"__future__", "backtest_runtime"}


def _top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative import (`from . import x`, `from .cli import main`) -- intra-package, always allowed
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def _imports_module(path: pathlib.Path, target: str) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == target for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == target:
            return True
    return False


def test_backtest_runtime_src_imports_only_stdlib_pandas_and_lumibot():
    offenders = {}
    for path in BACKTEST_RUNTIME_SRC.rglob("*.py"):
        disallowed = _top_level_imports(path) - ALLOWED_TOP_LEVEL_MODULES
        if disallowed:
            offenders[str(path)] = sorted(disallowed)
    assert offenders == {}


def test_backtest_runtime_never_imports_trading_research_or_paper_runtime():
    offenders = [
        str(path)
        for path in BACKTEST_RUNTIME_SRC.rglob("*.py")
        if _imports_module(path, "trading_research") or _imports_module(path, "trading_paper_runtime")
    ]
    assert offenders == []


def test_main_project_never_imports_backtest_runtime():
    offenders = [
        str(path) for path in MAIN_PROJECT_SRC.rglob("*.py") if _imports_module(path, "backtest_runtime")
    ]
    assert offenders == []


def test_paper_runtime_never_imports_backtest_runtime():
    offenders = [
        str(path) for path in PAPER_RUNTIME_SRC.rglob("*.py") if _imports_module(path, "backtest_runtime")
    ]
    assert offenders == []
