"""Review follow-up on PR 38: a signal generated at the close of `split_date` can only execute in the
next session, so it belongs to the out-of-sample window, not the in-sample one."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from trading_research.backtesting.fixture_history import build_fixture_snapshot
from trading_research.backtesting.signal_replay import replay_signals
from trading_research.backtesting.strategy_backtest_runner import build_strategy, run_strategy_backtest_report
from trading_research.evaluation.market_calendar import next_trading_session
from trading_research.storage.database import connect

START, END = date(2023, 1, 3), date(2024, 12, 31)
SYMBOLS = ("AAA", "BBB", "CCC")


@pytest.fixture(scope="module")
def signal_days():
    snapshot = build_fixture_snapshot(
        symbols=(*SYMBOLS, "SPY"), start=START, end=END, adjustment="split",
        as_of=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    replay = replay_signals(
        strategy=build_strategy("momentum_breakout", None), provider=AlpacaHistoricalDataProvider(snapshot),
        symbols=SYMBOLS, start=START, end=END, history_start=warmup_start(START),
        screening_config=load_screening_config(None),
    )
    return sorted(s.data_as_of.date() for s in replay.signals if s.data_as_of is not None)


def _report(tmp_path, split):
    return run_strategy_backtest_report(
        strategy_id="momentum_breakout", symbols=SYMBOLS, start=START, end=END, initial_cash=Decimal("100000"),
        data_source="fixture", snapshot_path=tmp_path / "s.snapshot.json", split_date=split,
        conn=connect(tmp_path / "db.sqlite3"), env={},
    )


def test_a_signal_on_the_split_date_is_assigned_to_the_out_of_sample_window(tmp_path, signal_days):
    boundary = next(day for day in signal_days if START < day < END)
    expected_in = sum(1 for day in signal_days if next_trading_session(day) <= boundary)
    expected_out = sum(1 for day in signal_days if boundary < next_trading_session(day) <= END)
    assert signal_days.count(boundary) >= 1

    split = _report(tmp_path, boundary)["split"]
    assert split["in_sample"]["signals"] == expected_in
    assert split["out_of_sample"]["signals"] == expected_out
    # The boundary-day signal is the one the old rule put in the wrong window.
    assert expected_in == sum(1 for day in signal_days if day < boundary)


def test_every_signal_that_can_execute_is_in_exactly_one_window(tmp_path, signal_days):
    boundary = signal_days[len(signal_days) // 2]
    split = _report(tmp_path, boundary)["split"]
    executable = sum(1 for day in signal_days if next_trading_session(day) <= END)
    assert split["in_sample"]["signals"] + split["out_of_sample"]["signals"] == executable
