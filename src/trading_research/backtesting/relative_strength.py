"""Point-in-time relative strength for historical replay (docs/historical-backtesting-plan.md D5).

relative_strength = (symbol close_t / symbol close_(t-N) - 1)
                  - (benchmark close_t / benchmark close_(t-N) - 1)

The window is the benchmark's last N+1 sessions known at `now`; the symbol must
have a bar on both end sessions. The result is a plain fractional return
difference, so 0.0 means "in line with the benchmark", which is the scale
`minimum_relative_strength: 0.0` assumes. `as_of` is the latest `available_at`
among the bars used, never `now`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .models import HistoricalBar

DEFAULT_RELATIVE_STRENGTH_SESSIONS = 20

RELATIVE_STRENGTH_DEFINITION = {
    "id": "relative_strength_vs_benchmark",
    "version": 1,
    "formula": "symbol N-session return minus benchmark N-session return over the same sessions",
    "scale": "fractional return difference; 0.0 is in line with the benchmark",
}


@dataclass(frozen=True)
class RelativeStrength:
    value: float
    as_of: datetime
    sessions: int
    window_start: date
    window_end: date


def compute_relative_strength(
    symbol_bars: tuple[HistoricalBar, ...], benchmark_bars: tuple[HistoricalBar, ...],
    *, now: datetime, sessions: int = DEFAULT_RELATIVE_STRENGTH_SESSIONS,
) -> RelativeStrength | None:
    """Returns None when either series cannot support the window (fail closed)."""
    if sessions <= 0:
        raise ValueError("sessions must be positive")
    benchmark = [bar for bar in benchmark_bars if bar.available_at <= now]
    if len(benchmark) < sessions + 1:
        return None
    bench_end, bench_start = benchmark[-1], benchmark[-(sessions + 1)]
    by_date = {bar.session_date: bar for bar in symbol_bars if bar.available_at <= now}
    sym_end, sym_start = by_date.get(bench_end.session_date), by_date.get(bench_start.session_date)
    if sym_end is None or sym_start is None:
        return None
    symbol_return = float(sym_end.close / sym_start.close) - 1.0
    benchmark_return = float(bench_end.close / bench_start.close) - 1.0
    return RelativeStrength(
        value=symbol_return - benchmark_return,
        as_of=max(bar.available_at for bar in (sym_end, sym_start, bench_end, bench_start)),
        sessions=sessions, window_start=bench_start.session_date, window_end=bench_end.session_date,
    )
