"""Subprocess probe for backtest_runtime credential/network-safety tests.

Installs env-read tracing and a fail-closed network guard, then imports
`backtest_runtime.__main__` -- the exact bootstrap path production runs --
and, if an input/output file pair is given, executes the real CLI through
it. Writes a diagnostics JSON file; never records a credential value, only
key names and booleans, so the evidence file this produces is always safe
to inspect or commit.

Usage: <python> credential_probe.py <diagnostics_json_path> [<input_json_path> <output_json_path>]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import guards  # noqa: E402

guards.install_env_tracer()
guards.install_network_guard()

import backtest_runtime.__main__ as entry  # noqa: E402  (bootstrap runs on this import)

exit_code = None
if len(sys.argv) > 3:
    input_path, output_path = sys.argv[2], sys.argv[3]
    exit_code = entry.main([input_path, output_path])

from lumibot import credentials as lumibot_credentials  # noqa: E402

diagnostics = {
    "exit_code": exit_code,
    "credential_values_from_environment": guards.credential_values_from_environment(),
    "credential_reads_with_values": sorted(set(guards.credential_reads_with_values())),
    "broker_is_none": getattr(lumibot_credentials, "broker", None) is None,
    "data_source_is_none": getattr(lumibot_credentials, "data_source", None) is None,
    "network_attempt_count": len(guards.NETWORK_ATTEMPTS),
    "network_attempts": guards.NETWORK_ATTEMPTS,
    "sentinel_env_keys_after_run": guards.sentinel_env_keys(),
}

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(diagnostics, handle)
