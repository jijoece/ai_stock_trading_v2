"""Point-in-time signal replay (docs/historical-backtesting-plan.md Step 2).

Replays a deterministic strategy scanner over past sessions. For each session
`s`, `now` is that session's actual regular close; a strategy sees only bars
whose `available_at <= now` (and whose session is not after `s`), relative
strength and screening are derived from those same bars, and every resulting
signal is kept. Only ELIGIBLE signals are meant for `run_strategy_backtest`;
every other outcome is counted for the report.

The engine's own availability check runs once per run, against the end of the
range (PR 8 section 4.3). This module closes that gap at the replay layer, so
`engine.py` is unchanged.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache

from ..analysis.screener import ScreeningConfig, ScreeningResult
from ..evaluation.market_calendar import is_trading_day, regular_session_close
from ..models.trading_models import DataFreshness, TechnicalFactorInput
from ..strategies.contracts import (
    CandidateStrategy,
    StrategyContext,
    StrategyMarketData,
    StrategySignal,
    StrategyStatus,
)
from .data_provider import HistoricalDataProvider
from .models import BacktestError, HistoricalBar
from .relative_strength import (
    DEFAULT_RELATIVE_STRENGTH_SESSIONS,
    RELATIVE_STRENGTH_DEFINITION,
    compute_relative_strength,
)
from .screening_profile import backtest_screening_result, describe_profile


@dataclass(frozen=True)
class ReplayResult:
    strategy_id: str
    strategy_version: str
    configuration_hash: str
    benchmark_symbol: str
    symbols: tuple[str, ...]
    sessions: tuple[date, ...]
    evaluations: int
    # ELIGIBLE signals only, in (session, symbol) order.
    signals: tuple[StrategySignal, ...]
    # One entry per evaluation, so the values sum to `evaluations`.
    status_counts: dict[str, int]
    # How many evaluations carried each code (parameter suffix after ':' dropped).
    # An evaluation carries several codes, so these do not sum to `evaluations`.
    reason_code_counts: dict[str, int]
    screening_profile: dict
    relative_strength_definition: dict


@dataclass(frozen=True)
class ReplayEvaluation:
    """One strategy evaluation of one symbol on one session, any status."""

    session: date
    signal: StrategySignal
    screening: ScreeningResult


def assert_point_in_time(
    *, session: date, now: datetime, bars: tuple[HistoricalBar, ...],
    technical: TechnicalFactorInput | None,
) -> None:
    """Raise unless nothing handed to a strategy could be unknown at `now`."""
    for bar in bars:
        if bar.available_at > now:
            raise BacktestError(
                f"look-ahead: {bar.symbol} {bar.session_date} available_at {bar.available_at.isoformat()} "
                f"is after replay time {now.isoformat()}"
            )
        if bar.session_date > session:
            raise BacktestError(
                f"look-ahead: {bar.symbol} bar for session {bar.session_date} is after replay session {session}"
            )
    if technical is not None and technical.freshness is not None and technical.freshness.as_of > now:
        raise BacktestError(
            f"look-ahead: technical freshness as_of {technical.freshness.as_of.isoformat()} "
            f"is after replay time {now.isoformat()}"
        )


def _sessions(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise BacktestError("replay end must not precede start")
    days: list[date] = []
    day = start
    while day <= end:
        if is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    if not days:
        raise BacktestError(f"no trading sessions between {start} and {end}")
    return tuple(days)


class _History:
    """One symbol's bars plus a cursor, so each session's view is cheap to build."""

    def __init__(self, symbol: str, bars: tuple[HistoricalBar, ...], session_close: Callable[[date], datetime]):
        self.symbol = symbol
        self._bars = bars
        self._cursor = 0
        self._prefix_max: list[datetime] = []
        latest: datetime | None = None
        previous: date | None = None
        for bar in bars:
            if previous is not None and bar.session_date <= previous:
                raise BacktestError(f"{symbol}: bars are not strictly ordered by session date")
            previous = bar.session_date
            if bar.available_at < session_close(bar.session_date):
                raise BacktestError(
                    f"{symbol} {bar.session_date}: available_at {bar.available_at.isoformat()} is before "
                    "its session closed"
                )
            latest = bar.available_at if latest is None else max(latest, bar.available_at)
            self._prefix_max.append(latest)

    def known(self, session: date, now: datetime) -> tuple[HistoricalBar, ...]:
        while self._cursor < len(self._bars) and self._bars[self._cursor].session_date <= session:
            self._cursor += 1
        view = self._bars[: self._cursor]
        if self._cursor and self._prefix_max[self._cursor - 1] > now:
            view = tuple(bar for bar in view if bar.available_at <= now)
        return view


def replay_signals(
    *, strategy: CandidateStrategy, provider: HistoricalDataProvider, symbols: tuple[str, ...],
    start: date, end: date, history_start: date, screening_config: ScreeningConfig,
    benchmark_symbol: str = "SPY", relative_strength_sessions: int = DEFAULT_RELATIVE_STRENGTH_SESSIONS,
    on_evaluation: Callable[[ReplayEvaluation], None] | None = None,
) -> ReplayResult:
    names = tuple(sorted({s.strip().upper() for s in symbols if s.strip()}))
    if not names:
        raise BacktestError("at least one symbol is required")
    if history_start > start:
        raise BacktestError("history_start must not be after start")
    sessions = _sessions(start, end)
    final_as_of = regular_session_close(sessions[-1])

    @cache
    def session_close(day: date) -> datetime:
        try:
            return regular_session_close(day)
        except Exception as exc:
            raise BacktestError(f"{day} is not a trading session: {exc}") from exc

    def load(symbol: str, label: str) -> _History:
        bars = provider.bars(symbol=symbol, start=history_start, end=end, as_of=final_as_of)
        if not bars:
            raise BacktestError(f"no {label} history for {symbol}")
        return _History(symbol, bars, session_close)

    benchmark = load(benchmark_symbol, "benchmark")
    histories = {symbol: load(symbol, "symbol") for symbol in names}

    signals: list[StrategySignal] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    definitions: set[tuple[str, str]] = set()
    evaluations = 0

    for session in sessions:
        now = session_close(session)
        benchmark_bars = benchmark.known(session, now)
        for symbol in names:
            bars = histories[symbol].known(session, now)
            screening = backtest_screening_result(symbol, bars, config=screening_config, now=now)
            strength = compute_relative_strength(
                bars, benchmark_bars, now=now, sessions=relative_strength_sessions,
            )
            technical = None
            if strength is not None:
                technical = TechnicalFactorInput(
                    symbol=symbol, relative_strength=strength.value,
                    freshness=DataFreshness(
                        source=f"{RELATIVE_STRENGTH_DEFINITION['id']}/v{RELATIVE_STRENGTH_DEFINITION['version']}",
                        as_of=strength.as_of,
                    ),
                )
            assert_point_in_time(session=session, now=now, bars=bars, technical=technical)
            signal = strategy.evaluate(
                symbol, StrategyMarketData(symbol=symbol, bars=bars, technical=technical),
                StrategyContext(now=now, screening_result=screening),
            )
            evaluations += 1
            if on_evaluation is not None:
                on_evaluation(ReplayEvaluation(session=session, signal=signal, screening=screening))
            definitions.add((signal.strategy_version, signal.configuration_hash))
            status_counts[signal.status.value] += 1
            reason_counts.update(code.split(":", 1)[0] for code in signal.reason_codes)
            if signal.status == StrategyStatus.ELIGIBLE:
                signals.append(signal)

    if len(definitions) > 1:
        raise BacktestError(
            f"replay for {strategy.strategy_id!r} produced mixed strategy definitions: {sorted(definitions)}"
        )
    version, config_hash = next(iter(definitions))
    return ReplayResult(
        strategy_id=strategy.strategy_id, strategy_version=version, configuration_hash=config_hash,
        benchmark_symbol=benchmark_symbol, symbols=names, sessions=sessions, evaluations=evaluations,
        signals=tuple(signals), status_counts=dict(sorted(status_counts.items())),
        reason_code_counts=dict(sorted(reason_counts.items())),
        screening_profile=describe_profile(screening_config),
        relative_strength_definition={
            **RELATIVE_STRENGTH_DEFINITION, "sessions": relative_strength_sessions,
            "benchmark": benchmark_symbol,
        },
    )
