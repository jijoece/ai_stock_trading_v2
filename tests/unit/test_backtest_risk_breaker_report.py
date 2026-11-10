"""The report must say when the engine's drawdown breaker stopped a run, because
results after that point reflect the halt rather than the strategy."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trading_research.backtesting import BacktestConfiguration, EntrySignal, run_backtest
from trading_research.backtesting.data_provider import FixtureHistoricalDataProvider
from trading_research.backtesting.models import (
    BacktestDailyState,
    BacktestError,
    BacktestFill,
    BacktestResult,
    HistoricalBar,
)
from trading_research.backtesting.strategy_backtest_runner import (
    risk_breaker_summary,
    run_strategy_backtest_report,
)
from trading_research.evaluation.market_calendar import regular_session_close
from trading_research.storage.database import connect

from tests.unit._backtest_history_helpers import session_dates

LIMIT = Decimal("0.15")
START, END = date(2023, 1, 3), date(2024, 12, 31)


def _state(day: date, drawdown: str) -> BacktestDailyState:
    return BacktestDailyState(
        market_date=day, cash=Decimal("0"), equity=Decimal("100"), realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"), drawdown_fraction=Decimal(drawdown),
    )


def _result(
    drawdowns: list[str], rejected: list[str], fill_days: tuple[int, ...] = (),
    rejected_days: tuple[int, ...] | None = None, breach_days: tuple[int, ...] = (),
) -> BacktestResult:
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
        rejected_entries=tuple(
            {"signal_id": f"s{i}", "reason": reason, "market_date": base + timedelta(days=day)}
            for i, (reason, day) in enumerate(zip(rejected, rejected_days or tuple(range(len(rejected)))))
        ),
        metrics={}, unresolved_evaluations=(),
        drawdown_breach_sessions=tuple(base + timedelta(days=day) for day in breach_days),
    )


def test_untripped_run_reports_no_halt():
    out = risk_breaker_summary(_result(["0", "-0.05", "-0.10"], []), LIMIT)
    assert out["tripped"] is False
    assert out["first_close_at_limit"] is None
    assert out["closes_at_limit"] == 0 and out["sessions_total"] == 3
    assert out["entries_blocked"] == 0 and out["warning"] is None
    assert out["drawdown_limit"] == "0.15"


def test_tripped_run_reports_when_how_long_and_what_was_blocked():
    out = risk_breaker_summary(
        _result(["0", "-0.10", "-0.16", "-0.16", "-0.16", "-0.16"],
                ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT", "DAILY_LOSS_LIMIT", "NO_NEXT_SESSION"], fill_days=(0, 1),
                rejected_days=(2, 3, 3, 5), breach_days=(2, 3, 4, 5)),
        LIMIT,
    )
    assert out["tripped"] is True
    assert out["first_close_at_limit"] == "2024-01-04"
    assert out["closes_at_limit"] == 4 and out["sessions_total"] == 6
    assert out["share_of_closes_at_limit"] == "0.6667"
    assert out["entries_blocked"] == 2 and out["daily_loss_entries_blocked"] == 1
    assert out["last_fill_session"] == "2024-01-03"
    assert "blocked 2 entries" in out["warning"] and "not the strategy" in out["warning"]
    assert out["blocked_sessions"] == 2
    assert out["first_blocked_session"] == "2024-01-04" and out["last_blocked_session"] == "2024-01-05"
    assert out["resumed"] is False and out["first_fill_after_block"] is None and out["entries_after_block"] == 0
    assert out["ended_at_limit"] is True and "final close was still at the limit" in out["warning"]


def test_block_followed_by_a_later_buy_is_reported_as_resumed_not_halted():
    out = risk_breaker_summary(
        _result(["-0.16", "-0.16", "-0.05", "0", "0"], ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT"],
                fill_days=(0, 3), rejected_days=(1, 2)),
        LIMIT,
    )
    assert out["tripped"] is True and out["entries_blocked"] == 2 and out["blocked_sessions"] == 2
    assert out["first_blocked_session"] == "2024-01-03" and out["last_blocked_session"] == "2024-01-04"
    assert out["resumed"] is True and out["first_fill_after_block"] == "2024-01-05"
    assert out["entries_after_block"] == 1 and out["ended_at_limit"] is False
    assert "does not latch" in out["warning"] and "resumed on 2024-01-05" in out["warning"]
    assert "halt" not in out["warning"] and "no entry has filled" not in out["warning"]


def test_a_sell_after_the_block_is_not_resumed_trading():
    result = _result(["-0.16", "-0.16"], ["DRAWDOWN_LIMIT"], fill_days=(), rejected_days=(0,))
    sell = dataclasses.replace(
        _result(["0"], [], fill_days=(1,)).fills[0], side="SELL",
    )
    result = dataclasses.replace(result, fills=(sell,))
    out = risk_breaker_summary(result, LIMIT)
    assert out["resumed"] is False and out["entries_after_block"] == 0
    assert "no entry has filled" in out["warning"]


def test_recovery_without_a_later_fill_is_not_described_as_a_permanent_halt():
    out = risk_breaker_summary(
        _result(["-0.16", "-0.16", "-0.05", "-0.05"], ["DRAWDOWN_LIMIT"], rejected_days=(1,), breach_days=(1,)),
        LIMIT,
    )
    assert out["tripped"] is True and out["resumed"] is False
    assert out["recovered"] is True and out["recovered_on"] == "2024-01-04"
    assert out["last_breach_session"] == "2024-01-03" and out["ended_at_limit"] is False
    assert "back inside the limit from 2024-01-04" in out["warning"]
    assert "reflect the halt" not in out["warning"] and "cannot recover" not in out["warning"]
    assert "Compare windows that start fresh" not in out["warning"]


def test_run_still_beyond_the_limit_at_the_final_open_is_still_a_halt():
    out = risk_breaker_summary(
        _result(["-0.16", "-0.16", "-0.16"], ["DRAWDOWN_LIMIT"], rejected_days=(0,), breach_days=(0, 1, 2)),
        LIMIT,
    )
    assert out["recovered"] is False and out["recovered_on"] is None
    assert out["last_breach_session"] == "2024-01-04" and out["resumed"] is False
    assert "reflect the halt" in out["warning"] and "final session (2024-01-04)" in out["warning"]


def test_noncontiguous_blocked_sessions_recover_only_after_the_last_breach():
    reasons = ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT"]
    # Blocked on days 0 and 3 with an open back inside the limit on days 1-2: the later breach counts.
    breached = _result(["-0.16", "-0.05", "-0.05", "-0.16", "-0.16"], reasons, rejected_days=(0, 3),
                       breach_days=(0, 3, 4))
    out = risk_breaker_summary(breached, LIMIT)
    assert out["blocked_sessions"] == 2 and out["recovered"] is False
    assert out["last_breach_session"] == "2024-01-06"
    # The same blocks followed by a recovery that holds to the end.
    recovered = _result(["-0.16", "-0.05", "-0.05", "-0.16", "-0.05"], reasons, rejected_days=(0, 3),
                        breach_days=(0, 3))
    out = risk_breaker_summary(recovered, LIMIT)
    assert out["recovered"] is True and out["recovered_on"] == "2024-01-06"
    assert out["first_blocked_session"] == "2024-01-02" and out["last_blocked_session"] == "2024-01-05"


def test_a_breach_after_the_last_block_without_a_signal_delays_recovery():
    out = risk_breaker_summary(
        _result(["-0.16", "-0.16", "-0.05"], ["DRAWDOWN_LIMIT"], rejected_days=(0,), breach_days=(0, 1)),
        LIMIT,
    )
    assert out["last_blocked_session"] == "2024-01-02" and out["last_breach_session"] == "2024-01-03"
    assert out["recovered"] is True and out["recovered_on"] == "2024-01-04"


def test_resumed_then_rebreached_through_the_final_open_discloses_the_terminal_halt():
    # Blocked on day 0, a buy fills on day 2, then the breaker trips again on days 3-4 (the final open).
    out = risk_breaker_summary(
        _result(["-0.16", "-0.05", "0", "-0.16", "-0.16"], ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT"],
                fill_days=(2,), rejected_days=(0, 3), breach_days=(0, 3, 4)),
        LIMIT,
    )
    assert out["resumed"] is True and out["first_fill_after_block"] == "2024-01-04"
    assert out["recovered"] is False and out["recovered_on"] is None
    assert out["last_breach_session"] == "2024-01-06" and out["ended_at_limit"] is True
    warning = out["warning"]
    assert "Entries resumed on 2024-01-04" in warning and "final session (2024-01-06)" in warning
    assert "breaker was active when the run ended" in warning and "Compare windows that start fresh" in warning
    assert "does not latch" not in warning


def test_resumed_then_recovered_at_the_end_is_still_described_as_recovered():
    out = risk_breaker_summary(
        _result(["-0.16", "-0.05", "0", "-0.16", "-0.05"], ["DRAWDOWN_LIMIT", "DRAWDOWN_LIMIT"],
                fill_days=(2,), rejected_days=(0, 3), breach_days=(0, 3)),
        LIMIT,
    )
    assert out["resumed"] is True and out["recovered"] is True and out["recovered_on"] == "2024-01-06"
    assert out["last_breach_session"] == "2024-01-05"
    assert "does not latch" in out["warning"] and "breaker was active when the run ended" not in out["warning"]


def test_blocked_entries_alone_count_as_tripped():
    out = risk_breaker_summary(_result(["0", "-0.05"], ["DRAWDOWN_LIMIT"]), LIMIT)
    assert out["tripped"] is True and out["entries_blocked"] == 1
    assert out["first_close_at_limit"] is None and out["warning"] and out["observation"] is None


def test_closing_drawdown_alone_is_an_observation_not_a_halt():
    out = risk_breaker_summary(_result(["0", "-0.15"], []), LIMIT)  # limit is inclusive
    assert out["tripped"] is False and out["warning"] is None and out["entries_blocked"] == 0
    assert out["closes_at_limit"] == 1 and out["first_close_at_limit"] == "2024-01-03"
    assert "not halted" in out["observation"] and "2024-01-03" in out["observation"]


def test_tripped_run_has_no_observation():
    assert risk_breaker_summary(_result(["-0.16"], ["DRAWDOWN_LIMIT"]), LIMIT)["observation"] is None


# -- engine-produced results: the breaker is evaluated at the next open --------------------------


def _bar(symbol: str, day: date, open_: str, close: str) -> HistoricalBar:
    o, c = Decimal(open_), Decimal(close)
    return HistoricalBar(
        symbol=symbol, session_date=day, open=o, high=max(o, c) + 1, low=min(o, c) - Decimal("0.5"),
        close=c, volume=1000, available_at=regular_session_close(day), source_id="t",
    )


def _engine_run(
    *, aaa: list[tuple[str, str]], bbb: list[tuple[str, str]], last: int, aaa_stop: str = "95",
    retry_bbb: bool = False,
):
    """AAA is bought on session 3 at 100 for 100 shares ($10k of $20k); BBB is signalled after session 3.

    A close of 69 on AAA is a $3,100 loss on $20,000: a 15.5% drawdown, past the 15% limit. With the
    default stop of 95, AAA is sold at the next open and the loss is locked in; a lower `aaa_stop` keeps
    the position open so it can recover. `retry_bbb` adds a second BBB signal after session 4.
    """
    days = session_dates(date(2024, 1, 2), 6)
    bars = {
        "AAA": tuple(_bar("AAA", d, o, c) for d, (o, c) in zip(days, aaa)),
        "BBB": tuple(_bar("BBB", d, o, c) for d, (o, c) in zip(days, bbb)),
    }
    signals = (
        EntrySignal(
            signal_id="aaa", symbol="AAA", generated_after_session=days[2], limit_price=Decimal("100"),
            quantity_hint=Decimal("100"), initial_stop_reference=Decimal(aaa_stop),
            target_reference=Decimal("1000"), maximum_holding_sessions=100,
        ),
        EntrySignal(
            signal_id="bbb", symbol="BBB", generated_after_session=days[3], limit_price=Decimal("50"),
            quantity_hint=Decimal("100"), initial_stop_reference=Decimal("45"),
            target_reference=Decimal("1000"), maximum_holding_sessions=100,
        ),
    )
    if retry_bbb:
        signals += (
            EntrySignal(
                signal_id="bbb-retry", symbol="BBB", generated_after_session=days[4], limit_price=Decimal("50"),
                quantity_hint=Decimal("100"), initial_stop_reference=Decimal("45"),
                target_reference=Decimal("1000"), maximum_holding_sessions=100,
            ),
        )
    config = BacktestConfiguration(
        start_date=days[0], end_date=days[last], symbols=("AAA", "BBB"), initial_cash=Decimal("20000"),
        atr_period=2, risk_fraction=Decimal("0.5"),
    )
    return config, run_backtest(
        configuration=config, data_provider=FixtureHistoricalDataProvider(bars), signals=signals,
    )


FLAT = [("100", "100")] * 3
BBB_FLAT = [("50", "50")] * 6


def test_engine_close_below_limit_that_recovers_by_the_next_open_is_not_a_halt():
    aaa = FLAT + [("100", "69"), ("100", "100"), ("100", "100")]
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=5)
    assert result.daily_states[3].drawdown_fraction <= -LIMIT  # the close crossed the limit
    assert [f.symbol for f in result.fills if f.side == "BUY"] == ["AAA", "BBB"]  # BBB was still allowed in
    assert result.rejected_entries == ()
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["tripped"] is False and out["warning"] is None and out["entries_blocked"] == 0
    assert out["closes_at_limit"] == 1 and "not halted" in out["observation"]


def test_engine_breach_on_the_final_session_is_not_a_halt():
    aaa = FLAT + [("100", "69")]
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=3)
    assert result.daily_states[-1].drawdown_fraction <= -LIMIT
    assert [f.symbol for f in result.fills] == ["AAA"]
    assert [row["reason"] for row in result.rejected_entries] == ["NO_NEXT_SESSION"]
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["tripped"] is False and out["warning"] is None and out["entries_blocked"] == 0
    assert out["observation"]


def test_engine_open_below_limit_blocks_the_entry_and_is_reported_as_a_halt():
    aaa = FLAT + [("100", "69"), ("69", "69"), ("69", "69")]  # still down at the next open
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=5)
    assert [row["reason"] for row in result.rejected_entries] == ["DRAWDOWN_LIMIT"]
    assert [f.symbol for f in result.fills if f.side == "BUY"] == ["AAA"]
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["tripped"] is True and out["entries_blocked"] == 1
    assert out["warning"] and out["observation"] is None
    assert out["resumed"] is False and out["ended_at_limit"] is True
    assert out["first_blocked_session"] == out["last_blocked_session"] == result.daily_states[4].market_date.isoformat()
    assert "no entry has filled" in out["warning"] and "reflect the halt" in out["warning"]


def test_engine_rejection_then_recovery_then_a_later_fill_is_resumed_not_halted():
    # AAA stays open through the 31% dip (stop 50) and gaps back up, so opening drawdown recovers.
    aaa = FLAT + [("100", "69"), ("69", "69"), ("100", "100")]
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=5, aaa_stop="50", retry_bbb=True)
    days = [state.market_date for state in result.daily_states]
    rejected = [(row["signal_id"], row["reason"], row["market_date"]) for row in result.rejected_entries]
    assert rejected == [("bbb", "DRAWDOWN_LIMIT", days[4])]  # blocked at the session-4 open ...
    assert result.daily_states[4].drawdown_fraction <= -LIMIT  # ... still in breach at that close,
    assert result.daily_states[5].drawdown_fraction > -LIMIT  # then recovered
    buys = [(f.symbol, f.market_date) for f in result.fills if f.side == "BUY"]
    assert buys == [("AAA", days[3]), ("BBB", days[5])]  # and a later entry filled
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["tripped"] is True and out["entries_blocked"] == 1 and out["blocked_sessions"] == 1
    assert out["first_blocked_session"] == out["last_blocked_session"] == days[4].isoformat()
    assert out["resumed"] is True and out["first_fill_after_block"] == days[5].isoformat()
    assert out["entries_after_block"] == 1 and out["ended_at_limit"] is False
    assert "does not latch" in out["warning"] and days[5].isoformat() in out["warning"]
    assert "halt" not in out["warning"] and "no entry has filled" not in out["warning"]


def test_engine_recovery_with_no_later_signal_is_not_reported_as_a_permanent_halt():
    aaa = FLAT + [("100", "69"), ("69", "69"), ("100", "100")]
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=5, aaa_stop="50")
    days = [state.market_date for state in result.daily_states]
    assert [(r["signal_id"], r["reason"]) for r in result.rejected_entries] == [("bbb", "DRAWDOWN_LIMIT")]
    assert [f.symbol for f in result.fills if f.side == "BUY"] == ["AAA"]  # nothing filled after the block
    assert result.drawdown_breach_sessions == (days[4],)  # the session-5 open was inside the limit
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["tripped"] is True and out["resumed"] is False
    assert out["recovered"] is True and out["recovered_on"] == days[5].isoformat()
    assert "reflect the halt" not in out["warning"] and days[5].isoformat() in out["warning"]


def test_engine_recovery_followed_by_an_unrelated_rejection_is_not_reported_as_a_permanent_halt():
    aaa = FLAT + [("100", "69"), ("69", "69"), ("100", "100")]
    bbb = [("50", "50")] * 5 + [("60", "60")]  # the retry's 50 limit is not reachable on the last session
    config, result = _engine_run(aaa=aaa, bbb=bbb, last=5, aaa_stop="50", retry_bbb=True)
    days = [state.market_date for state in result.daily_states]
    assert [(r["signal_id"], r["reason"]) for r in result.rejected_entries] == [
        ("bbb", "DRAWDOWN_LIMIT"), ("bbb-retry", "LIMIT_NOT_FILLED"),
    ]
    assert [f.symbol for f in result.fills if f.side == "BUY"] == ["AAA"]
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["entries_blocked"] == 1 and out["resumed"] is False
    assert out["recovered"] is True and out["recovered_on"] == days[5].isoformat()
    assert "reflect the halt" not in out["warning"]


def test_engine_unrecovered_run_records_every_breached_open_even_without_signals():
    aaa = FLAT + [("100", "69"), ("69", "69"), ("69", "69")]
    config, result = _engine_run(aaa=aaa, bbb=BBB_FLAT, last=5)
    days = [state.market_date for state in result.daily_states]
    assert [row["market_date"] for row in result.rejected_entries] == [days[4]]
    assert result.drawdown_breach_sessions == (days[4], days[5])  # session 6 had no signal to reject
    out = risk_breaker_summary(result, config.max_drawdown_fraction)
    assert out["recovered"] is False and out["last_breach_session"] == days[5].isoformat()
    assert out["last_blocked_session"] == days[4].isoformat()
    assert "reflect the halt" in out["warning"]


def test_limit_is_inclusive():
    out = risk_breaker_summary(_result(["-0.15"], ["DRAWDOWN_LIMIT"]), LIMIT)
    assert out["tripped"] is True and out["closes_at_limit"] == 1


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
        assert {
            "tripped", "first_close_at_limit", "entries_blocked", "warning", "observation", "drawdown_limit",
        } <= set(block["risk_breaker"])
    assert out["summary"]["risk_breaker"]["entries_blocked"] == out["engine_report"]["drawdown_blocks"]


def test_a_tripped_run_is_flagged_in_the_report(tmp_path):
    # Large per-order fees force a drawdown past the 15% limit on the fixture data.
    out = _report(tmp_path, fee_per_order=Decimal("2000"))
    breaker = out["summary"]["risk_breaker"]
    assert breaker["tripped"] is True
    assert breaker["first_close_at_limit"] is not None
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
