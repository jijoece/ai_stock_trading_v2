"""Zero outbound network attempts across a full backtest, under the
fail-closed guard (docs/adr/0009-lumibot-backtest-distribution-boundary.md
Decision 2, property 4), plus proof that no benchmark or live historical-data
access is even reachable (Decision 3: `benchmark_asset=None` and
`analyze_backtest=False` are hardcoded, not caller-configurable)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SUPPORT_DIR = Path(__file__).resolve().parent / "support"
CREDENTIAL_PROBE = SUPPORT_DIR / "credential_probe.py"


def test_zero_outbound_attempts_across_a_full_backtest(tmp_path):
    from support.fixtures import valid_input_document

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(valid_input_document()))
    diagnostics_path = tmp_path / "diagnostics.json"
    import os

    env = {"PATH": os.environ.get("PATH", "")}
    subprocess.run(
        [
            sys.executable,
            str(CREDENTIAL_PROBE),
            str(diagnostics_path),
            str(input_path),
            str(output_path),
        ],
        env=env,
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["exit_code"] == 0
    assert diagnostics["network_attempt_count"] == 0
    assert diagnostics["network_attempts"] == []


def test_benchmark_and_analyze_backtest_are_hardcoded_off():
    """Spy on `Strategy.run_backtest` (in-process, protected by conftest's
    session-wide guard) to prove the actual call this module makes always
    passes `benchmark_asset=None, analyze_backtest=False` -- not merely that
    the source text mentions them."""
    from unittest.mock import patch

    from lumibot.strategies.strategy import Strategy

    from backtest_runtime.contract import parse_input_document
    from backtest_runtime.strategy import run_backtest
    from support.fixtures import valid_input_document

    backtest_input = parse_input_document(valid_input_document())

    captured_kwargs = {}
    real_run_backtest = Strategy.run_backtest.__func__

    def spy(cls, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_run_backtest(cls, *args, **kwargs)

    with patch.object(Strategy, "run_backtest", classmethod(spy)):
        run_backtest(backtest_input)

    assert captured_kwargs["benchmark_asset"] is None
    assert captured_kwargs["analyze_backtest"] is False
