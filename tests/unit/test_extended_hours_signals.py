from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_research.strategies.extended_hours_signals import (
    INSUFFICIENT_PREMARKET_HISTORY,
    SignalValue,
    continuation,
    post_hold,
    post_report_move,
    premarket_gap,
    premarket_high,
    premarket_hold,
    premarket_low,
    premarket_rel_volume,
    premarket_vwap,
)

from ._intraday_test_helpers import (
    NY,
    bar,
    et,
    history_day,
    prior_sessions,
    series,
)

DAY = date(2026, 11, 13)
REPORT = date(2026, 11, 12)


def _pre(prices, volumes=None, start=(8, 0)):
    out = []
    cur = et(DAY, *start)
    for i, p in enumerate(prices):
        vol = volumes[i] if volumes else 1000
        out.append(bar(DAY, cur.hour, cur.minute + 0, p, vol))
        cur = cur.replace(minute=cur.minute + 5) if cur.minute < 55 else cur.replace(hour=cur.hour + 1, minute=0)
    return out


def test_premarket_gap_uses_last_bar_available_at_as_of_and_reports_available_at():
    bars = _pre([10.5, 10.7, 11.5])  # 08:00, 08:05, 08:10 starts
    gap = premarket_gap(bars, DAY, Decimal("10"), et(DAY, 8, 10))  # only the first two bars have closed
    assert gap.value == Decimal("7.0")
    assert gap.available_at == et(DAY, 8, 10).astimezone(timezone.utc)
    assert premarket_gap(bars, DAY, Decimal("10"), et(DAY, 8, 15)).value == Decimal("15.0")


def test_bar_that_has_not_closed_at_as_of_is_never_read():
    bars = _pre([10.5])
    assert premarket_gap(bars, DAY, Decimal("10"), et(DAY, 8, 4)).value is None
    assert premarket_high(bars, DAY, et(DAY, 8, 4)).value is None
    assert premarket_hold(bars, DAY, et(DAY, 8, 4)).value is None


def test_zero_volume_bars_do_not_set_price_levels_or_vwap():
    bars = _pre([10.5, 12.0], volumes=[1000, 0])
    as_of = et(DAY, 8, 10)
    assert premarket_gap(bars, DAY, Decimal("10"), as_of).value == Decimal("5.0")
    assert premarket_high(bars, DAY, as_of).value == Decimal("10.6")
    assert premarket_low(bars, DAY, as_of).value == Decimal("10.4")


def test_levels_and_vwap_over_usable_pre_market_bars():
    bars = _pre([10.0, 11.0], volumes=[1000, 3000])
    as_of = et(DAY, 8, 10)
    assert premarket_high(bars, DAY, as_of).value == Decimal("11.1")
    assert premarket_low(bars, DAY, as_of).value == Decimal("9.9")
    # typical price == close for these symmetric bars
    assert premarket_vwap(bars, DAY, as_of).value == Decimal("10.75")
    assert premarket_hold(bars, DAY, as_of).value is True  # last 11.0 > 10.75
    flat = _pre([10.0, 10.0])
    assert premarket_hold(flat, DAY, as_of).value is False  # strict: equal to VWAP is not a hold


def test_premarket_signals_only_read_the_named_session_date():
    other = [bar(date(2026, 11, 12), 8, 0, 50, 1000)]
    bars = _pre([10.5]) + other
    assert premarket_high(bars, DAY, et(DAY, 9, 0)).value == Decimal("10.6")


def test_not_before_excludes_bars_that_started_before_the_release():
    bars = _pre([10.5, 10.7, 10.9])  # starts 08:00, 08:05, 08:10
    release = et(DAY, 8, 5)
    gap = premarket_gap(bars, DAY, Decimal("10"), et(DAY, 8, 15), not_before=release)
    assert gap.value == Decimal("9.0")
    assert premarket_low(bars, DAY, et(DAY, 8, 15), not_before=release).value == Decimal("10.6")
    # nothing started after the release by 08:05 -> undefined
    assert premarket_gap(bars, DAY, Decimal("10"), et(DAY, 8, 5), not_before=release).value is None


def test_post_report_move_excludes_closing_print_and_pre_release_bars():
    closing = bar(REPORT, 16, 0, 10, 165_000, closing_print=True)
    early = bar(REPORT, 16, 0, 20, 100)  # a post bar before the release; not the closing print
    later = bar(REPORT, 16, 5, 10.6, 100)
    as_of = et(REPORT, 16, 30)
    release = et(REPORT, 16, 5)
    move = post_report_move([closing, early, later], REPORT, Decimal("10"), as_of, not_before=release)
    assert move.value == Decimal("6.0")
    assert move.available_at == et(REPORT, 16, 10).astimezone(timezone.utc)
    # only the closing print and the pre-release bar -> undefined, never the closing print
    assert post_report_move([closing, early], REPORT, Decimal("10"), as_of, not_before=release).value is None
    # without the release bound the pre-release bar WOULD have been used
    assert post_report_move([closing, early], REPORT, Decimal("10"), as_of).value == Decimal("100.0")


def test_post_report_move_ignores_bars_after_as_of():
    bars = [bar(REPORT, 16, 5, 10.6, 100), bar(REPORT, 16, 30, 13, 100)]
    move = post_report_move(bars, REPORT, Decimal("10"), et(REPORT, 16, 30))
    assert move.value == Decimal("6.0")


def test_post_hold_compares_last_price_to_post_market_vwap():
    bars = [bar(REPORT, 16, 5, 10.0, 1000), bar(REPORT, 16, 10, 11.0, 1000)]
    assert post_hold(bars, REPORT, et(REPORT, 20, 0)).value is True
    falling = [bar(REPORT, 16, 5, 11.0, 1000), bar(REPORT, 16, 10, 10.0, 1000)]
    assert post_hold(falling, REPORT, et(REPORT, 20, 0)).value is False
    # the later, losing bar has not closed at 16:10: only the first bar is used
    early = post_hold(falling, REPORT, et(REPORT, 16, 10))
    assert early.available_at == et(REPORT, 16, 10).astimezone(timezone.utc)
    assert post_hold(falling, REPORT, et(REPORT, 16, 5)).value is None  # nothing closed yet


def test_continuation_is_gap_minus_post_move_with_latest_available_at():
    gap = SignalValue(Decimal("9"), et(DAY, 9, 25).astimezone(timezone.utc))
    move = SignalValue(Decimal("6"), et(REPORT, 16, 30).astimezone(timezone.utc))
    cont = continuation(gap, move)
    assert cont.value == Decimal("3")
    assert cont.available_at == gap.available_at
    assert continuation(gap, SignalValue(None, None)).value is None


def _history(days, per_day_pre_volume=100):
    return [(d, history_day(d, pre_volume=per_day_pre_volume)) for d in days]


def test_rel_volume_same_time_cumulative_against_prior_20_sessions():
    days = prior_sessions(DAY, 20)
    history = _history(days)  # 04:00-09:25 flat 100/bar
    today = series(DAY, (4, 0), (8, 0), 10.5, 1000)  # 48 bars
    as_of = et(DAY, 8, 0)
    sig = premarket_rel_volume(today, DAY, history, as_of)
    # today 48 * 1000 = 48000; baseline same window 48 * 100 = 4800
    assert sig.value == Decimal(10)
    assert sig.available_at == as_of.astimezone(timezone.utc)


def test_rel_volume_fewer_than_20_sessions_is_insufficient_history():
    days = prior_sessions(DAY, 19)
    sig = premarket_rel_volume(series(DAY, (4, 0), (9, 25), 10.5, 1000), DAY, _history(days), et(DAY, 9, 25))
    assert sig.value is None and sig.note == INSUFFICIENT_PREMARKET_HISTORY


def test_rel_volume_session_without_premarket_volume_does_not_count_as_history():
    days = prior_sessions(DAY, 20)
    history = _history(days)
    history[3] = (days[3], history_day(days[3], pre_volume=0))
    sig = premarket_rel_volume(series(DAY, (4, 0), (9, 25), 10.5, 1000), DAY, history, et(DAY, 9, 25))
    assert sig.note == INSUFFICIENT_PREMARKET_HISTORY


def test_rel_volume_rejects_history_on_or_after_the_signal_session():
    with pytest.raises(ValueError):
        premarket_rel_volume([], DAY, [(DAY, [])], et(DAY, 9, 25))


def test_rel_volume_never_counts_bars_after_as_of():
    days = prior_sessions(DAY, 20)
    base = series(DAY, (4, 0), (8, 0), 10.5, 1000)
    extra = series(DAY, (8, 0), (9, 25), 10.5, 999_999)
    a = premarket_rel_volume(base, DAY, _history(days), et(DAY, 8, 0))
    b = premarket_rel_volume(base + extra, DAY, _history(days), et(DAY, 8, 0))
    assert a == b


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        premarket_high([], DAY, datetime(2026, 11, 13, 9, 25))
    assert NY  # tz helper import used
