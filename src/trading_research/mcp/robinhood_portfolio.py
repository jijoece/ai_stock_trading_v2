"""Read-only Robinhood equity portfolio: positions valued at the last completed close.

Used by the dashboard's Portfolio (Robinhood) page. It enables exactly three
read-only tools on one OAuth client: ``get_accounts``, ``get_equity_positions``
and ``get_equity_historicals`` (all on the allowlist in ``config/tool_policy.yaml``).

What it is and is not:

* Long equity positions only, summed across every active account. Cash, buying
  power, options and crypto are not included.
* Prices are the last completed regular-session close, not live quotes, so
  values lag the market intraday. The as-of session is reported.
* Average cost is the quantity-weighted mean across accounts.
* Account numbers never leave this module. Fail closed: any account whose positions
  cannot be read, or any held symbol whose price cannot be read, raises.
* A non-long position (a short, or an unknown type) raises rather than being
  silently left out of the totals.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from ..evaluation.market_calendar import MarketCalendarError, is_trading_day, regular_session_close
from .oauth_readonly_client import ROBINHOOD, McpError, client_from_environment
from .robinhood_history import TOOL_NAME as HISTORICALS_TOOL, RobinhoodHistoryClient
from .robinhood_holdings import (
    ACCOUNTS_TOOL,
    MAX_PAGES,
    POSITIONS_TOOL,
    next_cursor,
    parse_active_accounts,
)

__all__ = ["PortfolioLine", "PortfolioSnapshot", "RobinhoodPortfolioClient", "RobinhoodPortfolioError",
           "parse_position_lines", "robinhood_portfolio_client_from_environment"]

_PRICE_WINDOW_DAYS = 10  # calendar days of daily bars requested to find the last completed close


class RobinhoodPortfolioError(McpError):
    """The Robinhood MCP could not supply a trustworthy portfolio view."""


@dataclass(frozen=True)
class PortfolioLine:
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    last_close: Decimal

    @property
    def cost_basis(self) -> Decimal:
        return self.quantity * self.average_cost

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.last_close

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> Decimal | None:
        return None if self.cost_basis == 0 else self.unrealized_pnl / self.cost_basis * 100


@dataclass(frozen=True)
class PortfolioSnapshot:
    lines: tuple[PortfolioLine, ...]
    as_of_session: date
    accounts_checked: int

    @property
    def market_value(self) -> Decimal:
        return sum((line.market_value for line in self.lines), Decimal(0))

    @property
    def cost_basis(self) -> Decimal:
        return sum((line.cost_basis for line in self.lines), Decimal(0))

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.market_value - self.cost_basis


# --------------------------------------------------------------------------
# Response parsing (pure)
# --------------------------------------------------------------------------

def _decimal(raw: Any, what: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise RobinhoodPortfolioError(f"a position {what} is not a decimal string")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise RobinhoodPortfolioError(f"a position {what} is not a number") from None
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise RobinhoodPortfolioError(f"a position {what} is out of range")
    return value


def parse_position_lines(payload: Any) -> list[tuple[str, Decimal, Decimal]]:
    """Return (symbol, quantity, average buy price) for every long position in one response page."""
    data = payload.get("data") if isinstance(payload, dict) else None
    positions = data.get("positions") if isinstance(data, dict) else None
    if not isinstance(positions, list):
        raise RobinhoodPortfolioError("unexpected positions response shape")
    out: list[tuple[str, Decimal, Decimal]] = []
    for pos in positions:
        if not isinstance(pos, dict):
            raise RobinhoodPortfolioError("a position entry is not an object")
        symbol = pos.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise RobinhoodPortfolioError("a position entry has no symbol")
        if pos.get("type") != "long":
            raise RobinhoodPortfolioError(
                f"{symbol.upper()} has a position that is not a plain long; the portfolio view cannot total it")
        quantity = _decimal(pos.get("quantity"), "quantity")
        if quantity == 0:
            continue
        out.append((symbol.upper(), quantity, _decimal(pos.get("average_buy_price"), "average price")))
    return out


def last_completed_session(now: datetime) -> date:
    """Latest XNYS session whose regular close is at or before `now`."""
    if now.tzinfo is None:
        raise RobinhoodPortfolioError("'now' must be timezone-aware")
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    try:
        for _ in range(14):
            if is_trading_day(day) and regular_session_close(day) <= now:
                return day
            day -= timedelta(days=1)
    except MarketCalendarError as exc:
        raise RobinhoodPortfolioError(f"market calendar unavailable: {exc}") from None
    raise RobinhoodPortfolioError("no completed trading session found in the last 14 days")


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class RobinhoodPortfolioClient:
    def __init__(self, mcp: Any, *, now: Any = None) -> None:
        missing = {ACCOUNTS_TOOL, POSITIONS_TOOL, HISTORICALS_TOOL} - mcp.allowed_tools
        if missing:
            raise RobinhoodPortfolioError(f"the MCP client does not enable {sorted(missing)}")
        self._mcp = mcp
        self._history = RobinhoodHistoryClient(mcp)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _positions(self) -> tuple[dict[str, tuple[Decimal, Decimal]], int]:
        """symbol -> (total quantity, total cost), and the number of accounts read."""
        accounts = parse_active_accounts(self._mcp.call(ACCOUNTS_TOOL, {}))
        totals: dict[str, tuple[Decimal, Decimal]] = {}
        for number in accounts:
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(MAX_PAGES):
                args: dict[str, Any] = {"account_number": number}
                if cursor:
                    args["cursor"] = cursor
                payload = self._mcp.call(POSITIONS_TOOL, args)
                for symbol, quantity, average in parse_position_lines(payload):
                    held, cost = totals.get(symbol, (Decimal(0), Decimal(0)))
                    totals[symbol] = (held + quantity, cost + quantity * average)
                cursor = next_cursor(payload)
                if cursor is None:
                    break
                if cursor in seen:
                    raise RobinhoodPortfolioError("pagination did not advance")
                seen.add(cursor)
            else:
                raise RobinhoodPortfolioError("too many pages of positions")
        return totals, len(accounts)

    def snapshot(self) -> PortfolioSnapshot:
        now = self._now()
        session = last_completed_session(now)
        totals, accounts_checked = self._positions()
        lines: list[PortfolioLine] = []
        for symbol in sorted(totals):
            quantity, cost = totals[symbol]
            bars = self._history.get_price_history(
                symbol, start=session - timedelta(days=_PRICE_WINDOW_DAYS), end=session,
                as_of=regular_session_close(session))
            if not bars or bars[-1].session_date != session:
                raise RobinhoodPortfolioError(f"no close for {symbol} on {session.isoformat()}")
            lines.append(PortfolioLine(symbol=symbol, quantity=quantity, average_cost=cost / quantity,
                                       last_close=Decimal(str(bars[-1].close))))
        return PortfolioSnapshot(lines=tuple(lines), as_of_session=session, accounts_checked=accounts_checked)


def robinhood_portfolio_client_from_environment(env: Mapping[str, str] | None = None) -> RobinhoodPortfolioClient:
    """Build the client; raises ``McpLoginRequired`` when nobody has logged in yet."""
    return RobinhoodPortfolioClient(client_from_environment(
        ROBINHOOD, allowed_tools=[ACCOUNTS_TOOL, POSITIONS_TOOL, HISTORICALS_TOOL], env=env))
