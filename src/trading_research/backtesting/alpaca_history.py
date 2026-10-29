"""Real daily-bar history for the backtest engine (docs/historical-backtesting-plan.md Step 1).

A one-time fetch from Alpaca is written to a local JSON snapshot with a SHA-256
content hash. Every later run reads only the snapshot, so a backtest is offline
and reproducible, and the hash identifies exactly which data it used.

Point-in-time rule: a daily bar becomes knowable at the actual regular-session
close of its date (early closes included). The provider never returns a bar
whose `available_at` is after the caller's `as_of`.

This module imports no broker, runtime or paper-book code. Credentials are read
only when a fetch is actually needed.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from ..evaluation.market_calendar import MarketCalendarError, regular_session_close
from ..evidence_providers.errors import ProviderError
from ..evidence_providers.models import PriceBar
from ..hashing import hash_config
from .models import BacktestError, HistoricalBar

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_PROVIDER = "alpaca-data"
# Snapshot provider label -> prefix of each bar's `source_id`.
SOURCE_PREFIXES = {SNAPSHOT_PROVIDER: "alpaca", "fixture": "fixture"}
ADJUSTMENTS = ("raw", "split", "all")

API_KEY_ENV = "ALPACA_MARKET_DATA_API_KEY"
API_SECRET_ENV = "ALPACA_MARKET_DATA_API_SECRET"

# mean_reversion's long_trend_sma_days (200) plus the engine's default ATR
# period (14): the longest lookback any current strategy needs before `start`.
DEFAULT_WARMUP_SESSIONS = 214
_WARMUP_CALENDAR_PADDING_DAYS = 14  # holidays; over-fetching is harmless


class PriceHistoryClient(Protocol):
    def get_price_history(
        self, symbol: str, *, start: date, end: date, as_of: datetime, adjustment: str = "raw",
    ) -> tuple[PriceBar, ...]: ...


def _check_adjustment(adjustment: str) -> None:
    if adjustment not in ADJUSTMENTS:
        raise BacktestError(f"unknown price adjustment {adjustment!r}; expected one of {ADJUSTMENTS}")


def _check_provider(provider: str) -> None:
    if provider not in SOURCE_PREFIXES:
        raise BacktestError(f"unknown snapshot provider {provider!r}; expected one of {sorted(SOURCE_PREFIXES)}")


def warmup_start(start: date, sessions: int = DEFAULT_WARMUP_SESSIONS) -> date:
    """First calendar date to fetch so `sessions` trading days precede `start`."""
    if sessions < 0:
        raise BacktestError("warm-up sessions must not be negative")
    if sessions == 0:
        return start
    return start - timedelta(days=math.ceil(sessions * 7 / 5) + _WARMUP_CALENDAR_PADDING_DAYS)


def price_bar_to_historical(
    bar: PriceBar, *, adjustment: str, provider: str = SNAPSHOT_PROVIDER,
) -> HistoricalBar:
    _check_adjustment(adjustment)
    _check_provider(provider)
    if bar.adjusted != (adjustment != "raw"):
        raise BacktestError(
            f"{bar.symbol} {bar.session_date}: bar adjusted={bar.adjusted} contradicts "
            f"requested adjustment {adjustment!r}"
        )
    try:
        available_at = regular_session_close(bar.session_date)
    except MarketCalendarError as exc:
        raise BacktestError(f"{bar.symbol} {bar.session_date}: not a trading session ({exc})") from exc
    return HistoricalBar(
        symbol=bar.symbol, session_date=bar.session_date, open=bar.open, high=bar.high,
        low=bar.low, close=bar.close, volume=bar.volume, available_at=available_at,
        point_in_time_safe=True, source_id=f"{SOURCE_PREFIXES[provider]}:{adjustment}",
    )


# -- snapshot ----------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalSnapshot:
    symbols: tuple[str, ...]
    fetch_start: date
    end: date
    adjustment: str
    bars_by_symbol: Mapping[str, tuple[HistoricalBar, ...]]
    content_hash: str
    # Metadata only: deliberately outside the hash so refetching identical
    # data yields the same identity.
    fetched_as_of: datetime
    provider: str = SNAPSHOT_PROVIDER


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _row(bar: HistoricalBar) -> list[str]:
    return [
        bar.session_date.isoformat(), _decimal_text(bar.open), _decimal_text(bar.high),
        _decimal_text(bar.low), _decimal_text(bar.close), str(bar.volume),
    ]


def _content_hash(
    *, provider: str, adjustment: str, fetch_start: date, end: date,
    bars_by_symbol: Mapping[str, tuple[HistoricalBar, ...]],
) -> str:
    return hash_config({
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "provider": provider,
        "adjustment": adjustment, "fetch_start": fetch_start.isoformat(), "end": end.isoformat(),
        "bars": {symbol: [_row(bar) for bar in bars_by_symbol[symbol]] for symbol in sorted(bars_by_symbol)},
    })


def _build_snapshot(
    *, provider: str, adjustment: str, fetch_start: date, end: date,
    bars_by_symbol: Mapping[str, tuple[HistoricalBar, ...]], fetched_as_of: datetime,
) -> HistoricalSnapshot:
    ordered = {symbol: bars_by_symbol[symbol] for symbol in sorted(bars_by_symbol)}
    return HistoricalSnapshot(
        symbols=tuple(ordered), fetch_start=fetch_start, end=end, adjustment=adjustment,
        bars_by_symbol=ordered, fetched_as_of=fetched_as_of, provider=provider,
        content_hash=_content_hash(
            provider=provider, adjustment=adjustment, fetch_start=fetch_start, end=end, bars_by_symbol=ordered,
        ),
    )


def _normalize_symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({s.strip().upper() for s in symbols if s.strip()}))
    if not normalized:
        raise BacktestError("at least one symbol is required")
    return normalized


def fetch_snapshot(
    client: PriceHistoryClient, *, symbols: tuple[str, ...], start: date, end: date,
    adjustment: str, as_of: datetime, warmup_sessions: int = DEFAULT_WARMUP_SESSIONS,
    provider: str = SNAPSHOT_PROVIDER,
) -> HistoricalSnapshot:
    """Fetch warm-up + requested history for every symbol; fails closed on any gap."""
    _check_adjustment(adjustment)
    _check_provider(provider)
    if as_of.tzinfo is None:
        raise BacktestError("as_of must be timezone-aware")
    if end < start:
        raise BacktestError("snapshot end must not precede start")
    fetch_start = warmup_start(start, warmup_sessions)
    names = _normalize_symbols(symbols)
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...]] = {}
    for symbol in names:
        try:
            raw = client.get_price_history(
                symbol, start=fetch_start, end=end, as_of=as_of, adjustment=adjustment,
            )
        except ProviderError as exc:
            raise BacktestError(f"market data fetch failed for {symbol}: {type(exc).__name__}") from exc
        except Exception as exc:  # never forward provider text: it may echo credentials
            raise BacktestError(f"market data fetch failed for {symbol}: {type(exc).__name__}") from None
        if any(bar.provider != provider for bar in raw):
            raise BacktestError(f"{symbol}: provider returned bars not labelled {provider!r}")
        converted = tuple(price_bar_to_historical(bar, adjustment=adjustment, provider=provider) for bar in raw)
        # A session that has not closed by `as_of` is incomplete data: drop it.
        known = tuple(bar for bar in converted if bar.available_at <= as_of)
        dates = [bar.session_date for bar in known]
        if dates != sorted(set(dates)):
            raise BacktestError(f"{symbol}: bars are not strictly ordered by session date")
        if any(bar.symbol != symbol for bar in known):
            raise BacktestError(f"{symbol}: provider returned bars for a different symbol")
        if not known:
            raise BacktestError(f"no history returned for {symbol} between {fetch_start} and {end}")
        bars_by_symbol[symbol] = known
    return _build_snapshot(
        provider=provider, adjustment=adjustment, fetch_start=fetch_start, end=end,
        bars_by_symbol=bars_by_symbol, fetched_as_of=as_of,
    )


def write_snapshot(path: Path | str, snapshot: HistoricalSnapshot) -> None:
    target = Path(path)
    if target.exists():
        raise BacktestError(f"snapshot file already exists: {target}; refusing to overwrite")
    document = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "provider": snapshot.provider,
        "adjustment": snapshot.adjustment, "fetch_start": snapshot.fetch_start.isoformat(),
        "end": snapshot.end.isoformat(), "fetched_as_of": snapshot.fetched_as_of.isoformat(),
        "content_hash": snapshot.content_hash, "symbols": list(snapshot.symbols),
        "bars": {symbol: [_row(bar) for bar in snapshot.bars_by_symbol[symbol]] for symbol in snapshot.symbols},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def read_snapshot(path: Path | str) -> HistoricalSnapshot:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BacktestError(f"cannot read snapshot {source}: {type(exc).__name__}") from None
    try:
        if document["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
            raise BacktestError(f"unsupported snapshot schema_version {document['schema_version']!r}")
        adjustment = document["adjustment"]
        _check_adjustment(adjustment)
        provider = document["provider"]
        _check_provider(provider)
        fetch_start = date.fromisoformat(document["fetch_start"])
        end = date.fromisoformat(document["end"])
        fetched_as_of = datetime.fromisoformat(document["fetched_as_of"])
        stored_hash = document["content_hash"]
        raw_bars = document["bars"]
        if sorted(document["symbols"]) != sorted(raw_bars):
            raise BacktestError("snapshot symbols do not match its bar sets")
        bars_by_symbol: dict[str, tuple[HistoricalBar, ...]] = {}
        for symbol, rows in raw_bars.items():
            bars_by_symbol[symbol] = tuple(
                _bar_from_row(symbol, row, adjustment=adjustment, provider=provider) for row in rows
            )
    except BacktestError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise BacktestError(f"malformed snapshot {source}: {type(exc).__name__}") from None
    snapshot = _build_snapshot(
        provider=provider, adjustment=adjustment, fetch_start=fetch_start, end=end,
        bars_by_symbol=bars_by_symbol, fetched_as_of=fetched_as_of,
    )
    if snapshot.content_hash != stored_hash:
        raise BacktestError(f"snapshot {source} content hash mismatch; file was altered or corrupted")
    return snapshot


def _bar_from_row(symbol: str, row: list, *, adjustment: str, provider: str) -> HistoricalBar:
    session_date = date.fromisoformat(row[0])
    open_, high, low, close = (Decimal(value) for value in row[1:5])
    try:
        available_at = regular_session_close(session_date)
    except MarketCalendarError as exc:
        raise BacktestError(f"{symbol} {session_date}: not a trading session ({exc})") from exc
    return HistoricalBar(
        symbol=symbol, session_date=session_date, open=open_, high=high, low=low, close=close,
        volume=int(row[5]), available_at=available_at, point_in_time_safe=True,
        source_id=f"{SOURCE_PREFIXES[provider]}:{adjustment}",
    )


# -- provider ----------------------------------------------------------------


class AlpacaHistoricalDataProvider:
    """`HistoricalDataProvider` served entirely from a snapshot (no network)."""

    def __init__(self, snapshot: HistoricalSnapshot):
        self._snapshot = snapshot

    @property
    def snapshot(self) -> HistoricalSnapshot:
        return self._snapshot

    def bars(
        self, *, symbol: str, start: date, end: date, as_of: datetime,
    ) -> tuple[HistoricalBar, ...]:
        if as_of.tzinfo is None:
            raise BacktestError("as_of must be timezone-aware")
        if start < self._snapshot.fetch_start or end > self._snapshot.end:
            raise BacktestError(
                f"requested range {start}..{end} is outside snapshot coverage "
                f"{self._snapshot.fetch_start}..{self._snapshot.end}"
            )
        selected = tuple(
            bar for bar in self._snapshot.bars_by_symbol.get(symbol, ())
            if start <= bar.session_date <= end and bar.available_at <= as_of
        )
        return tuple(sorted(selected, key=lambda bar: bar.session_date))


# -- credentials and load-or-fetch ---------------------------------------------


def alpaca_client_from_environment(env: Mapping[str, str] | None = None, *, feed: str = "iex"):
    """Build the market-data client; credentials are read here and only here."""
    source = os.environ if env is None else env
    key, secret = source.get(API_KEY_ENV), source.get(API_SECRET_ENV)
    if not key or not secret:
        raise BacktestError(
            f"fetching history requires {API_KEY_ENV} and {API_SECRET_ENV} to be set; "
            "alternatively supply an existing snapshot"
        )
    from ..evidence_providers.http_client import HttpJsonClient
    from ..evidence_providers.market_data_provider import AlpacaMarketDataClient
    from ..evidence_providers.rate_limits import MinIntervalRateLimiter

    http = HttpJsonClient(
        base_headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        rate_limiter=MinIntervalRateLimiter(0.35), provider=SNAPSHOT_PROVIDER,
    )
    return AlpacaMarketDataClient(api_key=key, api_secret=secret, http_client=http, feed=feed)


def load_or_fetch_snapshot(
    path: Path | str, *, client_factory: Callable[[], PriceHistoryClient], symbols: tuple[str, ...],
    start: date, end: date, adjustment: str, as_of: datetime,
    warmup_sessions: int = DEFAULT_WARMUP_SESSIONS, provider: str = SNAPSHOT_PROVIDER,
) -> HistoricalSnapshot:
    """Read `path` if it exists (never touching credentials); otherwise fetch once and write it."""
    _check_adjustment(adjustment)
    _check_provider(provider)
    target = Path(path)
    names = _normalize_symbols(symbols)
    if target.exists():
        snapshot = read_snapshot(target)
        if snapshot.provider != provider:
            raise BacktestError(f"snapshot provider {snapshot.provider!r} differs from requested {provider!r}")
        if snapshot.adjustment != adjustment:
            raise BacktestError(
                f"snapshot adjustment {snapshot.adjustment!r} differs from requested {adjustment!r}"
            )
        missing = sorted(set(names) - set(snapshot.symbols))
        if missing:
            raise BacktestError(f"snapshot is missing requested symbols: {missing}")
        if snapshot.fetch_start > warmup_start(start, warmup_sessions) or snapshot.end < end:
            raise BacktestError(
                f"snapshot coverage {snapshot.fetch_start}..{snapshot.end} does not cover the "
                f"requested warm-up and range ending {end}"
            )
        return snapshot
    snapshot = fetch_snapshot(
        client_factory(), symbols=names, start=start, end=end, adjustment=adjustment,
        as_of=as_of, warmup_sessions=warmup_sessions, provider=provider,
    )
    write_snapshot(target, snapshot)
    return snapshot
