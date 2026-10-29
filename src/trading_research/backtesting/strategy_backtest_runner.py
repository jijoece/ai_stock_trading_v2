"""End-to-end historical strategy backtest and its JSON report
(docs/historical-backtesting-plan.md Steps 3-4).

Research only: no broker, runtime or scheduler is touched. Data comes from a
local snapshot (see `alpaca_history`); credentials are read only when a missing
Alpaca snapshot must be fetched. Signals are replayed point-in-time
(`signal_replay`) and then run through the existing engine unchanged.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from ..analysis.screener import load_screening_config
from ..hashing import hash_config
from ..strategies.backtest_adapter import StrategyBacktestResult, run_strategy_backtest
from ..strategies.config import load_strategy_config
from ..strategies.contracts import CandidateStrategy, StrategySignal
from ..strategies.mean_reversion import MeanReversionStrategy
from ..strategies.momentum_breakout import MomentumBreakoutStrategy
from .alpaca_history import (
    SNAPSHOT_PROVIDER,
    AlpacaHistoricalDataProvider,
    HistoricalSnapshot,
    alpaca_client_from_environment,
    load_or_fetch_snapshot,
    warmup_start,
)
from .configuration import BacktestConfiguration
from .fixture_history import FIXTURE_PROVIDER, FixtureHistoryClient, build_fixture_snapshot
from .models import BacktestError
from .screening_profile import profile_hash
from .signal_replay import ReplayResult, replay_signals

REPORT_VERSION = 1
DEFAULT_MINIMUM_ROUND_TRIPS = 30
SUPPORTED_STRATEGIES = ("momentum_breakout", "mean_reversion")
DATA_SOURCES = ("fixture", "alpaca")

CAVEATS = (
    "Research output only; not financial advice.",
    "Survivorship bias: the universe is an operator-supplied symbol list of names that exist today; "
    "delisted symbols are absent.",
    "Daily bars only: the intraday path is unknown, so when a stop and a target are both touched in one "
    "bar the stop is assumed to fill first (CONSERVATIVE_STOP_FIRST).",
    "Screening gates listed under screening_profile.not_evaluated were not evaluated; passing the backtest "
    "screen does not imply production eligibility.",
    "A single run is not evidence for changing activation_stage; use out-of-sample results and adequate samples.",
)
SYNTHETIC_CAVEAT = "Fixture data is synthetic and says nothing about any real market."
MEAN_REVERSION_NOTE = (
    "mean_reversion requires a point-in-time catalyst/SEC-risk snapshot that this backtest does not "
    "supply, so every evaluation is INCOMPLETE (missing_catalyst_risk_data) until point-in-time "
    "filings data exists."
)


def _jsonable(value) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(_jsonable(k)): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_strategy(strategy_id: str, config_path: Path | str | None) -> CandidateStrategy:
    if strategy_id == "event_catalyst":
        raise BacktestError("event_catalyst needs point-in-time event history and is not backtestable yet")
    if strategy_id not in SUPPORTED_STRATEGIES:
        raise BacktestError(f"unsupported strategy {strategy_id!r}; expected one of {SUPPORTED_STRATEGIES}")
    config = load_strategy_config(config_path)
    section = getattr(config, strategy_id)
    if not section.enabled:
        raise BacktestError(f"strategy {strategy_id!r} is disabled in the strategy configuration")
    if strategy_id == "momentum_breakout":
        return MomentumBreakoutStrategy(section)
    return MeanReversionStrategy(section)


def acquire_snapshot(
    *, data_source: str, snapshot_path: Path | str | None, symbols: tuple[str, ...], start: date, end: date,
    adjustment: str, env: Mapping[str, str] | None, now: datetime,
) -> HistoricalSnapshot:
    as_of = min(datetime.combine(end, time(23, 59, 59), tzinfo=timezone.utc), now)
    if data_source == "fixture":
        if snapshot_path is None:
            return build_fixture_snapshot(symbols=symbols, start=start, end=end, adjustment=adjustment, as_of=as_of)
        return load_or_fetch_snapshot(
            snapshot_path, client_factory=FixtureHistoryClient, symbols=symbols, start=start, end=end,
            adjustment=adjustment, as_of=as_of, provider=FIXTURE_PROVIDER,
        )
    if snapshot_path is None:
        raise BacktestError(
            "--snapshot is required with --data-source alpaca so the run can be repeated offline"
        )
    return load_or_fetch_snapshot(
        snapshot_path, client_factory=lambda: alpaca_client_from_environment(env), symbols=symbols,
        start=start, end=end, adjustment=adjustment, as_of=as_of, provider=SNAPSHOT_PROVIDER,
    )


def _summary(result: StrategyBacktestResult) -> dict:
    engine = result.backtest_result.metrics
    strategy_return, benchmark_return = engine["total_return"], engine["benchmark_return"]
    return _jsonable({
        "strategy_return": strategy_return,
        "benchmark_return": benchmark_return,
        "excess_return": strategy_return - benchmark_return if benchmark_return is not None else None,
        "maximum_drawdown": engine["maximum_drawdown"],
        "exposure": result.metrics.exposure,
        "round_trips": result.metrics.number_of_trades,
    })


def _sample(result: StrategyBacktestResult, minimum: int) -> dict:
    trips = result.metrics.number_of_trades
    return {
        "minimum_round_trips": minimum, "round_trips": trips,
        "status": "SUFFICIENT_SAMPLE" if trips >= minimum else "INSUFFICIENT_SAMPLE",
    }


def run_strategy_backtest_report(
    *, strategy_id: str, symbols: tuple[str, ...], start: date, end: date, initial_cash: Decimal,
    data_source: str, snapshot_path: Path | str | None, benchmark: str = "SPY",
    slippage_bps: Decimal = Decimal("0"), fee_per_order: Decimal = Decimal("0"),
    split_date: date | None = None, cost_grid: tuple[Decimal, ...] = (),
    min_trades: int = DEFAULT_MINIMUM_ROUND_TRIPS, adjustment: str = "split", conn=None,
    strategy_config_path: Path | str | None = None, screening_config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> dict:
    if data_source not in DATA_SOURCES:
        raise BacktestError(f"unknown data source {data_source!r}; expected one of {DATA_SOURCES}")
    if min_trades < 1:
        raise BacktestError("minimum round trips must be at least 1")
    if end < start:
        raise BacktestError("end must not precede start")
    if split_date is not None and not (start < split_date < end):
        raise BacktestError(f"split date {split_date} must fall strictly inside {start}..{end}")
    strategy = build_strategy(strategy_id, strategy_config_path)
    screening_config = load_screening_config(screening_config_path)
    names = tuple(sorted({s.strip().upper() for s in symbols if s.strip()}))
    benchmark = benchmark.strip().upper()
    if not names or not benchmark:
        raise BacktestError("at least one symbol and a benchmark are required")
    universe = tuple(sorted({*names, benchmark}))

    snapshot = acquire_snapshot(
        data_source=data_source, snapshot_path=snapshot_path, symbols=universe, start=start, end=end,
        adjustment=adjustment, env=env, now=now or datetime.now(timezone.utc),
    )
    provider = AlpacaHistoricalDataProvider(snapshot)
    replay = replay_signals(
        strategy=strategy, provider=provider, symbols=names, start=start, end=end,
        history_start=warmup_start(start), screening_config=screening_config, benchmark_symbol=benchmark,
    )
    run_context = {
        "snapshot_hash": snapshot.content_hash, "data_provider": snapshot.provider,
        "price_adjustment": snapshot.adjustment, "screening_profile_hash": profile_hash(screening_config),
        "relative_strength_hash": hash_config(replay.relative_strength_definition),
        "strategy_version": replay.strategy_version, "strategy_config_hash": replay.configuration_hash,
        "benchmark": benchmark, "universe": list(names),
    }

    def run(signals: tuple[StrategySignal, ...], window_start: date, window_end: date, slippage: Decimal):
        config = BacktestConfiguration(
            start_date=window_start, end_date=window_end, symbols=universe, initial_cash=initial_cash,
            slippage_bps=slippage, fee_per_order=fee_per_order, benchmark_symbol=benchmark,
        )
        return run_strategy_backtest(
            strategy_id=strategy_id, signals=signals, configuration=config, data_provider=provider,
            conn=conn, run_context=run_context,
        )

    def within(window_start: date, window_end: date) -> tuple[StrategySignal, ...]:
        return tuple(
            s for s in replay.signals
            if s.data_as_of is not None and window_start <= s.data_as_of.date() <= window_end
        )

    primary = run(replay.signals, start, end, slippage_bps)
    run_id = primary.backtest_result.backtest_run_id
    report: dict = {
        "report_version": REPORT_VERSION,
        "run": {
            "backtest_run_id": run_id, "strategy_id": strategy_id, "strategy_version": replay.strategy_version,
            "strategy_config_hash": replay.configuration_hash, "start": start.isoformat(), "end": end.isoformat(),
            "symbols": list(names), "benchmark": benchmark, "initial_cash": str(initial_cash),
            "slippage_bps": str(slippage_bps), "fee_per_order": str(fee_per_order),
        },
        "data": {
            "provider": snapshot.provider, "price_adjustment": snapshot.adjustment,
            "snapshot_hash": snapshot.content_hash, "fetch_start": snapshot.fetch_start.isoformat(),
            "end": snapshot.end.isoformat(), "symbols": list(snapshot.symbols),
        },
        "screening_profile": replay.screening_profile,
        "relative_strength": replay.relative_strength_definition,
        "signals": _signal_counts(replay),
        "summary": _summary(primary),
        "sample": _sample(primary, min_trades),
        "engine_report": _jsonable(primary.backtest_result.metrics),
        "strategy_metrics": _jsonable(primary.metrics),
        "strategy_notes": [MEAN_REVERSION_NOTE] if strategy_id == "mean_reversion" else [],
        "caveats": list(CAVEATS) + ([SYNTHETIC_CAVEAT] if snapshot.provider == FIXTURE_PROVIDER else []),
    }

    if cost_grid:
        rows = []
        for level in cost_grid:
            result = run(replay.signals, start, end, level)
            rows.append({
                "slippage_bps": str(level), "backtest_run_id": result.backtest_result.backtest_run_id,
                "summary": _summary(result), "sample": _sample(result, min_trades),
            })
        report["cost_sensitivity"] = rows

    if split_date is not None:
        out_start = split_date + timedelta(days=1)
        windows = {"in_sample": (start, split_date), "out_of_sample": (out_start, end)}
        split: dict = {
            "split_date": split_date.isoformat(),
            "guidance": "Use only the in_sample window for configuration tuning; judge the result on out_of_sample.",
        }
        for label, (window_start, window_end) in windows.items():
            signals = within(window_start, window_end)
            result = run(signals, window_start, window_end, slippage_bps)
            split[label] = {
                "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
                "backtest_run_id": result.backtest_result.backtest_run_id, "signals": len(signals),
                "summary": _summary(result), "sample": _sample(result, min_trades),
            }
        report["split"] = split
    return report


def _signal_counts(replay: ReplayResult) -> dict:
    return {
        "evaluations": replay.evaluations, "eligible": len(replay.signals),
        "status_counts": replay.status_counts, "reason_code_counts": replay.reason_code_counts,
        "reason_code_counts_note": "per-code occurrence counts; one evaluation carries several codes",
    }
