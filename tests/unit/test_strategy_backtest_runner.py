"""Steps 3-4 of docs/historical-backtesting-plan.md: runner, report, integrity checks."""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_research.backtesting.alpaca_history import fetch_snapshot, read_snapshot, write_snapshot
from trading_research.backtesting.fixture_history import FixtureHistoryClient
from trading_research.backtesting.models import BacktestError
from trading_research.backtesting.strategy_backtest_runner import run_strategy_backtest_report
from trading_research.storage.database import connect

START, END = date(2023, 1, 3), date(2024, 12, 31)
SPLIT = date(2023, 12, 29)
SYMBOLS = ("AAA", "BBB", "CCC")
GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "backtest_strategy_report_shape.json"


def _run(tmp_path, name="run", **overrides):
    kwargs = dict(
        strategy_id="momentum_breakout", symbols=SYMBOLS, start=START, end=END,
        initial_cash=Decimal("100000"), data_source="fixture", snapshot_path=tmp_path / f"{name}.snapshot.json",
        benchmark="SPY", conn=connect(tmp_path / f"{name}.sqlite3"), env={},
    )
    kwargs.update(overrides)
    return run_strategy_backtest_report(**kwargs)


def _shape(value):
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("bt"), name="main")


# -- Step 3 ----------------------------------------------------------------------


def test_report_contains_identity_data_screening_signals_and_metrics(report):
    assert report["run"]["backtest_run_id"].startswith("backtest-")
    assert report["run"]["strategy_id"] == "momentum_breakout"
    assert report["data"]["provider"] == "fixture"
    assert len(report["data"]["snapshot_hash"]) == 64
    assert report["engine_report"]["run_context"]["snapshot_hash"] == report["data"]["snapshot_hash"]
    assert report["screening_profile"]["not_evaluated"]
    assert all(g["status"] == "EVALUATED" for g in report["screening_profile"]["evaluated"])
    assert report["signals"]["eligible"] > 0
    assert sum(report["signals"]["status_counts"].values()) == report["signals"]["evaluations"]
    assert report["strategy_metrics"]["number_of_signals"] == report["signals"]["eligible"]
    assert "benchmark_return" in report["engine_report"]
    assert report["summary"]["benchmark_return"] is not None


def test_report_carries_the_fixed_caveats(report):
    text = " ".join(report["caveats"]).lower()
    for phrase in ("survivorship", "daily bars", "stop", "not evaluated", "not financial advice", "synthetic"):
        assert phrase in text


def test_report_shape_matches_golden_file(report):
    assert _shape(report) == json.loads(GOLDEN.read_text())


def test_results_are_persisted_to_the_backtest_tables(tmp_path):
    conn = connect(tmp_path / "p.sqlite3")
    out = _run(tmp_path, name="p", conn=conn)
    row = conn.execute(
        "SELECT strategy_id FROM backtest_runs WHERE backtest_run_id = ?", (out["run"]["backtest_run_id"],)
    ).fetchone()
    assert row[0] == "momentum_breakout"
    assert conn.execute("SELECT COUNT(*) FROM backtest_fills").fetchone()[0] > 0


def test_event_catalyst_and_unknown_strategies_are_rejected(tmp_path):
    with pytest.raises(BacktestError, match="point-in-time"):
        _run(tmp_path, strategy_id="event_catalyst")
    with pytest.raises(BacktestError, match="unsupported"):
        _run(tmp_path, strategy_id="nope")


def test_mean_reversion_notes_missing_catalyst_inputs(tmp_path):
    out = _run(tmp_path, strategy_id="mean_reversion")
    assert out["signals"]["eligible"] == 0
    assert any("catalyst" in note for note in out["strategy_notes"])


def test_alpaca_source_requires_a_snapshot_and_credentials_only_for_fetching(tmp_path):
    with pytest.raises(BacktestError, match="snapshot"):
        _run(tmp_path, data_source="alpaca", snapshot_path=None)
    with pytest.raises(BacktestError) as excinfo:
        _run(tmp_path, data_source="alpaca", snapshot_path=tmp_path / "missing.json", env={})
    assert "ALPACA_MARKET_DATA_API_KEY" in str(excinfo.value)


def test_existing_alpaca_snapshot_runs_offline_without_credentials(tmp_path):
    class Relabel:
        def get_price_history(self, symbol, *, start, end, as_of, adjustment="raw"):
            return tuple(
                replace(bar, provider="alpaca-data")
                for bar in FixtureHistoryClient().get_price_history(
                    symbol, start=start, end=end, as_of=as_of, adjustment=adjustment)
            )

    snap = fetch_snapshot(
        Relabel(), symbols=(*SYMBOLS, "SPY"), start=START, end=END, adjustment="split",
        as_of=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    path = tmp_path / "alpaca.snapshot.json"
    write_snapshot(path, snap)
    out = _run(tmp_path, data_source="alpaca", snapshot_path=path, env={})
    assert out["data"]["provider"] == "alpaca-data"
    assert out["data"]["snapshot_hash"] == snap.content_hash


def test_snapshot_of_another_provider_is_refused(tmp_path):
    _run(tmp_path, name="fx", snapshot_path=tmp_path / "shared.json")
    with pytest.raises(BacktestError, match="provider"):
        _run(tmp_path, name="al", data_source="alpaca", snapshot_path=tmp_path / "shared.json")


def test_second_run_reads_the_snapshot_it_wrote(tmp_path):
    first = _run(tmp_path, name="a", snapshot_path=tmp_path / "s.json")
    written = read_snapshot(tmp_path / "s.json").content_hash
    second = _run(tmp_path, name="b", snapshot_path=tmp_path / "s.json")
    assert first["data"]["snapshot_hash"] == second["data"]["snapshot_hash"] == written


# -- Step 4 ----------------------------------------------------------------------


def test_identical_inputs_give_identical_run_id_metrics_and_report_bytes(tmp_path):
    a = _run(tmp_path, name="one")
    b = _run(tmp_path, name="two")
    assert a["run"]["backtest_run_id"] == b["run"]["backtest_run_id"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_different_dataset_gets_a_different_run_id(tmp_path):
    a = _run(tmp_path, name="one")
    b = _run(tmp_path, name="two", symbols=("AAA", "BBB"))
    assert a["run"]["backtest_run_id"] != b["run"]["backtest_run_id"]


def test_summary_shows_benchmark_next_to_strategy_figures(report):
    summary = report["summary"]
    for key in ("strategy_return", "benchmark_return", "excess_return", "maximum_drawdown", "exposure", "round_trips"):
        assert key in summary


def test_cost_grid_reruns_at_each_slippage_level(tmp_path):
    out = _run(tmp_path, cost_grid=(Decimal("0"), Decimal("10"), Decimal("25")))
    rows = out["cost_sensitivity"]
    assert [r["slippage_bps"] for r in rows] == ["0", "10", "25"]
    assert len({r["backtest_run_id"] for r in rows}) == 3
    returns = [Decimal(r["summary"]["strategy_return"]) for r in rows]
    assert returns[0] >= returns[1] >= returns[2]


def test_split_date_reports_each_window_separately(tmp_path):
    out = _run(tmp_path, split_date=SPLIT)
    split = out["split"]
    assert split["split_date"] == SPLIT.isoformat()
    assert split["in_sample"]["window"] == {"start": START.isoformat(), "end": SPLIT.isoformat()}
    assert split["out_of_sample"]["window"]["start"] > SPLIT.isoformat()
    assert split["out_of_sample"]["window"]["end"] == END.isoformat()
    ids = {split["in_sample"]["backtest_run_id"], split["out_of_sample"]["backtest_run_id"], out["run"]["backtest_run_id"]}
    assert len(ids) == 3
    assert "tuning" in split["guidance"].lower()
    assert "split" not in _run(tmp_path, name="nosplit")


def test_split_date_must_fall_inside_the_range(tmp_path):
    with pytest.raises(BacktestError, match="split"):
        _run(tmp_path, split_date=START)
    with pytest.raises(BacktestError, match="split"):
        _run(tmp_path, split_date=END)


def test_small_samples_are_flagged_insufficient(tmp_path):
    flagged = _run(tmp_path, name="a", min_trades=10_000)
    assert flagged["sample"]["status"] == "INSUFFICIENT_SAMPLE"
    assert flagged["sample"]["minimum_round_trips"] == 10_000
    fine = _run(tmp_path, name="b", min_trades=1)
    assert fine["sample"]["status"] == "SUFFICIENT_SAMPLE"
    assert _run(tmp_path, name="c")["sample"]["minimum_round_trips"] == 30


# -- boundaries ------------------------------------------------------------------

NEW_MODULES = (
    "alpaca_history", "fixture_history", "relative_strength", "screening_profile",
    "signal_replay", "strategy_backtest_runner",
)
FORBIDDEN_PREFIXES = (
    "paper_books", "runtime", "execution", "paper", "shadow", "recommendations", "lumibot", "alpaca",
    "trading_paper_runtime",
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * node.level) + (node.module or ""))
            names.update(("." * node.level) + (node.module or "") + "." + alias.name for alias in node.names)
    return names


def test_new_backtest_modules_import_no_broker_or_runtime_package():
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "trading_research" / "backtesting"
    for module in NEW_MODULES:
        for name in _imported_modules(root / f"{module}.py"):
            bare = name.lstrip(".")
            parts = bare.replace("trading_research.", "").split(".")
            assert parts[0] not in FORBIDDEN_PREFIXES, f"{module} imports {name}"


def test_running_a_backtest_loads_no_broker_sdk_or_external_broker_module():
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    code = (
        f"import sys, tempfile, pathlib\nsys.path.insert(0, {str(src)!r})\n"
        "from datetime import date\nfrom decimal import Decimal\n"
        "from trading_research.backtesting.strategy_backtest_runner import run_strategy_backtest_report\n"
        "d = pathlib.Path(tempfile.mkdtemp())\n"
        "run_strategy_backtest_report(strategy_id='momentum_breakout', symbols=('AAA',), start=date(2024,1,2),"
        " end=date(2024,6,28), initial_cash=Decimal('10000'), data_source='fixture', snapshot_path=None, env={})\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in {'lumibot','alpaca','trading_paper_runtime'}"
        " or m.startswith(('trading_research.paper_books.external_broker','trading_research.shadow'))]\n"
        "print('FORBIDDEN', bad)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert "FORBIDDEN []" in result.stdout
