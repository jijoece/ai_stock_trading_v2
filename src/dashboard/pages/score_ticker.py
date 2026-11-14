"""Opt-in on-demand ticker scoring (read-only market data; no orders)."""
from __future__ import annotations

import streamlit as st

from dashboard.services.scoring_service import (
    ENABLE_ENV,
    MAX_SYMBOLS,
    ScoringError,
    ScoringOutcome,
    parse_symbols,
    run_scoring,
    scoring_enabled,
)

_STATE_KEY = "score-ticker-outcome"
_HOLDING_OPTIONS = {"Unknown": "unknown", "Holding": "holding", "Not holding": "not_holding"}
_SOURCES = {"Alpaca (market data)": "alpaca", "Robinhood (read-only MCP)": "robinhood"}


def _signed(value: object) -> str:
    return f"{value:+d}" if isinstance(value, int) else "?"


def _render_card(card: dict) -> None:
    pillars = card.get("pillars", {})
    decision = card.get("decision", {})
    st.subheader(f"{card.get('symbol') or 'Symbol'} · {decision.get('action', 'n/a')}")
    columns = st.columns(4)
    columns[0].metric("Trend", _signed(pillars.get("trend", {}).get("score")))
    columns[1].metric("Momentum", _signed(pillars.get("momentum", {}).get("score")))
    columns[2].metric("Macro", _signed(pillars.get("macro_sentiment", {}).get("score")))
    columns[3].metric("Total (-6..+6)", _signed(card.get("pillar_total")))
    if decision.get("rationale"):
        st.write(decision["rationale"])
    if decision.get("framing"):
        st.caption(decision["framing"])
    flags = decision.get("flags", {})
    for label, key in (("Exhaustion", "exhaustion"), ("Bearish", "bearish"), ("Rebound", "rebound")):
        if flags.get(key):
            st.write(f"**{label}:** {'; '.join(flags[key])}")
    if card.get("warning"):
        st.warning(card["warning"])
    with st.expander("Pillar detail and data"):
        st.write({
            "Trend": pillars.get("trend", {}).get("detail"),
            "Momentum": pillars.get("momentum", {}).get("detail"),
            "Bars": card.get("n_bars"),
        })
        data = card.get("data") or {}
        if data:
            note = "" if data.get("adjustment_verified", True) else " (adjustment requested, not verified)"
            st.caption(
                f"{data.get('provider')} {data.get('feed') or ''} {data.get('adjustment')}"
                f" · {data.get('first_session')} to {data.get('last_session')}{note}"
            )


def _render_outcome(outcome: ScoringOutcome) -> None:
    if outcome.as_of:
        st.caption(f"As of last completed session: {outcome.as_of}")
    if outcome.macro:
        macro = outcome.macro
        st.info(
            f"Macro pillar: {macro.get('score', 0):+d} {macro.get('label', '')} "
            f"(regime {macro.get('regime', 'n/a')})"
        )
    for card in outcome.cards:
        _render_card(card)
    for symbol, message in outcome.errors:
        st.error(f"{symbol}: {message}")


def render() -> None:
    st.title("Score a Ticker")
    st.caption(
        "Runs scripts/score.py on demand using read-only market data. "
        "No orders, approvals or account positions are involved. "
        "Research output, not financial advice."
    )
    if not scoring_enabled():
        st.info(f"On-demand scoring is disabled. Set {ENABLE_ENV}=1 and restart the dashboard to enable it.")
        return

    with st.form("score-ticker-form"):
        raw = st.text_input("Ticker symbols", placeholder=f"NVDA, AAPL (up to {MAX_SYMBOLS})")
        source = st.radio("Data source", list(_SOURCES), horizontal=True)
        holding = st.radio("Do you hold it?", list(_HOLDING_OPTIONS), horizontal=True)
        macro = st.checkbox("Include automatic macro pillar (fetches 8 more ETFs)", value=True)
        submitted = st.form_submit_button("Score")

    if submitted:
        try:
            symbols = parse_symbols(raw)
            with st.spinner("Fetching data and scoring..."):
                st.session_state[_STATE_KEY] = run_scoring(
                    symbols,
                    _SOURCES[source],  # type: ignore[arg-type]
                    holding=_HOLDING_OPTIONS[holding],  # type: ignore[arg-type]
                    macro_auto=macro,
                )
        except ScoringError as exc:
            st.session_state.pop(_STATE_KEY, None)
            st.error(str(exc))

    outcome = st.session_state.get(_STATE_KEY)
    if outcome is not None:
        _render_outcome(outcome)
