"""U.S. equity market calendar backed by `exchange_calendars` (docs/milestone-4.md
Step 13; migrated from a hand-written fixed-rule calendar per
docs/milestones/rebuild/4.md, PR 3).

The XNYS calendar from `exchange_calendars` is the sole authority for U.S.
equity sessions: holidays, weekend-observance shifting, early closes, and
one-off exchange closures all come from its packaged offline calendar data.
No network access, no custom holiday arithmetic, no fixed-close assumption.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from exchange_calendars import get_calendar

MARKET_TIMEZONE_NAME = "America/New_York"

_XNYS_CALENDAR_NAME = "XNYS"


class MarketCalendarError(RuntimeError):
    """The market-session policy cannot be determined — fail closed rather
    than guess (docs/milestone-4.md Step 13: "Do not submit an order when
    the market-session policy cannot be determined")."""


@lru_cache(maxsize=1)
def _calendar():
    try:
        return get_calendar(_XNYS_CALENDAR_NAME)
    except Exception as exc:
        raise MarketCalendarError(
            "the XNYS exchange calendar could not be resolved — refusing to guess"
        ) from exc


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def is_trading_day(day: date) -> bool:
    cal = _calendar()
    try:
        return bool(cal.is_session(pd.Timestamp(day)))
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve XNYS session status for {day.isoformat()}"
        ) from exc


def is_market_holiday(day: date) -> bool:
    """True for a non-weekend weekday on which XNYS has no session,
    including one-off exchange closures (e.g. a day the whole market closed
    for a national observance) — not merely the fixed federal-holiday set."""
    return not is_weekend(day) and not is_trading_day(day)


def next_trading_session(day: date, *, inclusive: bool = False) -> date:
    """The next valid trading session on or after `day` (`inclusive=True`)
    or strictly after `day` (`inclusive=False`) — the deterministic rule for
    "a horizon lands on a holiday or weekend" (docs/milestone-4.md Step 13)."""
    cal = _calendar()
    ts = pd.Timestamp(day)
    try:
        session = cal.date_to_session(ts, "next")
        if not inclusive and session == ts:
            session = cal.next_session(session)
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve the next XNYS trading session for {day.isoformat()}"
        ) from exc
    return session.date()


def add_trading_days(start: date, n: int) -> date:
    """`start` is treated as day zero regardless of whether it is itself a
    trading session; returns the date reached after stepping forward `n`
    trading sessions (docs/milestone-4.md Step 11 horizons: 1/5/10/20/60
    trading days)."""
    if n < 0:
        raise ValueError("n must not be negative")
    if n == 0:
        return start
    cal = _calendar()
    ts = pd.Timestamp(start)
    try:
        anchor = cal.date_to_session(ts, "next")
        if anchor == ts:
            anchor = cal.next_session(anchor)
        result = cal.session_offset(anchor, n - 1)
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve a trading-day horizon of {n} session(s) from "
            f"{start.isoformat()}"
        ) from exc
    return result.date()


def is_market_open(moment: datetime) -> bool:
    """Regular U.S. equities hours only (docs/milestone-4.md Step 13 default
    policy: "regular-hours U.S. equities only", "no extended-hours
    orders"), using the actual XNYS session schedule — including early
    closes — rather than a fixed close time."""
    if moment.tzinfo is None:
        raise MarketCalendarError("is_market_open requires a timezone-aware datetime")
    try:
        local = moment.astimezone(ZoneInfo(MARKET_TIMEZONE_NAME))
    except ZoneInfoNotFoundError as exc:
        raise MarketCalendarError(
            "the America/New_York timezone database is not available in this environment — "
            "cannot determine the market session, refusing to guess"
        ) from exc
    cal = _calendar()
    try:
        return bool(cal.is_open_on_minute(pd.Timestamp(local)))
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve XNYS session status for {moment.isoformat()}"
        ) from exc


def regular_session_close(day: date) -> datetime:
    """The actual scheduled close for `day`, including early closes, as an
    aware New York datetime."""
    cal = _calendar()
    try:
        close = cal.session_close(pd.Timestamp(day))
    except Exception as exc:
        raise MarketCalendarError(f"{day.isoformat()} is not a trading session") from exc
    try:
        return close.tz_convert(ZoneInfo(MARKET_TIMEZONE_NAME)).to_pydatetime()
    except ZoneInfoNotFoundError as exc:
        raise MarketCalendarError("America/New_York timezone data is unavailable") from exc
