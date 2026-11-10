"""Step 2 / D1: backtest screening profile — only bar-computable gates are
evaluated; every other production gate is recorded NOT_EVALUATED."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.screening_profile import (
    EVALUATED_GATES,
    PROFILE_ID,
    backtest_screening_result,
    describe_profile,
    not_evaluated_gates,
)

from tests.unit._backtest_history_helpers import make_bars

CONFIG = load_screening_config()


def _gates(result):
    return {g.gate: g for g in result.gate_results}


def test_only_bar_computable_gates_are_emitted():
    bars = make_bars("AAA", [10.0] * 30)
    result = backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at)
    assert set(_gates(result)) == set(EVALUATED_GATES)
    assert result.passed
    assert result.config_hash and result.symbol == "AAA"


def test_share_price_gate_uses_latest_close():
    bars = make_bars("AAA", [10.0] * 29 + [CONFIG.max_share_price + 1])
    result = backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at)
    gate = _gates(result)["max_share_price"]
    assert not gate.passed and gate.observed == pytest.approx(CONFIG.max_share_price + 1)


def test_dollar_volume_is_mean_of_last_20_sessions():
    # 19 sessions at $1M, the latest at $41M => mean exactly $3M, above the floor.
    closes, volumes = [10.0] * 30, [100_000] * 29 + [4_100_000]
    bars = make_bars("AAA", closes, volumes=volumes)
    gate = _gates(backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at))[
        "min_avg_daily_dollar_volume"]
    assert gate.observed == pytest.approx(3_000_000.0) and gate.passed
    thin = make_bars("AAA", [10.0] * 30, volumes=[1_000] * 30)
    gate = _gates(backtest_screening_result("AAA", thin, config=CONFIG, now=thin[-1].available_at))[
        "min_avg_daily_dollar_volume"]
    assert not gate.passed


def test_realized_volatility_gate_fails_for_wild_series():
    closes = [10.0 if i % 2 else 14.0 for i in range(30)]
    bars = make_bars("AAA", closes)
    gate = _gates(backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at))[
        "max_realized_volatility"]
    assert not gate.passed and gate.observed > CONFIG.max_realized_volatility


def test_staleness_gate_compares_now_to_latest_bar():
    bars = make_bars("AAA", [10.0] * 30)
    fresh = backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at)
    assert _gates(fresh)["max_data_staleness_seconds"].passed
    later = bars[-1].available_at + timedelta(seconds=CONFIG.max_data_staleness_seconds + 1)
    stale = backtest_screening_result("AAA", bars, config=CONFIG, now=later)
    assert not _gates(stale)["max_data_staleness_seconds"].passed


def test_insufficient_or_missing_bars_fail_closed():
    bars = make_bars("AAA", [10.0] * 5)
    result = backtest_screening_result("AAA", bars, config=CONFIG, now=bars[-1].available_at)
    gates = _gates(result)
    assert not gates["min_avg_daily_dollar_volume"].passed and gates["min_avg_daily_dollar_volume"].observed is None
    assert not gates["max_realized_volatility"].passed and gates["max_realized_volatility"].observed is None
    empty = backtest_screening_result("AAA", (), config=CONFIG, now=bars[-1].available_at)
    assert not empty.passed and all(not g.passed for g in empty.gate_results)


def test_bars_after_now_are_not_used():
    bars = make_bars("AAA", [10.0] * 30 + [9999.0])
    result = backtest_screening_result("AAA", bars, config=CONFIG, now=bars[29].available_at)
    assert _gates(result)["max_share_price"].observed == pytest.approx(10.0)


def test_not_evaluated_gates_cover_the_rest_of_production_screening():
    names = not_evaluated_gates(CONFIG)
    assert not set(names) & set(EVALUATED_GATES)
    for gate in ("min_market_cap", "exclude_going_concern_warning", "max_bid_ask_spread_bps",
                 "earnings_blackout_days", "exclude_recent_halt", "exclude_recent_reverse_split",
                 "min_operating_history_years", "max_dilution_share_growth_yoy", "min_cash_runway_quarters"):
        assert gate in names
    relaxed = replace(CONFIG, exclude_recent_halt=False, exclude_otc=False)
    assert "exclude_recent_halt" not in not_evaluated_gates(relaxed)
    assert "exclude_otc" not in not_evaluated_gates(relaxed)


def test_profile_description_is_deterministic_and_complete():
    a, b = describe_profile(CONFIG), describe_profile(CONFIG)
    assert a == b
    assert a["profile"] == PROFILE_ID
    assert a["source_config_hash"] == CONFIG.config_hash
    assert {g["gate"] for g in a["evaluated"]} == set(EVALUATED_GATES)
    assert all(g["status"] == "EVALUATED" for g in a["evaluated"])
    assert {g["gate"] for g in a["not_evaluated"]} == set(not_evaluated_gates(CONFIG))
    assert all(g["status"] == "NOT_EVALUATED" for g in a["not_evaluated"])
