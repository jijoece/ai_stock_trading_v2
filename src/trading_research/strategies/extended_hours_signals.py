"""Point-in-time extended-hours signals for the earnings-gap research strategy (plan 5.1).

Every function is pure and takes an explicit `as_of`: only bars with
`available_at <= as_of` are read, so a bar that has not yet closed at the
decision time can never influence a value. Each result records the
`available_at` of the latest bar it actually used.

Conservative conventions (recorded in the runbook):

* Zero-volume bars and closing-print bars (`is_closing_print`) are excluded from
  every price, level, VWAP and volume figure; they are not tradable liquidity.
* `not_before` (the report's release bound, plan D10) restricts every signal to
  bars that START at or after it, so a bar printed before the report was public
  is never read.
* Bar VWAP uses the typical price (high + low + close) / 3 weighted by volume.
* "Last price" is the close of the latest usable bar.
* Percent values are percentages (5 means 5%).

Relative imports only; this module must not import research/evidence modules.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from ..backtesting.models import IntradayBar
from ..evaluation.market_calendar import MARKET_TIMEZONE_NAME

_NY = ZoneInfo(MARKET_TIMEZONE_NAME)
_HUNDRED = Decimal(100)
_THREE = Decimal(3)

INSUFFICIENT_PREMARKET_HISTORY = "insufficient_premarket_history"
MINIMUM_PREMARKET_HISTORY_SESSIONS = 20


@dataclass(frozen=True)
class SignalValue:
    """A signal value and the `available_at` of the latest bar used.

    `value` is None when no usable bar exists (or the signal is undefined);
    `available_at` is then None too, and `note` may explain why."""

    value: Decimal | bool | None
    available_at: datetime | None
    note: str | None = None


def _usable(
    bars: Iterable[IntradayBar],
    session_date: date,
    session: Literal["pre", "regular", "post"],
    as_of: datetime,
    not_before: datetime | None,
) -> list[IntradayBar]:
    """Bars of `session` on `session_date` knowable at `as_of`, with real,
    tradable volume, ordered by start."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not_before is not None and not_before.tzinfo is None:
        raise ValueError("not_before must be timezone-aware")
    out = [
        b
        for b in bars
        if b.session == session
        and b.start.astimezone(_NY).date() == session_date
        and b.available_at <= as_of
        and b.volume > 0
        and not b.is_closing_print
        and (not_before is None or b.start >= not_before)
    ]
    out.sort(key=lambda b: b.start)
    return out


def _typical(bar: IntradayBar) -> Decimal:
    return (bar.high + bar.low + bar.close) / _THREE


def _vwap(bars: Sequence[IntradayBar]) -> Decimal | None:
    volume = sum(b.volume for b in bars)
    if volume <= 0:
        return None
    return sum((_typical(b) * b.volume for b in bars), Decimal(0)) / Decimal(volume)


def _last_price(bars: Sequence[IntradayBar]) -> SignalValue:
    if not bars:
        return SignalValue(None, None, "no_usable_bars")
    last = bars[-1]
    return SignalValue(last.close, last.available_at)


def _percent_move(price: Decimal, reference: Decimal) -> Decimal:
    return (price / reference - Decimal(1)) * _HUNDRED


def post_report_move(
    bars: Iterable[IntradayBar],
    session_date: date,
    regular_close: Decimal,
    as_of: datetime,
    *,
    not_before: datetime | None = None,
) -> SignalValue:
    """Last post-market price at `as_of` (default decision: post_signal_time)
    versus the regular close, in percent. `session_date` is the report date."""
    if regular_close <= 0:
        raise ValueError("regular_close must be positive")
    last = _last_price(_usable(bars, session_date, "post", as_of, not_before))
    if last.value is None:
        return last
    assert isinstance(last.value, Decimal)
    return SignalValue(_percent_move(last.value, regular_close), last.available_at)


def post_hold(
    bars: Iterable[IntradayBar],
    session_date: date,
    as_of: datetime,
    *,
    not_before: datetime | None = None,
) -> SignalValue:
    """True when the last post-market price at `as_of` (default 20:00) is above
    the post-market VWAP: did the after-hours move hold?"""
    usable = _usable(bars, session_date, "post", as_of, not_before)
    vwap = _vwap(usable)
    if vwap is None:
        return SignalValue(None, None, "no_usable_bars")
    return SignalValue(usable[-1].close > vwap, usable[-1].available_at)


def premarket_gap(
    bars: Iterable[IntradayBar],
    session_date: date,
    prior_close: Decimal,
    as_of: datetime,
    *,
    not_before: datetime | None = None,
) -> SignalValue:
    """Last pre-market price at `as_of` (default 09:25) versus the prior
    regular close, in percent."""
    if prior_close <= 0:
        raise ValueError("prior_close must be positive")
    last = _last_price(_usable(bars, session_date, "pre", as_of, not_before))
    if last.value is None:
        return last
    assert isinstance(last.value, Decimal)
    return SignalValue(_percent_move(last.value, prior_close), last.available_at)


def _level(
    bars: Iterable[IntradayBar],
    session_date: date,
    as_of: datetime,
    not_before: datetime | None,
    kind: Literal["high", "low", "vwap"],
) -> SignalValue:
    usable = _usable(bars, session_date, "pre", as_of, not_before)
    if not usable:
        return SignalValue(None, None, "no_usable_bars")
    if kind == "high":
        value = max(b.high for b in usable)
    elif kind == "low":
        value = min(b.low for b in usable)
    else:
        vwap = _vwap(usable)
        assert vwap is not None  # usable bars all have volume > 0
        value = vwap
    return SignalValue(value, usable[-1].available_at)


def premarket_high(
    bars: Iterable[IntradayBar], session_date: date, as_of: datetime, *, not_before: datetime | None = None
) -> SignalValue:
    return _level(bars, session_date, as_of, not_before, "high")


def premarket_low(
    bars: Iterable[IntradayBar], session_date: date, as_of: datetime, *, not_before: datetime | None = None
) -> SignalValue:
    return _level(bars, session_date, as_of, not_before, "low")


def premarket_vwap(
    bars: Iterable[IntradayBar], session_date: date, as_of: datetime, *, not_before: datetime | None = None
) -> SignalValue:
    return _level(bars, session_date, as_of, not_before, "vwap")


def premarket_hold(
    bars: Iterable[IntradayBar],
    session_date: date,
    as_of: datetime,
    *,
    not_before: datetime | None = None,
) -> SignalValue:
    """True when the last pre-market price at `as_of` is strictly above the
    pre-market VWAP (strict: a flat tape that equals its VWAP has not held)."""
    usable = _usable(bars, session_date, "pre", as_of, not_before)
    vwap = _vwap(usable)
    if vwap is None:
        return SignalValue(None, None, "no_usable_bars")
    return SignalValue(usable[-1].close > vwap, usable[-1].available_at)


def _cumulative_premarket_volume(
    bars: Iterable[IntradayBar],
    session_date: date,
    window_start: time,
    window_end: time,
) -> int:
    """Pre-market volume in [window_start, window_end] ET time of day for one
    session; bars must lie wholly inside the window."""
    total = 0
    for b in bars:
        if b.session != "pre" or b.volume <= 0 or b.is_closing_print:
            continue
        start = b.start.astimezone(_NY)
        end = b.end.astimezone(_NY)
        if start.date() != session_date:
            continue
        if start.time() >= window_start and end.time() <= window_end:
            total += b.volume
    return total


def premarket_rel_volume(
    bars: Iterable[IntradayBar],
    session_date: date,
    history: Sequence[tuple[date, Sequence[IntradayBar]]],
    as_of: datetime,
    *,
    not_before: datetime | None = None,
    minimum_history_sessions: int = MINIMUM_PREMARKET_HISTORY_SESSIONS,
) -> SignalValue:
    """Cumulative pre-market volume up to `as_of` versus the symbol's average
    cumulative pre-market volume over the same clock window in the prior
    sessions in `history` (each `(session_date, bars)`; all must be before
    `session_date`).

    The clock window starts at 04:00 ET, or at `not_before` when the report was
    released inside the pre-market, and ends at `as_of`: the same window is
    used today and for every history session, so an early decision time
    compares same-time cumulative volume.

    A history session counts only if it had pre-market volume in its whole
    session (history is fully in the past); fewer than
    `minimum_history_sessions` such sessions, or a zero same-window baseline,
    gives value None with note `insufficient_premarket_history`."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of_et = as_of.astimezone(_NY)
    start_et = time(4, 0)
    if not_before is not None:
        nb = not_before.astimezone(_NY)
        if nb.date() == session_date:
            start_et = max(start_et, nb.time())
    window_end = as_of_et.time()

    today = _usable(bars, session_date, "pre", as_of, not_before)
    today_volume = sum(b.volume for b in today)
    available_at = today[-1].available_at if today else None

    seen_dates: set[date] = set()
    baselines: list[int] = []
    for day, day_bars in history:
        if day >= session_date:
            raise ValueError("history sessions must precede the signal session")
        if day in seen_dates:
            raise ValueError(f"duplicate history session {day}")
        seen_dates.add(day)
        day_list = list(day_bars)
        has_premarket = _cumulative_premarket_volume(day_list, day, time(4, 0), time(9, 30)) > 0
        if has_premarket:
            baselines.append(_cumulative_premarket_volume(day_list, day, start_et, window_end))
    if len(baselines) < minimum_history_sessions:
        return SignalValue(None, available_at, INSUFFICIENT_PREMARKET_HISTORY)
    average = Decimal(sum(baselines)) / Decimal(len(baselines))
    if average <= 0:
        return SignalValue(None, available_at, INSUFFICIENT_PREMARKET_HISTORY)
    return SignalValue(Decimal(today_volume) / average, available_at)


def continuation(gap: SignalValue, move: SignalValue) -> SignalValue:
    """`premarket_gap - post_report_move` (did the gap grow or shrink
    overnight?). Undefined when either input is; available once both are."""
    if not isinstance(gap.value, Decimal) or not isinstance(move.value, Decimal):
        return SignalValue(None, None, "undefined_input")
    assert gap.available_at is not None and move.available_at is not None
    return SignalValue(gap.value - move.value, max(gap.available_at, move.available_at))
