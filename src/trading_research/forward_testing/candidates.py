"""Stage 3 daily read-only candidate list (docs/paper-testing-plan.md Phase 1).

Evaluates exactly one closed session through the same `replay_signals` the
backtest uses, persists every evaluation (any status) to the append-only
`strategy_signals` table, and returns the shortlist. Read-only: no broker,
runtime, scheduler or model module is imported or touched.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path

from ..analysis.screener import ScreeningResult, load_screening_config
from ..backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from ..backtesting.models import BacktestError
from ..backtesting.screening_profile import profile_hash
from ..backtesting.signal_replay import ReplayEvaluation, replay_signals
from ..backtesting.strategy_backtest_runner import (
    DATA_SOURCES,
    SUPPORTED_STRATEGIES,
    acquire_snapshot,
    build_strategy,
)
from ..evaluation.market_calendar import regular_session_close
from ..hashing import hash_config
from ..storage.strategy_signals_repositories import StrategySignalRecord, insert_strategy_signal
from ..storage.transactions import transaction
from ..strategies.config import ACTIVATION_STAGES, load_strategy_config
from ..strategies.contracts import (
    StrategySignal,
    derive_strategy_evaluation_id,
    derive_strategy_signal_content_id,
)
from ..strategies.selector import select_shortlist

MINIMUM_ACTIVATION_STAGE = "STAGE_3_DAILY_READ_ONLY_CANDIDATE_LIST"
NOT_EVALUATED = "NOT_EVALUATED"

CAVEATS = (
    "Research output only; not financial advice. No order is created or submitted.",
    "Eligibility here uses the backtest screening profile (daily-bar gates only); production-only gates are "
    "recorded as NOT_EVALUATED unless a production screening result is supplied.",
)


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _text(value) -> str | None:
    return None if value is None else str(value)


def build_signal_record(
    evaluation: ReplayEvaluation, *, data_provider: str, snapshot_hash: str, screening_profile_hash: str,
    relative_strength_hash: str, production_screening: ScreeningResult | None = None,
) -> StrategySignalRecord:
    signal = evaluation.signal
    if production_screening is None:
        production_status, production_json = NOT_EVALUATED, None
    else:
        production_status = "PASSED" if production_screening.passed else "FAILED"
        production_json = _dumps(asdict(production_screening))
    unhashed = StrategySignalRecord(
        record_hash="", strategy_evaluation_id=derive_strategy_evaluation_id(signal),
        signal_content_id=derive_strategy_signal_content_id(signal),
        strategy_id=signal.strategy_id, strategy_version=signal.strategy_version,
        configuration_hash=signal.configuration_hash, symbol=signal.symbol,
        session_date=evaluation.session.isoformat(), signal_timestamp=signal.signal_timestamp.isoformat(),
        data_as_of=signal.data_as_of.isoformat() if signal.data_as_of is not None else None,
        status=signal.status.value, signal_strength=signal.signal_strength,
        entry_reference=_text(signal.entry_reference), limit_reference=_text(signal.limit_reference),
        invalidation_price=_text(signal.invalidation_price),
        initial_stop_reference=_text(signal.initial_stop_reference), target_reference=_text(signal.target_reference),
        expected_holding_period=signal.expected_holding_period,
        reason_codes_json=_dumps(list(signal.reason_codes)), factor_values_json=_dumps(signal.factor_values),
        data_quality=signal.data_quality, backtest_screening_passed=evaluation.screening.passed,
        backtest_screening_json=_dumps(asdict(evaluation.screening)),
        production_screening_status=production_status, production_screening_json=production_json,
        data_provider=data_provider, snapshot_hash=snapshot_hash, screening_profile_hash=screening_profile_hash,
        relative_strength_hash=relative_strength_hash,
    )
    content = asdict(unhashed)
    del content["record_hash"]
    return replace(unhashed, record_hash=hash_config(content))


def _require_stage(activation_stage: str) -> None:
    if ACTIVATION_STAGES.index(activation_stage) < ACTIVATION_STAGES.index(MINIMUM_ACTIVATION_STAGE):
        raise BacktestError(
            f"activation_stage is {activation_stage}; candidates need {MINIMUM_ACTIVATION_STAGE} or later. "
            "Only the operator moves the stage, by a reviewed change to config/strategies.yaml."
        )


def run_strategy_candidates(
    *, strategy_ids: tuple[str, ...], symbols: tuple[str, ...], as_of: date, data_source: str,
    snapshot_path: Path | str | None, conn, benchmark: str = "SPY", adjustment: str = "split",
    strategy_config_path: Path | str | None = None, screening_config_path: Path | str | None = None,
    production_screening: Mapping[str, ScreeningResult] | None = None,
    env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> dict:
    if data_source not in DATA_SOURCES:
        raise BacktestError(f"unknown data source {data_source!r}; expected one of {DATA_SOURCES}")
    strategy_ids = tuple(dict.fromkeys(strategy_ids))
    if not strategy_ids:
        raise BacktestError("at least one strategy is required")
    for strategy_id in strategy_ids:
        if strategy_id not in SUPPORTED_STRATEGIES:
            raise BacktestError(f"unsupported strategy {strategy_id!r}; expected one of {SUPPORTED_STRATEGIES}")
    names = tuple(sorted({s.strip().upper() for s in symbols if s.strip()}))
    benchmark = benchmark.strip().upper()
    if not names or not benchmark:
        raise BacktestError("at least one symbol and a benchmark are required")

    config = load_strategy_config(strategy_config_path)
    _require_stage(config.activation_stage)
    screening_config = load_screening_config(screening_config_path)
    clock = now or datetime.now(timezone.utc)
    try:
        session_close = regular_session_close(as_of)
    except Exception as exc:
        raise BacktestError(f"{as_of} is not a trading session: {exc}") from exc
    if session_close > clock:
        raise BacktestError(f"session {as_of} has not closed yet (closes {session_close.isoformat()})")

    snapshot = acquire_snapshot(
        data_source=data_source, snapshot_path=snapshot_path, symbols=tuple(sorted({*names, benchmark})),
        start=as_of, end=as_of, adjustment=adjustment, env=env, now=clock,
    )
    provider = AlpacaHistoricalDataProvider(snapshot)

    signals_by_strategy: dict[str, tuple[StrategySignal, ...]] = {}
    per_strategy: dict[str, dict] = {}
    recorded_at = clock.isoformat()
    with transaction(conn):
        for strategy_id in strategy_ids:
            strategy = build_strategy(strategy_id, strategy_config_path)
            collected: list[ReplayEvaluation] = []
            replay = replay_signals(
                strategy=strategy, provider=provider, symbols=names, start=as_of, end=as_of,
                history_start=warmup_start(as_of), screening_config=screening_config,
                benchmark_symbol=benchmark, on_evaluation=collected.append,
            )
            added = 0
            for evaluation in collected:
                record = build_signal_record(
                    evaluation, data_provider=snapshot.provider, snapshot_hash=snapshot.content_hash,
                    screening_profile_hash=profile_hash(screening_config),
                    relative_strength_hash=hash_config(replay.relative_strength_definition),
                    production_screening=(production_screening or {}).get(evaluation.signal.symbol),
                )
                added += insert_strategy_signal(conn, record, recorded_at=recorded_at)
            signals_by_strategy[strategy_id] = replay.signals
            per_strategy[strategy_id] = {
                "strategy_version": replay.strategy_version, "strategy_config_hash": replay.configuration_hash,
                "evaluations": replay.evaluations, "eligible": len(replay.signals),
                "status_counts": replay.status_counts, "recorded": added,
                "already_recorded": replay.evaluations - added,
            }

    shortlist = select_shortlist(signals_by_strategy, config.shortlist)
    return {
        "as_of": as_of.isoformat(), "activation_stage": config.activation_stage,
        "data": {"provider": snapshot.provider, "price_adjustment": snapshot.adjustment,
                 "snapshot_hash": snapshot.content_hash},
        "screening_profile_hash": profile_hash(screening_config), "benchmark": benchmark,
        "symbols": list(names), "strategies": per_strategy,
        "shortlist": [
            {"symbol": e.symbol, "best_signal_strength": e.best_signal_strength,
             "signals": [
                 {"strategy_id": s.strategy_id, "signal_content_id": derive_strategy_signal_content_id(s),
                  "signal_strength": s.signal_strength, "entry_reference": str(s.entry_reference),
                  "limit_reference": str(s.limit_reference), "initial_stop_reference": str(s.initial_stop_reference),
                  "target_reference": _text(s.target_reference),
                  "expected_holding_period": s.expected_holding_period}
                 for s in e.strategy_signals
             ]}
            for e in shortlist.entries
        ],
        "excluded": [{"symbol": e.symbol, "reason": e.reason} for e in shortlist.excluded],
        "caveats": list(CAVEATS),
    }
