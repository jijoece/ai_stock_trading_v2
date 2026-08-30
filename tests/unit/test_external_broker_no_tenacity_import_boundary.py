"""Structural AST guard for library-migration PR 14 (`MASTER_PLAN.md` row 14,
`DECISIONS.md` D12): Tenacity's own scoping is decorator/context-manager
based, not a global interceptor, so it can be kept away from the ambiguous
external-broker-retry path (`paper_books/external_broker.py`) simply by never
importing it there. This test enforces that boundary structurally, the same
way `test_lumibot_import_boundary.py` enforces the LumiBot boundary — by
parsing source with `ast`, not by importing `tenacity` (which is not a
dependency of this repository), so it runs unconditionally in every
environment, never skips, and fails rather than silently passing if
`tenacity` is ever added as a dependency and someone wraps this exact module
with it.

Scope is deliberately this one file, not the whole `src/trading_research`
tree: `COMPONENT_MATRIX.md`'s "Generic transient retries" row leaves generic
per-provider transport retry code (`evidence_providers/http_client.py`)
eligible for a future, separately-approved Tenacity adoption; only the
ambiguous-broker-retry path is structurally excluded.
"""
from __future__ import annotations

import ast
import pathlib

_EXTERNAL_BROKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "trading_research" / "paper_books" / "external_broker.py"
)


def _find_tenacity_import_offenders(tree: ast.Module) -> list[str]:
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "tenacity" for a in node.names):
            offenders.append(f"import tenacity at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tenacity":
            offenders.append(f"from tenacity import ... at line {node.lineno}")
    return offenders


def test_no_tenacity_import_in_external_broker():
    tree = ast.parse(_EXTERNAL_BROKER_PATH.read_text())
    assert _find_tenacity_import_offenders(tree) == []


def test_detector_flags_a_synthetic_tenacity_import(tmp_path):
    """Proves the detector actually fires, rather than vacuously passing --
    mirrors `test_lumibot_import_boundary.py`'s corresponding proof test."""
    offending_module = tmp_path / "synthetic_external_broker.py"
    offending_module.write_text(
        "from tenacity import retry\n\n"
        "@retry\n"
        "def retry_external_paper_order():\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == [
        "from tenacity import ... at line 1",
    ]


def test_detector_flags_a_synthetic_plain_import_tenacity(tmp_path):
    offending_module = tmp_path / "synthetic_external_broker_plain_import.py"
    offending_module.write_text("import tenacity\n")

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == ["import tenacity at line 1"]
