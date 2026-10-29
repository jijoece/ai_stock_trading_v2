"""Step 1 of docs/historical-backtesting-plan.md: historical bar provider and
data snapshot. No network: a stub client stands in for AlpacaMarketDataClient."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_research.backtesting.alpaca_history import (
    DEFAULT_WARMUP_SESSIONS,
    AlpacaHistoricalDataProvider,
    alpaca_client_from_environment,
    fetch_snapshot,
    load_or_fetch_snapshot,
    price_bar_to_historical,
    read_snapshot,
    warmup_start,
    write_snapshot,
)
from trading_research.backtesting.models import BacktestError
from trading_research.evidence_providers.models import PriceBar

NY = ZoneInfo("America/New_York")
NORMAL_DAY = date(2025, 11, 26)      # Wednesday, 16:00 ET close
EARLY_CLOSE_DAY = date(2025, 11, 28)  # Friday after Thanksgiving, 13:00 ET close
HOLIDAY = date(2025, 11, 27)          # Thanksgiving
AS_OF = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _price_bar(symbol: str, day: date, close: str = "100", *, adjusted: bool = False) -> PriceBar:
    c = Decimal(close)
    return PriceBar(
        symbol=symbol, session_date=day, open=c, high=c + 1, low=c - 1, close=c,
        volume=1000, adjusted=adjusted, provider="alpaca-data",
    )


class StubClient:
    """Mirrors AlpacaMarketDataClient.get_price_history; records every call."""

    def __init__(self, bars: dict[str, list[PriceBar]]):
        self._bars = bars
        self.calls: list[dict] = []

    def get_price_history(self, symbol, *, start, end, as_of, adjustment="raw"):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "adjustment": adjustment})
        return tuple(b for b in self._bars.get(symbol, []) if start <= b.session_date <= end)


def _client() -> StubClient:
    return StubClient({
        "AAPL": [_price_bar("AAPL", NORMAL_DAY, "100"), _price_bar("AAPL", EARLY_CLOSE_DAY, "101")],
        "SPY": [_price_bar("SPY", NORMAL_DAY, "500"), _price_bar("SPY", EARLY_CLOSE_DAY, "501")],
    })


def _snapshot(adjustment: str = "raw"):
    client = StubClient({
        s: [_price_bar(s, d, c, adjusted=adjustment != "raw") for d, c in rows]
        for s, rows in {
            "AAPL": [(NORMAL_DAY, "100"), (EARLY_CLOSE_DAY, "101")],
            "SPY": [(NORMAL_DAY, "500"), (EARLY_CLOSE_DAY, "501")],
        }.items()
    })
    return fetch_snapshot(
        client, symbols=("SPY", "AAPL"), start=date(2025, 11, 26), end=date(2025, 11, 28),
        adjustment=adjustment, as_of=AS_OF, warmup_sessions=0,
    )


# -- bar mapping -------------------------------------------------------------


def test_normal_day_maps_fields_and_regular_close():
    bar = price_bar_to_historical(_price_bar("AAPL", NORMAL_DAY, "100"), adjustment="raw")
    assert (bar.symbol, bar.session_date, bar.close, bar.volume) == ("AAPL", NORMAL_DAY, Decimal("100"), 1000)
    assert bar.available_at == datetime(2025, 11, 26, 16, 0, tzinfo=NY)
    assert bar.available_at.utcoffset() is not None
    assert bar.source_id == "alpaca:raw"
    assert bar.point_in_time_safe is True


def test_early_close_day_uses_actual_close():
    bar = price_bar_to_historical(_price_bar("AAPL", EARLY_CLOSE_DAY), adjustment="raw")
    assert bar.available_at == datetime(2025, 11, 28, 13, 0, tzinfo=NY)


def test_adjustment_is_recorded_in_source_id_and_must_match_bar():
    bar = price_bar_to_historical(_price_bar("AAPL", NORMAL_DAY, adjusted=True), adjustment="split")
    assert bar.source_id == "alpaca:split"
    with pytest.raises(BacktestError, match="adjust"):
        price_bar_to_historical(_price_bar("AAPL", NORMAL_DAY, adjusted=True), adjustment="raw")
    with pytest.raises(BacktestError, match="adjust"):
        price_bar_to_historical(_price_bar("AAPL", NORMAL_DAY, adjusted=False), adjustment="split")
    with pytest.raises(BacktestError, match="adjust"):
        price_bar_to_historical(_price_bar("AAPL", NORMAL_DAY), adjustment="bogus")


def test_bar_on_non_session_date_is_rejected():
    with pytest.raises(BacktestError, match="session"):
        price_bar_to_historical(_price_bar("AAPL", HOLIDAY), adjustment="raw")


# -- as_of enforcement -------------------------------------------------------


def test_provider_rejects_bars_after_as_of():
    provider = AlpacaHistoricalDataProvider(_snapshot())
    before_early_close = datetime(2025, 11, 28, 17, 59, tzinfo=timezone.utc)  # 12:59 ET
    bars = provider.bars(symbol="AAPL", start=NORMAL_DAY, end=EARLY_CLOSE_DAY, as_of=before_early_close)
    assert [b.session_date for b in bars] == [NORMAL_DAY]
    at_close = datetime(2025, 11, 28, 18, 0, tzinfo=timezone.utc)  # 13:00 ET
    bars = provider.bars(symbol="AAPL", start=NORMAL_DAY, end=EARLY_CLOSE_DAY, as_of=at_close)
    assert [b.session_date for b in bars] == [NORMAL_DAY, EARLY_CLOSE_DAY]


def test_provider_requires_aware_as_of():
    provider = AlpacaHistoricalDataProvider(_snapshot())
    with pytest.raises(BacktestError, match="timezone"):
        provider.bars(symbol="AAPL", start=NORMAL_DAY, end=EARLY_CLOSE_DAY, as_of=datetime(2025, 12, 31))


def test_provider_fails_closed_outside_snapshot_coverage():
    provider = AlpacaHistoricalDataProvider(_snapshot())
    with pytest.raises(BacktestError, match="coverage"):
        provider.bars(symbol="AAPL", start=date(2025, 1, 2), end=EARLY_CLOSE_DAY, as_of=AS_OF)
    with pytest.raises(BacktestError, match="coverage"):
        provider.bars(symbol="AAPL", start=NORMAL_DAY, end=date(2026, 1, 15), as_of=AS_OF)


def test_provider_unknown_symbol_returns_no_bars():
    provider = AlpacaHistoricalDataProvider(_snapshot())
    assert provider.bars(symbol="ZZZZ", start=NORMAL_DAY, end=EARLY_CLOSE_DAY, as_of=AS_OF) == ()


def test_fetch_drops_session_not_yet_closed_at_fetch_time():
    client = _client()
    mid_session = datetime(2025, 11, 28, 17, 0, tzinfo=timezone.utc)  # 12:00 ET on the early-close day
    snap = fetch_snapshot(
        client, symbols=("AAPL",), start=NORMAL_DAY, end=EARLY_CLOSE_DAY,
        adjustment="raw", as_of=mid_session, warmup_sessions=0,
    )
    assert [b.session_date for b in snap.bars_by_symbol["AAPL"]] == [NORMAL_DAY]


# -- snapshot ----------------------------------------------------------------


def test_snapshot_round_trip_and_hash_stability(tmp_path):
    snap = _snapshot()
    path = tmp_path / "snap.json"
    write_snapshot(path, snap)
    loaded = read_snapshot(path)
    assert loaded.content_hash == snap.content_hash
    assert loaded.bars_by_symbol == snap.bars_by_symbol
    assert loaded.adjustment == "raw"
    assert loaded.symbols == ("AAPL", "SPY")
    # Same data fetched again (different fetch time) hashes identically.
    again = fetch_snapshot(
        _client(), symbols=("AAPL", "SPY"), start=NORMAL_DAY, end=EARLY_CLOSE_DAY,
        adjustment="raw", as_of=AS_OF + timedelta(days=30), warmup_sessions=0,
    )
    assert again.content_hash == snap.content_hash


def test_hash_changes_with_data_and_adjustment():
    base = _snapshot()
    other_adjustment = _snapshot("split")
    assert other_adjustment.content_hash != base.content_hash
    changed = StubClient({
        "AAPL": [_price_bar("AAPL", NORMAL_DAY, "100.01"), _price_bar("AAPL", EARLY_CLOSE_DAY, "101")],
        "SPY": [_price_bar("SPY", NORMAL_DAY, "500"), _price_bar("SPY", EARLY_CLOSE_DAY, "501")],
    })
    snap = fetch_snapshot(
        changed, symbols=("AAPL", "SPY"), start=NORMAL_DAY, end=EARLY_CLOSE_DAY,
        adjustment="raw", as_of=AS_OF, warmup_sessions=0,
    )
    assert snap.content_hash != base.content_hash


def test_tampered_snapshot_is_rejected(tmp_path):
    path = tmp_path / "snap.json"
    write_snapshot(path, _snapshot())
    doc = json.loads(path.read_text())
    doc["bars"]["AAPL"][0][4] = "100.5"  # alter a close, staying inside the bar's range
    path.write_text(json.dumps(doc))
    with pytest.raises(BacktestError, match="hash"):
        read_snapshot(path)


def test_malformed_or_unsupported_snapshot_is_rejected(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("not json")
    with pytest.raises(BacktestError):
        read_snapshot(path)
    write_snapshot(tmp_path / "ok.json", _snapshot())
    doc = json.loads((tmp_path / "ok.json").read_text())
    doc["schema_version"] = 99
    path.write_text(json.dumps(doc))
    with pytest.raises(BacktestError, match="schema"):
        read_snapshot(path)
    with pytest.raises(BacktestError):
        read_snapshot(tmp_path / "missing.json")


def test_write_snapshot_refuses_to_overwrite(tmp_path):
    path = tmp_path / "snap.json"
    write_snapshot(path, _snapshot())
    with pytest.raises(BacktestError, match="exists"):
        write_snapshot(path, _snapshot())


def test_empty_symbol_history_fails_closed():
    client = StubClient({"AAPL": [_price_bar("AAPL", NORMAL_DAY)]})
    with pytest.raises(BacktestError, match="ZZZZ"):
        fetch_snapshot(
            client, symbols=("AAPL", "ZZZZ"), start=NORMAL_DAY, end=EARLY_CLOSE_DAY,
            adjustment="raw", as_of=AS_OF, warmup_sessions=0,
        )


def test_fetch_wraps_provider_errors_without_leaking_details():
    class Boom:
        def get_price_history(self, *a, **k):
            raise RuntimeError("secret-key-123 leaked in message")

    with pytest.raises(BacktestError) as excinfo:
        fetch_snapshot(
            Boom(), symbols=("AAPL",), start=NORMAL_DAY, end=EARLY_CLOSE_DAY,
            adjustment="raw", as_of=AS_OF, warmup_sessions=0,
        )
    assert "secret-key-123" not in str(excinfo.value)


# -- warm-up -----------------------------------------------------------------


def test_warmup_start_covers_the_requested_number_of_sessions():
    start = date(2024, 1, 2)
    fetch_from = warmup_start(start, DEFAULT_WARMUP_SESSIONS)
    assert DEFAULT_WARMUP_SESSIONS >= 200 + 14
    assert fetch_from < start
    # ~214 sessions needs at least 214 weekdays minus holidays; calendar span must exceed 214 days.
    assert (start - fetch_from).days >= DEFAULT_WARMUP_SESSIONS
    assert warmup_start(start, 0) == start


def test_fetch_requests_warmup_range_before_start():
    client = StubClient({"AAPL": [_price_bar("AAPL", NORMAL_DAY, adjusted=True)]})
    start = date(2025, 11, 26)
    fetch_snapshot(
        client, symbols=("AAPL",), start=start, end=EARLY_CLOSE_DAY,
        adjustment="split", as_of=AS_OF,
    )
    call = client.calls[0]
    assert call["start"] == warmup_start(start, DEFAULT_WARMUP_SESSIONS)
    assert call["start"] < start
    assert call["end"] == EARLY_CLOSE_DAY
    assert call["adjustment"] == "split"


# -- credentials and load-or-fetch -------------------------------------------


def test_missing_credentials_fail_closed_with_sanitized_error():
    for env in ({}, {"ALPACA_MARKET_DATA_API_KEY": "k-abc"}, {"ALPACA_MARKET_DATA_API_SECRET": "s-abc"}):
        with pytest.raises(BacktestError) as excinfo:
            alpaca_client_from_environment(env)
        message = str(excinfo.value)
        assert "ALPACA_MARKET_DATA_API_KEY" in message
        assert "k-abc" not in message and "s-abc" not in message


def test_client_builds_from_environment_without_network():
    client = alpaca_client_from_environment(
        {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"}
    )
    assert hasattr(client, "get_price_history")


def test_existing_snapshot_is_read_without_touching_credentials(tmp_path):
    path = tmp_path / "snap.json"
    write_snapshot(path, _snapshot())

    def explode():
        raise AssertionError("client factory must not be called when a snapshot exists")

    snap = load_or_fetch_snapshot(
        path, client_factory=explode, symbols=("AAPL", "SPY"), start=NORMAL_DAY,
        end=EARLY_CLOSE_DAY, adjustment="raw", as_of=AS_OF, warmup_sessions=0,
    )
    assert snap.content_hash == read_snapshot(path).content_hash


def test_existing_snapshot_without_enough_warmup_is_rejected(tmp_path):
    path = tmp_path / "snap.json"
    write_snapshot(path, _snapshot())  # fetched with no warm-up
    with pytest.raises(BacktestError, match="coverage"):
        load_or_fetch_snapshot(
            path, client_factory=_client, symbols=("AAPL", "SPY"), start=NORMAL_DAY,
            end=EARLY_CLOSE_DAY, adjustment="raw", as_of=AS_OF,
        )


def test_missing_snapshot_is_fetched_and_written(tmp_path):
    path = tmp_path / "new.json"
    snap = load_or_fetch_snapshot(
        path, client_factory=_client, symbols=("AAPL", "SPY"), start=NORMAL_DAY,
        end=EARLY_CLOSE_DAY, adjustment="raw", as_of=AS_OF, warmup_sessions=0,
    )
    assert path.exists()
    assert read_snapshot(path).content_hash == snap.content_hash


def test_existing_snapshot_must_match_requested_parameters(tmp_path):
    path = tmp_path / "snap.json"
    write_snapshot(path, _snapshot("raw"))
    with pytest.raises(BacktestError, match="adjustment"):
        load_or_fetch_snapshot(
            path, client_factory=_client, symbols=("AAPL", "SPY"), start=NORMAL_DAY,
            end=EARLY_CLOSE_DAY, adjustment="split", as_of=AS_OF, warmup_sessions=0,
        )
    with pytest.raises(BacktestError, match="symbols"):
        load_or_fetch_snapshot(
            path, client_factory=_client, symbols=("AAPL", "MSFT"), start=NORMAL_DAY,
            end=EARLY_CLOSE_DAY, adjustment="raw", as_of=AS_OF, warmup_sessions=0,
        )
