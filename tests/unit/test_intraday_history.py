import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_research.backtesting.intraday_history import (
    CachedAlpacaIntradayProvider,
    FixtureIntradayHistoryProvider,
    cache_key,
    classify_session,
    intraday_data_hash,
    missing_regular_slots,
    reconcile_daily_volume,
)
from trading_research.backtesting.models import BacktestError, IntradayBar

NY = ZoneInfo("America/New_York")
NORMAL = date(2026, 3, 11)
EARLY = date(2025, 11, 28)


def raw(day, hour, minute, volume=100, price="10"):
    t = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(timezone.utc)
    p = Decimal(price)
    return {
        "t": t.isoformat().replace("+00:00", "Z"), "o": str(p), "h": str(p + 1),
        "l": str(p - 1), "c": str(p), "v": volume,
    }


class FakeHttp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get_json(self, url, *, params, operation, symbol):
        self.calls.append((url, dict(params), operation, symbol))
        return self.pages.pop(0), None


class ExplodingHttp:
    def get_json(self, *a, **k):
        raise AssertionError("network must not be used")


def fixture_payload(day):
    return {"bars": [
        raw(day, 4, 0), raw(day, 9, 25), raw(day, 9, 30), raw(day, 15, 55),
        raw(day, 16, 0, volume=5000), raw(day, 16, 5), raw(day, 19, 55),
        raw(day, 20, 0),  # outside window: dropped
    ]}


# -- session tagging ----------------------------------------------------------


def test_session_tagging_normal_day(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([fixture_payload(NORMAL)]), "abc", NORMAL)
    bars = p.get_bars("ABC", NORMAL)
    assert [(b.start.astimezone(NY).strftime("%H:%M"), b.session) for b in bars] == [
        ("04:00", "pre"), ("09:25", "pre"), ("09:30", "regular"), ("15:55", "regular"),
        ("16:00", "post"), ("16:05", "post"), ("19:55", "post"),
    ]
    assert all(b.available_at == b.end for b in bars)


def test_session_tagging_early_close_day():
    assert classify_session(datetime(2025, 11, 28, 12, 55, tzinfo=NY), EARLY) == ("regular", False)
    assert classify_session(datetime(2025, 11, 28, 13, 0, tzinfo=NY), EARLY) == ("post", True)
    assert classify_session(datetime(2025, 11, 28, 16, 55, tzinfo=NY), EARLY) == ("post", False)
    assert classify_session(datetime(2025, 11, 28, 17, 0, tzinfo=NY), EARLY) is None
    # configurable early-close post-market end
    assert classify_session(datetime(2025, 11, 28, 17, 0, tzinfo=NY), EARLY, time(18, 0)) == ("post", False)


def test_classify_rejects_naive_and_out_of_window():
    with pytest.raises(BacktestError):
        classify_session(datetime(2026, 3, 11, 10, 0), NORMAL)
    assert classify_session(datetime(2026, 3, 11, 3, 55, tzinfo=NY), NORMAL) is None
    assert classify_session(datetime(2026, 3, 11, 20, 0, tzinfo=NY), NORMAL) is None
    assert classify_session(datetime(2026, 3, 12, 10, 0, tzinfo=NY), NORMAL) is None


def test_naive_timestamp_in_cache_rejected(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    with pytest.raises(BacktestError, match="naive"):
        p.fetch(FakeHttp([{"bars": [{"t": "2026-03-11T14:30:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}]),
                "ABC", NORMAL)


def test_early_close_day_provider_tags_post(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    payload = {"bars": [raw(EARLY, 12, 55), raw(EARLY, 13, 0, volume=900), raw(EARLY, 13, 5), raw(EARLY, 17, 0)]}
    p.fetch(FakeHttp([payload]), "ABC", EARLY)
    bars = p.get_bars("ABC", EARLY)
    assert [(b.session, b.is_closing_print) for b in bars] == [
        ("regular", False), ("post", True), ("post", False)]


# -- closing print ------------------------------------------------------------


def test_closing_print_flag_only_first_post_bar(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    flagged = [b for b in p.get_bars("ABC", NORMAL) if b.is_closing_print]
    assert len(flagged) == 1
    assert flagged[0].start.astimezone(NY).time() == time(16, 0) and flagged[0].volume == 5000


# -- missing / zero volume ----------------------------------------------------


def test_zero_volume_bar_kept_and_missing_slots_reported(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([{"bars": [raw(NORMAL, 9, 30, volume=0), raw(NORMAL, 9, 40)]}]), "ABC", NORMAL)
    bars = p.get_bars("ABC", NORMAL)
    assert bars[0].volume == 0 and len(bars) == 2
    missing = missing_regular_slots(bars, NORMAL)
    assert datetime(2026, 3, 11, 9, 35, tzinfo=NY) in missing
    assert len(missing) == 78 - 2  # 78 regular 5-minute slots


def test_empty_day_cached_returns_empty_but_uncached_raises(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([{"bars": None}]), "ABC", NORMAL)
    assert p.get_bars("ABC", NORMAL) == ()
    with pytest.raises(BacktestError, match="no cached intraday data"):
        p.get_bars("XYZ", NORMAL)


def test_malformed_response_rejected(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    with pytest.raises(BacktestError):
        p.fetch(FakeHttp([{"nope": 1}]), "ABC", NORMAL)
    bad = {"bars": [{"t": "2026-03-11T14:30:00Z", "o": "x", "h": 1, "l": 1, "c": 1, "v": 1}]}
    with pytest.raises(BacktestError, match="malformed"):
        p.fetch(FakeHttp([bad]), "ABC", NORMAL)


def test_duplicate_bar_rejected(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    with pytest.raises(BacktestError, match="duplicate"):
        p.fetch(FakeHttp([{"bars": [raw(NORMAL, 9, 30), raw(NORMAL, 9, 30)]}]), "ABC", NORMAL)


# -- cache --------------------------------------------------------------------


def test_cache_key_components():
    base = cache_key("abc", NORMAL, "5Min", "raw", "pre+regular+post")
    assert base == "ABC__2026-03-11__5Min__raw__pre+regular+post"
    variants = {
        cache_key("abd", NORMAL, "5Min", "raw", "pre+regular+post"),
        cache_key("abc", EARLY, "5Min", "raw", "pre+regular+post"),
        cache_key("abc", NORMAL, "1Min", "raw", "pre+regular+post"),
        cache_key("abc", NORMAL, "5Min", "split", "pre+regular+post"),
        cache_key("abc", NORMAL, "5Min", "raw", "regular"),
    }
    assert base not in variants and len(variants) == 5


def test_adjustment_changes_cache_path(tmp_path):
    raw_p = CachedAlpacaIntradayProvider(tmp_path, adjustment="raw")
    split_p = CachedAlpacaIntradayProvider(tmp_path, adjustment="split")
    assert raw_p.cache_path("ABC", NORMAL) != split_p.cache_path("ABC", NORMAL)
    with pytest.raises(BacktestError):
        CachedAlpacaIntradayProvider(tmp_path, adjustment="bogus")


def test_fetch_request_params_and_pagination(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path, feed="sip")
    http = FakeHttp([
        {"bars": [raw(NORMAL, 9, 30)], "next_page_token": "tok"},
        {"bars": [raw(NORMAL, 9, 35)], "next_page_token": None},
    ])
    p.fetch(http, "abc", NORMAL)
    assert len(http.calls) == 2
    url, params, op, symbol = http.calls[0]
    assert url.endswith("/ABC/bars") and symbol == "ABC"
    assert params["timeframe"] == "5Min" and params["feed"] == "sip"
    assert params["start"] == "2026-03-11T08:00:00Z" and params["end"] == "2026-03-12T00:00:00Z"
    assert http.calls[1][1]["page_token"] == "tok"
    assert len(p.get_bars("ABC", NORMAL)) == 2


def test_cache_determinism_same_input_same_hash(tmp_path):
    a = CachedAlpacaIntradayProvider(tmp_path / "a")
    b = CachedAlpacaIntradayProvider(tmp_path / "b")
    a.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    reordered = {"bars": list(reversed(fixture_payload(NORMAL)["bars"]))}
    b.fetch(FakeHttp([reordered]), "ABC", NORMAL)
    assert intraday_data_hash(a.get_bars("ABC", NORMAL)) == intraday_data_hash(b.get_bars("ABC", NORMAL))
    assert a.cache_path("ABC", NORMAL).read_text() == b.cache_path("ABC", NORMAL).read_text()


def test_hash_changes_with_data(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([{"bars": [raw(NORMAL, 9, 30)]}]), "ABC", NORMAL)
    h1 = intraday_data_hash(p.get_bars("ABC", NORMAL))
    p.fetch(FakeHttp([{"bars": [raw(NORMAL, 9, 30, volume=101)]}]), "ABC", NORMAL)
    assert intraday_data_hash(p.get_bars("ABC", NORMAL)) != h1


def test_cache_read_makes_no_network_calls(tmp_path, monkeypatch):
    p = CachedAlpacaIntradayProvider(tmp_path)
    p.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    import socket

    def boom(*a, **k):
        raise AssertionError("network used on cache read")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    assert len(CachedAlpacaIntradayProvider(tmp_path).get_bars("ABC", NORMAL)) == 7


def test_tampered_cache_detected(tmp_path):
    p = CachedAlpacaIntradayProvider(tmp_path)
    path = p.fetch(FakeHttp([{"bars": [raw(NORMAL, 9, 30)]}]), "ABC", NORMAL)
    doc = json.loads(path.read_text())
    doc["bars"][0]["v"] = 999
    path.write_text(json.dumps(doc))
    with pytest.raises(BacktestError, match="hash mismatch"):
        p.get_bars("ABC", NORMAL)


def test_fetch_non_session_rejected(tmp_path):
    with pytest.raises(BacktestError, match="not a trading session"):
        CachedAlpacaIntradayProvider(tmp_path).fetch(ExplodingHttp(), "ABC", date(2026, 3, 14))


# -- fixture provider ---------------------------------------------------------


def make_bar(day, hour, minute, session, volume=100):
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)
    end = start + timedelta(minutes=5)
    return IntradayBar("ABC", start, end, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"),
                       volume, session, end)


def test_fixture_provider():
    bars = (make_bar(NORMAL, 9, 35, "regular"), make_bar(NORMAL, 9, 30, "regular"))
    prov = FixtureIntradayHistoryProvider({("ABC", NORMAL): bars})
    got = prov.get_bars("abc", NORMAL)
    assert [b.start.minute for b in got] == [30, 35]
    assert prov.get_bars("ABC", EARLY) == ()


def test_fixture_provider_rejects_wrong_day_and_duplicates():
    with pytest.raises(BacktestError):
        FixtureIntradayHistoryProvider({("ABC", EARLY): (make_bar(NORMAL, 9, 30, "regular"),)}).get_bars("ABC", EARLY)
    dup = (make_bar(NORMAL, 9, 30, "regular"),) * 2
    with pytest.raises(BacktestError, match="duplicate"):
        FixtureIntradayHistoryProvider({("ABC", NORMAL): dup}).get_bars("ABC", NORMAL)


# -- reconciliation -----------------------------------------------------------


def test_reconcile_within_tolerance_and_flagged():
    bars = (make_bar(NORMAL, 9, 30, "regular", 600), make_bar(NORMAL, 9, 35, "regular", 400),
            make_bar(NORMAL, 16, 0, "post", 5000), make_bar(NORMAL, 8, 0, "pre", 7000))
    ok = reconcile_daily_volume(bars, 1020, Decimal("0.05"))
    assert ok.regular_volume == 1000 and not ok.flagged
    bad = reconcile_daily_volume(bars, 2000, Decimal("0.05"))
    assert bad.flagged and bad.difference == -1000 and bad.relative_difference == Decimal("0.5")
    assert bars[0].volume == 600  # never fixed


def test_reconcile_zero_daily_volume_and_negative_tolerance():
    bars = (make_bar(NORMAL, 9, 30, "regular", 10),)
    assert reconcile_daily_volume(bars, 0, Decimal("0.1")).flagged
    assert not reconcile_daily_volume((), 0, Decimal("0.1")).flagged
    with pytest.raises(BacktestError):
        reconcile_daily_volume(bars, 10, Decimal("-1"))


# -- feed isolation / recording ------------------------------------------------


def test_feed_changes_cache_key_and_path(tmp_path):
    assert cache_key("abc", NORMAL, "5Min", "raw", "x", "iex") != cache_key("abc", NORMAL, "5Min", "raw", "x", "sip")
    iex = CachedAlpacaIntradayProvider(tmp_path, feed="iex")
    sip = CachedAlpacaIntradayProvider(tmp_path, feed="sip")
    assert iex.cache_path("ABC", NORMAL) != sip.cache_path("ABC", NORMAL)


def test_sip_provider_does_not_read_iex_cache(tmp_path):
    CachedAlpacaIntradayProvider(tmp_path, feed="iex").fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    sip = CachedAlpacaIntradayProvider(tmp_path, feed="sip")
    with pytest.raises(BacktestError, match="no cached intraday data"):
        sip.get_bars("ABC", NORMAL)


def test_feed_mismatch_in_cache_document_fails_closed(tmp_path):
    iex = CachedAlpacaIntradayProvider(tmp_path, feed="iex")
    path = iex.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    sip = CachedAlpacaIntradayProvider(tmp_path, feed="sip")
    sip_path = sip.cache_path("ABC", NORMAL)
    sip_path.parent.mkdir(parents=True, exist_ok=True)
    sip_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")  # IEX document at the SIP path
    with pytest.raises(BacktestError, match="feed mismatch"):
        sip.get_bars("ABC", NORMAL)


def test_early_close_end_is_part_of_cache_identity(tmp_path):
    a = CachedAlpacaIntradayProvider(tmp_path, early_close_postmarket_end=time(17, 0))
    b = CachedAlpacaIntradayProvider(tmp_path, early_close_postmarket_end=time(18, 0))
    assert a.cache_path("ABC", NORMAL) != b.cache_path("ABC", NORMAL)
    a.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    with pytest.raises(BacktestError, match="no cached intraday data"):
        b.get_bars("ABC", NORMAL)  # never silently reuses coverage fetched for another end time


@pytest.mark.parametrize("text", ["[]", "null", "3", '"x"'])
def test_non_object_cache_document_is_a_backtest_error(tmp_path, text):
    prov = CachedAlpacaIntradayProvider(tmp_path)
    path = prov.cache_path("ABC", NORMAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BacktestError, match="not a JSON object"):
        prov.get_bars("ABC", NORMAL)


def test_recording_provider_returns_the_first_snapshot_it_recorded(tmp_path):
    from trading_research.backtesting.intraday_history import RecordingIntradayProvider

    prov = CachedAlpacaIntradayProvider(tmp_path)
    path = prov.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    rec = RecordingIntradayProvider(prov)
    first = rec.get_bars("ABC", NORMAL)
    manifest = rec.manifest_hash()
    path.write_text("[]", encoding="utf-8")  # the cache file changes mid-run
    assert rec.get_bars("ABC", NORMAL) == first
    assert rec.manifest_hash() == manifest


def test_recording_provider_manifest_tracks_every_read(tmp_path):
    from trading_research.backtesting.intraday_history import RecordingIntradayProvider

    prov = CachedAlpacaIntradayProvider(tmp_path)
    prov.fetch(FakeHttp([fixture_payload(NORMAL)]), "ABC", NORMAL)
    rec = RecordingIntradayProvider(prov)
    empty = rec.manifest_hash()
    assert rec.get_bars("abc", NORMAL)
    one = rec.manifest_hash()
    assert one != empty and one == rec.manifest_hash()
    assert rec.manifest_hash({"eps": "1.0"}) != one
