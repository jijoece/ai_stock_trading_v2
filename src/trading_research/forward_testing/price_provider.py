"""Snapshot-backed price provider for the local paper book (docs/paper-testing-plan.md Phase 3).

Serves the paper-book valuation and lifecycle code from a historical snapshot,
with no network access. A close is only returned for a session whose bar was
available by the requested time; the lifecycle's own point-in-time and
staleness checks then apply unchanged.
"""
from __future__ import annotations

from datetime import date, datetime

from ..backtesting.alpaca_history import HistoricalSnapshot
from ..evaluation.price_provider import PricePoint
from ..evidence_providers.models import PriceBar


class SnapshotPriceProvider:
    def __init__(self, snapshot: HistoricalSnapshot):
        self._snapshot = snapshot

    def get_close(self, symbol: str, as_of: date) -> PricePoint | None:
        for bar in self._snapshot.bars_by_symbol.get(symbol, ()):
            if bar.session_date == as_of:
                return PricePoint(
                    symbol=symbol, as_of=as_of, close=bar.close, source=f"snapshot:{self._snapshot.provider}",
                    available_at=bar.available_at,
                )
        return None

    def get_price_history(self, symbol: str, *, start: date, end: date, as_of: datetime) -> tuple[PriceBar, ...]:
        return tuple(
            PriceBar(
                symbol=bar.symbol, session_date=bar.session_date, open=bar.open, high=bar.high, low=bar.low,
                close=bar.close, volume=bar.volume, adjusted=self._snapshot.adjustment != "raw",
                provider=self._snapshot.provider,
            )
            for bar in self._snapshot.bars_by_symbol.get(symbol, ())
            if start <= bar.session_date <= end and bar.available_at <= as_of
        )
