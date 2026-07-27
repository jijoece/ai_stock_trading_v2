"""Library-migration PR 5: import-boundary enforcement for `vector_research`,
analogous to the existing LumiBot AST import-boundary test
(`tests/unit/test_lumibot_adapter.py::test_no_lumibot_import_outside_runtime_package`).

Two boundaries are enforced, both by direct AST inspection (not a
source-text substring check):

1. `vectorbt` is only ever imported from
   `src/trading_research/vector_research/` -- no other module in the main
   trading-desk process may import it.
2. `trading_paper_runtime` (the isolated LumiBot/Alpaca runtime process)
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


def test_vector_research_package_exists():
    assert (SRC_ROOT / "vector_research" / "adapter.py").is_file()


def test_vectorbt_only_imported_from_vector_research_package():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if "vector_research" not in path.parts and "vectorbt" in _top_level_import_names(path)
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
        if "vector_research" in _top_level_import_names(path)
    ]
    assert offenders == []
