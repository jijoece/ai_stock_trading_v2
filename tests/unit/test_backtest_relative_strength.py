"""Step 2 / D5: point-in-time relative strength = symbol N-session return minus
benchmark N-session return over the same sessions."""
from __future__ import annotations

import pytest

from trading_research.backtesting.relative_strength import (
    RELATIVE_STRENGTH_DEFINITION,
    compute_relative_strength,
)

from tests.unit._backtest_history_helpers import make_bars


def _linear(start: float, end: float, n: int) -> list[float]:
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def test_value_is_symbol_return_minus_benchmark_return():
    stock = make_bars("AAA", _linear(100.0, 110.0, 21))
    bench = make_bars("SPY", _linear(400.0, 420.0, 21))
    rs = compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20)
    assert rs is not None
    assert rs.value == pytest.approx(0.10 - 0.05)
    assert (rs.window_start, rs.window_end) == (stock[0].session_date, stock[-1].session_date)


def test_uses_only_the_trailing_window_of_n_sessions():
    closes = [50.0] * 10 + _linear(100.0, 110.0, 21)
    stock = make_bars("AAA", closes)
    bench = make_bars("SPY", [400.0] * 10 + _linear(400.0, 420.0, 21))
    rs = compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20)
    assert rs is not None and rs.value == pytest.approx(0.05)


def test_as_of_is_latest_available_at_of_bars_used():
    stock = make_bars("AAA", _linear(100.0, 110.0, 21))
    bench = make_bars("SPY", _linear(400.0, 420.0, 21))
    rs = compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20)
    assert rs is not None
    assert rs.as_of == max(stock[-1].available_at, bench[-1].available_at)
    assert rs.as_of <= stock[-1].available_at


def test_bars_after_now_are_ignored():
    stock = make_bars("AAA", _linear(100.0, 110.0, 21) + [500.0])
    bench = make_bars("SPY", _linear(400.0, 420.0, 21) + [900.0])
    now = stock[20].available_at
    rs = compute_relative_strength(stock, bench, now=now, sessions=20)
    assert rs is not None
    assert rs.value == pytest.approx(0.05)
    assert rs.as_of <= now


def test_insufficient_history_returns_none():
    stock = make_bars("AAA", _linear(100.0, 110.0, 20))
    bench = make_bars("SPY", _linear(400.0, 420.0, 20))
    assert compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20) is None


def test_symbol_missing_a_window_session_returns_none():
    stock = make_bars("AAA", _linear(100.0, 110.0, 21))
    bench = make_bars("SPY", _linear(400.0, 420.0, 21))
    gappy = stock[:-1]  # symbol lacks the latest benchmark session
    assert compute_relative_strength(gappy, bench, now=bench[-1].available_at, sessions=20) is None
    no_start = stock[1:]  # symbol lacks the window start session
    assert compute_relative_strength(no_start, bench, now=bench[-1].available_at, sessions=20) is None


def test_definition_is_versioned_and_deterministic():
    assert RELATIVE_STRENGTH_DEFINITION["version"] == 1
    assert RELATIVE_STRENGTH_DEFINITION["formula"]
    stock = make_bars("AAA", _linear(100.0, 110.0, 21))
    bench = make_bars("SPY", _linear(400.0, 420.0, 21))
    a = compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20)
    b = compute_relative_strength(stock, bench, now=stock[-1].available_at, sessions=20)
    assert a == b
