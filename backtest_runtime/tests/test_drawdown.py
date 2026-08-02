"""Drawdown-sign regression: `daily_states[*].drawdown_fraction` and
`max_drawdown_fraction` must follow the same convention as the existing
`backtesting/engine.py` -- (equity - running_peak_equity) / running_peak_equity,
producing zero or negative values, never positive ones. Runs the real CLI as
a subprocess so this proves the actual normalized output, not just the
internal helper in isolation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CLEAN_ENV = {"PATH": os.environ.get("PATH", "")}


def _run_cli(input_doc: dict, tmp_path: Path, label: str) -> dict:
    input_path = tmp_path / f"{label}_input.json"
    output_path = tmp_path / f"{label}_output.json"
    input_path.write_text(json.dumps(input_doc))
    completed = subprocess.run(
        [sys.executable, "-m", "backtest_runtime", str(input_path), str(output_path)],
        env=CLEAN_ENV,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output_path.read_text())


def test_falling_equity_produces_negative_drawdown_with_correct_aggregate(tmp_path):
    from support.fixtures import falling_equity_input_document

    result = _run_cli(falling_equity_input_document(), tmp_path, "falling")

    daily_drawdowns = [state["drawdown_fraction"] for state in result["daily_states"]]

    # Every daily value is zero or negative -- never a positive "gain" framed
    # as a drawdown.
    assert all(value <= 0.0 for value in daily_drawdowns)
    # The fixture actually falls below its own running peak somewhere.
    assert any(value < 0.0 for value in daily_drawdowns)
    # The aggregate is the most negative daily value, or zero if none exists.
    assert result["max_drawdown_fraction"] == min(daily_drawdowns + [0.0])
    assert result["max_drawdown_fraction"] < 0.0


def test_default_fixture_daily_drawdowns_stay_non_positive_and_aggregate_matches_minimum(tmp_path):
    from support.fixtures import valid_input_document

    result = _run_cli(valid_input_document(), tmp_path, "default_fixture")

    daily_drawdowns = [state["drawdown_fraction"] for state in result["daily_states"]]
    assert all(value <= 0.0 for value in daily_drawdowns)
    assert result["max_drawdown_fraction"] == min(daily_drawdowns + [0.0])
