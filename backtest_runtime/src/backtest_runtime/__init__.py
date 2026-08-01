"""Isolated, credential-free LumiBot backtest evaluation runtime.

A separate top-level distribution, sibling to ``paper_runtime/`` and never
installed into either the main project's environment or ``paper_runtime``'s
(see docs/adr/0009-lumibot-backtest-distribution-boundary.md). This package
may import only the standard library, pandas, and lumibot -- never
``trading_research`` or ``trading_paper_runtime`` -- and it is never imported
by either of those.

Input and output travel as files, not stdin/stdout or the paper-runtime.v2
protocol: see ``contract.py`` for the versioned schemas and ``cli.py`` for the
file-based entry point.
"""
from __future__ import annotations

SCHEMA_VERSION_INPUT = "backtest_runtime.input.v1"
SCHEMA_VERSION_RESULT = "backtest_runtime.result.v1"
LUMIBOT_PINNED_VERSION = "4.5.78"
