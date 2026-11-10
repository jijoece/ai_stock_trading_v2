"""Step 2 of docs/historical-backtesting-plan.md: point-in-time signal replay."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.configuration import BacktestConfiguration
from trading_research.backtesting.data_provider import FixtureHistoricalDataProvider
from trading_research.backtesting.models import BacktestError
from trading_research.backtesting.signal_replay import assert_point_in_time, replay_signals
from trading_research.evaluation.market_calendar import regular_session_close
from trading_research.models.trading_models import DataFreshness, TechnicalFactorInput
from trading_research.strategies.backtest_adapter import run_strategy_backtest
from trading_research.strategies.config import load_strategy_config
from trading_research.strategies.contracts import StrategyStatus
from trading_research.strategies.mean_reversion import MeanReversionStrategy
from trading_research.strategies.momentum_breakout import MomentumBreakoutStrategy

from tests.unit._backtest_history_helpers import make_bars, session_dates

STRATEGIES = load_strategy_config()
SCREENING = replace(load_screening_config(), max_share_price=10_000.0)

N_BARS = 120
JUMP = 90  # index of the engineered breakout session


def _stock_closes() -> list[float]:
    closes = [100.0 + 0.1 * i for i in range(N_BARS)]
    for i in range(JUMP, N_BARS):
        closes[i] = closes[JUMP - 1] * 1.015 + 0.1 * (i - JUMP)
    return closes


def _datasets():
    volumes = [1_000_000] * N_BARS
    volumes[JUMP] = 3_000_000
    stock = make_bars("AAA", _stock_closes(), volumes=volumes)
    bench = make_bars("SPY", [400.0] * N_BARS)
    return stock, bench


def _provider(stock=None, bench=None):
    s, b = _datasets()
    return FixtureHistoricalDataProvider({"AAA": stock or s, "SPY": bench or b})


def _momentum():
    return MomentumBreakoutStrategy(STRATEGIES.momentum_breakout)


def _replay(provider=None, *, strategy=None, end=None, symbols=("AAA",)):
    days = session_dates(date(2024, 1, 2), N_BARS)
    return replay_signals(
        strategy=strategy or _momentum(), provider=provider or _provider(), symbols=symbols,
        start=days[60], end=end or days[-1], history_start=days[0], screening_config=SCREENING,
        benchmark_symbol="SPY",
    )


DAYS = session_dates(date(2024, 1, 2), N_BARS)


# -- behaviour ---------------------------------------------------------------


def test_replay_finds_exactly_the_engineered_breakout():
    result = _replay()
    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.symbol == "AAA" and signal.status == StrategyStatus.ELIGIBLE
    assert signal.data_as_of is not None and signal.data_as_of.date() == DAYS[JUMP]
    assert signal.signal_timestamp == regular_session_close(DAYS[JUMP])


def test_status_counts_add_up_to_total_evaluations():
    result = _replay()
    assert result.sessions == tuple(DAYS[60:])
    assert result.evaluations == len(DAYS[60:]) * 1
    assert sum(result.status_counts.values()) == result.evaluations
    assert result.status_counts[StrategyStatus.ELIGIBLE.value] == 1
    assert all(":" not in code for code in result.reason_code_counts)
    assert result.reason_code_counts["breakout_confirmed"] >= 1


def test_report_records_profile_definition_and_strategy_identity():
    result = _replay()
    assert result.screening_profile["profile"] == "backtest-screening/1"
    assert result.screening_profile["not_evaluated"]
    assert result.relative_strength_definition["version"] == 1
    assert (result.strategy_id, result.strategy_version) == ("momentum_breakout", "1.0.0")
    assert result.configuration_hash == STRATEGIES.momentum_breakout.configuration_hash
    assert result.benchmark_symbol == "SPY"


def test_rerun_is_identical():
    assert _replay() == _replay()


def test_truncation_and_future_perturbation_leave_earlier_signals_unchanged():
    cutoff = JUMP + 3
    full = _replay(end=DAYS[cutoff])
    stock, bench = _datasets()
    truncated = _replay(
        _provider(stock[: cutoff + 1], bench[: cutoff + 1]), end=DAYS[cutoff],
    )
    assert truncated.signals == full.signals and len(full.signals) == 1

    def scramble(bars):
        out = list(bars)
        for i in range(cutoff + 1, len(out)):
            b = out[i]
            out[i] = replace(
                b, open=b.open * 5, high=b.high * 5, low=b.low * 5, close=b.close * 5, volume=b.volume * 9,
            )
        return tuple(out)

    perturbed = _replay(_provider(scramble(stock), scramble(bench)))
    early = tuple(s for s in perturbed.signals if s.data_as_of is not None and s.data_as_of.date() <= DAYS[cutoff])
    assert early == full.signals


def test_cutting_before_the_breakout_removes_the_signal():
    stock, bench = _datasets()
    result = _replay(_provider(stock[:JUMP], bench[:JUMP]), end=DAYS[JUMP - 1])
    assert result.signals == ()


def test_replayed_signals_drive_the_existing_engine():
    result = _replay()
    config = BacktestConfiguration(
        start_date=DAYS[60], end_date=DAYS[-1], symbols=("AAA",), initial_cash=Decimal("100000"),
    )
    backtest = run_strategy_backtest(
        strategy_id="momentum_breakout", signals=result.signals, configuration=config, data_provider=_provider(),
    )
    assert backtest.metrics.number_of_signals == 1
    buys = [f for f in backtest.backtest_result.fills if f.side == "BUY"]
    assert buys and buys[0].market_date == DAYS[JUMP + 1]


# -- look-ahead protection -----------------------------------------------------


def test_bar_claiming_availability_before_its_session_closed_is_rejected():
    stock, bench = _datasets()
    poisoned = list(stock)
    poisoned[100] = replace(poisoned[100], available_at=regular_session_close(DAYS[70]))
    with pytest.raises(BacktestError, match="before its session"):
        _replay(_provider(tuple(poisoned), bench))


def test_assert_point_in_time_guard():
    stock, _ = _datasets()
    now = stock[50].available_at
    assert_point_in_time(session=DAYS[50], now=now, bars=stock[:51], technical=None)
    with pytest.raises(BacktestError, match="available_at"):
        assert_point_in_time(session=DAYS[50], now=now, bars=stock[:52], technical=None)
    future_session = replace(stock[50], session_date=DAYS[51])
    with pytest.raises(BacktestError, match="session"):
        assert_point_in_time(session=DAYS[50], now=now, bars=stock[:50] + (future_session,), technical=None)
    future_freshness = TechnicalFactorInput(
        symbol="AAA", relative_strength=0.1, freshness=DataFreshness(source="x", as_of=now + timedelta(seconds=1)),
    )
    with pytest.raises(BacktestError, match="freshness"):
        assert_point_in_time(session=DAYS[50], now=now, bars=stock[:51], technical=future_freshness)
    fine = TechnicalFactorInput(symbol="AAA", relative_strength=0.1, freshness=DataFreshness(source="x", as_of=now))
    assert_point_in_time(session=DAYS[50], now=now, bars=stock[:51], technical=fine)


def test_mixed_strategy_versions_in_one_run_are_refused():
    class Flipping:
        strategy_id = "momentum_breakout"
        strategy_version = "1.0.0"

        def __init__(self):
            self._inner, self._n = _momentum(), 0

        def evaluate(self, symbol, market_data, context):
            self._n += 1
            signal = self._inner.evaluate(symbol, market_data, context)
            return replace(signal, strategy_version="2.0.0") if self._n > 5 else signal

    with pytest.raises(BacktestError, match="mixed"):
        _replay(strategy=Flipping())


def test_missing_benchmark_history_fails_closed():
    stock, _ = _datasets()
    with pytest.raises(BacktestError, match="SPY"):
        _replay(FixtureHistoricalDataProvider({"AAA": stock}))


def test_end_before_start_is_rejected():
    with pytest.raises(BacktestError):
        replay_signals(
            strategy=_momentum(), provider=_provider(), symbols=("AAA",), start=DAYS[80], end=DAYS[70],
            history_start=DAYS[0], screening_config=SCREENING, benchmark_symbol="SPY",
        )


# -- screening and strategy-input honesty ----------------------------------------


def test_production_share_price_cap_blocks_expensive_symbols():
    production = load_screening_config()  # max_share_price 25 in this repository
    days = DAYS
    result = replay_signals(
        strategy=_momentum(), provider=_provider(), symbols=("AAA",), start=days[60], end=days[-1],
        history_start=days[0], screening_config=production, benchmark_symbol="SPY",
    )
    assert result.signals == ()
    assert result.reason_code_counts.get("screener_gate_failed", 0) > 0 or any(
        k.startswith("screener_gate_failed") for k in result.reason_code_counts
    )


def test_mean_reversion_is_incomplete_without_point_in_time_catalyst_data():
    closes = [100.0 + (i % 7) * 0.3 for i in range(260)]
    provider = FixtureHistoricalDataProvider({
        "AAA": make_bars("AAA", closes), "SPY": make_bars("SPY", [400.0 + (i % 5) for i in range(260)]),
    })
    days = session_dates(date(2024, 1, 2), 260)
    result = replay_signals(
        strategy=MeanReversionStrategy(STRATEGIES.mean_reversion), provider=provider, symbols=("AAA",),
        start=days[230], end=days[-1], history_start=days[0], screening_config=SCREENING, benchmark_symbol="SPY",
    )
    assert result.signals == ()
    assert result.reason_code_counts.get("missing_catalyst_risk_data", 0) == len(days[230:])
    assert result.status_counts[StrategyStatus.INCOMPLETE.value] == len(days[230:])
