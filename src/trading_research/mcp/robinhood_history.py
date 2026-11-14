"""Read-only daily price history from the hosted Robinhood MCP.

Used by ``scripts/score.py --robinhood``. This module is only the domain layer:
what to ask ``get_equity_historicals`` for and how to validate the answer. OAuth,
token storage, the read-only tool policy and the transport are in the generic
:mod:`oauth_readonly_client`. A human logs in once (``python -m
trading_research.mcp.robinhood_history login``); later runs are non-interactive.

* Exactly one tool is enabled on this client, ``get_equity_historicals``.
* Only completed, regular-hours, split-adjusted ``day`` bars are accepted.
  Robinhood does not say in the response whether bars were adjusted, so the
  adjustment is *requested* (``adjustment_type=split``) and trusted; callers
  record that it was not verifiable.
* Gap-fill (``interpolated``) bars carry no information. They are ignored after
  the as-of session and are an error at or before it, so a hole is never
  silently scored.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..evaluation.market_calendar import MarketCalendarError, is_trading_day
from ..evidence_providers.models import PriceBar
from .oauth_readonly_client import (
    ROBINHOOD,
    McpError,
    McpLoginRequired,
    ReadOnlyOAuthMcpClient,
    client_from_environment,
    main as _login_main,
)

PROVIDER = "robinhood-mcp"
TOOL_NAME = "get_equity_historicals"

__all__ = [
    "PROVIDER", "TOOL_NAME", "McpLoginRequired", "RobinhoodHistoryClient", "RobinhoodHistoryError",
    "RobinhoodSessionNotPublished", "parse_historicals", "robinhood_client_from_environment",
]


class RobinhoodHistoryError(McpError):
    """The Robinhood MCP could not supply usable history."""


class RobinhoodSessionNotPublished(RobinhoodHistoryError):
    """The as-of session traded, but Robinhood's daily bar for it is still a placeholder.

    ``session_not_published`` is how callers recognise this one retryable condition without importing
    this module (``scripts/score.py`` loads repo code only when asked to fetch).
    """

    session_not_published = True


# --------------------------------------------------------------------------
# Response parsing (pure)
# --------------------------------------------------------------------------

def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RobinhoodHistoryError(f"bar field {field} is not a decimal string")
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        raise RobinhoodHistoryError(f"bar field {field} is not a number") from None
    if not out.is_finite() or out <= 0:
        raise RobinhoodHistoryError(f"bar field {field} is not a positive number")
    return out


def _session_date(begins_at: Any) -> date:
    if not isinstance(begins_at, str):
        raise RobinhoodHistoryError("bar begins_at is not a string")
    try:
        stamp = datetime.fromisoformat(begins_at.replace("Z", "+00:00"))
    except ValueError:
        raise RobinhoodHistoryError("bar begins_at is not an RFC3339 timestamp") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise RobinhoodHistoryError("bar begins_at is not UTC")
    if (stamp.hour, stamp.minute, stamp.second, stamp.microsecond) != (0, 0, 0, 0):
        raise RobinhoodHistoryError("daily bar does not begin at 00:00Z")
    return stamp.date()


def parse_historicals(payload: Any, *, symbol: str, last_session: date) -> tuple[PriceBar, ...]:
    """Validate a ``get_equity_historicals`` response and return daily bars up to ``last_session``."""
    data = payload.get("data") if isinstance(payload, dict) else None
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RobinhoodHistoryError("unexpected response shape (no data.results list)")
    matching = [r for r in results if isinstance(r, dict) and r.get("symbol") == symbol]
    if len(matching) != 1 or len(results) != 1:
        raise RobinhoodHistoryError(f"expected exactly one result for {symbol}, got {len(results)}")
    (result,) = matching
    if result.get("interval") != "day" or result.get("bounds") != "regular":
        raise RobinhoodHistoryError("result is not regular-hours daily bars")
    raw_bars = result.get("bars")
    if not isinstance(raw_bars, list):
        raise RobinhoodHistoryError("result has no bars list")

    bars: list[PriceBar] = []
    for raw in raw_bars:
        if not isinstance(raw, dict):
            raise RobinhoodHistoryError("bar is not an object")
        day = _session_date(raw.get("begins_at"))
        if day > last_session:
            continue  # beyond the as-of session (includes today's gap-fill bar)
        if raw.get("interpolated") is True:
            if day == last_session:
                raise RobinhoodSessionNotPublished(
                    f"the daily bar for {day} is a gap-filled (interpolated) placeholder: Robinhood has not "
                    "published that session's daily bar yet. Retry later, or score the previous session")
            raise RobinhoodHistoryError(f"gap-filled (interpolated) bar on {day}; refusing to score a hole")
        if raw.get("session") != "reg":
            raise RobinhoodHistoryError(f"bar on {day} is not a regular session")
        try:
            if not is_trading_day(day):
                raise RobinhoodHistoryError(f"bar dated {day}, which is not a trading session")
        except MarketCalendarError as exc:
            raise RobinhoodHistoryError(f"market calendar unavailable: {exc}") from None
        volume = raw.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int):
            raise RobinhoodHistoryError(f"bar on {day} has a non-integer volume")
        try:
            bars.append(PriceBar(
                symbol=symbol, session_date=day,
                open=_decimal(raw.get("open_price"), "open_price"),
                high=_decimal(raw.get("high_price"), "high_price"),
                low=_decimal(raw.get("low_price"), "low_price"),
                close=_decimal(raw.get("close_price"), "close_price"),
                volume=volume,
                adjusted=True,  # requested via adjustment_type=split; the response cannot confirm it
                provider=PROVIDER,
            ))
        except ValueError as exc:
            raise RobinhoodHistoryError(str(exc)) from None
    _require_no_missing_sessions(bars)
    return tuple(bars)


def _require_no_missing_sessions(bars: list[PriceBar]) -> None:
    if len(bars) < 2:
        return
    have = {b.session_date for b in bars}
    day, last, missing = bars[0].session_date, bars[-1].session_date, 0
    try:
        while day <= last:
            if is_trading_day(day) and day not in have:
                missing += 1
            day += timedelta(days=1)
    except MarketCalendarError as exc:
        raise RobinhoodHistoryError(f"market calendar unavailable: {exc}") from None
    if missing:
        raise RobinhoodHistoryError(f"{missing} trading session(s) missing inside the returned range")


# --------------------------------------------------------------------------
# Price-history client (same call shape as the repo's Alpaca client)
# --------------------------------------------------------------------------

class RobinhoodHistoryClient:
    def __init__(self, mcp: ReadOnlyOAuthMcpClient) -> None:
        if TOOL_NAME not in mcp.allowed_tools:
            raise RobinhoodHistoryError(f"the MCP client does not enable {TOOL_NAME}")
        self._mcp = mcp

    def get_price_history(self, symbol: str, *, start: date, end: date, as_of: datetime,
                          adjustment: str = "split") -> tuple[PriceBar, ...]:
        """Daily bars for sessions in [start, end]; ``end`` is the as-of session."""
        if adjustment != "split":
            raise RobinhoodHistoryError("Robinhood daily bars are split-adjusted only (adjustment 'split')")
        if as_of.tzinfo is None:
            raise RobinhoodHistoryError("as_of must be timezone-aware")
        payload = self._mcp.call(TOOL_NAME, {
            "symbols": [symbol],
            "interval": "day",
            "bounds": "regular",
            "adjustment_type": "split",
            "start_time": f"{start.isoformat()}T00:00:00Z",
            # a daily bar spans <date>T00:00Z..<next date>T00:00Z, so end at the day after the as-of session
            "end_time": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
        })
        return parse_historicals(payload, symbol=symbol, last_session=end)


def robinhood_client_from_environment(env: Mapping[str, str] | None = None) -> RobinhoodHistoryClient:
    """Build the client; raises ``McpLoginRequired`` when nobody has logged in yet."""
    return RobinhoodHistoryClient(client_from_environment(ROBINHOOD, allowed_tools=[TOOL_NAME], env=env))


def main(argv: list[str] | None = None) -> int:
    """Login helper kept at this path: ``python -m trading_research.mcp.robinhood_history login``."""
    return _login_main(argv, default_server=ROBINHOOD.name)


if __name__ == "__main__":
    raise SystemExit(main())
