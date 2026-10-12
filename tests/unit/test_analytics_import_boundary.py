"""Library-migration PR 17: structural enforcement of the single analytics
authority, analogous to the existing VectorBT/LumiBot AST import-boundary
tests (`tests/unit/test_vector_research_import_boundary.py`,
`tests/unit/test_lumibot_adapter.py`).

PR 11 added these checks to keep `evaluation/analytics_parity.py` (a
non-authoritative parity candidate) out of production. PR 17 removed the
custom formulas from `evaluation/metrics.py`, made `empyrical-reloaded`
their sole authority there, and deleted `analytics_parity.py`
(`docs/library-migration/DECISIONS.md` D15). The checks now prove, by
construction rather than convention, that:

1. `empyrical` is imported only by `evaluation/metrics.py`, so there is
   exactly one place its formulas can become a reported metric;
2. `quantstats_lumi` is imported only by
   `evaluation/analytics_presentation.py`, and no production module
   imports that presentation-only module;
3. the parallel parity module is gone, not left behind as a second
   implementation;
4. each of the five removed formulas now delegates to the library (or, for
   Calmar, composes the two library-backed metrics), and
   `empyrical.calmar_ratio` — a different, CAGR-based convention — is never
   called.

Runs unconditionally: pure `ast` source parsing, no library import.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "trading_research"
METRICS_PATH = SRC_ROOT / "evaluation" / "metrics.py"
PRESENTATION_PATH = SRC_ROOT / "evaluation" / "analytics_presentation.py"
REMOVED_PARITY_PATH = SRC_ROOT / "evaluation" / "analytics_parity.py"

_LIBRARY_DELEGATING_METRICS = ("cumulative_return", "sharpe_ratio", "sortino_ratio", "max_drawdown")


def _top_level_import_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def _imports_module(path: pathlib.Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if module_name in alias.name.split("."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if module_name in (node.module or "").split("."):
                return True
            if node.level and any(alias.name == module_name for alias in node.names):
                return True
    return False


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"evaluation/metrics.py no longer defines {name}")


def _empyrical_attributes(node: ast.AST) -> set[str]:
    return {
        n.attr for n in ast.walk(node)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "empyrical"
    }


def _called_names(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_empyrical_only_imported_by_metrics():
    offenders = [
        str(path) for path in SRC_ROOT.rglob("*.py")
        if path != METRICS_PATH and "empyrical" in _top_level_import_names(path)
    ]
    assert offenders == []
    assert "empyrical" in _top_level_import_names(METRICS_PATH)


def test_quantstats_lumi_only_imported_by_presentation_module():
    offenders = [
        str(path) for path in SRC_ROOT.rglob("*.py")
        if path != PRESENTATION_PATH and "quantstats_lumi" in _top_level_import_names(path)
    ]
    assert offenders == []


def test_no_production_module_imports_presentation_module():
    offenders = [
        str(path) for path in SRC_ROOT.rglob("*.py")
        if path != PRESENTATION_PATH and _imports_module(path, "analytics_presentation")
    ]
    assert offenders == []


def test_parallel_parity_module_removed_and_unreferenced():
    assert not REMOVED_PARITY_PATH.exists()
    offenders = [str(path) for path in SRC_ROOT.rglob("*.py") if _imports_module(path, "analytics_parity")]
    assert offenders == []


def test_removed_formulas_delegate_to_empyrical():
    tree = ast.parse(METRICS_PATH.read_text())
    for name in _LIBRARY_DELEGATING_METRICS:
        assert _empyrical_attributes(_function(tree, name)), f"{name} no longer delegates to empyrical"


def test_calmar_composes_library_metrics_and_never_calls_empyrical_calmar():
    tree = ast.parse(METRICS_PATH.read_text())
    calmar = _function(tree, "calmar_ratio")
    assert {"cumulative_return", "max_drawdown"} <= _called_names(calmar)
    assert "calmar_ratio" not in _empyrical_attributes(tree)


def test_metrics_module_has_no_hand_written_statistics():
    # The removed Sharpe/Sortino formulas were the only users of `math`
    # (sqrt/isclose); its return would signal a reintroduced formula.
    assert "math" not in _top_level_import_names(METRICS_PATH)
