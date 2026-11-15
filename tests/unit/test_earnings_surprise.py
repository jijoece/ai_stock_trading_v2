from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.strategies.earnings_surprise import (
    EarningsSurpriseError,
    load_earnings_csv,
    reaction_window,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "earnings_surprises_sample.csv"
HEADER = "symbol,fiscal_period,report_date,timing,release_time,eps_estimate,eps_actual,source,captured_at\n"
GOOD = "AAA,2026Q3,2026-11-10,am,,1.0,1.1,fx,2026-11-10T10:00:00+00:00\n"


def _write(tmp_path, body, header=HEADER):
    p = tmp_path / "e.csv"
    p.write_text(header + body)
    return p


def test_fixture_loads_sorted_and_typed():
    rows = load_earnings_csv(FIXTURE)
    assert [r.symbol for r in rows] == ["ABCD", "NMAX"]
    nmax = rows[1]
    assert nmax.timing == "pm" and nmax.release_time == time(16, 5)
    assert nmax.eps_actual == Decimal("0.15") and nmax.beat
    assert not rows[0].beat
    assert nmax.captured_at.utcoffset().total_seconds() == 0


def test_valid_row_blank_release_time(tmp_path):
    (row,) = load_earnings_csv(_write(tmp_path, GOOD))
    assert row.release_time is None


@pytest.mark.parametrize(
    "body",
    [
        "AAA,2026Q3,2026-11-10,eve,,1.0,1.1,fx,2026-11-10T10:00:00+00:00\n",  # timing
        "AAA,2026Q3,2026-11-10,am,,,1.1,fx,2026-11-10T10:00:00+00:00\n",  # blank est
        "AAA,2026Q3,2026-11-10,am,,1.0,,fx,2026-11-10T10:00:00+00:00\n",  # blank act
        "AAA,2026Q3,2026-11-10,am,,1.0,NaN,fx,2026-11-10T10:00:00+00:00\n",
        "AAA,2026Q3,2026-11-10,am,,1.0,abc,fx,2026-11-10T10:00:00+00:00\n",
        "AAA,2026Q3,2026-13-40,am,,1.0,1.1,fx,2026-11-10T10:00:00+00:00\n",
        "AAA,2026Q3,2026-11-10,am,25:99,1.0,1.1,fx,2026-11-10T10:00:00+00:00\n",
        "AAA,2026Q3,2026-11-10,am,,1.0,1.1,fx,2026-11-10T10:00:00\n",  # naive
        "AAA,2026Q3,2026-11-10,am,,1.0,1.1,fx,notadate\n",
        "AAA,2026Q3,2026-11-10,am,,1.0,1.1,,2026-11-10T10:00:00+00:00\n",  # source
        GOOD + GOOD,  # duplicate
    ],
)
def test_loader_errors(tmp_path, body):
    with pytest.raises(EarningsSurpriseError):
        load_earnings_csv(_write(tmp_path, body))


def test_missing_and_extra_columns(tmp_path):
    with pytest.raises(EarningsSurpriseError, match="missing"):
        load_earnings_csv(_write(tmp_path, "", header="symbol,timing\n"))
    with pytest.raises(EarningsSurpriseError, match="unexpected"):
        load_earnings_csv(_write(tmp_path, "", header=HEADER.strip() + ",extra\n"))


def test_duplicate_csv_columns_rejected(tmp_path):
    header = HEADER.strip() + ",eps_estimate\n"
    body = "AAA,2026Q3,2026-11-10,am,,1.0,1.1,fx,2026-11-10T10:00:00+00:00,2.0\n"
    with pytest.raises(EarningsSurpriseError, match="duplicate CSV columns"):
        load_earnings_csv(_write(tmp_path, body, header=header))


def test_missing_file(tmp_path):
    with pytest.raises(EarningsSurpriseError):
        load_earnings_csv(tmp_path / "nope.csv")


def test_am_window_default_release_is_open():
    w = reaction_window(date(2026, 11, 10), "am", None)
    assert w.reaction_date == date(2026, 11, 10)
    assert w.post_market is None and w.pre_market is None  # D10: unverified am
    assert w.regular.start.time() == time(9, 30)
    assert not w.release_time_supplied
    assert w.release_time_unverified


def test_am_window_with_release_time():
    w = reaction_window(date(2026, 11, 10), "am", time(7, 0))
    assert w.pre_market.start.time() == time(7, 0)
    assert w.pre_market.end.time() == time(9, 30)
    assert w.regular.end.time() == time(16, 0)
    assert w.assumed_release.utcoffset() is not None


def test_nmax_pm_post_then_next_day():
    w = reaction_window(date(2026, 11, 12), "pm", time(16, 5))
    assert w.post_market.session_date == date(2026, 11, 12)
    assert w.post_market.start.time() == time(16, 5)
    assert w.post_market.end.time() == time(20, 0)
    assert w.reaction_date == date(2026, 11, 13)
    assert w.pre_market.start.time() == time(4, 0)
    assert w.regular.session_date == date(2026, 11, 13)


def test_pm_default_release_excludes_post_market():
    w = reaction_window(date(2026, 11, 12), "pm", None)
    assert w.post_market is None
    assert w.assumed_release.time() == time(20, 0)
    assert w.pre_market.start.time() == time(4, 0)


def test_pm_release_later_than_normal_clips_post_market():
    w = reaction_window(date(2026, 11, 12), "pm", time(18, 30))
    assert w.post_market.start.time() == time(18, 30)
    late = reaction_window(date(2026, 11, 12), "pm", time(20, 30))
    assert late.post_market is None


def test_pm_release_before_close_starts_at_close():
    w = reaction_window(date(2026, 11, 12), "pm", time(15, 0))
    assert w.post_market.start.time() == time(16, 0)


def test_friday_pm_goes_to_monday():
    w = reaction_window(date(2026, 11, 13), "pm", time(16, 10))
    assert w.post_market.session_date == date(2026, 11, 13)
    assert w.reaction_date == date(2026, 11, 16)


def test_holiday_skipped_and_early_close():
    w = reaction_window(date(2026, 11, 25), "pm", time(16, 10))
    assert w.reaction_date == date(2026, 11, 27)  # Thanksgiving skipped
    assert w.regular.end.time() == time(13, 0)  # early close


def test_early_close_post_market_end():
    w = reaction_window(date(2026, 11, 27), "pm", time(13, 5))
    assert w.post_market.end.time() == time(17, 0)
    assert w.reaction_date == date(2026, 11, 30)
    w2 = reaction_window(
        date(2026, 11, 27), "pm", time(13, 5), early_close_postmarket_end=time(16, 0)
    )
    assert w2.post_market.end.time() == time(16, 0)
    assert reaction_window(date(2026, 11, 27), "pm", time(17, 30)).post_market is None


def test_non_session_and_bad_timing():
    with pytest.raises(EarningsSurpriseError):
        reaction_window(date(2026, 11, 26), "am", None)  # holiday
    with pytest.raises(EarningsSurpriseError):
        reaction_window(date(2026, 11, 14), "pm", None)  # Saturday
    with pytest.raises(EarningsSurpriseError):
        reaction_window(date(2026, 11, 12), "x", None)
