"""Phase 1 of docs/paper-testing-plan.md: strategy_signals store and the daily candidate list."""
from __future__ import annotations

import ast
import pathlib
import re
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from trading_research.backtesting.fixture_history import build_fixture_snapshot
from trading_research.backtesting.models import BacktestError
from trading_research.backtesting.signal_replay import replay_signals
from trading_research.backtesting.strategy_backtest_runner import build_strategy
from trading_research.forward_testing.candidates import run_strategy_candidates
from trading_research.storage.database import connect
from trading_research.storage.schema_version import CURRENT_SCHEMA_VERSION
from trading_research.storage.strategy_signals_repositories import (
    StrategySignalConflictError,
    insert_strategy_signal,
    list_strategy_signals,
)
from trading_research.strategies.config import DEFAULT_STRATEGY_CONFIG_PATH
from trading_research.strategies.contracts import derive_strategy_signal_content_id

SYMBOLS = ("AAA", "BBB", "CCC")
START, END = date(2023, 1, 3), date(2024, 12, 31)
NOW = datetime(2025, 3, 1, tzinfo=timezone.utc)
SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "trading_research"


@pytest.fixture()
def stage3_config(tmp_path):
    text = DEFAULT_STRATEGY_CONFIG_PATH.read_text()
    patched = re.sub(r"activation_stage: \w+", "activation_stage: STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST", text)
    assert patched != text
    path = tmp_path / "strategies.yaml"
    path.write_text(patched)
    return path


@pytest.fixture(scope="module")
def full_replay():
    snapshot = build_fixture_snapshot(
        symbols=("AAA", "BBB", "CCC", "SPY"), start=START, end=END, adjustment="split",
        as_of=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    return replay_signals(
        strategy=build_strategy("momentum_breakout", None), provider=AlpacaHistoricalDataProvider(snapshot),
        symbols=SYMBOLS, start=START, end=END, history_start=warmup_start(START),
        screening_config=load_screening_config(None),
    )


def _candidates(tmp_path, conn, as_of, config, **overrides):
    kwargs = dict(
        strategy_ids=("momentum_breakout",), symbols=SYMBOLS, as_of=as_of, data_source="fixture",
        snapshot_path=tmp_path / f"{as_of}.snapshot.json", conn=conn, strategy_config_path=config, now=NOW,
    )
    kwargs.update(overrides)
    return run_strategy_candidates(**kwargs)


# -- storage ---------------------------------------------------------------------


def test_migration_registers_strategy_signals(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'strategy_signals'").fetchone()


def test_every_status_is_persisted_and_rerun_is_a_noop(tmp_path, stage3_config, full_replay):
    conn = connect(tmp_path / "db.sqlite3")
    day = full_replay.signals[0].data_as_of.date()
    first = _candidates(tmp_path, conn, day, stage3_config)
    rows = list_strategy_signals(conn, strategy_id="momentum_breakout", session_date=day.isoformat())
    assert len(rows) == len(SYMBOLS) == first["strategies"]["momentum_breakout"]["evaluations"]
    assert first["strategies"]["momentum_breakout"]["recorded"] == len(SYMBOLS)
    assert {r["status"] for r in rows} >= {"ELIGIBLE"}

    again = _candidates(tmp_path, conn, day, stage3_config)
    assert again["strategies"]["momentum_breakout"]["recorded"] == 0
    assert again["strategies"]["momentum_breakout"]["already_recorded"] == len(SYMBOLS)
    assert len(list_strategy_signals(conn)) == len(SYMBOLS)


def test_rows_cannot_be_updated_or_deleted(tmp_path, stage3_config, full_replay):
    conn = connect(tmp_path / "db.sqlite3")
    _candidates(tmp_path, conn, full_replay.signals[0].data_as_of.date(), stage3_config)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE strategy_signals SET status = 'ELIGIBLE'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM strategy_signals")


def test_same_evaluation_with_different_content_is_rejected(tmp_path, stage3_config, full_replay):
    conn = connect(tmp_path / "db.sqlite3")
    _candidates(tmp_path, conn, full_replay.signals[0].data_as_of.date(), stage3_config)
    row = list_strategy_signals(conn)[0]
    import trading_research.storage.strategy_signals_repositories as repo

    record = repo.StrategySignalRecord(**{f: row[f] for f in repo.StrategySignalRecord.__dataclass_fields__
                                          if f != "backtest_screening_passed"},
                                       backtest_screening_passed=bool(row["backtest_screening_passed"]))
    assert insert_strategy_signal(conn, record, recorded_at="x") is False
    with pytest.raises(StrategySignalConflictError):
        insert_strategy_signal(conn, replace(record, record_hash="different"), recorded_at="x")


def test_both_screening_results_are_stored(tmp_path, stage3_config, full_replay):
    conn = connect(tmp_path / "db.sqlite3")
    _candidates(tmp_path, conn, full_replay.signals[0].data_as_of.date(), stage3_config)
    row = list_strategy_signals(conn)[0]
    assert row["backtest_screening_json"] and row["screening_profile_hash"]
    assert row["production_screening_status"] == "NOT_EVALUATED" and row["production_screening_json"] is None
    assert len(row["snapshot_hash"]) == 64


# -- gates -----------------------------------------------------------------------


def test_refuses_before_stage_3(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    with pytest.raises(BacktestError, match="STAGE_3"):
        _candidates(tmp_path, conn, date(2024, 6, 3), DEFAULT_STRATEGY_CONFIG_PATH)
    assert list_strategy_signals(conn) == []


def test_refuses_a_session_that_has_not_closed(tmp_path, stage3_config):
    conn = connect(tmp_path / "db.sqlite3")
    with pytest.raises(BacktestError, match="not closed"):
        _candidates(tmp_path, conn, date(2024, 6, 3), stage3_config, now=datetime(2024, 6, 3, 15, tzinfo=timezone.utc))
    assert list_strategy_signals(conn) == []


def test_non_trading_day_is_rejected(tmp_path, stage3_config):
    conn = connect(tmp_path / "db.sqlite3")
    with pytest.raises(BacktestError, match="not a trading session"):
        _candidates(tmp_path, conn, date(2024, 6, 8), stage3_config)


def test_missing_session_bar_is_never_eligible(tmp_path, stage3_config, full_replay):
    """A snapshot that stops the day before `as_of` leaves every symbol stale."""
    day = full_replay.signals[0].data_as_of.date()
    full = build_fixture_snapshot(
        symbols=("AAA", "BBB", "CCC", "SPY"), start=START, end=END, adjustment="split", as_of=NOW,
    )
    snapshot = replace(full, bars_by_symbol={
        symbol: tuple(bar for bar in bars if bar.session_date != day) for symbol, bars in full.bars_by_symbol.items()
    })
    provider = AlpacaHistoricalDataProvider(snapshot)
    result = replay_signals(
        strategy=build_strategy("momentum_breakout", None), provider=provider, symbols=SYMBOLS, start=day, end=day,
        history_start=warmup_start(day), screening_config=load_screening_config(None),
    )
    assert result.signals == ()
    assert set(result.status_counts) <= {"STALE", "INCOMPLETE"}


# -- parity ----------------------------------------------------------------------


def test_candidates_match_the_backtest_replay_for_the_same_date(tmp_path, stage3_config, full_replay):
    day = full_replay.signals[0].data_as_of.date()
    expected = {derive_strategy_signal_content_id(s) for s in full_replay.signals if s.data_as_of.date() == day}
    assert expected
    result = _candidates(tmp_path, connect(tmp_path / "db.sqlite3"), day, stage3_config)
    got = {s["signal_content_id"] for e in result["shortlist"] for s in e["signals"]}
    assert got == expected


# -- boundary --------------------------------------------------------------------


def test_candidate_path_imports_no_broker_or_runtime_module():
    forbidden = ("runtime", "execution", "paper_books", "shadow", "paper_runtime")
    for relative in ("forward_testing/candidates.py", "storage/strategy_signals_repositories.py"):
        tree = ast.parse((SRC / relative).read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [(node.module or "")] if node.level == 0 else [(node.module or "").split(".")[0]]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{relative} imports {name}"


# -- CLI -------------------------------------------------------------------------


def _cli_argv(tmp_path, day, *extra):
    return ["strategy-candidates", "--as-of", day.isoformat(), "--symbols", "AAA,BBB,CCC", "--strategies",
            "momentum_breakout", "--data-source", "fixture", "--db", str(tmp_path / "cli.sqlite3"), *extra]


def test_cli_prints_shortlist_and_persists(tmp_path, stage3_config, full_replay, capsys):
    import json

    from trading_research.cli import main

    day = full_replay.signals[0].data_as_of.date()
    assert main(_cli_argv(tmp_path, day, "--strategy-config", str(stage3_config))) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["activation_stage"] == "STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST"
    assert out["shortlist"]
    assert len(list_strategy_signals(connect(tmp_path / "cli.sqlite3"))) == len(SYMBOLS)


def test_cli_refuses_at_the_default_stage_and_exits_2(tmp_path, full_replay, capsys):
    import json

    from trading_research.cli import main

    day = full_replay.signals[0].data_as_of.date()
    assert main(_cli_argv(tmp_path, day)) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CANDIDATES_ERROR"
    assert list_strategy_signals(connect(tmp_path / "cli.sqlite3")) == []
