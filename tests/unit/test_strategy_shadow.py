"""Phase 2 of docs/paper-testing-plan.md: shadow tracking of stored strategy signals."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from trading_research.backtesting.fixture_history import build_fixture_snapshot
from trading_research.backtesting.models import BacktestError, HistoricalBar
from trading_research.backtesting.signal_replay import replay_signals
from trading_research.backtesting.strategy_backtest_runner import build_strategy
from trading_research.evaluation.market_calendar import add_trading_days, regular_session_close
from trading_research.forward_testing.candidates import run_strategy_candidates
from trading_research.forward_testing.shadow import (
    CLOSED,
    OPEN,
    PENDING,
    entry_signal_from_row,
    evaluate_signal_outcome,
    run_shadow_update,
)
from trading_research.storage.database import connect
from trading_research.storage.strategy_shadow_repositories import list_terminal_outcomes
from trading_research.storage.strategy_signals_repositories import list_strategy_signals
from trading_research.strategies.backtest_adapter import strategy_signal_to_entry_signal
from trading_research.strategies.config import DEFAULT_STRATEGY_CONFIG_PATH

SYMBOLS = ("AAA", "BBB", "CCC")
START, END = date(2023, 1, 3), date(2024, 12, 31)
NOW = datetime(2025, 3, 1, tzinfo=timezone.utc)
UNIVERSE = ("AAA", "BBB", "CCC", "SPY")


@pytest.fixture()
def stage4_config(tmp_path):
    text = DEFAULT_STRATEGY_CONFIG_PATH.read_text()
    patched = re.sub(r"activation_stage: \w+", "activation_stage: STAGE_4_SHADOW_RECOMMENDATION_TRACKING", text)
    path = tmp_path / "strategies.yaml"
    path.write_text(patched)
    return path


@pytest.fixture(scope="module")
def wide_snapshot():
    return build_fixture_snapshot(
        symbols=UNIVERSE, start=START, end=END, adjustment="split",
        as_of=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )


@pytest.fixture(scope="module")
def full_replay(wide_snapshot):
    return replay_signals(
        strategy=build_strategy("momentum_breakout", None), provider=AlpacaHistoricalDataProvider(wide_snapshot),
        symbols=SYMBOLS, start=START, end=END, history_start=warmup_start(START),
        screening_config=load_screening_config(None),
    )


@pytest.fixture()
def stored(tmp_path, stage4_config, full_replay):
    """A database holding one session's stored evaluations, with an eligible signal."""
    conn = connect(tmp_path / "db.sqlite3")
    day = full_replay.signals[0].data_as_of.date()
    run_strategy_candidates(
        strategy_ids=("momentum_breakout",), symbols=SYMBOLS, as_of=day, data_source="fixture",
        snapshot_path=tmp_path / "cand.snapshot.json", conn=conn, strategy_config_path=stage4_config, now=NOW,
    )
    return conn, day


def _update(tmp_path, conn, as_of, config, **overrides):
    kwargs = dict(
        as_of=as_of, data_source="fixture", snapshot_path=tmp_path / f"shadow-{as_of}.snapshot.json", conn=conn,
        strategy_config_path=config, now=NOW,
    )
    kwargs.update(overrides)
    return run_shadow_update(**kwargs)


def _eligible_rows(conn):
    return [r for r in list_strategy_signals(conn) if r["status"] == "ELIGIBLE"]


def test_stored_row_rebuilds_exactly_the_adapters_entry_signal(stored, full_replay):
    conn, day = stored
    expected = {
        s.symbol: strategy_signal_to_entry_signal(s, quantity_hint=Decimal(1))
        for s in full_replay.signals if s.data_as_of.date() == day
    }
    rows = _eligible_rows(conn)
    assert rows
    for row in rows:
        assert entry_signal_from_row(row) == expected[row["symbol"]]


def test_next_day_is_pending_then_resolves_and_never_reopens(tmp_path, stored, stage4_config):
    conn, day = stored
    same_day = _update(tmp_path, conn, day, stage4_config)
    assert same_day["states"] == {PENDING: len(_eligible_rows(conn))}
    assert list_terminal_outcomes(conn, strategy_id="momentum_breakout") == []

    horizon = add_trading_days(day, 40)
    later = _update(tmp_path, conn, horizon, stage4_config)
    assert later["newly_terminal"] == later["unresolved_signals"] > 0
    terminal = list_terminal_outcomes(conn, strategy_id="momentum_breakout")
    assert len(terminal) == len(_eligible_rows(conn))
    assert {r["state"] for r in terminal} <= {CLOSED, "NO_FILL", "REJECTED"}

    after = _update(tmp_path, conn, add_trading_days(horizon, 1), stage4_config)
    assert after["unresolved_signals"] == 0


def test_replaying_a_day_is_idempotent(tmp_path, stored, stage4_config):
    conn, day = stored
    first = _update(tmp_path, conn, add_trading_days(day, 3), stage4_config)
    count = conn.execute("SELECT COUNT(*) FROM strategy_shadow_outcomes").fetchone()[0]
    second = _update(tmp_path, conn, add_trading_days(day, 3), stage4_config)
    assert conn.execute("SELECT COUNT(*) FROM strategy_shadow_outcomes").fetchone()[0] == count
    assert first["recorded"] == count
    assert second["recorded"] == 0


def test_changed_cost_assumption_for_the_same_day_is_a_conflict(tmp_path, stored, stage4_config):
    from trading_research.storage.strategy_shadow_repositories import ShadowOutcomeConflictError

    conn, day = stored
    session = add_trading_days(day, 3)
    _update(tmp_path, conn, session, stage4_config)
    if not conn.execute("SELECT 1 FROM strategy_shadow_outcomes WHERE row_kind = 'PROGRESS'").fetchone():
        pytest.skip("every signal resolved on the first update; nothing left to conflict with")
    with pytest.raises(ShadowOutcomeConflictError):
        _update(tmp_path, conn, session, stage4_config, slippage_bps=Decimal("99"))


def test_rows_are_append_only(tmp_path, stored, stage4_config):
    conn, day = stored
    _update(tmp_path, conn, day, stage4_config)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE strategy_shadow_outcomes SET state = 'CLOSED'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM strategy_shadow_outcomes")


def test_one_terminal_row_per_signal(tmp_path, stored, stage4_config):
    conn, day = stored
    _update(tmp_path, conn, add_trading_days(day, 40), stage4_config)
    row = list_terminal_outcomes(conn, strategy_id="momentum_breakout")[0]
    duplicate = {k: row[k] for k in row.keys()}
    duplicate["outcome_id"] = "other"
    columns = ", ".join(duplicate)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO strategy_shadow_outcomes ({columns}) VALUES ({', '.join('?' for _ in duplicate)})",
            tuple(duplicate.values()),
        )


def test_terminal_state_is_checked_by_the_table(stored):
    conn, _ = stored
    row = _eligible_rows(conn)[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO strategy_shadow_outcomes (outcome_id, strategy_evaluation_id, signal_content_id, "
            "strategy_id, strategy_version, symbol, signal_session, as_of_session, row_kind, state, slippage_bps, "
            "fee_per_order, snapshot_hash, record_hash, recorded_at) VALUES ('x', ?, 'c', 's', 'v', 'AAA', 'd', 'd', "
            "'PROGRESS', 'CLOSED', '0', '0', 'h', 'r', 't')",
            (row["strategy_evaluation_id"],),
        )


# -- point in time ---------------------------------------------------------------


def test_a_bar_not_yet_available_is_never_used(stored, wide_snapshot):
    """With `as_of` equal to the signal session, every later bar is in the future."""
    conn, day = stored
    row = _eligible_rows(conn)[0]
    provider = AlpacaHistoricalDataProvider(wide_snapshot)
    state, _ = evaluate_signal_outcome(
        row, provider=provider, as_of=day, slippage_bps=Decimal("10"), fee_per_order=Decimal("0"),
    )
    assert state == PENDING


def test_a_planted_crash_bar_after_the_cutoff_cannot_change_the_outcome(stored, wide_snapshot):
    conn, day = stored
    row = _eligible_rows(conn)[0]
    cutoff_day = add_trading_days(day, 1)
    baseline = evaluate_signal_outcome(
        row, provider=AlpacaHistoricalDataProvider(wide_snapshot), as_of=cutoff_day,
        slippage_bps=Decimal("10"), fee_per_order=Decimal("0"),
    )
    planted_day = add_trading_days(day, 2)
    bars = []
    for bar in wide_snapshot.bars_by_symbol[row["symbol"]]:
        if bar.session_date == planted_day:
            bar = HistoricalBar(
                symbol=bar.symbol, session_date=bar.session_date, open=Decimal("1"), high=Decimal("1.01"),
                low=Decimal("0.5"), close=Decimal("1"), volume=bar.volume, available_at=bar.available_at,
            )
        bars.append(bar)
    planted = replace(wide_snapshot, bars_by_symbol={**wide_snapshot.bars_by_symbol, row["symbol"]: tuple(bars)})
    assert evaluate_signal_outcome(
        row, provider=AlpacaHistoricalDataProvider(planted), as_of=cutoff_day,
        slippage_bps=Decimal("10"), fee_per_order=Decimal("0"),
    ) == baseline
    crashed, details = evaluate_signal_outcome(
        row, provider=AlpacaHistoricalDataProvider(planted), as_of=planted_day,
        slippage_bps=Decimal("10"), fee_per_order=Decimal("0"),
    )
    assert crashed in (CLOSED, "NO_FILL", OPEN) and (crashed != CLOSED or details["exit_price"] <= Decimal("1"))


def test_session_that_has_not_closed_is_refused(tmp_path, stored, stage4_config):
    conn, day = stored
    with pytest.raises(BacktestError, match="not closed"):
        _update(tmp_path, conn, day, stage4_config, now=regular_session_close(day) - timedelta(hours=1))


def test_refuses_before_stage_4(tmp_path, stored):
    conn, day = stored
    with pytest.raises(BacktestError, match="STAGE_4"):
        _update(tmp_path, conn, day, DEFAULT_STRATEGY_CONFIG_PATH)
    assert conn.execute("SELECT COUNT(*) FROM strategy_shadow_outcomes").fetchone()[0] == 0


# -- report ----------------------------------------------------------------------


def _bands(tmp_path, **bands):
    import json

    path = tmp_path / "bands.json"
    path.write_text(json.dumps({"momentum_breakout": {k: {"low": v[0], "high": v[1]} for k, v in bands.items()}}))
    return path


def test_report_is_insufficient_below_the_sample_minimums(tmp_path, stored, stage4_config):
    from trading_research.forward_testing.shadow_report import run_shadow_report

    conn, day = stored
    _update(tmp_path, conn, add_trading_days(day, 40), stage4_config)
    report = run_shadow_report(
        conn=conn, strategy_id="momentum_breakout", bands_path=_bands(tmp_path, expectancy=(-1, 1)),
    )
    assert report["sample"]["status"] == "INSUFFICIENT_SAMPLE"
    assert report["metrics"]["expectancy"]["status"] == "INSUFFICIENT_SAMPLE"
    assert report["metrics"]["win_rate"]["status"] == "NO_BAND"
    assert report["any_below_band"] is False


def test_report_statuses_with_a_met_sample(tmp_path, stored, stage4_config):
    from trading_research.forward_testing.shadow_report import run_shadow_report

    conn, day = stored
    _update(tmp_path, conn, add_trading_days(day, 40), stage4_config)
    kwargs = dict(conn=conn, strategy_id="momentum_breakout", minimum_market_days=1, minimum_eligible_signals=1)
    wide = run_shadow_report(**kwargs, bands_path=_bands(tmp_path, expectancy=(-1, 1), round_trips=(0, 100)))
    assert wide["sample"]["status"] == "SUFFICIENT_SAMPLE"
    assert wide["metrics"]["round_trips"]["status"] == "WITHIN"
    if wide["metrics"]["expectancy"]["value"] is not None:
        assert wide["metrics"]["expectancy"]["status"] == "WITHIN"
    high = run_shadow_report(**kwargs, bands_path=_bands(tmp_path, round_trips=(1000, 2000)))
    assert high["metrics"]["round_trips"]["status"] == "BELOW" and high["any_below_band"] is True
    low = run_shadow_report(**kwargs, bands_path=_bands(tmp_path, round_trips=(0, 0)))
    assert low["metrics"]["round_trips"]["status"] == "ABOVE"


def test_metric_arithmetic():
    from trading_research.forward_testing.shadow_report import compute_metrics

    def closed(n, ret, hold, exit_day):
        return {"state": "CLOSED", "return_fraction": ret, "holding_sessions": hold, "exit_session": exit_day,
                "outcome_id": n}

    metrics = compute_metrics([
        closed("a", "0.10", 4, "2024-01-02"), closed("b", "-0.20", 6, "2024-01-03"),
        closed("c", "0.05", 5, "2024-01-04"), {"state": "NO_FILL"}, {"state": "REJECTED"},
    ])
    assert metrics["round_trips"] == 3
    assert metrics["fill_rate"] == Decimal(3) / Decimal(4)
    assert metrics["win_rate"] == Decimal(2) / Decimal(3)
    assert metrics["expectancy"] == Decimal("-0.05") / 3
    assert metrics["average_holding_sessions"] == 5
    assert metrics["maximum_drawdown"] == Decimal("0.2")  # 1.10 -> 0.88 from the 1.10 peak


def test_report_rejects_a_bad_bands_file(tmp_path, stored):
    from trading_research.forward_testing.shadow_report import run_shadow_report

    conn, _ = stored
    for text in ("not json", '{"mean_reversion": {}}', '{"momentum_breakout": {"nope": {"low": 0, "high": 1}}}',
                 '{"momentum_breakout": {"win_rate": {"low": 2, "high": 1}}}'):
        path = tmp_path / "bad.json"
        path.write_text(text)
        with pytest.raises(BacktestError):
            run_shadow_report(conn=conn, strategy_id="momentum_breakout", bands_path=path)


# -- CLI -------------------------------------------------------------------------


def test_cli_update_and_report(tmp_path, stage4_config, full_replay, capsys):
    import json

    from trading_research.cli import main

    day = full_replay.signals[0].data_as_of.date()
    db = str(tmp_path / "cli.sqlite3")
    assert main(["strategy-candidates", "--as-of", day.isoformat(), "--symbols", "AAA,BBB,CCC", "--strategies",
                 "momentum_breakout", "--data-source", "fixture", "--strategy-config", str(stage4_config),
                 "--db", db]) == 0
    capsys.readouterr()
    later = add_trading_days(day, 40)
    assert main(["strategy-shadow-update", "--as-of", later.isoformat(), "--data-source", "fixture",
                 "--strategy-config", str(stage4_config), "--db", db]) == 0
    update = json.loads(capsys.readouterr().out)
    assert update["newly_terminal"] > 0 and update["slippage_bps"] == "10"
    assert main(["strategy-shadow-report", "--strategy", "momentum_breakout", "--db", db]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sample"]["resolved_signals"] == update["newly_terminal"]


def test_cli_update_refuses_at_the_default_stage(tmp_path, full_replay, capsys):
    import json

    from trading_research.cli import main

    day = full_replay.signals[0].data_as_of.date()
    assert main(["strategy-shadow-update", "--as-of", day.isoformat(), "--data-source", "fixture",
                 "--db", str(tmp_path / "cli.sqlite3")]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "SHADOW_ERROR"
