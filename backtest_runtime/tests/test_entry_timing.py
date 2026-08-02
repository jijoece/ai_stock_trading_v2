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


# --- authoritative booking session ----------------------------------------


def test_fill_is_dated_to_the_session_the_broker_booked_it_in(tmp_path):
    """Reference strategy v2 reads the broker's event log, not the callback.

    LumiBot runs `process_pending_orders` at the end of the *submission*
    session, so the fill belongs to that session. `on_filled_order` is
    dispatched one session later; v1 stamped that later date onto the fill and
    was wrong by one session every time.
    """
    from support.fixtures import valid_input_document

    result = _run_cli(
        valid_input_document(entry_after_session="2024-01-03"), tmp_path, "booking"
    )

    # Submission happens on the first iteration after 2024-01-03, i.e. BARS[2]
    # = 2024-01-04, and the fill is booked in that same session at its open.
    assert result["fills"][0]["market_date"] == "2024-01-04"
    assert result["fills"][0]["fill_price"] == 102.0
    # The callback fires on 2024-01-05. Nothing may report that date.
    assert result["fills"][0]["market_date"] != "2024-01-05"


def test_entry_session_state_includes_that_session_fill(tmp_path):
    """The state row for the booking session must show the position.

    LumiBot samples the strategy before it processes the session's fills, so
    the raw sample shows no position on the entry session. v2 re-applies the
    session's own authoritative fills, which is what makes the state series
    mean "end of session" the way every other daily series does.
    """
    from support.fixtures import valid_input_document

    result = _run_cli(
        valid_input_document(entry_after_session="2024-01-03"), tmp_path, "entrystate"
    )
    states = {state["market_date"]: state for state in result["daily_states"]}

    assert states["2024-01-03"]["cash"] == 100_000.0
    entry = states["2024-01-04"]
    assert entry["cash"] == 100_000.0 - 10 * 102.0
    # BARS[2] closes at 101.5, so equity is that cash plus the marked position.
    assert entry["equity"] == pytest.approx(98_980.0 + 10 * 101.5)
    assert entry["unrealized_pnl"] == pytest.approx(10 * (101.5 - 102.0))


def test_entry_on_the_last_session_is_reported_and_not_silently_dropped(tmp_path):
    """Penultimate-session boundary: accepted input must not finish flat.

    `entry_after_session` = BARS[-2] leaves exactly one session on which the
    order can be submitted, and LumiBot books the fill in that same session --
    after the last state sample, and with no later iteration to observe it. An
    accepted document that produced a fill but no position, and an unchanged
    final cash, would be a self-contradicting result document.
    """
    from support.fixtures import valid_input_document

    document = valid_input_document(entry_after_session="2024-01-05")
    parse_input_document(document)  # accepted, so it must produce a real entry
    result = _run_cli(document, tmp_path, "penultimate")

    assert len(result["fills"]) == 1
    # BARS[4] is 2024-01-08 and its open is 103.5.
    assert result["fills"][0]["market_date"] == "2024-01-08"
    assert result["fills"][0]["fill_price"] == 103.5
    assert result["positions"] == [
        {"symbol": "SPKE", "quantity": 10.0, "average_price": 103.5}
    ]
    assert result["final_cash"] == 100_000.0 - 10 * 103.5
    assert result["daily_states"][-1]["market_date"] == "2024-01-08"
    assert result["daily_states"][-1]["cash"] == 100_000.0 - 10 * 103.5


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
