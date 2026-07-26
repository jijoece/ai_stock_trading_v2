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

The XNYS-dependent functions — `is_trading_day`, `next_trading_session`,
`add_trading_days`, `is_market_open`, and `regular_session_close` — either
return a correct result for a `day`/`moment` within `[_CALENDAR_START,
_CALENDAR_END]`, or raise `MarketCalendarError` for a date outside that
range; they never silently clamp, extrapolate, or fall back to a different
calendar. `is_weekend` is pure weekday arithmetic: it does not consult
XNYS at all, accepts any date in or out of the configured range, and never
raises `MarketCalendarError`. `is_market_holiday` calls `is_weekend` first
and short-circuits to `False` for weekend dates without consulting XNYS,
but is otherwise XNYS-dependent (and therefore range-enforcing) for
non-weekend dates.

`_CALENDAR_END` must be extended (and this module re-released) before the
current date reaches it; this module does not extend itself automatically.
A test (`_calendar_end_margin_ok`, guarded in
`tests/unit/test_market_calendar.py`) fails once fewer than
`_CALENDAR_END_MINIMUM_MARGIN_YEARS` remain, forcing a deliberate range
extension rather than a silent lapse.

Because one-off emergency closures (e.g. a national day of mourning) are
recorded in `exchange_calendars`' packaged data only as of the installed
library version, a *future* one-off closure that has not yet been added to
a released version of `exchange_calendars` cannot be reflected here until
the dependency is upgraded. Regular holiday and weekend rules are exact for
the full supported range; ad hoc future closures are not predictable in
advance by any calendar library.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
# this must not be the library's moving default window. Note: XNYS's own
# actual first/last session can fall a few days inside this range (e.g.
# `_CALENDAR_START` landing on a holiday) — `regular_session_close` accounts
# for that gap explicitly; `is_trading_day`/`next_trading_session`/
# `add_trading_days`/`is_market_open` still fail closed for a date in that
# gap, but via a less specific "could not resolve" message rather than a
# dedicated non-session classification (out of scope for this correction).
_CALENDAR_START = date(1990, 1, 1)
_CALENDAR_END = date(2035, 12, 31)

# `regular_session_close`'s range pre-check classifies a date as "outside
# the supported range" using these constants directly, not
# `cal.first_session`/`cal.last_session` — see the note above.
_CALENDAR_END_MINIMUM_MARGIN_YEARS = 5


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


def _calendar_end_margin_ok(as_of: date) -> bool:
    """True if `_CALENDAR_END` is still at least
    `_CALENDAR_END_MINIMUM_MARGIN_YEARS` beyond `as_of`. False signals
    `_CALENDAR_END` must be extended deliberately (see the module
    docstring); this function does not raise or extend the range itself —
    it exists to be asserted against in a test that fails once the margin
    has eroded, rather than letting the range silently approach its edge."""
    return _CALENDAR_END - as_of >= timedelta(days=365 * _CALENDAR_END_MINIMUM_MARGIN_YEARS)


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
    supported calendar range (`_CALENDAR_START`/`_CALENDAR_END`, see module
    docstring); XNYS itself could not be constructed or queried; or the
    timezone database is unavailable. Range membership is classified using
    `_CALENDAR_START`/`_CALENDAR_END` directly, not
    `cal.first_session`/`cal.last_session` — XNYS's own actual first/last
    session can fall a few days inside the configured range (e.g.
    `_CALENDAR_START` itself landing on a holiday), and a date in that gap
    is still "inside the supported range but not a session", not "outside
    the supported range"."""
    if day < _CALENDAR_START or day > _CALENDAR_END:
        raise MarketCalendarError(
            f"{day.isoformat()} is outside the supported XNYS calendar range "
            f"({_CALENDAR_START.isoformat()} to {_CALENDAR_END.isoformat()})"
        )
    cal = _calendar()
    try:
        close = cal.session_close(pd.Timestamp(day))
    except (NotSessionError, DateOutOfBounds, RequestedSessionOutOfBounds) as exc:
        # `day` has already been confirmed inside [_CALENDAR_START,
        # _CALENDAR_END] above. exchange_calendars still raises
        # DateOutOfBounds/RequestedSessionOutOfBounds (not NotSessionError)
        # for a date inside that range but outside its own actual
        # first/last session — from this module's perspective that is the
        # same outcome as an ordinary weekend/holiday non-session.
        raise MarketCalendarError(
            f"{day.isoformat()} is not a trading session (weekend or exchange holiday)"
        ) from exc
    except Exception as exc:
        raise MarketCalendarError(
            f"could not resolve the XNYS session close for {day.isoformat()}"
        ) from exc
    try:
        return close.tz_convert(ZoneInfo(MARKET_TIMEZONE_NAME)).to_pydatetime()
    except ZoneInfoNotFoundError as exc:
        raise MarketCalendarError("America/New_York timezone data is unavailable") from exc
