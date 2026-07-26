from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from trading_research.evaluation.market_calendar import (
    MarketCalendarError,
    _CALENDAR_END,
    _calendar_end_margin_ok,
    add_trading_days,
    is_market_holiday,
    is_market_open,
    is_trading_day,
    is_weekend,
    next_trading_session,
    regular_session_close,
)

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 1),   # New Year's Day (Thursday)
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents Day
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day observed (July 4 is a Saturday)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas
    ],
)
def test_known_2026_holidays(day):
    assert is_market_holiday(day) is True
    assert is_trading_day(day) is False


def test_july_4_saturday_shifts_observance_to_friday_not_saturday_itself():
    # July 4, 2026 falls on a Saturday, which is already a non-trading day
    # for weekend reasons — the *observed* holiday shift lands on Friday.
    assert is_market_holiday(date(2026, 7, 4)) is False  # not itself in the holiday set
    assert is_market_holiday(date(2026, 7, 3)) is True
    assert is_trading_day(date(2026, 7, 4)) is False  # still a non-trading day (weekend)


def test_ordinary_weekday_is_a_trading_day():
    assert is_trading_day(date(2026, 7, 13)) is True  # a Monday, no nearby holiday


def test_weekend_is_not_a_trading_day():
    assert is_weekend(date(2026, 7, 11)) is True  # Saturday
    assert is_trading_day(date(2026, 7, 11)) is False
    assert is_trading_day(date(2026, 7, 12)) is False  # Sunday


def test_next_trading_session_skips_weekend():
    friday = date(2026, 7, 10)
    assert next_trading_session(friday) == date(2026, 7, 13)  # following Monday


def test_next_trading_session_skips_holiday_and_weekend_together():
    # Independence Day (observed Friday July 3, 2026) directly precedes the weekend.
    thursday_before = date(2026, 7, 2)
    assert next_trading_session(thursday_before) == date(2026, 7, 6)  # Monday


def test_next_trading_session_inclusive_returns_same_day_if_already_trading():
    monday = date(2026, 7, 13)
    assert next_trading_session(monday, inclusive=True) == monday


def test_add_trading_days_one_day_horizon():
    monday = date(2026, 7, 13)
    assert add_trading_days(monday, 1) == date(2026, 7, 14)


def test_add_trading_days_crosses_weekend():
    friday = date(2026, 7, 10)
    assert add_trading_days(friday, 1) == date(2026, 7, 13)  # skips the weekend


def test_add_trading_days_five_day_horizon_is_one_calendar_week_later():
    monday = date(2026, 7, 13)
    assert add_trading_days(monday, 5) == date(2026, 7, 20)


def test_add_trading_days_zero_returns_start():
    day = date(2026, 7, 13)
    assert add_trading_days(day, 0) == day


def test_add_trading_days_negative_rejected():
    with pytest.raises(ValueError):
        add_trading_days(date(2026, 7, 13), -1)


def test_is_market_open_during_regular_hours():
    moment = datetime(2026, 7, 13, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open(moment) is True


def test_is_market_open_false_before_open():
    moment = datetime(2026, 7, 13, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open(moment) is False


def test_is_market_open_false_after_close():
    moment = datetime(2026, 7, 13, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open(moment) is False


def test_is_market_open_false_on_weekend():
    moment = datetime(2026, 7, 11, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open(moment) is False


def test_is_market_open_false_on_holiday():
    moment = datetime(2026, 12, 25, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open(moment) is False


def test_is_market_open_requires_timezone_aware_datetime():
    with pytest.raises(MarketCalendarError):
        is_market_open(datetime(2026, 7, 13, 10, 0))


def test_is_market_open_converts_from_utc():
    # 14:00 UTC on a July weekday is 10:00 America/New_York (EDT, UTC-4).
    moment = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    assert is_market_open(moment) is True


def test_is_market_open_edt_offset_from_utc():
    # July is Eastern Daylight Time, UTC-4: the 13:30 UTC open corresponds
    # to 09:30 America/New_York.
    moment = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)
    assert is_market_open(moment) is True
    moment_before_open_utc = datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc)
    assert is_market_open(moment_before_open_utc) is False


def test_is_market_open_est_offset_from_utc():
    # January is Eastern Standard Time, UTC-5: the 14:30 UTC open corresponds
    # to 09:30 America/New_York — one hour later in UTC than the EDT case.
    moment = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    assert is_market_open(moment) is True
    moment_before_open_utc = datetime(2026, 1, 2, 14, 29, tzinfo=timezone.utc)
    assert is_market_open(moment_before_open_utc) is False


def test_is_market_open_exact_open_boundary():
    assert is_market_open(datetime(2026, 7, 13, 9, 30, tzinfo=NY)) is True
    assert is_market_open(datetime(2026, 7, 13, 9, 29, tzinfo=NY)) is False


def test_is_market_open_exact_close_boundary():
    assert is_market_open(datetime(2026, 7, 13, 15, 59, tzinfo=NY)) is True
    assert is_market_open(datetime(2026, 7, 13, 16, 0, tzinfo=NY)) is False


def test_is_market_open_pre_market_is_false():
    # 7:00 AM New York time is well before the 9:30 regular open.
    moment = datetime(2026, 7, 13, 7, 0, tzinfo=NY)
    assert is_market_open(moment) is False


def test_is_market_open_after_hours_is_false():
    # 6:00 PM New York time is well after the 4:00 PM regular close.
    moment = datetime(2026, 7, 13, 18, 0, tzinfo=NY)
    assert is_market_open(moment) is False


def test_is_market_open_false_on_early_close_afternoon():
    # The day after Thanksgiving is a well-known NYSE early-close half day
    # (1:00 PM regular close instead of 4:00 PM).
    day_after_thanksgiving = date(2026, 11, 27)
    assert is_market_open(datetime(2026, 11, 27, 12, 59, tzinfo=NY)) is True
    assert is_market_open(datetime(2026, 11, 27, 13, 0, tzinfo=NY)) is False
    assert is_market_open(datetime(2026, 11, 27, 15, 0, tzinfo=NY)) is False
    assert is_trading_day(day_after_thanksgiving) is True


def test_regular_session_close_ordinary_session():
    close = regular_session_close(date(2026, 7, 13))
    assert close == datetime(2026, 7, 13, 16, 0, tzinfo=NY)


def test_regular_session_close_on_early_close_session():
    # Christmas Eve, when it falls on a trading day, is a well-known NYSE
    # early-close half day (1:00 PM instead of 4:00 PM).
    close = regular_session_close(date(2026, 12, 24))
    assert close == datetime(2026, 12, 24, 13, 0, tzinfo=NY)


def test_regular_session_close_non_session_raises():
    with pytest.raises(MarketCalendarError):
        regular_session_close(date(2026, 7, 11))  # Saturday


def test_historical_one_off_exchange_closure():
    # December 5, 2018: U.S. markets were closed for a National Day of
    # Mourning (funeral of President George H.W. Bush) — a one-off closure,
    # not a fixed annual federal holiday.
    closure_day = date(2018, 12, 5)
    assert is_weekend(closure_day) is False
    assert is_market_holiday(closure_day) is True
    assert is_trading_day(closure_day) is False


def test_next_trading_session_inclusive_on_holiday_skips_to_next_session():
    # Christmas Day 2026 is a Friday holiday; inclusive lookup must still
    # advance past it to the next real session, not return the holiday.
    christmas = date(2026, 12, 25)
    assert next_trading_session(christmas, inclusive=True) == date(2026, 12, 28)


def test_add_trading_days_across_year_boundary():
    # New Year's Day 2026 (Thursday) is a holiday: one trading day after
    # Dec 31, 2025 (Wednesday) lands on Jan 2, 2026 (Friday), not Jan 1.
    new_years_eve = date(2025, 12, 31)
    assert add_trading_days(new_years_eve, 1) == date(2026, 1, 2)


# --- Explicit calendar range (docs/milestones/rebuild/5.md, PR #9) ---
#
# `exchange_calendars.get_calendar("XNYS")` called with no explicit start/end
# defaults to a *moving* window of roughly "now minus 20 years" to "now plus
# 1 year". These fixtures use dates deliberately outside that historical
# moving-default window (but inside this module's fixed, explicit
# `_CALENDAR_START`/`_CALENDAR_END` range) to prove the calendar is no
# longer silently bounded by wall-clock time.


def test_valid_historical_date_before_library_default_window():
    # 1995-06-01 predates exchange_calendars' own moving 20-year-lookback
    # default; it must still resolve correctly under the fixed explicit range.
    assert is_trading_day(date(1995, 6, 1)) is True


def test_regular_session_close_for_older_historical_session():
    close = regular_session_close(date(1995, 6, 1))
    assert close == datetime(1995, 6, 1, 16, 0, tzinfo=NY)


def test_valid_future_date_after_library_default_window():
    # 2030-06-03 is beyond exchange_calendars' own moving 1-year-lookahead
    # default; it must still resolve correctly under the fixed explicit range.
    assert is_trading_day(date(2030, 6, 3)) is True


def test_next_trading_session_near_configured_upper_boundary():
    # 2035-12-31 is this module's configured last supported session.
    sunday_before_boundary = date(2035, 12, 30)
    assert next_trading_session(sunday_before_boundary) == date(2035, 12, 31)


def test_next_trading_session_at_upper_boundary_inclusive_returns_last_session():
    last_supported_session = date(2035, 12, 31)
    assert (
        next_trading_session(last_supported_session, inclusive=True)
        == last_supported_session
    )


def test_next_trading_session_past_upper_boundary_raises():
    # No supported session exists strictly after the configured last session.
    last_supported_session = date(2035, 12, 31)
    with pytest.raises(MarketCalendarError):
        next_trading_session(last_supported_session, inclusive=False)


def test_add_trading_days_crossing_upper_boundary_raises():
    near_boundary = date(2035, 12, 20)
    with pytest.raises(MarketCalendarError):
        add_trading_days(near_boundary, 20)


def test_regular_session_close_before_supported_range_raises():
    with pytest.raises(MarketCalendarError):
        regular_session_close(date(1985, 1, 2))


def test_regular_session_close_after_supported_range_raises():
    with pytest.raises(MarketCalendarError):
        regular_session_close(date(2036, 1, 2))


# --- Non-session start-date fixtures for add_trading_days/next_trading_session
# (docs/milestones/rebuild/5.md, PR #9) ---
#
# `start` must remain day zero even when it is not itself a trading session,
# and positive `n` must count only subsequent XNYS sessions, for every kind
# of non-session start: weekend days, a fixed-rule holiday, a one-off
# exchange closure, and (for symmetry) an early-close session that *is*
# itself a valid trading session.


@pytest.mark.parametrize(
    ("label", "start", "first_session_after"),
    [
        ("saturday", date(2026, 7, 11), date(2026, 7, 13)),
        ("sunday", date(2026, 7, 12), date(2026, 7, 13)),
        ("exchange_holiday", date(2026, 1, 1), date(2026, 1, 2)),
        ("one_off_closure", date(2018, 12, 5), date(2018, 12, 6)),
    ],
)
def test_add_trading_days_one_from_non_session_start(label, start, first_session_after):
    assert add_trading_days(start, 1) == first_session_after


@pytest.mark.parametrize(
    ("label", "start", "first_session_after"),
    [
        ("saturday", date(2026, 7, 11), date(2026, 7, 13)),
        ("sunday", date(2026, 7, 12), date(2026, 7, 13)),
        ("exchange_holiday", date(2026, 1, 1), date(2026, 1, 2)),
        ("one_off_closure", date(2018, 12, 5), date(2018, 12, 6)),
    ],
)
def test_next_trading_session_from_non_session_start(label, start, first_session_after):
    # A non-session start behaves identically under inclusive=True and
    # inclusive=False, since `start` itself is never a candidate session.
    assert next_trading_session(start, inclusive=False) == first_session_after
    assert next_trading_session(start, inclusive=True) == first_session_after


def test_add_trading_days_one_from_early_close_session_start():
    # 2026-11-27 (day after Thanksgiving) is itself a valid, early-close
    # trading session — `start` is still treated as day zero, not counted.
    early_close_session = date(2026, 11, 27)
    assert add_trading_days(early_close_session, 1) == date(2026, 11, 30)


def test_next_trading_session_from_early_close_session_start():
    early_close_session = date(2026, 11, 27)
    assert next_trading_session(early_close_session, inclusive=False) == date(2026, 11, 30)
    assert next_trading_session(early_close_session, inclusive=True) == early_close_session


# --- regular_session_close range classification (follow-up correction to
# PR #10): classify using _CALENDAR_START/_CALENDAR_END directly, not
# cal.first_session/cal.last_session, since XNYS's own actual first/last
# session can fall a few days inside the configured range. ---


def test_regular_session_close_on_calendar_start_edge_is_non_session_not_out_of_range():
    # 1990-01-01 (`_CALENDAR_START` itself, a Monday) is New Year's Day, a
    # holiday — inside the configured range but not an XNYS session.
    # XNYS's own actual first session is 1990-01-02, one day later, so this
    # specifically exercises the gap between the configured range and the
    # library's own session-bound checking.
    with pytest.raises(MarketCalendarError, match="not a trading session"):
        regular_session_close(date(1990, 1, 1))


def test_regular_session_close_before_calendar_start_raises_out_of_range():
    with pytest.raises(MarketCalendarError, match="outside the supported"):
        regular_session_close(date(1989, 12, 31))


def test_regular_session_close_first_valid_1990_session_still_resolves():
    # January 2, 1990 is XNYS's actual first session at/after
    # `_CALENDAR_START` (January 1, 1990 was a holiday).
    close = regular_session_close(date(1990, 1, 2))
    assert close == datetime(1990, 1, 2, 16, 0, tzinfo=NY)


# --- _CALENDAR_END maintenance-margin guard ---
#
# `_calendar_end_margin_ok` is deterministic (it takes `as_of` as a plain
# argument rather than reading the clock itself), so its own correctness is
# tested here with injected dates, independent of when this suite runs.


def test_calendar_end_margin_ok_with_sufficient_headroom():
    # 2030-06-01 is comfortably more than five years before `_CALENDAR_END`
    # (2035-12-31).
    assert _calendar_end_margin_ok(date(2030, 6, 1)) is True


def test_calendar_end_margin_ok_false_with_insufficient_headroom():
    # 2032-01-01 is less than five years before `_CALENDAR_END`
    # (2035-12-31): 2032-01-01 + 5 years = 2037-01-01, past `_CALENDAR_END`.
    assert _calendar_end_margin_ok(date(2032, 1, 1)) is False


def test_calendar_end_margin_ok_false_once_calendar_end_itself_is_reached():
    assert _calendar_end_margin_ok(_CALENDAR_END) is False


def test_calendar_end_has_sufficient_margin_today():
    # The live maintenance guard: uses the real current date, not an
    # injected one, so this test starts failing for real once fewer than
    # five years remain before `_CALENDAR_END` — forcing a deliberate
    # extension of `_CALENDAR_END` (see the module docstring) rather than
    # letting the supported range silently approach its edge.
    assert _calendar_end_margin_ok(date.today())
