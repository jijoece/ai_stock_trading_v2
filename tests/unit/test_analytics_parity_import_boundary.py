"""Library-migration PR 11: import-boundary enforcement for
`evaluation/analytics_parity.py`, analogous to the existing VectorBT/LumiBot
AST import-boundary tests
(`tests/unit/test_vector_research_import_boundary.py`,
`tests/unit/test_lumibot_adapter.py`).

`evaluation/analytics_parity.py` proves fixture parity for a later removal
decision (`docs/library-migration/MASTER_PLAN.md` row 17); it is not yet
this repository's authoritative analytics implementation. This test proves
that by construction, not by convention: nothing under
`src/trading_research/` other than `evaluation/analytics_parity.py` itself
may import `empyrical` or `quantstats_lumi`, so a future change cannot
silently start relying on the unproven, non-authoritative path.

Runs unconditionally (pure `ast` source parsing, no import of either
library), unlike the parity tests themselves, which skip without the
`analytics` extra installed.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "trading_research"
ANALYTICS_PARITY_PATH = SRC_ROOT / "evaluation" / "analytics_parity.py"

_LIBRARY_TOP_LEVEL_NAMES = {"empyrical", "quantstats_lumi"}


def _top_level_import_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_analytics_parity_module_exists():
    assert ANALYTICS_PARITY_PATH.is_file()


def test_empyrical_and_quantstats_lumi_only_imported_from_analytics_parity():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if path != ANALYTICS_PARITY_PATH and _top_level_import_names(path) & _LIBRARY_TOP_LEVEL_NAMES
    ]
    assert offenders == []


def test_no_production_module_imports_analytics_parity():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if path != ANALYTICS_PARITY_PATH and _imports_analytics_parity(path)
    ]
    assert offenders == []


def _imports_analytics_parity(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "analytics_parity" in alias.name.split("."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            if "analytics_parity" in module_parts:
                return True
            if node.level and node.level > 0:
                if any(alias.name == "analytics_parity" for alias in node.names):
                    return True
    return False
