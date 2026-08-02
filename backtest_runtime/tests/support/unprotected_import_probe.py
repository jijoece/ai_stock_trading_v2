"""Negative-control probe: imports `lumibot` directly, WITHOUT the
credential scrub or `LUMIBOT_DISABLE_DOTENV`, in a process whose environment
and working directory are entirely controlled by the caller.

Exists only to prove the guard in `backtest_runtime.credential_guard` is
doing real work -- i.e. that a sentinel credential (present in the process
environment or in a `.env`/`.env.local` in the CWD) would otherwise reach
LumiBot and construct a broker -- rather than the protected-path tests
passing vacuously because there was never anything to protect against. This
mirrors the pre-step's own positive-control methodology
(docs/library-migration/pre-step-06/EVALUATION.md section 2.3, runs S1/S5/P1).

Usage: <python> unprotected_import_probe.py <diagnostics_json_path>
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import guards  # noqa: E402

guards.install_env_tracer()
guards.install_network_guard()

import lumibot  # noqa: E402
from lumibot import credentials as lumibot_credentials  # noqa: E402

diagnostics = {
    "broker_is_none": getattr(lumibot_credentials, "broker", None) is None,
    "data_source_is_none": getattr(lumibot_credentials, "data_source", None) is None,
    "network_attempt_count": len(guards.NETWORK_ATTEMPTS),
    "sentinel_env_keys_after_import": guards.sentinel_env_keys(),
    "lumibot_version": lumibot.__version__,
}

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(diagnostics, handle)
