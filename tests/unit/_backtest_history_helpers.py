"""Shared builders for historical-replay tests: real XNYS sessions and
`available_at` = actual regular-session close. Not collected by pytest."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from trading_research.backtesting.models import HistoricalBar
from trading_research.evaluation.market_calendar import is_trading_day, regular_session_close


def session_dates(start: date, count: int) -> list[date]:
    out: list[date] = []
    day = start
    while len(out) < count:
        if is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def make_bars(
    symbol: str, closes: list[float], *, start: date = date(2024, 1, 2),
    volumes: list[int] | None = None, high_pad: float = 1.01, low_pad: float = 0.99,
) -> tuple[HistoricalBar, ...]:
    days = session_dates(start, len(closes))
    volumes = volumes if volumes is not None else [1_000_000] * len(closes)
    bars = []
    for day, close, volume in zip(days, closes, volumes):
        c = Decimal(str(round(close, 4)))
        bars.append(HistoricalBar(
            symbol=symbol, session_date=day, open=c, high=(c * Decimal(str(high_pad))).quantize(Decimal("0.0001")),
            low=(c * Decimal(str(low_pad))).quantize(Decimal("0.0001")), close=c, volume=volume,
            available_at=regular_session_close(day), source_id="test",
        ))
    return tuple(bars)
