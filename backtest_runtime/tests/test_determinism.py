"""docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2/3: the
same input must produce a byte-identical normalized result document, and a
changed input bar must change both the checksum and the result. Runs the
real CLI as a subprocess (not an in-process call) so this proves the actual
`python -m backtest_runtime` entry point is deterministic, not just the
`strategy.run_backtest` function in isolation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CLEAN_ENV = {"PATH": os.environ.get("PATH", "")}


def _run_cli(input_doc: dict, tmp_path: Path, label: str) -> str:
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
    return output_path.read_text()


def test_identical_input_produces_byte_identical_output(tmp_path):
    from support.fixtures import valid_input_document

    document = valid_input_document()
    first = _run_cli(document, tmp_path, "run1")
    second = _run_cli(document, tmp_path, "run2")
    assert first == second


def test_changed_bar_changes_checksum_and_result(tmp_path):
    from support.fixtures import perturbed_input_document, valid_input_document

    baseline = json.loads(_run_cli(valid_input_document(), tmp_path, "baseline"))
    perturbed = json.loads(_run_cli(perturbed_input_document(), tmp_path, "perturbed"))

    assert (
        baseline["historical_bar_dataset_checksum"]
        != perturbed["historical_bar_dataset_checksum"]
    )
    # run-configuration (strategy) is unchanged between the two documents.
    assert baseline["run_configuration_checksum"] == perturbed["run_configuration_checksum"]
    assert baseline["daily_states"] != perturbed["daily_states"]


def test_changed_run_configuration_changes_checksum_but_not_bar_checksum(tmp_path):
    from support.fixtures import valid_input_document

    baseline_doc = valid_input_document()
    reconfigured_doc = valid_input_document(quantity=20)

    baseline = json.loads(_run_cli(baseline_doc, tmp_path, "cfg_baseline"))
    reconfigured = json.loads(_run_cli(reconfigured_doc, tmp_path, "cfg_reconfigured"))

    assert (
        baseline["historical_bar_dataset_checksum"]
        == reconfigured["historical_bar_dataset_checksum"]
    )
    assert baseline["run_configuration_checksum"] != reconfigured["run_configuration_checksum"]
