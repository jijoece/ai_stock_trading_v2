#!/usr/bin/env python3
"""
score.py
========
Converts the indicator stack into the THREE PILLARS framework
(Trend / Momentum / Macro-Sentiment, each graded -2..+2) and applies the
Agentic account decision logic:

  EXIT (total or partial): exit when bullish momentum is EXHAUSTED,
                          or when bearish momentum is RELENTLESS.
  RE-ENTRY: after exiting a bullish trend (partial or total), re-enter
            when a rebound/reversal pattern marks the start of a new
            bullish cycle.

All recommendations are framed within this exhaustion/rebound logic.
Imports indicators.py. The Macro-Sentiment score is injected (comes from
macro_pillar.py, shared by all names in the session).

Run it with any `python3`: indicators.py needs numpy and TA-Lib, so when they are
missing (or, for the fetch modes, the trading_research package) and this file is
run directly, it re-launches itself under the repository's `.venv` (see
_project_env.py). Importing the module never re-launches anything.

Two input modes:
  score.py [input.json]       offline: a JSON file or the synthetic self-test.
                              Never imports the repo's trading_research package.
  score.py --alpaca SYM ...   fetch split-adjusted daily closes from Alpaca
                              (completed sessions only), then score. Needs the
                              project .venv. ALPACA_MARKET_DATA_API_KEY / _SECRET
                              are read from the repo's .env file (which wins),
                              else from the environment. The repo's Alpaca
                              client is imported only in this mode.
  score.py --robinhood SYM... same, through the hosted Robinhood MCP (read-only
                              get_equity_historicals only, split-adjusted day
                              bars). Needs a one-time OAuth login:
                              python3 scripts/mcp_login.py login robinhood
                              Robinhood does not label bars as adjusted, so the
                              card marks the adjustment as requested, not verified.

Both fetch modes also accept:
  --macro-score auto          fetch the eight cross-asset ETFs and run macro_pillar.py
  --yield-spread fred         with auto: add the 10Y-2Y spread from FRED (T10Y2Y, public CSV,
                              no key); or --yield-spread 0.45 for a level-only number
  --check-holding             set holding from your Robinhood positions (read-only,
                              every active account; needs the Robinhood login)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import _project_env

try:
    import indicators as I
except ImportError as _exc:
    # `python3 scripts/score.py ...` under a Python without numpy/TA-Lib: re-launch under the repo's .venv.
    if __name__ == "__main__":
        _project_env.reexec_in_project_venv(__file__)  # does not return on success
        raise SystemExit(f"error: {_project_env.missing_environment_message('numpy/TA-Lib')}\n({_exc})")
    raise

import macro_pillar  # stdlib only; used by --macro-score auto
import yield_spread  # stdlib only; used by --yield-spread fred

DEFAULT_MIN_SESSIONS = 220
# Extra sessions fetched beyond --min-sessions so the EMA200 slope has room.
_HISTORY_PADDING_SESSIONS = 40
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


# --------------------------------------------------------------------------
# Pillar 1: Trend (EMA structure + price position + long-term slope)
# --------------------------------------------------------------------------

def score_trend(ind: dict) -> tuple[int, str]:
    c = ind["close"]
    e20, e50, e200 = ind["ema20"], ind["ema50"], ind["ema200"]
    s200 = ind["ema200_slope"]
    pts, bits = 0, []
    if e20 is not None:
        if c > e20: pts += 1; bits.append("price>EMA20")
        else: pts -= 1; bits.append("price<EMA20")
    if e20 is not None and e50 is not None:
        if e20 > e50: pts += 1; bits.append("EMA20>EMA50")
        else: pts -= 1; bits.append("EMA20<EMA50")
    if e50 is not None and e200 is not None:
        if e50 > e200: pts += 1; bits.append("EMA50>EMA200")
        else: pts -= 1; bits.append("EMA50<EMA200")
    if s200 is not None:
        if s200 > 0: pts += 1; bits.append("EMA200↑")
        else: pts -= 1; bits.append("EMA200↓")
    score = 2 if pts >= 3 else 1 if pts >= 1 else 0 if pts == 0 else -1 if pts >= -2 else -2
    return score, ", ".join(bits)


# --------------------------------------------------------------------------
# Pillar 2: Momentum (RSI-Wilder + MACD histogram + TRIX)
# --------------------------------------------------------------------------

def score_momentum(ind: dict) -> tuple[int, str]:
    rsi = ind["rsi14"]
    hist = ind["macd_hist"]
    trix, trix_sig = ind["trix"], ind["trix_signal"]
    pts, bits = 0, []
    if rsi is not None:
        if rsi >= 55: pts += 1; bits.append(f"RSI {rsi:.0f}≥55")
        elif rsi <= 45: pts -= 1; bits.append(f"RSI {rsi:.0f}≤45")
        else: bits.append(f"RSI {rsi:.0f} neutral")
    if hist is not None:
        if hist > 0: pts += 1; bits.append("MACD hist>0")
        else: pts -= 1; bits.append("MACD hist<0")
    if trix is not None and trix_sig is not None:
        if trix > trix_sig and trix > 0: pts += 1; bits.append("TRIX>signal>0")
        elif trix < trix_sig and trix < 0: pts -= 1; bits.append("TRIX<signal<0")
        else: bits.append("TRIX mixed")
    score = 2 if pts >= 2 else 1 if pts == 1 else 0 if pts == 0 else -1 if pts == -1 else -2
    return score, ", ".join(bits)


# --------------------------------------------------------------------------
# Decision Layer: exhaustion / relentless / rebound
# --------------------------------------------------------------------------

def _flags(ind: dict) -> dict:
    c = ind["close"]
    e20 = ind["ema20"]; e50 = ind["ema50"]; e200 = ind["ema200"]
    s200 = ind["ema200_slope"]
    rsi, rsi_p = ind["rsi14"], ind["rsi14_prev"]
    hist, hist_p = ind["macd_hist"], ind["macd_hist_prev"]
    trix, trix_sig = ind["trix"], ind["trix_signal"]
    pb = ind["percent_b"]
    stretch = (c / e20 - 1.0) if e20 else 0.0

    exhaustion, bearish, rebound = [], [], []

    # --- Bullish exhaustion ---
    if rsi is not None and rsi_p is not None and rsi >= 70 and rsi < rsi_p:
        exhaustion.append(f"RSI turning from overbought ({rsi_p:.0f}→{rsi:.0f})")
    if hist is not None and hist_p is not None and hist > 0 and hist < hist_p:
        exhaustion.append("MACD histogram shrinking in positive territory")
    if pb is not None and pb >= 1.0:
        exhaustion.append("price at/above upper Bollinger Band (%B≥1)")
    if stretch >= 0.10:
        exhaustion.append(f"price stretched {stretch*100:.0f}% above EMA20")

    # --- Relentless bearish ---
    if e50 and e200 and s200 is not None and c < e50 and e50 < e200 and s200 < 0:
        bearish.append("price<EMA50<EMA200 with EMA200↓")
    if hist is not None and hist_p is not None and hist < 0 and hist < hist_p:
        bearish.append("MACD histogram deepening in negative territory")
    if trix is not None and trix_sig is not None and trix < trix_sig and trix < 0:
        bearish.append("TRIX<signal below zero")
    if rsi is not None and rsi_p is not None and rsi < 45 and rsi < rsi_p:
        bearish.append(f"RSI weak and falling ({rsi:.0f})")

    # --- Rebound / reversal (for re-entry) ---
    if rsi is not None and rsi_p is not None and rsi_p < 35 and rsi > rsi_p:
        rebound.append(f"RSI turning from oversold ({rsi_p:.0f}→{rsi:.0f})")
    if hist is not None and hist_p is not None and hist > hist_p and hist_p < 0:
        rebound.append("MACD histogram crossing bullishly")
    # Genuine recovery of EMA20: currently above EMA20, but closed below it within last 5 bars.
    # Without a recent dip, it is a normal uptrend, not a rebound.
    bsb = ind.get("bars_since_below_ema20")
    if (e20 and c > e20 and ind["ema20_slope"] is not None and ind["ema20_slope"] > 0
            and bsb is not None and 1 <= bsb <= 5):
        rebound.append(f"price reclaims EMA20 (closed below {bsb} bar{'s' if bsb > 1 else ''} ago)")
    # Fresh TRIX cross detected on the crossover bar (not while it persists).
    trix_p, sig_p = ind["trix_prev"], ind["trix_signal_prev"]
    if (trix is not None and trix_sig is not None and trix_p is not None and sig_p is not None
            and trix > trix_sig and trix_p <= sig_p and trix <= 0):
        rebound.append("fresh bullish TRIX cross below zero")

    # Structure in a true death-cross (not proxied by trend score)
    death_cross = bool(e50 and e200 and e50 < e200 and c < e50)

    return {"exhaustion": exhaustion, "bearish": bearish, "rebound": rebound,
            "death_cross": death_cross, "stretch_pct": round(stretch * 100, 1)}


def decide(ind: dict, trend: int, mom: int, macro: Optional[int],
           holding: Optional[bool]) -> dict:
    """
    Decision cascade aligned to the Agentic account style: short-term returns
    via capital rotation. The cycle is enter on rebound → ride → exit on
    exhaustion → wait for next trigger. Accumulating positions is not the default
    (keeps capital trapped). A rebound inside a death-cross is a counter-trend
    TACTICAL opportunity (reduced size, tight stop), not a new cycle.
    """
    f = _flags(ind)
    n_exh, n_bear, n_reb = len(f["exhaustion"]), len(f["bearish"]), len(f["rebound"])
    dc = f["death_cross"]
    in_pos = holding is True  # None treated as flat (entry framing)

    # Priority cascade — separated by holding status:
    # HOLDER: exit triggers first (exhaustion, relentless).
    # FLAT:   fresh entry triggers first (lagging structural bearish flags
    #         should not overshadow a tactical rebound).
    if in_pos and n_exh >= 2:
        action = "EXIT / TRIM"
        rationale = "Bullish momentum EXHAUSTED."
        framing = ("Partial or full exit: buying momentum is dying out. "
                   "Rotate capital and flag for re-entry on the next rebound.")
    elif in_pos and (n_bear >= 3 or (dc and n_bear >= 2)):
        action = "EXIT"
        rationale = "Bearish momentum RELENTLESS."
        framing = "Exit: selling pressure is sustained. Do not average down."
        if n_reb >= 2:
            framing += (" Rebound in progress: use it to exit at a better price, "
                        "not to justify holding.")
    elif not in_pos and n_reb >= 2 and not dc:
        action = "RE-ENTRY (new cycle)"
        rationale = "Rebound/reversal with healthy EMA structure: likely start of a new bullish cycle."
        framing = ("Valid entry trigger. Confirm with candle/volume before entering "
                   "full size; stop below the rebound pivot.")
    elif not in_pos and n_reb >= 2 and dc:
        action = "TACTICAL REBOUND (counter-trend)"
        rationale = "Rebound signals within a death-cross: tactical trade, NOT a new cycle."
        framing = ("Short-term opportunity against the structure: reduced size, "
                   "close target (EMA20/EMA50 or middle band), tight stop, and quick "
                   "exit. Do not let it turn into a hold — the underlying trend remains bearish.")
        if n_bear >= 2:
            framing += " Bearish flags still active: extra tight leash."
    elif not in_pos and (n_bear >= 3 or (dc and n_bear >= 2)):
        action = "STAY OUT / AVOID"
        rationale = "Bearish momentum RELENTLESS, no fresh rebound trigger."
        framing = "Out. Watch for capitulation: the trigger would be a fresh RSI/MACD turn."
    elif trend >= 1 and mom >= 1:
        if in_pos:
            action = "HOLD (ride the cycle)"
            rationale = "Bullish cycle intact (Trend and Momentum positive)."
            framing = ("Hold and watch for exhaustion: the next expected action is "
                       "EXIT with profit, not adding to position. Accumulating is not the "
                       "default (capital rotation > large position).")
        else:
            action = "WAIT (do not chase)"
            rationale = "Healthy trend but no fresh entry trigger."
            framing = ("Entering mid-trend is chasing: poor R/R for the short term. "
                       "Wait for pullback to EMA20 and turn, or the next confirmed rebound.")
    elif trend <= -1 and mom <= -1:
        if in_pos:
            action = "HOLD (under review)"
            rationale = "Weak structure and momentum, but no full exit trigger."
            framing = ("Do not add. Prepare to exit: if more bearish flags appear or the "
                       "current rebound fizzles out, execute EXIT. If a rebound is active, "
                       "it can be used to exit at a better price.")
        else:
            action = "STAY OUT / AVOID"
            rationale = "Negative structure and momentum, no signs of turning."
            framing = "Out. The next trigger here would be a confirmed rebound (tactical trade)."
    else:
        action = "HOLD / OBSERVE" if in_pos else "OBSERVE"
        rationale = "Mixed signals; no clear exhaustion or rebound trigger."
        framing = "No action. Watch the next close."

    # Adjust for adverse macro
    if macro is not None and macro <= -1:
        if action == "HOLD (ride the cycle)":
            framing += " ⚠ Adverse macro: lower the exit threshold (take profit earlier)."
        elif action == "TACTICAL REBOUND (counter-trend)":
            framing += " ⚠ Adverse macro: reduce size further or skip this rebound."
        elif action == "RE-ENTRY (new cycle)":
            framing += " ⚠ Adverse macro: entry in reduced size."

    # Position context
    if in_pos and n_reb >= 2 and action.startswith("HOLD"):
        framing += " (Rebound signals in progress reinforce holding.)"
    if holding is False and action in ("EXIT / TRIM", "EXIT"):
        framing += " (You are flat: the exit signal only confirms not entering long.)"

    return {"action": action, "rationale": rationale, "framing": framing, "flags": f}


# --------------------------------------------------------------------------
# Full Scorecard Card
# --------------------------------------------------------------------------

def score_symbol(close: list[float], macro_score: Optional[int] = None,
                 symbol: Optional[str] = None, holding: Optional[bool] = None,
                 slope_lookback: int = 5) -> dict:
    ind = I.compute(close, slope_lookback)
    t, t_detail = score_trend(ind)
    m, m_detail = score_momentum(ind)
    dec = decide(ind, t, m, macro_score, holding)
    composite = t + m + (macro_score if macro_score is not None else 0)
    return {
        "symbol": symbol,
        "n_bars": ind["n_bars"],
        "warning": ind["warning"],
        "pillars": {
            "trend": {"score": t, "detail": t_detail},
            "momentum": {"score": m, "detail": m_detail},
            "macro_sentiment": {"score": macro_score, "detail": "injected from macro_pillar.py"},
        },
        "pillar_total": composite,
        "decision": dec,
        "indicators": I._round(ind),
    }


def render(card: dict) -> str:
    p = card["pillars"]; d = card["decision"]
    ms = p["macro_sentiment"]["score"]
    L = []
    L.append(f"{'═'*54}")
    L.append(f" {card['symbol'] or 'SYMBOL'}   ·   {card['n_bars']} bars")
    L.append(f"{'═'*54}")
    def line(name, sc, det):
        s = f"{sc:+d}" if sc is not None else " ?"
        return f"  {name:<16} {s:>3}   {det}"
    L.append(line("Trend", p["trend"]["score"], p["trend"]["detail"]))
    L.append(line("Momentum", p["momentum"]["score"], p["momentum"]["detail"]))
    L.append(line("Macro-Sentiment", ms, p["macro_sentiment"]["detail"] if ms is not None else "(not injected)"))
    L.append(f"  {'─'*50}")
    tot = card["pillar_total"]
    L.append(f"  TOTAL (-6..+6): {tot:+d}")
    L.append(f"{'─'*54}")
    L.append(f"  ► {d['action']}  —  {d['rationale']}")
    L.append(f"    {d['framing']}")
    f = d["flags"]
    if f["exhaustion"]:
        L.append(f"    exhaustion: {'; '.join(f['exhaustion'])}")
    if f["bearish"]:
        L.append(f"    bearish: {'; '.join(f['bearish'])}")
    if f["rebound"]:
        L.append(f"    rebound: {'; '.join(f['rebound'])}")
    if f.get("death_cross"):
        L.append("    structure: active death-cross (EMA50<EMA200, price<EMA50)")
    if card["warning"]:
        L.append(f"    ⚠ {card['warning']}")
    data = card.get("data")
    if data:
        label = " ".join(str(x) for x in (data["provider"], data["feed"], data["adjustment"]) if x)
        trust = "" if data.get("adjustment_verified", True) else " [adjustment requested, not verifiable]"
        L.append(f"    data: {label}, "
                 f"{data['first_session']}..{data['last_session']} ({data['bars']} bars){trust}")
        if data.get("holding_source"):
            state = "HOLDING" if data["holding"] else "not holding"
            L.append(f"    position: {state} (read-only check of {data['accounts_checked']} active "
                     f"Robinhood account(s))")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Alpaca fetch path (--alpaca only). The repo's client is imported lazily so the
# offline modes keep using only the standard library.
# --------------------------------------------------------------------------

class ScoreError(Exception):
    """A per-symbol failure (fetch or data validation); the run continues."""


class SessionNotPublished(ScoreError):
    """The as-of session's final bar is not available yet (a retryable condition, unlike bad data)."""


class ScoreConfigError(Exception):
    """A run-level failure (credentials, environment, calendar); the run stops."""


class SessionNotPublishedError(ScoreConfigError):
    """Run-level form of SessionNotPublished: every macro input failed only because the session is unpublished."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"  # the repo's git-ignored env file


def _alpaca_env(key_names: tuple[str, ...]) -> dict[str, str]:
    """The process environment, with `key_names` taken from the repo's `.env` file when it defines them.

    The file wins for these keys (you asked for the keys to always come from it); the process environment is
    the fallback. The file is parsed in place and nothing is exported into this process or printed.
    """
    env = dict(os.environ)
    try:
        from dotenv import dotenv_values
        if _ENV_FILE.is_file():
            values = dotenv_values(_ENV_FILE)
            for name in key_names:
                value = values.get(name)
                if value:
                    env[name] = value
    except (ImportError, OSError):
        pass  # no python-dotenv or an unreadable file: fall back to the process environment
    return env


def _alpaca_client(feed: str):
    """Build the repo's rate-limited Alpaca market-data client (credentials read there only)."""
    try:
        from trading_research.backtesting.alpaca_history import (
            API_KEY_ENV, API_SECRET_ENV, alpaca_client_from_environment)
        from trading_research.backtesting.models import BacktestError
    except ImportError:
        if __name__ == "__main__":
            _project_env.reexec_in_project_venv(__file__)  # does not return on success
        raise ScoreConfigError(
            "--alpaca needs the project environment (trading_research is not importable); "
            "run with .venv/bin/python scripts/score.py --alpaca ..."
        ) from None
    try:
        return alpaca_client_from_environment(_alpaca_env((API_KEY_ENV, API_SECRET_ENV)), feed=feed)
    except BacktestError:
        raise ScoreConfigError(
            f"--alpaca requires {API_KEY_ENV} and {API_SECRET_ENV}: set them in {_ENV_FILE} "
            "or in the environment"
        ) from None


def _robinhood_client():
    """Build the read-only Robinhood MCP history client (needs a prior one-time OAuth login)."""
    try:
        from trading_research.mcp.oauth_readonly_client import McpError
        from trading_research.mcp.robinhood_history import robinhood_client_from_environment
    except ImportError:
        if __name__ == "__main__":
            _project_env.reexec_in_project_venv(__file__)  # does not return on success
        raise ScoreConfigError(
            "--robinhood needs the project environment (trading_research is not importable); "
            "run with .venv/bin/python scripts/score.py --robinhood ..."
        ) from None
    try:
        return robinhood_client_from_environment()
    except McpError as exc:  # our own messages: login hint or token-file permissions
        raise ScoreConfigError(str(exc)) from None


def _last_completed_session(now: datetime) -> date:
    """Latest XNYS session whose regular close (early closes included) is at or before `now`."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    try:
        from trading_research.evaluation.market_calendar import (
            MarketCalendarError, is_trading_day, regular_session_close)
    except ImportError:
        raise ScoreConfigError("trading_research market calendar is not importable") from None
    if now.tzinfo is None:
        raise ScoreConfigError("'now' must be timezone-aware")
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    try:
        for _ in range(14):
            if is_trading_day(day) and regular_session_close(day) <= now:
                return day
            day -= timedelta(days=1)
    except MarketCalendarError as exc:
        raise ScoreConfigError(f"market calendar unavailable: {exc}") from None
    raise ScoreConfigError("no completed trading session found in the last 14 days")


def _previous_session(day: date) -> date:
    """The trading session before `day`."""
    from datetime import timedelta
    try:
        from trading_research.evaluation.market_calendar import MarketCalendarError, is_trading_day
    except ImportError:
        raise ScoreConfigError("trading_research market calendar is not importable") from None
    try:
        prior = day - timedelta(days=1)
        for _ in range(14):
            if is_trading_day(prior):
                return prior
            prior -= timedelta(days=1)
    except MarketCalendarError as exc:
        raise ScoreConfigError(f"market calendar unavailable: {exc}") from None
    raise ScoreConfigError(f"no trading session found in the 14 days before {day}")


def _check_as_of_session(day: date, last_completed: date) -> None:
    from trading_research.evaluation.market_calendar import MarketCalendarError, is_trading_day
    try:
        trading = is_trading_day(day)
    except MarketCalendarError as exc:
        raise ScoreConfigError(f"market calendar unavailable: {exc}") from None
    if not trading:
        raise ScoreConfigError(f"--as-of {day} is not a trading session")
    if day > last_completed:
        raise ScoreConfigError(
            f"--as-of {day} has not completed yet (last completed session: {last_completed})")


def _fetch_error_message(exc: Exception) -> str:
    """Our own `trading_research.mcp` errors carry fixed, token-free text (validation messages, login hints), so
    they are shown. Anything else is reduced to its type name: provider text may echo credentials."""
    if type(exc).__module__.startswith("trading_research.mcp."):
        return f"market data fetch failed: {exc}"
    return f"market data fetch failed ({type(exc).__name__})"


def fetch_closes(client, symbol: str, *, as_of_session: date, adjustment: str,
                 min_sessions: int, provider: Optional[str] = None) -> dict:
    """Fetch daily closes up to and including `as_of_session`; fails closed on any doubt.

    `provider` is the label every bar must carry (default: the Alpaca snapshot label).
    """
    from trading_research.backtesting.alpaca_history import SNAPSHOT_PROVIDER, warmup_start
    expected_provider = provider or SNAPSHOT_PROVIDER
    from trading_research.evaluation.market_calendar import MarketCalendarError, regular_session_close
    try:
        as_of = regular_session_close(as_of_session)
        start = warmup_start(as_of_session, sessions=min_sessions + _HISTORY_PADDING_SESSIONS)
        raw = client.get_price_history(
            symbol, start=start, end=as_of_session, as_of=as_of, adjustment=adjustment)
    except MarketCalendarError as exc:
        raise ScoreConfigError(f"market calendar unavailable: {exc}") from None
    except Exception as exc:
        if getattr(exc, "session_not_published", False):  # set by the repo's own provider errors
            raise SessionNotPublished(_fetch_error_message(exc)) from None
        raise ScoreError(_fetch_error_message(exc)) from None
    bars = [b for b in raw if b.session_date <= as_of_session]  # safeguard; client already drops later bars
    if any(b.symbol != symbol for b in bars):
        raise ScoreError("provider returned bars for a different symbol")
    if any(b.provider != expected_provider for b in bars):
        raise ScoreError(f"provider returned bars not labelled {expected_provider!r}")
    if any(not b.adjusted for b in bars):  # split/all are both adjusted; raw is never accepted
        raise ScoreError(f"bars are not adjusted, contradicting requested adjustment {adjustment!r}")
    dates = [b.session_date for b in bars]
    if dates != sorted(set(dates)):
        raise ScoreError("bars are not strictly ordered by session date")
    if len(bars) < min_sessions:
        raise ScoreError(f"insufficient history: {len(bars)} sessions, need at least {min_sessions}")
    if dates[-1] != as_of_session:
        raise SessionNotPublished(
            f"history ends {dates[-1]}, not on the as-of session {as_of_session}; "
            "the final session's bar is missing")
    return {
        "closes": [float(b.close) for b in bars],
        "first_session": dates[0].isoformat(),
        "last_session": dates[-1].isoformat(),
        "bars": len(bars),
        "provider": expected_provider,
    }


def _normalize_symbols(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low.endswith(".json") or "/" in tok or "\\" in tok:
            raise ValueError(f"{tok!r} looks like a file; --alpaca cannot be combined with an input file")
        sym = tok.strip().upper()
        if not _SYMBOL_RE.match(sym):
            raise ValueError(f"invalid symbol {tok!r}")
        if sym not in out:
            out.append(sym)
    return out


MACRO_SYMBOLS = ("SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP")


def _robinhood_holdings():
    """Totals of long equity positions across every active Robinhood account (read-only)."""
    try:
        from trading_research.mcp.oauth_readonly_client import McpError
        from trading_research.mcp.robinhood_holdings import robinhood_holdings_client_from_environment
    except ImportError:
        if __name__ == "__main__":
            _project_env.reexec_in_project_venv(__file__)  # does not return on success
        raise ScoreConfigError(
            "--check-holding needs the project environment (trading_research is not importable); "
            "run with .venv/bin/python scripts/score.py ..."
        ) from None
    try:
        return robinhood_holdings_client_from_environment().snapshot()
    except McpError as exc:  # our own messages; never contains an account number or a position
        raise ScoreConfigError(f"--check-holding could not read Robinhood positions: {exc}") from None


def _compute_macro(client, as_of_session: date, args, provider: Optional[str]) -> dict:
    """Run macro_pillar.py on the eight cross-asset ETFs fetched from the same source and session."""
    series: dict[str, list[float]] = {}
    failed: dict[str, list[str]] = {}  # message -> symbols, so one shared cause is printed once
    only_unpublished = True
    for etf in MACRO_SYMBOLS:
        try:
            series[etf] = fetch_closes(client, etf, as_of_session=as_of_session, adjustment=args.adjustment,
                                       min_sessions=args.min_sessions, provider=provider)["closes"]
        except ScoreError as exc:
            failed.setdefault(str(exc), []).append(etf)
            only_unpublished = only_unpublished and isinstance(exc, SessionNotPublished)
    if failed:  # a silently degraded macro score would be worse than none
        causes = "; ".join(f"{', '.join(syms)}: {msg}" for msg, syms in failed.items())
        error_type = SessionNotPublishedError if only_unpublished else ScoreConfigError
        raise error_type("--macro-score auto needs all of " + ", ".join(MACRO_SYMBOLS)
                         + "; failed: " + causes)
    data: dict = {"series": series, "as_of": as_of_session.isoformat()}
    yield_note = "not provided; its 20% weight is redistributed"
    yield_info: Optional[dict] = None
    if args.yield_spread == "fred":
        try:  # the session actually being scored, so a fallback session gets its own data
            fred = yield_spread.fetch_fred_yield_spread(as_of_session)
        except yield_spread.YieldSpreadError as exc:
            raise ScoreConfigError(f"--yield-spread fred failed: {exc}") from None
        data["yield_spread"] = fred.values
        yield_note = f"{fred.summary()} (level and slope)"
        yield_info = {"series": fred.series_id, "observations": len(fred.values),
                      "first_observation": fred.first_date.isoformat(),
                      "last_observation": fred.last_date.isoformat(), "latest": fred.values[-1]}
    elif args.yield_spread is not None:
        data["yield_spread"] = args.yield_spread
        yield_note = "included (level only)"
    try:
        res = macro_pillar.score_macro(data)
    except (macro_pillar.MarketDataShapeError, ValueError) as exc:
        raise ScoreConfigError(f"--macro-score auto failed in macro_pillar.py: {exc}") from None
    macro = {
        "score": res.pillar_score, "label": res.pillar_label, "regime": res.regime,
        "composite": res.composite, "notes": list(res.notes), "symbols": list(MACRO_SYMBOLS),
        "yield_spread": yield_note,
    }
    if yield_info is not None:
        macro["yield_spread_source"] = yield_info
    return macro


def _position_error_message(exc: Exception) -> str:
    if type(exc).__module__.startswith("trading_research.mcp."):  # our own fixed, account-free text
        return f"position check failed: {exc}"
    return f"position check failed ({type(exc).__name__})"


def _attempt(client, provider, feed, adjustment_verified, args, symbols, as_of_session, now, holdings):
    """One full pass at one session: macro first, then every symbol. Writes nothing to disk.

    Returns (results, errors, context, unpublished, saved) where `unpublished` says some symbol failed only
    because the session's final bar is not available yet, and `saved` holds the --save-input payloads.
    Raises ScoreConfigError for run-level failures (SessionNotPublishedError when macro hit only that).
    """
    context: dict = {}
    macro_score = args.macro_score
    if args.macro_score == "auto":
        context["macro"] = _compute_macro(client, as_of_session, args, provider)
        macro_score = context["macro"]["score"]

    results: list[dict] = []
    errors: list[dict] = []
    saved: list[tuple[str, dict]] = []
    unpublished = False
    for sym in symbols:
        try:
            got = fetch_closes(client, sym, as_of_session=as_of_session,
                               adjustment=args.adjustment, min_sessions=args.min_sessions,
                               provider=provider)
            try:
                holding = holdings.holds(sym) if holdings is not None else args.holding
            except Exception as exc:  # e.g. a short position: blocks this symbol only
                raise ScoreError(_position_error_message(exc)) from None
            card = score_symbol(got["closes"], macro_score=macro_score, symbol=sym, holding=holding)
            card["data"] = {
                "provider": got["provider"], "feed": feed, "adjustment": args.adjustment,
                # Alpaca bars carry an `adjusted` flag that is checked; Robinhood's response has none,
                # so the adjustment is requested and trusted, not verified.
                "adjustment_verified": adjustment_verified,
                "first_session": got["first_session"], "last_session": got["last_session"],
                "bars": got["bars"], "fetched_at": now.isoformat(),
            }
            if holdings is not None:  # counts only: no account number or quantity is ever recorded
                card["data"]["holding_source"] = "robinhood-mcp"
                card["data"]["holding"] = holding
                card["data"]["accounts_checked"] = holdings.accounts_checked
            if "macro" in context:
                card["data"]["macro_source"] = "macro_pillar.py (auto)"
            saved.append((sym, {"symbol": sym, "close": got["closes"], "macro_score": macro_score,
                                "holding": holding, "data": card["data"]}))
            results.append(card)
        except SessionNotPublished as exc:
            unpublished = True
            errors.append({"symbol": sym, "error": str(exc)})
        except ScoreError as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return results, errors, context, unpublished, saved


def _score_remote(args, symbols: list[str], source: str) -> tuple[date, list[dict], list[dict], dict]:
    now = _utc_now()
    as_of_session = _last_completed_session(now)
    if args.as_of:
        requested = date.fromisoformat(args.as_of)
        _check_as_of_session(requested, as_of_session)
        as_of_session = requested
    if source == "robinhood":
        client, provider, feed, adjustment_verified = _robinhood_client(), "robinhood-mcp", None, False
    else:
        client, provider, feed, adjustment_verified = _alpaca_client(args.feed), None, args.feed, True

    # Resolved once, before any price is fetched: a failure stops the run rather than scoring with a
    # position that was asked for but not obtained. (It does not depend on the session.)
    holdings = _robinhood_holdings() if args.check_holding else None

    fallback: Optional[dict] = None
    try:
        results, errors, context, unpublished, saved = _attempt(
            client, provider, feed, adjustment_verified, args, symbols, as_of_session, now, holdings)
    except SessionNotPublishedError:
        if not args.fallback_previous_session:
            raise
        results, errors, context, unpublished, saved = [], [], {}, True, []
    if unpublished and args.fallback_previous_session:
        # One explicit step back, for everything at once so macro and every symbol share a session.
        prior = _previous_session(as_of_session)
        fallback = {"requested_session": as_of_session.isoformat(), "used_session": prior.isoformat(),
                    "reason": "the requested session's final daily bar is not published yet"}
        as_of_session = prior
        results, errors, context, _, saved = _attempt(
            client, provider, feed, adjustment_verified, args, symbols, as_of_session, now, holdings)
        context["fallback"] = fallback

    if args.save_input:  # only the pass that is actually used is written
        save_dir = Path(args.save_input)
        save_dir.mkdir(parents=True, exist_ok=True)
        for sym, payload in saved:
            (save_dir / f"{sym}.json").write_text(json.dumps(payload, indent=2) + "\n")
    return as_of_session, results, errors, context


def _macro_arg(text: str):
    if text == "auto":
        return "auto"
    try:
        value = int(text)
    except ValueError:
        value = None
    if value is None or not -2 <= value <= 2:
        raise argparse.ArgumentTypeError(f"{text!r}: must be an integer from -2 to +2, or 'auto'")
    return value


def _yield_arg(text: str):
    if text.lower() == "fred":
        return "fred"
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is None or value != value or abs(value) > 10:  # NaN or implausible (percentage points)
        raise argparse.ArgumentTypeError(f"{text!r}: must be 'fred' or a number of percentage points (|x| <= 10)")
    return value


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Three pillars scoring + exit/re-entry decision.")
    ap.add_argument("input", nargs="?", help="JSON: {symbol, close:[...], macro_score?, holding?}. Without file: self-test.")
    ap.add_argument("--json", action="store_true")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--alpaca", nargs="+", metavar="SYMBOL",
                     help="fetch daily closes from Alpaca for these symbols, then score (needs the project .venv)")
    src.add_argument("--robinhood", nargs="+", metavar="SYMBOL",
                     help="fetch daily closes from the Robinhood MCP (read-only, split-adjusted; needs the "
                          "project .venv and a prior `python3 scripts/mcp_login.py login robinhood`)")
    ap.add_argument("--adjustment", choices=("split", "all"), default=None, help="default split; raw is not accepted")
    ap.add_argument("--feed", choices=("iex", "sip"), default=None, help="default iex")
    ap.add_argument("--macro-score", type=_macro_arg, metavar="N|auto", default=None,
                    help="macro pillar: an integer -2..+2, or 'auto' to fetch SPY/RSP/IWM/HYG/LQD/TLT/XLY/XLP "
                         "from the same source and run macro_pillar.py (empty if not given)")
    ap.add_argument("--yield-spread", type=_yield_arg, metavar="PCT|fred", default=None,
                    help="10Y-2Y spread for --macro-score auto: 'fred' downloads the last 60 observations of "
                         "FRED series T10Y2Y (public, no key; level and slope), or a number gives the latest "
                         "level in percentage points (level only). Without it that component's 20%% weight "
                         "is redistributed")
    hold = ap.add_mutually_exclusive_group()
    hold.add_argument("--holding", dest="holding", action="store_const", const=True, default=None)
    hold.add_argument("--not-holding", dest="holding", action="store_const", const=False)
    hold.add_argument("--check-holding", dest="check_holding", action="store_const", const=True, default=None,
                      help="read your long positions across all active Robinhood accounts (read-only, needs the "
                           "Robinhood login) and set holding from them; works with --alpaca or --robinhood")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD", default=None, help="default: last completed session")
    ap.add_argument("--fallback-previous-session", dest="fallback_previous_session", action="store_const",
                    const=True, default=None,
                    help="if the latest session's final daily bar is not published yet (Robinhood publishes it "
                         "late), score the previous session instead, for every symbol at once, and say so; "
                         "one step back only, and not with --as-of")
    ap.add_argument("--min-sessions", type=int, default=None, help=f"default {DEFAULT_MIN_SESSIONS}")
    ap.add_argument("--save-input", metavar="DIR", default=None,
                    help="write each symbol's fetched JSON (replayable as file input)")
    return ap


_ALPACA_ONLY = ("adjustment", "feed", "macro_score", "yield_spread", "holding", "check_holding", "as_of",
                "fallback_previous_session", "min_sessions", "save_input")


def _render_fallback(fallback: dict) -> str:
    return (f"NOTE: scored the previous session {fallback['used_session']}, not {fallback['requested_session']}: "
            f"{fallback['reason']}.")


def _render_macro(macro: dict) -> str:
    lines = [f"MACRO (auto, macro_pillar.py): {macro['score']:+d} {macro['label']} — regime {macro['regime']}, "
             f"composite {macro['composite']:+.3f}",
             f"  yield curve 10Y-2Y: {macro['yield_spread']}"]
    lines += [f"  note: {n}" for n in macro["notes"]]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.alpaca or args.robinhood:
        source = "robinhood" if args.robinhood else "alpaca"
        if args.input:
            ap.error(f"--{source} cannot be combined with an input file")
        if source == "robinhood":
            if args.feed is not None:
                ap.error("--feed does not apply to --robinhood")
            if args.adjustment not in (None, "split"):
                ap.error("--robinhood supports only --adjustment split for daily bars")
        try:
            symbols = _normalize_symbols(args.alpaca or args.robinhood)
            if args.as_of:
                date.fromisoformat(args.as_of)
        except ValueError as exc:
            ap.error(str(exc))
        args.adjustment = args.adjustment or "split"
        args.feed = None if source == "robinhood" else (args.feed or "iex")
        if args.min_sessions is None:
            args.min_sessions = DEFAULT_MIN_SESSIONS
        if args.min_sessions < 1:
            ap.error("--min-sessions must be at least 1")
        if args.yield_spread is not None and args.macro_score != "auto":
            ap.error("--yield-spread only applies with --macro-score auto")
        if args.fallback_previous_session and args.as_of:
            ap.error("--fallback-previous-session cannot be combined with --as-of")
        try:
            as_of_session, results, errors, context = _score_remote(args, symbols, source)
        except ScoreConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"as_of": as_of_session.isoformat(), **context, "results": results,
                              "errors": errors}, indent=2, ensure_ascii=False))
        else:
            blocks = [render(card) for card in results]
            if "macro" in context:
                blocks.insert(0, _render_macro(context["macro"]))
            if "fallback" in context:
                blocks.insert(0, _render_fallback(context["fallback"]))
            if errors:
                blocks.append("FAILED:\n" + "\n".join(f"  {e['symbol']}: {e['error']}" for e in errors))
            print("\n\n".join(blocks))
        if not errors:
            return 0
        return 2 if results else 1

    given = [f"--{n.replace('_', '-')}" for n in _ALPACA_ONLY if getattr(args, n) is not None]
    if given:
        ap.error(f"{', '.join(given)} only apply with --alpaca or --robinhood")

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
        card = score_symbol(
            [float(x) for x in raw["close"]],
            macro_score=raw.get("macro_score"),
            symbol=raw.get("symbol"),
            holding=raw.get("holding"),
        )
    else:
        import math
        # bullish series stretching toward a ceiling (should trigger exhaustion)
        close = [round(100 + i * 0.25 + 6 * math.sin(i / 12), 2) for i in range(260)]
        close += [close[-1] * 1.05, close[-1] * 1.10]  # final spike
        card = score_symbol(close, macro_score=1, symbol="SELFTEST", holding=True)
        print("[synthetic self-test]\n", file=sys.stderr)

    print(json.dumps(card, indent=2, ensure_ascii=False) if args.json else render(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
