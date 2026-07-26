"""U.S. equity market calendar backed by `exchange_calendars` (docs/milestone-4.md
Step 13; migrated from a hand-written fixed-rule calendar per
docs/milestones/rebuild/4.md, PR 3; date-range and error-classification
corrections per docs/milestones/rebuild/5.md, PR #9).

The XNYS calendar from `exchange_calendars` is the sole authority for U.S.
equity sessions: holidays, weekend-observance shifting, early closes, and
one-off exchange closures all come from its packaged offline calendar data
bundled with the library. No network access, no custom holiday arithmetic,
no fixed-close assumption.

Supported range: XNYS is constructed once (`functools.lru_cache(maxsize=1)`)
with an explicit, fixed range, `_CALENDAR_START` (1990-01-01) to
`_CALENDAR_END` (2035-12-31), not `exchange_calendars`' library default.
That library default computes a *moving* window ("now minus ~20 years" to
"now plus ~1 year") at construction time and, combined with the
process-lifetime cache, would freeze at whatever window happened to be
current the first time this process called `_calendar()` — silently
narrowing over the life of a long-running process. A fixed range that is
extended deliberately is used instead.

Every public function in this module either returns a correct result for a
`day`/`moment` within `[_CALENDAR_START, _CALENDAR_END]`, or raises
`MarketCalendarError` for a date outside that range — it never silently
clamps, extrapolates, or falls back to a different calendar. `_CALENDAR_END`
must be extended (and this module re-released) before the current date
reaches it; this module does not extend itself automatically.

Because one-off emergency closures (e.g. a national day of mourning) are
recorded in `exchange_calendars`' packaged data only as of the installed
library version, a *future* one-off closure that has not yet been added to
a released version of `exchange_calendars` cannot be reflected here until
the dependency is upgraded. Regular holiday and weekend rules are exact for
the full supported range; ad hoc future closures are not predictable in
advance by any calendar library.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from exchange_calendars import get_calendar
from exchange_calendars.errors import (
    DateOutOfBounds,
    NotSessionError,
    RequestedSessionOutOfBounds,
)

MARKET_TIMEZONE_NAME = "America/New_York"

_XNYS_CALENDAR_NAME = "XNYS"

# Fixed, explicit range for `_calendar()` — see the module docstring for why
# this must not be the library's moving default window.
_CALENDAR_START = date(1990, 1, 1)
_CALENDAR_END = date(2035, 12, 31)


class MarketCalendarError(RuntimeError):
    """The market-session policy cannot be determined — fail closed rather
    than guess (docs/milestone-4.md Step 13: "Do not submit an order when
    the market-session policy cannot be determined")."""


@lru_cache(maxsize=1)
def _calendar():
    try:
        return get_calendar(_XNYS_CALENDAR_NAME, start=_CALENDAR_START, end=_CALENDAR_END)
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
    aware New York datetime.

    Raises `MarketCalendarError`, distinguishing the cause: `day` is a
    weekend/exchange holiday (not a trading session); `day` is outside the
    supported calendar range (see module docstring); XNYS itself could not
    be constructed or queried; or the timezone database is unavailable."""
    cal = _calendar()
    first = cal.first_session.date()
    last = cal.last_session.date()
    if day < first or day > last:
        raise MarketCalendarError(
            f"{day.isoformat()} is outside the supported XNYS calendar range "
            f"({first.isoformat()} to {last.isoformat()})"
        )
    try:
        close = cal.session_close(pd.Timestamp(day))
    except NotSessionError as exc:
        raise MarketCalendarError(
            f"{day.isoformat()} is not a trading session (weekend or exchange holiday)"
        ) from exc
    except (DateOutOfBounds, RequestedSessionOutOfBounds) as exc:
        raise MarketCalendarError(
            f"{day.isoformat()} is outside the supported XNYS calendar range "
            f"({first.isoformat()} to {last.isoformat()})"
        ) from exc
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve the XNYS session close for {day.isoformat()}"
        ) from exc
    try:
        return close.tz_convert(ZoneInfo(MARKET_TIMEZONE_NAME)).to_pydatetime()
    except ZoneInfoNotFoundError as exc:
        raise MarketCalendarError("America/New_York timezone data is unavailable") from exc
