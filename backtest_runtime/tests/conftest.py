"""Applies the production credential guard before pytest collects anything.

Any test module below may trigger `import lumibot` transitively, and a bare
module-level import happens at collection time -- before any fixture can
run. This repository's own scratch verification during PR 6 development
confirmed the hazard directly: importing `lumibot` from the repository root
with no guard loaded the real `.env` and attempted a live broker connection
(docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2). Running
this file's guard here, at import time, protects every test in this suite
the same way `backtest_runtime.__main__` protects the real entry point --
before pytest ever reaches a test module's own top-level imports.

Tests that specifically exercise the guard's necessity construct their own
subprocess with a controlled environment (see `support/credential_probe.py`)
rather than relying on -- or fighting with -- this session-wide protection.
"""
from __future__ import annotations

from backtest_runtime.credential_guard import (
    scrub_credential_environment,
    suppress_dotenv_discovery,
)

scrub_credential_environment()
suppress_dotenv_discovery()
