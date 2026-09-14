"""Library-migration PR 16: import-boundary enforcement for
`observability.py`, analogous to the existing VectorBT/empyrical/
quantstats_lumi AST import-boundary tests
(`tests/unit/test_vector_research_import_boundary.py`,
`tests/unit/test_analytics_parity_import_boundary.py`).

`observability.py` is new, additive OpenTelemetry tracing/metrics SDK
wiring (`docs/library-migration/MASTER_PLAN.md` row 16); it is not called
by any other production module yet, and domain telemetry
(`research/cycle_telemetry.py`, `paper_books/metrics.py`) is unrelated and
must stay that way. This test proves both boundaries by construction, not
by convention: `opentelemetry` may only be imported from `observability.py`,
and nothing else under `src/trading_research/` may import `observability`.

Runs unconditionally (pure `ast` source parsing, no import of
`opentelemetry`), unlike the behavioral tests in `test_observability.py`,
which skip without the `observability` extra installed.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "trading_research"
OBSERVABILITY_PATH = SRC_ROOT / "observability.py"

_LIBRARY_TOP_LEVEL_NAMES = {"opentelemetry"}


def _top_level_import_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _imports_observability(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "observability" in alias.name.split("."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            if "observability" in module_parts:
                return True
            if node.level and node.level > 0:
                if any(alias.name == "observability" for alias in node.names):
                    return True
    return False


def test_observability_module_exists():
    assert OBSERVABILITY_PATH.is_file()


def test_opentelemetry_only_imported_from_observability():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if path != OBSERVABILITY_PATH and _top_level_import_names(path) & _LIBRARY_TOP_LEVEL_NAMES
    ]
    assert offenders == []


def test_no_production_module_imports_observability():
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if path != OBSERVABILITY_PATH and _imports_observability(path)
    ]
    assert offenders == []


def test_domain_telemetry_modules_do_not_import_opentelemetry():
    domain_telemetry_paths = [
        SRC_ROOT / "research" / "cycle_telemetry.py",
        SRC_ROOT / "paper_books" / "metrics.py",
    ]
    for path in domain_telemetry_paths:
        assert path.is_file()
        assert not (_top_level_import_names(path) & _LIBRARY_TOP_LEVEL_NAMES)
