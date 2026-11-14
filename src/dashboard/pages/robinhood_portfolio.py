"""Opt-in read-only Robinhood equity portfolio, valued at the last completed close."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from dashboard.services.robinhood_portfolio_service import (
    ENABLE_ENV,
    PortfolioViewError,
    load_portfolio,
    portfolio_enabled,
    token_state,
)


@st.cache_data(ttl=300, show_spinner=False)
def _load(token_fingerprint: tuple[int, int, int] | None):
    # `token_fingerprint` is only the cache key (no leading underscore, so Streamlit hashes it): a login or
    # logout changes the token file, which forces a fresh read instead of the previous session's snapshot.
    del token_fingerprint
    return load_portfolio()


def _money(value: Decimal) -> str:
    return f"${value:,.2f}" if value >= 0 else f"-${-value:,.2f}"


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def render() -> None:
    st.title("Robinhood Portfolio")
    st.caption(
        "Read-only. Long equity positions across your active accounts, valued at the last "
        "completed close (not live quotes). Cash, options and crypto are not included. "
        "Research output, not financial advice."
    )
    if not portfolio_enabled():
        st.info(f"The Robinhood portfolio is disabled. Set {ENABLE_ENV}=1 and restart the dashboard to enable it.")
        return
    if st.button("Refresh", key="refresh-robinhood-portfolio"):
        _load.clear()
    try:
        with st.spinner("Reading positions and prices..."):
            snapshot = _load(token_state())
    except PortfolioViewError as exc:
        st.error(str(exc))
        return

    st.caption(f"Prices as of the {snapshot.as_of_session.isoformat()} close · "
               f"{snapshot.accounts_checked} active account(s)")
    columns = st.columns(3)
    columns[0].metric("Market value", _money(snapshot.market_value))
    columns[1].metric("Cost basis", _money(snapshot.cost_basis))
    columns[2].metric("Unrealized P/L", _money(snapshot.unrealized_pnl))
    if snapshot.unpriced:
        held = "; ".join(f"{item.symbol} ({item.quantity.normalize():f} shares, cost basis {_money(item.cost_basis)})"
                         for item in snapshot.unpriced)
        st.warning(f"Not priced and excluded from every total above: {held}. Robinhood reports these as "
                   "inactive instruments (delisted or bankrupt), so no price history exists.")
    if not snapshot.lines:
        st.write("No open long equity positions." if not snapshot.unpriced else "No priced equity positions.")
        return
    total = snapshot.market_value
    st.dataframe(
        pd.DataFrame([{
            "Symbol": line.symbol,
            "Quantity": f"{line.quantity.normalize():f}",
            "Avg cost": _money(line.average_cost),
            "Last close": _money(line.last_close),
            "Value": _money(line.market_value),
            "P/L": _money(line.unrealized_pnl),
            "P/L %": _pct(line.unrealized_pnl_pct),
            "Weight": f"{(line.market_value / total * 100) if total else Decimal(0):.1f}%",
        } for line in sorted(snapshot.lines, key=lambda item: item.market_value, reverse=True)]),
        hide_index=True,
        use_container_width=True,
    )
