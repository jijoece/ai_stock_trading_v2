"""Read-only "do I hold this symbol?" check against every active Robinhood account.

Used by ``scripts/score.py --check-holding``. Domain layer over the generic
:mod:`oauth_readonly_client`: it enables exactly two tools, ``get_accounts`` and
``get_equity_positions`` (both on the read-only allowlist), lists the active
accounts, and totals long equity quantity per symbol across all of them.

Privacy and safety:

* Account numbers stay inside this module. The result carries only per-symbol
  totals and a count of accounts checked; neither errors nor logs contain an
  account number or a position.
* Fail closed: if any active account's positions cannot be read or parsed, the
  whole check raises. It never reports "not holding" from a partial view.
* Only ``long`` positions are understood. A non-long position (a short, or a type
  never seen before) is not interpreted: it blocks the position check for that one
  symbol, with a clear message, and has no effect on any other symbol.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .oauth_readonly_client import ROBINHOOD, McpError, ReadOnlyOAuthMcpClient, client_from_environment

ACCOUNTS_TOOL = "get_accounts"
POSITIONS_TOOL = "get_equity_positions"
MAX_PAGES = 20

__all__ = ["HoldingsSnapshot", "RobinhoodHoldingsClient", "RobinhoodHoldingsError",
           "robinhood_holdings_client_from_environment"]


class RobinhoodHoldingsError(McpError):
    """The Robinhood MCP could not supply a trustworthy view of positions."""


@dataclass(frozen=True)
class HoldingsSnapshot:
    quantities: Mapping[str, Decimal]  # symbol -> total long quantity across accounts
    accounts_checked: int
    # Symbols with a non-long position (e.g. a short) in any account. Unrelated symbols are unaffected;
    # asking about one of these raises, because "holding" is only defined for long positions.
    unsupported: frozenset[str] = frozenset()

    def holds(self, symbol: str) -> bool:
        sym = symbol.upper()
        if sym in self.unsupported:
            raise RobinhoodHoldingsError(
                f"{sym} has a position that is not a plain long (for example a short) in a Robinhood account; "
                "the scoring model only handles long positions, so its position cannot be determined")
        return self.quantities.get(sym, Decimal(0)) > 0


# --------------------------------------------------------------------------
# Response parsing (pure)
# --------------------------------------------------------------------------

def parse_active_accounts(payload: Any) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        raise RobinhoodHoldingsError("unexpected accounts response shape")
    active: list[str] = []
    for acct in accounts:
        number = acct.get("account_number") if isinstance(acct, dict) else None
        if not isinstance(number, str) or not number:
            raise RobinhoodHoldingsError("an account entry has no account_number")
        flags = (acct.get("state"), acct.get("deactivated"), acct.get("permanently_deactivated"))
        if flags == ("active", False, False):
            active.append(number)
    if not active:
        raise RobinhoodHoldingsError("no active Robinhood account found")
    return active


def _quantity(raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise RobinhoodHoldingsError("a position quantity is not a decimal string")
    try:
        qty = Decimal(str(raw))
    except InvalidOperation:
        raise RobinhoodHoldingsError("a position quantity is not a number") from None
    if not qty.is_finite() or qty < 0:
        raise RobinhoodHoldingsError("a position quantity is not a non-negative number")
    return qty


def parse_positions(payload: Any) -> tuple[dict[str, Decimal], set[str]]:
    """Return (long quantities by symbol, symbols whose position is not a plain long).

    A non-long entry (a short, or a type this code has never seen) is not interpreted: its quantity is not read
    and only its symbol is recorded, so it can block that one symbol and nothing else.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    positions = data.get("positions") if isinstance(data, dict) else None
    if not isinstance(positions, list):
        raise RobinhoodHoldingsError("unexpected positions response shape")
    longs: dict[str, Decimal] = {}
    unsupported: set[str] = set()
    for pos in positions:
        if not isinstance(pos, dict):
            raise RobinhoodHoldingsError("a position entry is not an object")
        symbol = pos.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise RobinhoodHoldingsError("a position entry has no symbol")
        if pos.get("type") != "long":
            unsupported.add(symbol.upper())
            continue
        longs[symbol.upper()] = longs.get(symbol.upper(), Decimal(0)) + _quantity(pos.get("quantity"))
    return longs, unsupported


def next_cursor(payload: Any) -> str | None:
    """The pagination cursor, if the response carries one (`next`, in `data` or at top level)."""
    if not isinstance(payload, dict):
        return None
    for holder in (payload.get("data"), payload):
        if isinstance(holder, dict) and holder.get("next"):
            cursor = holder["next"]
            if not isinstance(cursor, str):
                raise RobinhoodHoldingsError("pagination cursor is not a string")
            return cursor
    return None


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class RobinhoodHoldingsClient:
    def __init__(self, mcp: ReadOnlyOAuthMcpClient) -> None:
        missing = {ACCOUNTS_TOOL, POSITIONS_TOOL} - mcp.allowed_tools
        if missing:
            raise RobinhoodHoldingsError(f"the MCP client does not enable {sorted(missing)}")
        self._mcp = mcp

    def snapshot(self) -> HoldingsSnapshot:
        """Total long quantity per symbol across every active account (all pages)."""
        accounts = parse_active_accounts(self._mcp.call(ACCOUNTS_TOOL, {}))
        totals: dict[str, Decimal] = {}
        unsupported: set[str] = set()
        for number in accounts:
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(MAX_PAGES):
                args: dict[str, Any] = {"account_number": number}
                if cursor:
                    args["cursor"] = cursor
                payload = self._mcp.call(POSITIONS_TOOL, args)
                longs, other = parse_positions(payload)
                for symbol, qty in longs.items():
                    totals[symbol] = totals.get(symbol, Decimal(0)) + qty
                unsupported |= other
                cursor = next_cursor(payload)
                if cursor is None:
                    break
                if cursor in seen:
                    raise RobinhoodHoldingsError("pagination did not advance")
                seen.add(cursor)
            else:
                raise RobinhoodHoldingsError("too many pages of positions")
        return HoldingsSnapshot(quantities=totals, accounts_checked=len(accounts),
                                unsupported=frozenset(unsupported))


def robinhood_holdings_client_from_environment(env: Mapping[str, str] | None = None) -> RobinhoodHoldingsClient:
    """Build the client; raises ``McpLoginRequired`` when nobody has logged in yet."""
    return RobinhoodHoldingsClient(client_from_environment(
        ROBINHOOD, allowed_tools=[ACCOUNTS_TOOL, POSITIONS_TOOL], env=env))
