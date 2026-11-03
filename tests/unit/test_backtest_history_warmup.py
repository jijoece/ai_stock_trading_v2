"""Review follow-up on PR 38: indicator history must be separable from the reporting window.

A backtest window (the primary run, and each side of a split) used to load bars only from its own
first session, so a signal near the start was rejected as ATR_UNAVAILABLE even though the snapshot
held plenty of preceding bars."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from trading_research.backtesting import BacktestConfiguration, EntrySignal, run_backtest
from trading_research.backtesting.data_provider import FixtureHistoricalDataProvider
from trading_research.backtesting.models import BacktestError

from tests.unit._backtest_history_helpers import make_bars

BARS = make_bars("AAA", [100.0 + (i % 5) for i in range(40)], start=date(2024, 1, 2))
BENCH = make_bars("SPY", [400.0 + i for i in range(40)], start=date(2024, 1, 2))
WINDOW_START = BARS[20].session_date  # 20 bars of history precede the window
PROVIDER = FixtureHistoricalDataProvider({"AAA": BARS, "SPY": BENCH})


def _run(*, history_start=None):
    config = BacktestConfiguration(
        start_date=WINDOW_START, end_date=BARS[-1].session_date, symbols=("AAA", "SPY"),
        initial_cash=Decimal("100000"), benchmark_symbol="SPY",
    )
    signal = EntrySignal(
        signal_id="early", symbol="AAA", generated_after_session=WINDOW_START,
        limit_price=Decimal("1000"), quantity_hint=Decimal("10"),
    )
    return run_backtest(
        configuration=config, data_provider=PROVIDER, signals=(signal,), history_start=history_start,
    )


def test_without_history_the_early_signal_has_no_atr():
    assert [r["reason"] for r in _run().rejected_entries] == ["ATR_UNAVAILABLE"]


def test_with_history_the_early_signal_fills():
    result = _run(history_start=BARS[0].session_date)
    assert result.rejected_entries == ()
    assert [f.side for f in result.fills][0] == "BUY"


def test_history_does_not_widen_the_reporting_window():
    plain, with_history = _run(), _run(history_start=BARS[0].session_date)
    assert [s.market_date for s in with_history.daily_states][0] == WINDOW_START
    assert len(with_history.daily_states) == len(plain.daily_states)
    expected = (BENCH[-1].close - BENCH[20].close) / BENCH[20].close
    assert with_history.metrics["benchmark_return"] == expected == plain.metrics["benchmark_return"]


def test_history_changes_the_run_identity_only_when_supplied():
    plain, with_history = _run(), _run(history_start=BARS[0].session_date)
    assert plain.backtest_run_id != with_history.backtest_run_id
    assert plain.backtest_run_id == _run().backtest_run_id


def test_history_start_after_the_window_is_rejected():
    with pytest.raises(BacktestError, match="history_start"):
        _run(history_start=BARS[30].session_date)
