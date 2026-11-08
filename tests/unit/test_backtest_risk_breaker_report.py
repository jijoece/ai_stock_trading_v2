"""The report must say when the engine's drawdown breaker stopped a run, because
results after that point reflect the halt rather than the strategy."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trading_research.backtesting.models import BacktestDailyState, BacktestError, BacktestFill, BacktestResult
from trading_research.backtesting.strategy_backtest_runner import (
    risk_breaker_summary,
    run_strategy_backtest_report,
)
from trading_research.storage.database import connect

LIMIT = Decimal("0.15")
START, END = date(2023, 1, 3), date(2024, 12, 31)


def _state(day: date, drawdown: str) -> BacktestDailyState:
    return BacktestDailyState(
        market_date=day, cash=Decimal("0"), equity=Decimal("100"), realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"), drawdown_fraction=Decimal(drawdown),
    )


def _result(drawdowns: list[str], rejected: list[str], fill_days: tuple[int, ...] = ()) -> BacktestResult:
    base = date(2024, 1, 2)
    states = tuple(_state(base + timedelta(days=i), d) for i, d in enumerate(drawdowns))
    fills = tuple(
        BacktestFill(
            fill_id=f"f{i}", order_id=f"o{i}", symbol="AAA", side="BUY", quantity=Decimal("1"),
            fill_price=Decimal("1"), fees=Decimal("0"), slippage=Decimal("0"),
            market_date=base + timedelta(days=offset),
        )
        for i, offset in enumerate(fill_days)
    )
    return BacktestResult(
        backtest_run_id="r", configuration_hash="h", daily_states=states, fills=fills,
        rejected_entries=tuple({"signal_id": f"s{i}", "reason": reason} for i, reason in enumerate(rejected)),
        metrics={}, unresolved_evaluations=(),
    )


def test_untripped_run_reports_no_halt():
    out = risk_breaker_summary(_result(["0", "-0.05", "-0.10"], []), LIMIT)
    assert out["tripped"] is False
    assert out["first_session_at_limit"] is None
    assert out["sessions_at_limit"] == 0 and out["sessions_total"] == 3
    assert out["entries_blocked"] == 0 and out["warning"] is None
    assert out["drawdown_limit"] == "0.15"


def test_tripped_run_reports_when_how_long_and_what_was_blocked():
    out = risk_breaker_summary(
        _result(["0", "-0.10", "-0.16", "-0.16", "-0.16", "-0.16"],
                ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT", "DAILY_LOSS_LIMIT", "NO_NEXT_SESSION"], fill_days=(0, 1)),
        LIMIT,
    )
    assert out["tripped"] is True
    assert out["first_session_at_limit"] == "2024-01-04"
    assert out["sessions_at_limit"] == 4 and out["sessions_total"] == 6
    assert out["share_of_sessions_at_limit"] == "0.6667"
    assert out["entries_blocked"] == 2 and out["daily_loss_entries_blocked"] == 1
    assert out["last_fill_session"] == "2024-01-03"
    assert "2024-01-04" in out["warning"] and "not the strategy" in out["warning"]


def test_blocked_entries_alone_count_as_tripped():
    out = risk_breaker_summary(_result(["0", "-0.05"], ["DRAWDOWN_LIMIT"]), LIMIT)
    assert out["tripped"] is True and out["entries_blocked"] == 1
    assert out["first_session_at_limit"] is None and out["warning"]


def test_limit_is_inclusive():
    assert risk_breaker_summary(_result(["-0.15"], []), LIMIT)["tripped"] is True


def _report(tmp_path, name="bt", **overrides):
    kwargs: dict[str, Any] = dict(
        strategy_id="momentum_breakout", symbols=("AAA", "BBB", "CCC"), start=START, end=END,
        initial_cash=Decimal("100000"), data_source="fixture", snapshot_path=None,
        conn=connect(tmp_path / f"{name}.sqlite3"), env={},
    )
    kwargs.update(overrides)
    return run_strategy_backtest_report(**kwargs)


def test_every_report_block_carries_a_breaker_summary_matching_the_engine(tmp_path):
    out = _report(tmp_path, split_date=date(2023, 12, 29), cost_grid=(Decimal("0"), Decimal("10")))
    blocks = [out["summary"], *(r["summary"] for r in out["cost_sensitivity"]),
              out["split"]["in_sample"]["summary"], out["split"]["out_of_sample"]["summary"]]
    for block in blocks:
        assert {"tripped", "first_session_at_limit", "entries_blocked", "warning", "drawdown_limit"} <= set(block["risk_breaker"])
    assert out["summary"]["risk_breaker"]["entries_blocked"] == out["engine_report"]["drawdown_blocks"]


def test_a_tripped_run_is_flagged_in_the_report(tmp_path):
    # Large per-order fees force a drawdown past the 15% limit on the fixture data.
    out = _report(tmp_path, fee_per_order=Decimal("2000"))
    breaker = out["summary"]["risk_breaker"]
    assert breaker["tripped"] is True
    assert breaker["first_session_at_limit"] is not None
    assert breaker["entries_blocked"] == out["engine_report"]["drawdown_blocks"] > 0
    assert breaker["warning"]


# -- configurable breaker limit ---------------------------------------------------


def test_default_limit_is_reported_and_carries_no_caveat(tmp_path):
    out = _report(tmp_path)
    assert out["run"]["max_drawdown_fraction"] == "0.15"
    assert out["summary"]["risk_breaker"]["drawdown_limit"] == "0.15"
    assert not any("circuit breaker" in c for c in out["caveats"])


def test_looser_limit_lets_a_run_keep_trading_and_changes_the_run_identity(tmp_path):
    default = _report(tmp_path, name="d", fee_per_order=Decimal("2000"))
    loose = _report(tmp_path, name="l", fee_per_order=Decimal("2000"), max_drawdown_fraction=Decimal("0.99"))
    assert default["summary"]["risk_breaker"]["tripped"] is True
    assert loose["summary"]["risk_breaker"]["tripped"] is False
    assert loose["summary"]["risk_breaker"]["drawdown_limit"] == "0.99"
    assert loose["summary"]["risk_breaker"]["entries_blocked"] == 0
    assert loose["summary"]["round_trips"] > default["summary"]["round_trips"]
    assert loose["run"]["backtest_run_id"] != default["run"]["backtest_run_id"]
    assert loose["run"]["max_drawdown_fraction"] == "0.99"
    assert any("circuit breaker" in c and "0.99" in c for c in loose["caveats"])


def test_limit_applies_to_cost_rows_and_split_windows(tmp_path):
    out = _report(
        tmp_path, max_drawdown_fraction=Decimal("0.5"), split_date=date(2023, 12, 29), cost_grid=(Decimal("0"),),
    )
    limits = {
        out["summary"]["risk_breaker"]["drawdown_limit"],
        out["cost_sensitivity"][0]["summary"]["risk_breaker"]["drawdown_limit"],
        out["split"]["in_sample"]["summary"]["risk_breaker"]["drawdown_limit"],
        out["split"]["out_of_sample"]["summary"]["risk_breaker"]["drawdown_limit"],
    }
    assert limits == {"0.5"}


@pytest.mark.parametrize("bad", ["0", "1", "1.5", "-0.1"])
def test_out_of_range_limits_are_rejected(tmp_path, bad):
    with pytest.raises(BacktestError, match="fractions"):
        _report(tmp_path, max_drawdown_fraction=Decimal(bad))
