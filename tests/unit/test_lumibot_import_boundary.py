"""The tree-walking AST boundary test, moved out of `test_lumibot_adapter.py`
(library-migration PR 6; docs/adr/0009-lumibot-backtest-distribution-boundary.md
section 4, "Collateral finding: the AST import boundary does not run in
ordinary CI").

`test_lumibot_adapter.py` begins with a module-level
`pytest.importorskip("lumibot")`, so `main-tests` (which installs `.[dev]`
only) never actually executed this walk -- the whole file, including this
test, skipped. This file has no such guard and no dependency on `lumibot` at
all -- it only parses source with `ast` -- so it runs for real, and fails
rather than skips, whether or not `lumibot` happens to be installed.

`runtime/lumibot/` remains the only directory under `src/trading_research`
permitted to import LumiBot. `backtest_runtime/` is a separate top-level
distribution outside `src/trading_research` entirely (ADR 0009), so this
rule needs no new exception for it -- see
`backtest_runtime/tests/test_import_boundary.py` for the corresponding rule
enforced from that distribution's side.
"""
from __future__ import annotations

import ast
import pathlib


def test_no_lumibot_import_outside_runtime_package():
    src_root = pathlib.Path(__file__).resolve().parents[2] / "src" / "trading_research"
    offenders = []
    for path in src_root.rglob("*.py"):
        if "runtime" in path.parts:
            continue  # the one package that is allowed to import lumibot
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "lumibot" for a in node.names):
                offenders.append(str(path))
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "lumibot":
                offenders.append(str(path))
    assert offenders == []
