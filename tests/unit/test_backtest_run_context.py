"""Run identity must cover the data and screening context a run used, not only
config + signals (docs/historical-backtesting-plan.md: the snapshot content hash
becomes part of the run identity)."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from trading_research.backtesting import BacktestConfiguration, EntrySignal, run_backtest
from trading_research.backtesting.data_provider import FixtureHistoricalDataProvider
from trading_research.backtesting.models import BacktestError
from trading_research.storage.database import connect

from tests.unit._backtest_history_helpers import make_bars


def _setup():
    bars = make_bars("AAA", [100.0 + i for i in range(10)], start=date(2024, 1, 2))
    provider = FixtureHistoricalDataProvider({"AAA": bars})
    config = BacktestConfiguration(
        start_date=bars[0].session_date, end_date=bars[-1].session_date,
        symbols=("AAA",), initial_cash=Decimal("10000"), atr_period=2,
    )
    signal = EntrySignal(
        signal_id="s", symbol="AAA", generated_after_session=bars[2].session_date,
        limit_price=Decimal("104"), quantity_hint=Decimal("10"),
    )
    return provider, config, signal


def test_run_context_changes_the_run_id():
    provider, config, signal = _setup()
    a = run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context={"snapshot": "aaa"})
    b = run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context={"snapshot": "bbb"})
    again = run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context={"snapshot": "aaa"})
    assert a.backtest_run_id != b.backtest_run_id
    assert a.backtest_run_id == again.backtest_run_id


def test_absent_run_context_keeps_the_legacy_run_id():
    provider, config, signal = _setup()
    legacy = run_backtest(configuration=config, data_provider=provider, signals=(signal,))
    explicit_none = run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context=None)
    with_context = run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context={"x": "y"})
    assert legacy.backtest_run_id == explicit_none.backtest_run_id != with_context.backtest_run_id
    assert "run_context" not in legacy.metrics


def test_run_context_is_reported_and_persisted(tmp_path):
    provider, config, signal = _setup()
    conn = connect(tmp_path / "bt.sqlite3")
    result = run_backtest(
        configuration=config, data_provider=provider, signals=(signal,), conn=conn,
        run_context={"snapshot_hash": "abc123"},
    )
    assert result.metrics["run_context"] == {"snapshot_hash": "abc123"}
    stored = conn.execute(
        "SELECT report_json FROM backtest_runs WHERE backtest_run_id = ?", (result.backtest_run_id,),
    ).fetchone()
    assert json.loads(stored[0])["run_context"] == {"snapshot_hash": "abc123"}


def test_run_context_must_be_hashable_canonical_data():
    provider, config, signal = _setup()
    with pytest.raises(BacktestError):
        run_backtest(configuration=config, data_provider=provider, signals=(signal,), run_context={"bad": object()})


def test_strategy_adapter_forwards_run_context():
    from trading_research.strategies.backtest_adapter import run_strategy_backtest

    provider, config, _ = _setup()
    a = run_strategy_backtest(
        strategy_id="momentum_breakout", signals=(), configuration=config, data_provider=provider,
        run_context={"snapshot": "aaa"},
    )
    b = run_strategy_backtest(
        strategy_id="momentum_breakout", signals=(), configuration=config, data_provider=provider,
        run_context={"snapshot": "bbb"},
    )
    assert a.backtest_result.backtest_run_id != b.backtest_result.backtest_run_id
