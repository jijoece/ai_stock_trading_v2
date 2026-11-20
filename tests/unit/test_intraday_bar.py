from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_research.backtesting.models import BacktestError, IntradayBar

NY = ZoneInfo("America/New_York")


def make_bar(hour=9, minute=30, session="regular", **overrides):
    start = datetime(2026, 3, 11, hour, minute, tzinfo=NY)
    end = start + timedelta(minutes=5)
    fields = dict(
        symbol="ABC", start=start, end=end, open=Decimal("10"), high=Decimal("11"),
        low=Decimal("9"), close=Decimal("10.5"), volume=100, session=session, available_at=end,
    )
    fields.update(overrides)
    return IntradayBar(**fields)


def test_valid_bar_and_defaults():
    bar = make_bar()
    assert bar.source_id == "fixture" and bar.is_closing_print is False


def test_utc_start_accepted():
    start = datetime(2026, 3, 11, 13, 30, tzinfo=timezone.utc)  # 09:30 EDT
    end = start + timedelta(minutes=5)
    bar = make_bar(start=start, end=end, available_at=end)
    assert bar.session == "regular"


@pytest.mark.parametrize("field", ["start", "end", "available_at"])
def test_naive_datetime_rejected(field):
    bar = make_bar()
    naive = getattr(bar, field).replace(tzinfo=None)
    with pytest.raises(BacktestError, match="timezone-aware"):
        replace(bar, **{field: naive})


def test_available_at_must_equal_end():
    bar = make_bar()
    with pytest.raises(BacktestError, match="available_at"):
        replace(bar, available_at=bar.end + timedelta(minutes=1))


def test_end_must_follow_start():
    bar = make_bar()
    with pytest.raises(BacktestError, match="end"):
        replace(bar, end=bar.start, available_at=bar.start)


@pytest.mark.parametrize("hour,minute", [(3, 55), (20, 0), (21, 0)])
def test_out_of_window_start_rejected(hour, minute):
    with pytest.raises(BacktestError, match="04:00-20:00"):
        make_bar(hour, minute, session="pre")


@pytest.mark.parametrize(
    "hour,minute,session",
    [(9, 30, "pre"), (9, 25, "regular"), (16, 0, "regular"), (10, 0, "post"), (12, 55, "post")],
)
def test_session_inconsistent_with_start_rejected(hour, minute, session):
    with pytest.raises(BacktestError):
        make_bar(hour, minute, session=session)


def test_unknown_session_rejected():
    with pytest.raises(BacktestError, match="session"):
        make_bar(session="overnight")


@pytest.mark.parametrize(
    "overrides",
    [
        {"low": Decimal("0")},
        {"high": Decimal("8")},
        {"open": Decimal("12")},
        {"close": Decimal("8")},
        {"volume": -1},
        {"high": Decimal("Infinity")},
        {"low": Decimal("NaN")},
    ],
)
def test_invalid_ohlc_or_volume_rejected(overrides):
    with pytest.raises(BacktestError):
        make_bar(**overrides)


def test_zero_volume_bar_allowed():
    assert make_bar(volume=0).volume == 0


def test_closing_print_only_on_post_bar():
    assert make_bar(16, 0, session="post", is_closing_print=True).is_closing_print
    with pytest.raises(BacktestError, match="closing print"):
        make_bar(10, 0, is_closing_print=True)


def test_post_bar_on_early_close_day_allowed():
    assert make_bar(13, 0, session="post").session == "post"
