"""Session-tagged 5-minute intraday history for the earnings-gap research strategy.

Offline by construction: `CachedAlpacaIntradayProvider.get_bars` reads only the
local disk cache and never touches the network. The credentialed fetch is a
separate explicit method, `fetch`, that takes an injected HTTP client (anything
with `get_json(url, params=, operation=, symbol=) -> (payload, meta)`); no test
or worker invokes it over the network.

Session tagging uses the XNYS calendar: 04:00 to the regular open is `pre`, the
regular session is `regular`, and the regular close up to the post-market end
(20:00 ET, or the configured early-close end, default 17:00 ET - UNCONFIRMED
against vendor/broker) is `post`. Bars starting outside that window are dropped.
The first post bar (starting at the regular close) carries closing-auction
volume and is flagged `is_closing_print`; it must never be treated as tradable
post-market liquidity.

This module imports nothing from `evidence_providers` (the strategy-safety
boundary); the Alpaca data URL is duplicated below deliberately.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..evaluation.market_calendar import (
    DEFAULT_EARLY_CLOSE_POSTMARKET_END,
    MARKET_TIMEZONE_NAME,
    MarketCalendarError,
    postmarket_close,
    premarket_open,
    regular_session_close,
    regular_session_open,
)
from ..hashing import hash_config
from .models import BacktestError, IntradayBar

DATA_BASE_URL = "https://data.alpaca.markets/v2/stocks"
INTERVAL_MINUTES = 5
INTERVAL_LABEL = "5Min"
ADJUSTMENTS = ("raw", "split", "all")
SESSION_COVERAGE = "pre+regular+post"
CACHE_SCHEMA_VERSION = 1

_NY = ZoneInfo(MARKET_TIMEZONE_NAME)


class IntradayHistoryProvider(Protocol):
    def get_bars(self, symbol: str, session_date: date) -> tuple[IntradayBar, ...]: ...


class JsonHttpClient(Protocol):
    def get_json(self, url: str, *, params: dict, operation: str, symbol: str) -> tuple[Any, Any]: ...


# -- session tagging ---------------------------------------------------------


def classify_session(
    start: datetime, session_date: date,
    early_close_postmarket_end: time = DEFAULT_EARLY_CLOSE_POSTMARKET_END,
) -> tuple[str, bool] | None:
    """(session, is_closing_print) for a bar starting at `start`, or None when the
    start lies outside [pre-market open, post-market end) for `session_date`."""
    if start.tzinfo is None:
        raise BacktestError("bar start must be timezone-aware")
    local = start.astimezone(_NY)
    if local.date() != session_date:
        return None
    pre_open = premarket_open(session_date)
    open_ = regular_session_open(session_date)
    close = regular_session_close(session_date)
    post_end = postmarket_close(session_date, early_close_postmarket_end)
    if local < pre_open or local >= post_end:
        return None
    if local < open_:
        return "pre", False
    if local < close:
        return "regular", False
    return "post", local == close


def _expected_slots(session_date: date) -> list[datetime]:
    open_ = regular_session_open(session_date)
    close = regular_session_close(session_date)
    slots, cur = [], open_
    while cur < close:
        slots.append(cur)
        cur += timedelta(minutes=INTERVAL_MINUTES)
    return slots


def missing_regular_slots(bars: tuple[IntradayBar, ...], session_date: date) -> tuple[datetime, ...]:
    """Regular-session 5-minute slots with no bar (reported, never fabricated)."""
    have = {b.start.astimezone(_NY) for b in bars if b.session == "regular"}
    return tuple(s for s in _expected_slots(session_date) if s not in have)


# -- hashing / reconciliation -----------------------------------------------


def _dec(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _bar_row(bar: IntradayBar) -> dict:
    return {
        "symbol": bar.symbol,
        "start": bar.start.astimezone(timezone.utc).isoformat(),
        "end": bar.end.astimezone(timezone.utc).isoformat(),
        "o": _dec(bar.open), "h": _dec(bar.high), "l": _dec(bar.low), "c": _dec(bar.close),
        "v": bar.volume, "session": bar.session, "source_id": bar.source_id,
        "closing_print": bar.is_closing_print,
    }


def intraday_data_hash(bars: tuple[IntradayBar, ...]) -> str:
    """Deterministic content hash, independent of input order."""
    rows = sorted((_bar_row(b) for b in bars), key=lambda r: (r["symbol"], r["start"]))
    return hash_config({"schema": CACHE_SCHEMA_VERSION, "bars": rows})


@dataclass(frozen=True)
class VolumeReconciliation:
    regular_volume: int
    daily_volume: int
    difference: int
    relative_difference: Decimal | None
    tolerance: Decimal
    within_tolerance: bool

    @property
    def flagged(self) -> bool:
        return not self.within_tolerance


def reconcile_daily_volume(
    bars: tuple[IntradayBar, ...], daily_volume: int, tolerance: Decimal,
) -> VolumeReconciliation:
    """Compare summed regular-session intraday volume with the daily bar volume.
    Flags a mismatch beyond `tolerance` (a fraction of daily volume); never
    adjusts either figure. Pre/post and closing-print bars are excluded."""
    if tolerance < 0:
        raise BacktestError("volume tolerance must not be negative")
    if daily_volume < 0:
        raise BacktestError("daily volume must not be negative")
    regular = sum(b.volume for b in bars if b.session == "regular" and not b.is_closing_print)
    diff = regular - daily_volume
    if daily_volume == 0:
        rel: Decimal | None = None
        within = regular == 0
    else:
        rel = abs(Decimal(diff)) / Decimal(daily_volume)
        within = rel <= tolerance
    return VolumeReconciliation(regular, daily_volume, diff, rel, tolerance, within)


# -- providers ---------------------------------------------------------------


def _validate_bars(symbol: str, session_date: date, bars: tuple[IntradayBar, ...]) -> tuple[IntradayBar, ...]:
    seen: set[datetime] = set()
    for bar in bars:
        if bar.symbol != symbol:
            raise BacktestError(f"bar symbol {bar.symbol} does not match requested {symbol}")
        if bar.start.astimezone(_NY).date() != session_date:
            raise BacktestError(f"{symbol}: bar {bar.start.isoformat()} is not on {session_date}")
        if bar.start in seen:
            raise BacktestError(f"{symbol}: duplicate bar at {bar.start.isoformat()}")
        seen.add(bar.start)
    return tuple(sorted(bars, key=lambda b: b.start))


class FixtureIntradayHistoryProvider:
    """In-memory provider keyed by (SYMBOL, session_date)."""

    def __init__(self, bars_by_symbol_date: Mapping[tuple[str, date], tuple[IntradayBar, ...]]):
        self._bars = {(s.upper(), d): tuple(b) for (s, d), b in bars_by_symbol_date.items()}

    def get_bars(self, symbol: str, session_date: date) -> tuple[IntradayBar, ...]:
        symbol = symbol.upper()
        return _validate_bars(symbol, session_date, self._bars.get((symbol, session_date), ()))


def cache_key(symbol: str, session_date: date, interval: str, adjustment: str, coverage: str) -> str:
    return "__".join([symbol.upper(), session_date.isoformat(), interval, adjustment, coverage])


class CachedAlpacaIntradayProvider:
    def __init__(
        self, cache_dir: Path | str, *, adjustment: str = "raw", feed: str = "iex",
        early_close_postmarket_end: time = DEFAULT_EARLY_CLOSE_POSTMARKET_END,
    ):
        if adjustment not in ADJUSTMENTS:
            raise BacktestError(f"unknown adjustment {adjustment!r}; expected one of {ADJUSTMENTS}")
        if feed not in ("iex", "sip"):
            raise BacktestError(f"unknown feed {feed!r}")
        self._dir = Path(cache_dir)
        self._adjustment = adjustment
        self._feed = feed
        self._post_end = early_close_postmarket_end

    def cache_path(self, symbol: str, session_date: date) -> Path:
        key = cache_key(symbol, session_date, INTERVAL_LABEL, self._adjustment, SESSION_COVERAGE)
        return self._dir / "intraday" / symbol.upper() / f"{key}.json"

    # offline read ----------------------------------------------------------

    def get_bars(self, symbol: str, session_date: date) -> tuple[IntradayBar, ...]:
        symbol = symbol.upper()
        path = self.cache_path(symbol, session_date)
        if not path.exists():
            raise BacktestError(
                f"{symbol} {session_date}: no cached intraday data at {path}; run the explicit fetch step"
            )
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BacktestError(f"{symbol} {session_date}: unreadable intraday cache ({exc})") from exc
        if doc.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise BacktestError(f"{symbol} {session_date}: unsupported intraday cache schema")
        expected = {
            "symbol": symbol, "session_date": session_date.isoformat(), "interval": INTERVAL_LABEL,
            "adjustment": self._adjustment, "coverage": SESSION_COVERAGE,
        }
        for field, value in expected.items():
            if doc.get(field) != value:
                raise BacktestError(f"{symbol} {session_date}: cache {field} mismatch")
        bars = self._parse(symbol, session_date, doc.get("bars") or [])
        if doc.get("data_hash") != intraday_data_hash(bars):
            raise BacktestError(f"{symbol} {session_date}: intraday cache hash mismatch")
        return bars

    def _parse(self, symbol: str, session_date: date, raw_bars: list) -> tuple[IntradayBar, ...]:
        out: list[IntradayBar] = []
        for raw in raw_bars:
            try:
                start = datetime.fromisoformat(str(raw["t"]).replace("Z", "+00:00"))
                if start.tzinfo is None:
                    raise BacktestError(f"{symbol}: bar timestamp {raw['t']!r} is naive")
                tag = classify_session(start, session_date, self._post_end)
                if tag is None:
                    continue  # outside the covered window for this date
                session, closing = tag
                end = start + timedelta(minutes=INTERVAL_MINUTES)
                vol = int(raw["v"])
                out.append(IntradayBar(
                    symbol=symbol, start=start, end=end,
                    open=Decimal(str(raw["o"])), high=Decimal(str(raw["h"])),
                    low=Decimal(str(raw["l"])), close=Decimal(str(raw["c"])),
                    volume=vol, session=session,  # type: ignore[arg-type]
                    available_at=end, source_id=f"alpaca:{self._adjustment}:{self._feed}",
                    is_closing_print=closing,
                ))
            except (KeyError, ValueError, InvalidOperation, TypeError) as exc:
                raise BacktestError(f"{symbol} {session_date}: malformed intraday bar ({exc})") from exc
        return _validate_bars(symbol, session_date, tuple(out))

    # explicit fetch (credentialed; never called from tests over the network) --

    def fetch(self, http_client: JsonHttpClient, symbol: str, session_date: date) -> Path:
        """Fetch one symbol-day over the injected client and write the cache."""
        symbol = symbol.upper()
        try:
            start = premarket_open(session_date)
            end = postmarket_close(session_date, self._post_end)
        except MarketCalendarError as exc:
            raise BacktestError(f"{symbol} {session_date}: not a trading session ({exc})") from exc
        params = {
            "timeframe": INTERVAL_LABEL,
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "adjustment": self._adjustment, "limit": 10000, "feed": self._feed, "sort": "asc",
        }
        raw_bars: list = []
        page_token = None
        for _ in range(20):  # bounded pagination
            page_params = dict(params)
            if page_token:
                page_params["page_token"] = page_token
            payload, _meta = http_client.get_json(
                f"{DATA_BASE_URL}/{symbol}/bars", params=page_params, operation="intraday_bars", symbol=symbol,
            )
            if not isinstance(payload, dict) or "bars" not in payload:
                raise BacktestError(f"{symbol} {session_date}: malformed intraday response (no 'bars')")
            raw_bars.extend(payload["bars"] or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        else:
            raise BacktestError(f"{symbol} {session_date}: intraday pagination did not terminate")
        bars = self._parse(symbol, session_date, raw_bars)
        doc = {
            "schema_version": CACHE_SCHEMA_VERSION, "symbol": symbol,
            "session_date": session_date.isoformat(), "interval": INTERVAL_LABEL,
            "adjustment": self._adjustment, "coverage": SESSION_COVERAGE, "feed": self._feed,
            "data_hash": intraday_data_hash(bars),
            "bars": [
                {"t": b.start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "o": _dec(b.open), "h": _dec(b.high), "l": _dec(b.low), "c": _dec(b.close), "v": b.volume}
                for b in bars
            ],
        }
        path = self.cache_path(symbol, session_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, sort_keys=True, indent=1)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path
