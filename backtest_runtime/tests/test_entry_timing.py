"""`strategy.entry_after_session` -- the one control reference strategy v2 adds.

docs/library-migration/DECISIONS.md D6 (revised): it moves *when* the single
buy is submitted and nothing else. These tests pin both halves of that claim:
the delay works, and nothing else about the strategy changed (still exactly
one order, still a buy, still held to the end, still no sell).

Runs the real CLI as a subprocess, like the determinism and drawdown tests, so
this covers the shipped `python -m backtest_runtime` entry point.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backtest_runtime.contract import ContractError, parse_input_document

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


def test_null_entry_after_session_reproduces_the_undelayed_entry(tmp_path):
    from support.fixtures import valid_input_document

    result = _run_cli(valid_input_document(entry_after_session=None), tmp_path, "undelayed")

    assert len(result["fills"]) == 1
    # BARS[1] is 2024-01-03 and its open is 100.5: the undelayed strategy
    # submits on the first iteration and fills at that session's open.
    assert result["fills"][0]["fill_price"] == 100.5


def test_entry_after_session_moves_the_entry_to_the_next_session(tmp_path):
    from support.fixtures import valid_input_document

    result = _run_cli(
        valid_input_document(entry_after_session="2024-01-03"), tmp_path, "delayed"
    )

    assert len(result["fills"]) == 1
    # BARS[2] is 2024-01-04 and its open is 102.0.
    assert result["fills"][0]["fill_price"] == 102.0
    # Delaying the entry spends less cash here, so the delay is observable in
    # the portfolio, not only in the fill record.
    assert result["final_cash"] == 100_000.0 - 10 * 102.0


def test_delay_changes_the_run_configuration_checksum_but_not_the_bar_checksum(tmp_path):
    from support.fixtures import valid_input_document

    undelayed = _run_cli(valid_input_document(), tmp_path, "cfg_undelayed")
    delayed = _run_cli(
        valid_input_document(entry_after_session="2024-01-03"), tmp_path, "cfg_delayed"
    )

    assert (
        undelayed["historical_bar_dataset_checksum"]
        == delayed["historical_bar_dataset_checksum"]
    )
    assert undelayed["run_configuration_checksum"] != delayed["run_configuration_checksum"]


def test_delay_adds_no_sell_and_no_second_order(tmp_path):
    """The scope boundary itself: v2 must still be buy-once-and-hold."""
    from support.fixtures import valid_input_document

    result = _run_cli(
        valid_input_document(entry_after_session="2024-01-03"), tmp_path, "scope"
    )

    assert len(result["orders"]) == 1
    assert len(result["fills"]) == 1
    assert result["orders"][0]["side"] == "buy"
    assert result["fills"][0]["side"] == "buy"
    assert all(state["realized_pnl"] == 0.0 for state in result["daily_states"])
    # Still holding at the end -- the delay does not introduce an exit.
    assert result["positions"] == [
        {"symbol": "SPKE", "quantity": 10.0, "average_price": 102.0}
    ]


def test_delayed_run_is_deterministic(tmp_path):
    from support.fixtures import valid_input_document

    document = valid_input_document(entry_after_session="2024-01-03")
    first = _run_cli(document, tmp_path, "det1")
    second = _run_cli(document, tmp_path, "det2")
    assert first == second


# --- contract validation of the new field ---------------------------------


def test_missing_entry_after_session_is_rejected():
    from support.fixtures import valid_input_document

    document = valid_input_document()
    del document["strategy"]["entry_after_session"]
    with pytest.raises(ContractError, match="missing fields"):
        parse_input_document(document)


@pytest.mark.parametrize("value", ["20240103", "2024-W01-2", "not-a-date", 20240103, 1.5])
def test_malformed_entry_after_session_is_rejected(value):
    from support.fixtures import valid_input_document

    document = valid_input_document()
    document["strategy"]["entry_after_session"] = value
    with pytest.raises(ContractError, match="entry_after_session"):
        parse_input_document(document)


def test_entry_after_the_last_session_is_rejected_rather_than_silently_never_entering():
    from support.fixtures import valid_input_document

    document = valid_input_document(entry_after_session="2024-01-08")
    with pytest.raises(ContractError, match="leaves no session"):
        parse_input_document(document)


def test_entry_after_session_is_parsed_as_a_date():
    from datetime import date

    from support.fixtures import valid_input_document

    parsed = parse_input_document(valid_input_document(entry_after_session="2024-01-03"))
    assert parsed.strategy.entry_after_session == date(2024, 1, 3)
    assert parse_input_document(valid_input_document()).strategy.entry_after_session is None
