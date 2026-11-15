"""Typed records for deterministic daily-bar control backtests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo


class BacktestError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalBar:
    symbol: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    available_at: datetime
    point_in_time_safe: bool = True
    source_id: str = "fixture"

    def __post_init__(self) -> None:
        if self.available_at.tzinfo is None:
            raise BacktestError("bar available_at must be timezone-aware")
        if not self.point_in_time_safe:
            raise BacktestError("historical bar must be point-in-time safe")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.high < self.low:
            raise BacktestError("bar OHLC values are invalid")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise BacktestError("open and close must lie within the daily range")


_INTRADAY_SESSIONS = ("pre", "regular", "post")
_NY = ZoneInfo("America/New_York")
_EARLIEST_POST_START = time(13, 0)  # earliest scheduled (early) close


@dataclass(frozen=True)
class IntradayBar:
    """One session-tagged intraday bar. `start`/`end` are tz-aware; `available_at`
    equals `end` (a bar is knowable only once it has closed). Bars flagged
    `is_closing_print` carry closing-auction volume and are never tradable
    post-market liquidity."""

    symbol: str
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    session: Literal["pre", "regular", "post"]
    available_at: datetime
    source_id: str = "fixture"
    is_closing_print: bool = False

    def __post_init__(self) -> None:
        for name in ("start", "end", "available_at"):
            if getattr(self, name).tzinfo is None:
                raise BacktestError(f"intraday bar {name} must be timezone-aware")
        if self.end <= self.start:
            raise BacktestError("intraday bar end must be after start")
        if self.available_at != self.end:
            raise BacktestError("intraday bar available_at must equal end")
        if not all(v.is_finite() for v in (self.open, self.high, self.low, self.close)):
            raise BacktestError("intraday bar OHLC values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.high < self.low:
            raise BacktestError("intraday bar OHLC values are invalid")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise BacktestError("intraday bar open and close must lie within its range")
        if self.volume < 0:
            raise BacktestError("intraday bar volume must not be negative")
        if self.session not in _INTRADAY_SESSIONS:
            raise BacktestError(f"unknown intraday session {self.session!r}")
        local = self.start.astimezone(_NY).time()
        if not (time(4, 0) <= local < time(20, 0)):
            raise BacktestError("intraday bar start must lie within 04:00-20:00 ET")
        if self.session == "pre" and local >= time(9, 30):
            raise BacktestError("pre-market bar must start before 09:30 ET")
        if self.session == "regular" and not (time(9, 30) <= local < time(16, 0)):
            raise BacktestError("regular-session bar must start in [09:30, 16:00) ET")
        if self.session == "post" and local < _EARLIEST_POST_START:
            raise BacktestError("post-market bar must start at or after the earliest early close")
        if self.is_closing_print and self.session != "post":
            raise BacktestError("only a post-market bar can be a closing print")


@dataclass(frozen=True)
class EntrySignal:
    signal_id: str
    symbol: str
    generated_after_session: date
    limit_price: Decimal
    quantity_hint: Decimal
    initial_stop_reference: Decimal | None = None
    target_reference: Decimal | None = None
    maximum_holding_sessions: int | None = None
    strategy_signal_id: str | None = None

    def __post_init__(self) -> None:
        if self.limit_price <= 0 or self.quantity_hint <= 0:
            raise BacktestError("entry signal price and quantity must be positive")
        if self.quantity_hint != self.quantity_hint.to_integral_value():
            raise BacktestError("backtests support whole-share signals only")
        if self.initial_stop_reference is not None and self.initial_stop_reference <= 0:
            raise BacktestError("entry signal initial_stop_reference must be positive")
        if self.target_reference is not None and self.target_reference <= 0:
            raise BacktestError("entry signal target_reference must be positive")
        if self.maximum_holding_sessions is not None and self.maximum_holding_sessions <= 0:
            raise BacktestError("entry signal maximum_holding_sessions must be positive")


@dataclass(frozen=True)
class BacktestFill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    fill_price: Decimal
    fees: Decimal
    slippage: Decimal
    market_date: date
    exit_reason: str | None = None
    # Monotonic engine execution order. None is reserved for legacy rows;
    # metric reconstruction then preserves the supplied tuple order.
    fill_sequence: int | None = None
    # Opening fill identity carried through every exit of the same lot.
    position_id: str | None = None

    def __post_init__(self) -> None:
        if self.fill_sequence is not None and self.fill_sequence < 1:
            raise BacktestError("fill_sequence must be positive when present")


@dataclass(frozen=True)
class BacktestDailyState:
    market_date: date
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown_fraction: Decimal


@dataclass(frozen=True)
class BacktestResult:
    backtest_run_id: str
    configuration_hash: str
    daily_states: tuple[BacktestDailyState, ...]
    fills: tuple[BacktestFill, ...]
    rejected_entries: tuple[dict, ...]
    metrics: dict
    unresolved_evaluations: tuple[str, ...]
    # Sessions whose opening drawdown was at or beyond the limit, whether or not a signal arrived to be
    # rejected. Not persisted: it is derived from the same bars and configuration as the daily states.
    drawdown_breach_sessions: tuple[date, ...] = ()
