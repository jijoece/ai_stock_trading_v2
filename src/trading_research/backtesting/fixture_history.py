"""Deterministic synthetic daily history for offline runs and tests
(docs/historical-backtesting-plan.md Step 3, `--data-source fixture`).

Prices are pure functions of (symbol, calendar date) built from integers and
`Decimal`, with no RNG or transcendental math, so a given date range yields
bit-identical bars on any machine. The data is synthetic: it exercises the
pipeline end to end and says nothing about any real market.

Stocks follow a triangle-wave price between roughly $10 and $15 (inside the
production screening share-price cap) with periodic one-day volume-backed
upward spikes during the rising phase, which the momentum scanner can pick up.
Index-like benchmark symbols stay near-flat.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..evaluation.market_calendar import is_trading_day
from ..evidence_providers.models import PriceBar
from .alpaca_history import DEFAULT_WARMUP_SESSIONS, HistoricalSnapshot, fetch_snapshot

FIXTURE_PROVIDER = "fixture"
INDEX_LIKE_SYMBOLS = frozenset({"SPY", "QQQ", "IWM", "DIA"})

_CYCLE_DAYS = 600
_SPIKE_EVERY_DAYS = 63
_SPIKE_FACTOR = Decimal("1.016")
_SPIKE_VOLUME_MULTIPLE = 3


def _symbol_offset(symbol: str) -> int:
    return int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:4], "big")


def _bar(symbol: str, day: date, *, adjusted: bool) -> PriceBar:
    ordinal = day.toordinal()
    offset = _symbol_offset(symbol)
    wiggle = Decimal((ordinal * 7919 + offset) % 11 - 5) / Decimal(2000)
    volume = 1_000_000 + ((ordinal * 31 + offset) % 7) * 10_000
    if symbol in INDEX_LIKE_SYMBOLS:
        close = Decimal(400) + wiggle
    else:
        phase = (ordinal + offset) % _CYCLE_DAYS
        up = phase if phase <= _CYCLE_DAYS // 2 else _CYCLE_DAYS - phase
        close = Decimal(10) + Decimal(up) / Decimal(60) + wiggle
        rising = 50 < phase < _CYCLE_DAYS // 2 - 50
        if rising and (ordinal + offset) % _SPIKE_EVERY_DAYS == 0:
            close *= _SPIKE_FACTOR
            volume *= _SPIKE_VOLUME_MULTIPLE
    close = close.quantize(Decimal("0.0001"))
    return PriceBar(
        symbol=symbol, session_date=day, open=close, high=(close * Decimal("1.01")).quantize(Decimal("0.0001")),
        low=(close * Decimal("0.99")).quantize(Decimal("0.0001")), close=close, volume=volume,
        adjusted=adjusted, provider=FIXTURE_PROVIDER,
    )


class FixtureHistoryClient:
    """Same call shape as `AlpacaMarketDataClient.get_price_history`; no network."""

    def get_price_history(
        self, symbol: str, *, start: date, end: date, as_of: datetime, adjustment: str = "raw",
    ) -> tuple[PriceBar, ...]:
        bars = []
        day = start
        while day <= end:
            if is_trading_day(day):
                bars.append(_bar(symbol.upper(), day, adjusted=adjustment != "raw"))
            day += timedelta(days=1)
        return tuple(bars)


def build_fixture_snapshot(
    *, symbols: tuple[str, ...], start: date, end: date, adjustment: str, as_of: datetime,
    warmup_sessions: int = DEFAULT_WARMUP_SESSIONS,
) -> HistoricalSnapshot:
    return fetch_snapshot(
        FixtureHistoryClient(), symbols=symbols, start=start, end=end, adjustment=adjustment,
        as_of=as_of, warmup_sessions=warmup_sessions, provider=FIXTURE_PROVIDER,
    )
