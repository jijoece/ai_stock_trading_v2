"""Library-migration PR 5 (review-fix round): import-boundary enforcement
for `vector_research`, analogous to the existing LumiBot AST import-boundary
test (`tests/unit/test_lumibot_adapter.py::test_no_lumibot_import_outside_runtime_package`).

Three boundaries are enforced, all by direct AST inspection (not a
source-text substring check):

1. `vectorbt` is only ever imported from
   `src/trading_research/vector_research/` -- no other module in the main
   trading-desk process may import it.
2. No production module anywhere under `src/trading_research/` outside
   `vector_research/` itself may import `vector_research` -- this is
   deliberately the *only* rule for this additive PR (not a curated list of
   `execution/`, `paper_books/`, `runtime/`, scheduler, or broker-gateway
   paths, which would need updating every time a new package is added).
   A future consumer requires an explicit architecture decision and an
   update to this test, not a silent import.
3. `trading_paper_runtime` (the isolated LumiBot/Alpaca runtime process)
   and `vector_research` never import each other -- the new adapter has no
   execution authority and must stay fully decoupled from the broker
   runtime boundary.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "trading_research"
PAPER_RUNTIME_SRC_ROOT = REPO_ROOT / "paper_runtime" / "src"


def _top_level_import_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _imports_vector_research(path: pathlib.Path) -> bool:
    """True if `path` imports the `vector_research` package or a submodule,
    covering absolute (`import trading_research.vector_research...`,
    `from trading_research.vector_research import ...`) and relative
    (`from .vector_research import ...`, `from . import vector_research`)
    forms alike -- a plain top-level-name check misses all of these because
    `vector_research` is a *sub*-package of `trading_research`, not a
    top-level import target."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "vector_research" in alias.name.split("."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            if "vector_research" in module_parts:
                return True
            if node.level and node.level > 0:
                if any(alias.name == "vector_research" for alias in node.names):
                    return True
    return False


def test_vector_research_package_exists():
    assert (SRC_ROOT / "vector_research" / "adapter.py").is_file()


def test_vectorbt_only_imported_from_vector_research_package():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if "vector_research" not in path.parts and "vectorbt" in _top_level_import_names(path)
    ]
    assert offenders == []


def test_no_production_module_outside_vector_research_imports_it():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if "vector_research" not in path.parts and _imports_vector_research(path)
    ]
    assert offenders == []


def test_vector_research_never_imports_paper_runtime():
    vector_research_root = SRC_ROOT / "vector_research"
    offenders = [
        str(path)
        for path in vector_research_root.rglob("*.py")
        if _top_level_import_names(path) & {"trading_paper_runtime", "paper_runtime"}
    ]
    assert offenders == []


def test_paper_runtime_never_imports_vector_research():
    assert PAPER_RUNTIME_SRC_ROOT.is_dir(), "paper_runtime/src is expected to exist"
    offenders = [
        str(path)
        for path in PAPER_RUNTIME_SRC_ROOT.rglob("*.py")
        if _imports_vector_research(path)
    ]
    assert offenders == []
