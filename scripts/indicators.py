#!/usr/bin/env python3
"""
indicators.py
=============
DETERMINISTIC indicator engine for the trading desk's exact stack:
  EMA 20/50/200 · RSI-14 (Wilder) · MACD 12/26/9 · TRIX-15 (signal 9) · Bollinger 20/2

Purpose: Claude should NEVER calculate these values by "reasoning" over bars.
The correct flow is: Claude fetches raw bars via Robinhood MCP
(get_equity_historicals, ~290 daily bars) -> passes them to this module ->
numbers are computed, not estimated.

TA-Lib is the sole calculation authority for EMA/RSI/MACD/TRIX/Bollinger
(see docs/library-migration/STATUS.md for the parity record against the
former hand-written formulas, including the one documented intentional
semantic difference: flat-price RSI). Requires the `indicators` extra
(`pip install -e ".[indicators]"`). Input: list of close prices old->new.
For Bollinger %B precision, high/low can be passed, but close is enough.
"""
from __future__ import annotations
import json
import sys
from typing import Optional

import numpy as np

try:
    import talib
except ImportError as exc:  # pragma: no cover - exercised by dependency-extras-smoke CI
    raise ImportError(
        "scripts/indicators.py requires TA-Lib. "
        'Install the indicators extra: pip install -e ".[indicators]"'
    ) from exc


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _nan_to_none(arr: np.ndarray) -> list[Optional[float]]:
    """Convert a TA-Lib/NumPy float array to the project's None-padding convention."""
    return [None if np.isnan(v) else float(v) for v in arr]


def _pct_change(current: float, previous: float) -> float:
    """Percent change of `current` vs. `previous`, 0.0 (not NaN/inf) at previous == 0."""
    return (current - previous) / previous * 100.0 if previous != 0 else 0.0


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """
    EMA with None padding in the warmup. Seed = SMA of the first `period`
    observations (TradingView / ta-lib adjust=False convention).
    Returns list of same length as `values`.
    """
    arr = np.asarray(values, dtype=float)
    return _nan_to_none(talib.EMA(arr, timeperiod=period))


def _strip(values: list[Optional[float]]) -> list[float]:
    return [v for v in values if v is not None]


def rsi_wilder(close: list[float], period: int = 14) -> list[Optional[float]]:
    """RSI with Wilder smoothing. None padding in warmup.

    TA-Lib's RSI returns 0.0 (not 100.0) for the degenerate case where the
    average gain and average loss are both exactly zero, i.e. every price
    from the start of `close` through the current bar is identical. The
    pre-migration formula treated that zero-loss case as maximally bullish
    (RSI 100 -- the conventional Wilder divide-by-zero, RS -> infinity,
    result). This boundary is corrected back to that documented, intentional
    semantic; see docs/library-migration/STATUS.md for the comparison that
    surfaced the difference.
    """
    n = len(close)
    arr = np.asarray(close, dtype=float)
    out = _nan_to_none(talib.RSI(arr, timeperiod=period))
    first_change = next((i for i in range(1, n) if close[i] != close[i - 1]), n)
    for i in range(period, n):
        if out[i] is not None and i < first_change:
            out[i] = 100.0
    return out


def macd(close: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram), all None-padded.

    Built from TA-Lib's EMA primitive rather than `talib.MACD()` directly:
    `talib.MACD()` withholds the MACD line itself until enough bars exist
    for the *signal* EMA too, whereas this stack's convention (matched here)
    makes the line available `signal`-1 bars earlier, as soon as both the
    fast and slow EMAs exist -- callers depend on that earlier availability.
    """
    ef = ema_series(close, fast)
    es = ema_series(close, slow)
    line: list[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)
    ]
    valid = _strip(line)
    sig_valid = ema_series(valid, signal)
    # re-align signal to original length
    sig: list[Optional[float]] = [None] * len(close)
    first = next((i for i, v in enumerate(line) if v is not None), None)
    if first is not None:
        for off, v in enumerate(sig_valid):
            sig[first + off] = v
    hist: list[Optional[float]] = [
        (m - s) if (m is not None and s is not None) else None for m, s in zip(line, sig)
    ]
    return line, sig, hist


def trix(close: list[float], period: int = 15, signal: int = 9):
    """TRIX (% ROC of triple EMA) and its signal. None-padded to original length.

    Built from three chained TA-Lib EMA passes rather than `talib.TRIX()`
    directly, to keep explicit control of the zero-denominator convention
    (0.0, not NaN/inf, when the triple-smoothed EMA is itself exactly zero)
    and of the signal-line alignment, matching the pre-migration formula
    exactly (see `_pct_change` and docs/library-migration/STATUS.md).
    """
    n = len(close)
    e1 = _strip(ema_series(close, period))
    e2 = _strip(ema_series(e1, period))
    e3 = _strip(ema_series(e2, period))
    trix_valid: list[float] = [_pct_change(e3[i], e3[i - 1]) for i in range(1, len(e3))]
    sig_valid = _strip(ema_series(trix_valid, signal))
    # align to end (TRIX is one of the most lagging)
    t: list[Optional[float]] = [None] * n
    for off, v in enumerate(trix_valid):
        idx = n - len(trix_valid) + off
        if idx >= 0:
            t[idx] = v
    s: list[Optional[float]] = [None] * n
    for off, v in enumerate(sig_valid):
        idx = n - len(sig_valid) + off
        if idx >= 0:
            s[idx] = v
    return t, s


def bollinger(close: list[float], period: int = 20, mult: float = 2.0):
    """Returns (mid, upper, lower, percent_b) for the last bar.

    TA-Lib's BBANDS uses population standard deviation for `matype=0`
    (SMA; its `nbdevup`/`nbdevdn` multiply the population stddev),
    matching the pre-migration `statistics.pstdev`-based formula, like
    TradingView.
    """
    if len(close) < period:
        return None, None, None, None
    arr = np.asarray(close, dtype=float)
    upper, mid, lower = talib.BBANDS(arr, timeperiod=period, nbdevup=mult, nbdevdn=mult, matype=0)
    mid_v, upper_v, lower_v = float(mid[-1]), float(upper[-1]), float(lower[-1])
    rng = upper_v - lower_v
    pct_b = (close[-1] - lower_v) / rng if rng != 0 else 0.5
    return mid_v, upper_v, lower_v, pct_b


# --------------------------------------------------------------------------
# High-level API
# --------------------------------------------------------------------------

def _slope(series: list[Optional[float]], lookback: int) -> Optional[float]:
    """Absolute variation of the indicator relative to `lookback` bars ago."""
    valid_idx = [i for i, v in enumerate(series) if v is not None]
    if len(valid_idx) <= lookback:
        return None
    last_i = valid_idx[-1]
    prev_i = valid_idx[-1 - lookback]
    return series[last_i] - series[prev_i]


def compute(close: list[float], slope_lookback: int = 5) -> dict:
    """
    Computes the entire stack and returns the latest values + recent slopes.
    `slope_lookback`: bars to measure the slope (default 5 ~ one week).
    """
    if len(close) < 210:
        # Not a fatal error: EMA200 will simply be None. We warn about it.
        warn = f"Only {len(close)} bars; EMA200/some indicators may be None. Ideal >=220."
    else:
        warn = None

    ema20 = ema_series(close, 20)
    ema50 = ema_series(close, 50)
    ema200 = ema_series(close, 200)
    rsi = rsi_wilder(close, 14)
    macd_line, macd_sig, macd_hist = macd(close, 12, 26, 9)
    trix_line, trix_sig = trix(close, 15, 9)
    bb_mid, bb_up, bb_lo, pct_b = bollinger(close, 20, 2.0)

    def last(s):
        v = _strip(s)
        return v[-1] if v else None

    def prev(s):
        v = _strip(s)
        return v[-2] if len(v) >= 2 else None

    # Bars since the last close BELOW the EMA20 (0 = current bar closed below).
    # None if it never closed below in the available window. Helps distinguish
    # a genuine recovery of EMA20 (recent dip) from the normal state of an uptrend.
    bars_since_below_ema20 = None
    for back in range(len(close)):
        i = len(close) - 1 - back
        if ema20[i] is not None and close[i] < ema20[i]:
            bars_since_below_ema20 = back
            break

    return {
        "n_bars": len(close),
        "warning": warn,
        "close": close[-1],
        "ema20": last(ema20), "ema50": last(ema50), "ema200": last(ema200),
        "ema20_slope": _slope(ema20, slope_lookback),
        "ema50_slope": _slope(ema50, slope_lookback),
        "ema200_slope": _slope(ema200, slope_lookback),
        "rsi14": last(rsi), "rsi14_prev": prev(rsi),
        "macd_line": last(macd_line), "macd_signal": last(macd_sig),
        "macd_hist": last(macd_hist), "macd_hist_prev": prev(macd_hist),
        "trix": last(trix_line), "trix_prev": prev(trix_line),
        "trix_signal": last(trix_sig), "trix_signal_prev": prev(trix_sig),
        "bars_since_below_ema20": bars_since_below_ema20,
        "bb_mid": bb_mid, "bb_upper": bb_up, "bb_lower": bb_lo, "percent_b": pct_b,
    }


def _round(d: dict, nd: int = 4) -> dict:
    return {k: (round(v, nd) if isinstance(v, float) else v) for k, v in d.items()}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Deterministic indicator stack (input: JSON of closes).")
    ap.add_argument("input", nargs="?", help="JSON: {'close':[...]} or [..]. If no file: self-test.")
    ap.add_argument("--slope-lookback", type=int, default=5)
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
        close = raw["close"] if isinstance(raw, dict) else raw
        close = [float(x) for x in close]
    else:
        import math
        close = [round(100 + 18 * math.sin(i / 22) + i * 0.06, 2) for i in range(290)]
        print("[self-test: synthetic series of 290 bars]\n", file=sys.stderr)

    print(json.dumps(_round(compute(close, args.slope_lookback)), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
