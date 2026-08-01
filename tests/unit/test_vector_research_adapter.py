"""Library-migration PR 5 (review-fix round): VectorBT-backed
`vector_research` adapter coverage.

`vectorbt` is not part of `.[dev]`, so the behavioral tests in this file
skip (not fail) when VectorBT is unavailable -- this keeps the ordinary
`pytest tests/ -q` suite green on a plain `.[dev]` install, while the
dedicated `research-tests` CI job (which installs the `research` extra)
runs every test in this file for real. The missing-dependency test below
is the exception: it runs unconditionally in every environment, matching
the pattern already established for TA-Lib in `tests/unit/test_indicators.py`.

See docs/library-migration/STATUS.md for the PR 5 outcome record, including
the review-fix round that produced the signal-timing shift, the daily-
session/timezone-aware temporal contract, and the exploratory-only
analytics wrapper this file exercises.
"""
from __future__ import annotations

import importlib
import math
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ADAPTER_MODULE = "trading_research.vector_research.adapter"
_PACKAGE_MODULE = "trading_research.vector_research"

_N_BARS = 30


def _close(n: int = _N_BARS, flat: bool = False) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    if flat:
        return pd.Series([100.0] * n, index=idx)
    rng = np.random.default_rng(0)
    prices = 100 + np.cumsum(rng.normal(size=n))
    return pd.Series(np.clip(prices, 1.0, None), index=idx)


def _empty_signals(close: pd.Series, columns=("p1", "p2")) -> tuple[pd.DataFrame, pd.DataFrame]:
    entries = pd.DataFrame(False, index=close.index, columns=list(columns))
    exits = pd.DataFrame(False, index=close.index, columns=list(columns))
    return entries, exits


# ---------------------------------------------------------------------
# Missing-dependency guard -- must run in every environment, including one
# where VectorBT truly is not installed (the main-tests CI job).
# ---------------------------------------------------------------------

def test_missing_vectorbt_raises_actionable_import_error(monkeypatch):
    sys.modules.pop(_ADAPTER_MODULE, None)
    sys.modules.pop(_PACKAGE_MODULE, None)
    monkeypatch.setitem(sys.modules, "vectorbt", None)
    with pytest.raises(ImportError, match=r'pip install -e "\.\[research\]"'):
        importlib.import_module(_ADAPTER_MODULE)
    sys.modules.pop(_ADAPTER_MODULE, None)
    sys.modules.pop(_PACKAGE_MODULE, None)


# ---------------------------------------------------------------------
# Behavioral coverage -- requires VectorBT actually installed.
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def adapter():
    pytest.importorskip("vectorbt")
    return importlib.import_module(_ADAPTER_MODULE)


# --- Signal-timing contract: no look-ahead bias -----------------------

def test_signal_from_a_price_spike_cannot_buy_before_that_spike(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D", tz="UTC")
    close = pd.Series([100.0] * _N_BARS, index=idx)
    spike_bar = 10
    close.iloc[spike_bar] = 500.0  # one-bar spike, back to 100 immediately after
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[spike_bar, 0] = True  # signal fires because bar 10's own close spiked

    result = adapter.run_parameter_sweep(close, entries, exits)
    orders = result.portfolio.orders.records_readable
    buy_orders = orders[orders["Side"] == "Buy"]
    assert len(buy_orders) == 1
    fill_timestamp = buy_orders.iloc[0]["Timestamp"]
    assert fill_timestamp == idx[spike_bar + 1]
    assert buy_orders.iloc[0]["Price"] == close.iloc[spike_bar + 1]


def test_signal_on_final_bar_produces_no_fill(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[-1, 0] = True  # no next bar exists to execute this on

    result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.trade_count["p1"] == 0


def test_entry_occurs_on_first_eligible_subsequent_bar(adapter):
    close = _close()
    idx = close.index
    signal_bar = 5
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[signal_bar, 0] = True

    result = adapter.run_parameter_sweep(close, entries, exits)
    orders = result.portfolio.orders.records_readable
    buy_orders = orders[orders["Side"] == "Buy"]
    assert len(buy_orders) == 1
    assert buy_orders.iloc[0]["Timestamp"] == idx[signal_bar + 1]


def test_exit_signal_follows_same_timing_rule(adapter):
    close = _close()
    idx = close.index
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[3, 0] = True
    exit_bar = 15
    exits.iloc[exit_bar, 0] = True

    result = adapter.run_parameter_sweep(close, entries, exits)
    orders = result.portfolio.orders.records_readable
    sell_orders = orders[orders["Side"] == "Sell"]
    assert len(sell_orders) == 1
    assert sell_orders.iloc[0]["Timestamp"] == idx[exit_bar + 1]


def test_no_future_close_is_used_to_construct_an_earlier_trade(adapter):
    import vectorbt as vbt

    close = _close()
    entries, exits = _empty_signals(close, columns=("p1", "p2"))
    entries.iloc[4, 0] = True
    exits.iloc[9, 0] = True
    entries.iloc[12, 1] = True
    exits.iloc[20, 1] = True

    result = adapter.run_parameter_sweep(close, entries, exits)

    expected_entries = entries.shift(1, fill_value=False)
    expected_exits = exits.shift(1, fill_value=False)
    expected_portfolio = vbt.Portfolio.from_signals(
        close, expected_entries, expected_exits, init_cash=100_000.0, fees=0.0, freq="1D"
    )
    pd.testing.assert_frame_equal(
        result.portfolio.orders.records_readable,
        expected_portfolio.orders.records_readable,
    )


# --- Temporal / structural validation ----------------------------------

def test_rejects_non_series_close(adapter):
    with pytest.raises(adapter.VectorResearchInputError, match="pandas Series"):
        adapter.run_parameter_sweep([1.0, 2.0], pd.DataFrame(), pd.DataFrame())


def test_rejects_empty_close(adapter):
    with pytest.raises(adapter.VectorResearchInputError, match="not be empty"):
        adapter.run_parameter_sweep(pd.Series(dtype=float), pd.DataFrame(), pd.DataFrame())


def test_rejects_non_datetime_index(adapter):
    close = pd.Series(range(100, 100 + _N_BARS), index=range(_N_BARS), dtype=float)
    entries, exits = _empty_signals(close.set_axis(pd.RangeIndex(_N_BARS)))
    with pytest.raises(adapter.VectorResearchInputError, match="DatetimeIndex"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_tz_naive_index(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D")  # no tz
    close = pd.Series([100.0] * _N_BARS, index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="timezone-aware"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_duplicate_timestamps(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D", tz="UTC")
    idx = idx.insert(5, idx[4])  # duplicate an existing timestamp
    close = pd.Series([100.0] * len(idx), index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="duplicate timestamps"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_unsorted_timestamps(adapter):
    close = _close()
    shuffled = close.iloc[::-1]
    entries, exits = _empty_signals(shuffled)
    with pytest.raises(adapter.VectorResearchInputError, match="strictly increasing"):
        adapter.run_parameter_sweep(shuffled, entries, exits)


def test_rejects_intraday_spacing(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="h", tz="UTC")
    close = pd.Series([100.0] * _N_BARS, index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="daily-session data only"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_irregular_gap_too_large(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="30D", tz="UTC")  # monthly, not daily
    close = pd.Series([100.0] * _N_BARS, index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="gap larger than"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_weekly_cadence_within_gap_bounds(adapter):
    # Every gap is exactly 7 days -- within the [1D, 10D] min/max bound alone,
    # so this only fails via the median-cadence check, not the gap-bound checks.
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="7D", tz="UTC")
    close = pd.Series([100.0] * _N_BARS, index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="median"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_close_shorter_than_minimum_bars(adapter):
    idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    close = pd.Series([100.0, 101.0, 102.0], index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="at least"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_nan_in_close(adapter):
    close = _close()
    close.iloc[5] = float("nan")
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="NaN"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_infinite_price(adapter):
    close = _close()
    close.iloc[5] = float("inf")
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="finite"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_positive_price(adapter):
    close = _close()
    close.iloc[0] = 0.0
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="positive"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_boolean_close(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D", tz="UTC")
    close = pd.Series([True] * _N_BARS, index=idx)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="boolean"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_numeric_string_close(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D", tz="UTC")
    close = pd.Series([str(100.0 + i) for i in range(_N_BARS)], index=idx, dtype=object)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="numeric-looking strings"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_convertible_close(adapter):
    idx = pd.date_range("2024-01-01", periods=_N_BARS, freq="D", tz="UTC")
    close = pd.Series(["not-a-price"] * _N_BARS, index=idx, dtype=object)
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="could not be converted"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_signal_frame_index_mismatch(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    entries = entries.iloc[:-1]
    with pytest.raises(adapter.VectorResearchInputError, match="index must exactly match"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_signal_frame_timezone_mismatch(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    entries.index = entries.index.tz_convert("America/New_York")
    with pytest.raises(adapter.VectorResearchInputError, match="index must exactly match"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_boolean_signal_frame(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    entries = entries.astype(int)
    with pytest.raises(adapter.VectorResearchInputError, match="boolean-valued"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_signal_frame_with_missing_values(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    entries["p1"] = pd.array([False] * (_N_BARS - 1) + [pd.NA], dtype="boolean")
    with pytest.raises(adapter.VectorResearchInputError, match="boolean-valued"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_duplicate_signal_columns(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1", "p2"))
    entries.columns = ["p1", "p1"]
    with pytest.raises(adapter.VectorResearchInputError, match="columns must be unique"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_mismatched_entry_exit_columns(adapter):
    close = _close()
    entries, _ = _empty_signals(close, columns=("p1", "p2"))
    _, exits = _empty_signals(close, columns=("p1", "p3"))
    with pytest.raises(adapter.VectorResearchInputError, match="identical columns"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_positive_init_cash(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="init_cash"):
        adapter.run_parameter_sweep(close, entries, exits, init_cash=0.0)


def test_rejects_nan_init_cash(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="finite"):
        adapter.run_parameter_sweep(close, entries, exits, init_cash=float("nan"))


def test_rejects_negative_fees(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="fees"):
        adapter.run_parameter_sweep(close, entries, exits, fees=-0.001)


def test_rejects_infinite_fees(adapter):
    close = _close()
    entries, exits = _empty_signals(close)
    with pytest.raises(adapter.VectorResearchInputError, match="finite"):
        adapter.run_parameter_sweep(close, entries, exits, fees=float("inf"))


# --- Exploratory-only analytics contract --------------------------------

def test_no_trades_column_has_no_trades_status(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))  # all-False: never trades
    result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.trade_count["p1"] == 0
    assert result.total_return["p1"].status == "no_trades"
    assert result.sharpe_ratio["p1"].status == "no_trades"
    assert result.max_drawdown["p1"].status == "no_trades"
    assert result.total_return["p1"].value is None
    assert result.sharpe_ratio["p1"].value is None
    assert result.max_drawdown["p1"].value is None


def test_all_false_signals_produce_no_trades_for_every_column(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1", "p2", "p3"))
    result = adapter.run_parameter_sweep(close, entries, exits)
    for col in ("p1", "p2", "p3"):
        assert result.trade_count[col] == 0
        assert result.sharpe_ratio[col].status == "no_trades"


def test_one_trade_column_reports_ok_status_with_finite_values(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[3, 0] = True
    exits.iloc[10, 0] = True
    result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.trade_count["p1"] == 1
    assert result.total_return["p1"].status == "ok"
    assert result.sharpe_ratio["p1"].status == "ok"
    assert result.max_drawdown["p1"].status == "ok"
    assert isinstance(result.sharpe_ratio["p1"].value, float)
    assert math.isfinite(result.sharpe_ratio["p1"].value)


def test_zero_variance_column_has_zero_variance_status(adapter):
    close = _close(flat=True)  # flat price -> every return is exactly 0.0
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[3, 0] = True
    exits.iloc[10, 0] = True
    result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.trade_count["p1"] == 1  # a trade did occur, unlike the no_trades case
    assert result.sharpe_ratio["p1"].status == "zero_variance"
    assert result.sharpe_ratio["p1"].value is None


def test_non_finite_metric_without_zero_variance_is_classified_non_finite(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))
    entries.iloc[3, 0] = True
    exits.iloc[10, 0] = True
    with patch.object(
        adapter.vbt.Portfolio,
        "sharpe_ratio",
        return_value=pd.Series([float("nan")], index=["p1"]),
    ):
        result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.trade_count["p1"] == 1
    assert result.sharpe_ratio["p1"].status == "non_finite"
    assert result.sharpe_ratio["p1"].value is None


def test_result_has_vectorbt_exploratory_metric_source_and_frequency_fields(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))
    result = adapter.run_parameter_sweep(close, entries, exits)
    assert result.metric_source == adapter.METRIC_SOURCE == "VECTORBT_EXPLORATORY"
    assert result.frequency == "1D"
    assert result.year_freq == "365 days"


# --- Advisory-only surface ----------------------------------------------

def test_result_carries_no_execution_authority_surface(adapter):
    close = _close()
    entries, exits = _empty_signals(close, columns=("p1",))
    result = adapter.run_parameter_sweep(close, entries, exits)
    for forbidden in ("submit", "order", "broker", "authorize"):
        assert not hasattr(result, forbidden)
