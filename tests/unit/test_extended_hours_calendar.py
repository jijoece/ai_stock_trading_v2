from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_research.evaluation.market_calendar import (
    MarketCalendarError,
    postmarket_close,
    premarket_open,
    regular_session_close,
    regular_session_open,
)

NY = ZoneInfo("America/New_York")
NORMAL = date(2026, 3, 11)
EARLY = date(2025, 11, 28)  # day after Thanksgiving, 13:00 ET close
HOLIDAY = date(2025, 12, 25)
WEEKEND = date(2026, 3, 14)


def test_normal_day_boundaries():
    assert premarket_open(NORMAL).timetz().replace(tzinfo=None) == time(4, 0)
    assert regular_session_open(NORMAL).hour == 9 and regular_session_open(NORMAL).minute == 30
    assert regular_session_close(NORMAL).hour == 16
    assert postmarket_close(NORMAL).hour == 20
    assert premarket_open(NORMAL).tzinfo is not None
    assert premarket_open(NORMAL).utcoffset() == timedelta(hours=-4)


def test_early_close_day_default_postmarket_end():
    assert regular_session_close(EARLY).hour == 13
    assert postmarket_close(EARLY).hour == 17
    assert postmarket_close(EARLY).minute == 0


def test_early_close_postmarket_end_configurable():
    assert postmarket_close(EARLY, time(16, 0)).hour == 16
    assert postmarket_close(NORMAL, time(16, 0)).hour == 20  # only affects early-close days


def test_early_close_postmarket_end_before_close_rejected():
    with pytest.raises(MarketCalendarError):
        postmarket_close(EARLY, time(12, 0))


@pytest.mark.parametrize("fn", [premarket_open, regular_session_open, postmarket_close])
@pytest.mark.parametrize("day", [HOLIDAY, WEEKEND])
def test_non_sessions_raise(fn, day):
    with pytest.raises(MarketCalendarError):
        fn(day)


def test_out_of_range_raises():
    with pytest.raises(MarketCalendarError):
        regular_session_open(date(1980, 1, 2))
