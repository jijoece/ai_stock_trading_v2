"""Opt-in live Robinhood equity portfolio for the dashboard (read-only).

Disabled unless ``AI_STOCK_TRADING_ENABLE_ROBINHOOD_PORTFOLIO=1``: it reads real
account positions, which the rest of the dashboard never does. It calls only the
three read-only tools enabled by ``trading_research.mcp.robinhood_portfolio`` and
never displays account numbers.
"""
from __future__ import annotations

import os
from typing import Any, Callable

ENABLE_ENV = "AI_STOCK_TRADING_ENABLE_ROBINHOOD_PORTFOLIO"
MAX_ERROR_CHARS = 300


class PortfolioViewError(RuntimeError):
    """A sanitized failure that is safe to show to the user."""


def portfolio_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip() == "1"


def token_state() -> tuple[int, int, int] | None:
    """A fingerprint of the Robinhood OAuth token file (inode, size, mtime), or None when there is none.

    The page keys its cache on this, so a login, re-login or logout in another process (`mcp_login.py`)
    invalidates the cached snapshot at once instead of showing the previous session's holdings until the
    TTL expires. Only file metadata is read, never the tokens.
    """
    try:
        from trading_research.mcp.oauth_readonly_client import ROBINHOOD, default_token_path
    except ImportError:
        return None
    try:
        st = default_token_path(ROBINHOOD).stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def load_portfolio(client_factory: Callable[[], Any] | None = None):
    """Return a ``PortfolioSnapshot``; raises PortfolioViewError with a sanitized message."""
    if not portfolio_enabled():
        raise PortfolioViewError(f"The Robinhood portfolio is disabled. Set {ENABLE_ENV}=1 to enable it.")
    try:
        from trading_research.mcp.oauth_readonly_client import McpError
        from trading_research.mcp.robinhood_portfolio import robinhood_portfolio_client_from_environment
    except ImportError:
        raise PortfolioViewError("The project environment is missing the Robinhood MCP client.") from None
    try:
        return (client_factory or robinhood_portfolio_client_from_environment)().snapshot()
    except McpError as exc:  # our own messages: login hint, policy, or a validation failure; no account data
        raise PortfolioViewError(str(exc)[:MAX_ERROR_CHARS]) from None
    except Exception as exc:
        raise PortfolioViewError(f"Could not read the Robinhood portfolio ({type(exc).__name__}).") from None
