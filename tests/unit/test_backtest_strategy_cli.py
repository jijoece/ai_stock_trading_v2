"""`backtest-strategy` CLI command (docs/historical-backtesting-plan.md Step 3)."""
from __future__ import annotations

import json

import pytest

from trading_research.cli import main
from trading_research.storage.database import connect

BASE = [
    "backtest-strategy", "--strategy", "momentum_breakout", "--symbols", "AAA,BBB,CCC",
    "--start", "2023-01-03", "--end", "2024-12-31", "--initial-cash", "100000", "--data-source", "fixture",
]


def _argv(tmp_path, *extra):
    return [*BASE, "--db", str(tmp_path / "bt.sqlite3"), *extra]


def test_fixture_run_prints_a_json_report_and_persists(tmp_path, capsys):
    snapshot = tmp_path / "snap.json"
    assert main(_argv(tmp_path, "--snapshot", str(snapshot))) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run"]["strategy_id"] == "momentum_breakout"
    assert report["data"]["provider"] == "fixture"
    assert snapshot.exists()
    conn = connect(tmp_path / "bt.sqlite3")
    assert conn.execute(
        "SELECT COUNT(*) FROM backtest_runs WHERE backtest_run_id = ?", (report["run"]["backtest_run_id"],)
    ).fetchone()[0] == 1


def test_optional_flags_add_report_sections(tmp_path, capsys):
    argv = _argv(tmp_path, "--cost-grid", "0,10,25", "--split-date", "2023-12-29", "--min-trades", "5",
                 "--slippage-bps", "5", "--fee-per-order", "1")
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["slippage_bps"] for row in report["cost_sensitivity"]] == ["0", "10", "25"]
    assert report["split"]["split_date"] == "2023-12-29"
    assert report["sample"]["minimum_round_trips"] == 5
    assert report["run"]["slippage_bps"] == "5" and report["run"]["fee_per_order"] == "1"


def test_output_bytes_are_reproducible(tmp_path, capsys):
    main(_argv(tmp_path))
    first = capsys.readouterr().out
    main(_argv(tmp_path))
    assert capsys.readouterr().out == first


def test_missing_credentials_error_is_sanitized_and_exits_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_SECRET", raising=False)
    argv = [a if a != "fixture" else "alpaca" for a in _argv(tmp_path, "--snapshot", str(tmp_path / "none.json"))]
    assert main(argv) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "BACKTEST_ERROR"
    assert "ALPACA_MARKET_DATA_API_KEY" in out["error"]["message"]


def test_unexpected_failures_collapse_to_the_generic_error(tmp_path, capsys, monkeypatch):
    def boom(**_):
        raise RuntimeError("/secret/path/with-token-123")

    monkeypatch.setattr("trading_research.backtesting.strategy_backtest_runner.run_strategy_backtest_report", boom)
    assert main(_argv(tmp_path)) == 2
    text = capsys.readouterr().out
    assert "token-123" not in text and "/secret/path" not in text
    assert json.loads(text)["error"]["code"] == "EXTERNAL_RUNTIME_ERROR"


def test_event_catalyst_is_reported_as_unsupported(tmp_path, capsys):
    argv = [a if a != "momentum_breakout" else "event_catalyst" for a in _argv(tmp_path)]
    assert main(argv) == 2
    assert "point-in-time" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_bad_arguments_are_rejected_by_the_parser(tmp_path):
    with pytest.raises(SystemExit):
        main(_argv(tmp_path, "--cost-grid", "0,abc"))
    with pytest.raises(SystemExit):
        main([a if a != "2023-01-03" else "not-a-date" for a in _argv(tmp_path)])
