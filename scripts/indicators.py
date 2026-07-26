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

TA-Lib is authoritative for the EMA, RSI, and BBANDS primitives. MACD and
TRIX are composed from TA-Lib's EMA primitive in a thin custom adapter
that preserves this stack's public-contract semantics (earlier MACD-line
availability, TRIX's zero-denominator/alignment convention) -- TA-Lib is
not the sole authority for every calculation end-to-end; see
docs/library-migration/STATUS.md for the exact authority boundary, the
parity record against the former hand-written formulas, and the one
documented intentional semantic difference (flat-price RSI). All inputs
are validated at a shared fail-closed boundary (`_validate_prices`) before
any TA-Lib call -- malformed or non-finite data raises `IndicatorInputError`
rather than being silently coerced or masked as warm-up. Requires the
`indicators` extra
(`pip install -e ".[indicators]"`). Input: list of close prices old->new.
For Bollinger %B precision, high/low can be passed, but close is enough.
"""
from __future__ import annotations
import json
import math
import sys
from collections.abc import Sequence
from typing import Optional

import numpy as np

try:
    import talib
except ImportError as exc:  # pragma: no cover - exercised by dependency-extras-smoke CI
    raise ImportError(
        "scripts/indicators.py requires TA-Lib. "
        'Install the indicators extra: pip install -e ".[indicators]"'
    ) from exc


class IndicatorInputError(ValueError):
    """Malformed, non-finite, or otherwise invalid indicator input.

    Raised at the shared validation boundary before any TA-Lib call, and
    also if TA-Lib itself ever returns a NaN outside the documented
    warm-up window. Never silently coerced or masked -- a `None` in this
    module's output means "known warm-up gap"; letting anything else
    collapse to `None` would let `_strip()` discard it and `compute()`
    quietly return an older, stale indicator value instead of surfacing
    the corruption.
    """


def _validate_prices(values: object, *, name: str, minimum_length: int = 0) -> list[float]:
    """Validate raw price input before any TA-Lib call.

    Requires a flat (one-dimensional), non-nested sequence of real
    int/float values -- no booleans, no strings, no `None`, no `NaN`, no
    +/-infinity -- with at least `minimum_length` elements. Returns a new
    `list[float]`; nothing is silently coerced.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise IndicatorInputError(
            f"{name} must be a one-dimensional sequence of numbers, got {type(values).__name__}"
        )
    if len(values) < minimum_length:
        raise IndicatorInputError(
            f"{name} must contain at least {minimum_length} value(s), got {len(values)}"
        )
    validated: list[float] = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise IndicatorInputError(
                f"{name}[{i}] must be a real number (int or float), got {type(v).__name__}: {v!r}"
            )
        fv = float(v)
        if math.isnan(fv):
            raise IndicatorInputError(f"{name}[{i}] is NaN, which is not a valid price")
        if math.isinf(fv):
            raise IndicatorInputError(f"{name}[{i}] is infinite, which is not a valid price")
        validated.append(fv)
    return validated


def _validate_period(period: object, name: str) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise IndicatorInputError(f"{name} must be a positive integer, got {period!r}")


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _nan_to_none(arr: np.ndarray, *, expected_warmup: int) -> list[Optional[float]]:
    """Convert a TA-Lib/NumPy float array to the project's None warm-up convention.

    Only a NaN within the first `expected_warmup` entries is the documented
    warm-up and becomes `None`. A NaN at or after that index is unexpected
    -- it fails closed with `IndicatorInputError` rather than being folded
    into the same `None` warm-up convention, which `_strip()` would
    otherwise treat as more warm-up and silently let `compute()` return an
    older, stale indicator value instead of surfacing the corruption.
    """
    out: list[Optional[float]] = []
    for i, v in enumerate(arr):
        if np.isnan(v):
            if i < expected_warmup:
                out.append(None)
                continue
            raise IndicatorInputError(
                f"unexpected NaN at index {i}, past the expected {expected_warmup}-bar "
                "warm-up -- refusing to treat it as a stale-safe warm-up value"
            )
        out.append(float(v))
    return out


def _pct_change(current: float, previous: float) -> float:
    """Percent change of `current` vs. `previous`, 0.0 (not NaN/inf) at previous == 0."""
    return (current - previous) / previous * 100.0 if previous != 0 else 0.0


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """
    EMA with None padding in the warmup. Seed = SMA of the first `period`
    observations (TradingView / ta-lib adjust=False convention).
    Returns list of same length as `values`.
    """
    _validate_period(period, "period")
    prices = _validate_prices(values, name="values")
    arr = np.asarray(prices, dtype=float)
    expected_warmup = min(period - 1, len(prices))
    return _nan_to_none(talib.EMA(arr, timeperiod=period), expected_warmup=expected_warmup)


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
    _validate_period(period, "period")
    prices = _validate_prices(close, name="close")
    n = len(prices)
    arr = np.asarray(prices, dtype=float)
    expected_warmup = min(period, n)
    out = _nan_to_none(talib.RSI(arr, timeperiod=period), expected_warmup=expected_warmup)
    first_change = next((i for i in range(1, n) if prices[i] != prices[i - 1]), n)
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
    This is a thin custom adapter over TA-Lib's EMA primitive, not a
    from-scratch MACD formula -- see docs/library-migration/STATUS.md for
    the exact authority boundary.
    """
    _validate_period(fast, "fast")
    _validate_period(slow, "slow")
    _validate_period(signal, "signal")
    if fast >= slow:
        raise IndicatorInputError(f"MACD requires fast < slow, got fast={fast}, slow={slow}")
    prices = _validate_prices(close, name="close")
    ef = ema_series(prices, fast)
    es = ema_series(prices, slow)
    line: list[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)
    ]
    valid = _strip(line)
    sig_valid = ema_series(valid, signal)
    # re-align signal to original length
    sig: list[Optional[float]] = [None] * len(prices)
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
    exactly (see `_pct_change` and docs/library-migration/STATUS.md). This
    is a thin custom adapter over TA-Lib's EMA primitive, not a from-scratch
    TRIX formula.
    """
    _validate_period(period, "period")
    _validate_period(signal, "signal")
    prices = _validate_prices(close, name="close")
    n = len(prices)
    e1 = _strip(ema_series(prices, period))
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
    TradingView. `%B` (the boundary transform dividing by the band range)
    is a thin custom calculation on top of TA-Lib's bands, not a
    from-scratch Bollinger formula.
    """
    _validate_period(period, "period")
    prices = _validate_prices(close, name="close")
    if len(prices) < period:
        return None, None, None, None
    arr = np.asarray(prices, dtype=float)
    upper, mid, lower = talib.BBANDS(arr, timeperiod=period, nbdevup=mult, nbdevdn=mult, matype=0)
    mid_v, upper_v, lower_v = float(mid[-1]), float(upper[-1]), float(lower[-1])
    if math.isnan(mid_v) or math.isnan(upper_v) or math.isnan(lower_v):
        raise IndicatorInputError(
            "BBANDS returned NaN for the most recent bar despite sufficient, validated "
            "input -- refusing to return a stale/undefined Bollinger value"
        )
    rng = upper_v - lower_v
    pct_b = (prices[-1] - lower_v) / rng if rng != 0 else 0.5
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
    prices = _validate_prices(close, name="close", minimum_length=1)

    if len(prices) < 210:
        # Not a fatal error: EMA200 will simply be None. We warn about it.
        warn = f"Only {len(prices)} bars; EMA200/some indicators may be None. Ideal >=220."
    else:
        warn = None

    ema20 = ema_series(prices, 20)
    ema50 = ema_series(prices, 50)
    ema200 = ema_series(prices, 200)
    rsi = rsi_wilder(prices, 14)
    macd_line, macd_sig, macd_hist = macd(prices, 12, 26, 9)
    trix_line, trix_sig = trix(prices, 15, 9)
    bb_mid, bb_up, bb_lo, pct_b = bollinger(prices, 20, 2.0)

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
    for back in range(len(prices)):
        i = len(prices) - 1 - back
        if ema20[i] is not None and prices[i] < ema20[i]:
            bars_since_below_ema20 = back
            break

    return {
        "n_bars": len(prices),
        "warning": warn,
        "close": prices[-1],
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


def _extract_close_from_cli_input(raw: object) -> object:
    """Unwrap the CLI's two accepted JSON shapes with an actionable error otherwise.

    Element-level validation (type/finite/non-nested) is deferred to
    `_validate_prices`, called uniformly from `compute()`, so the CLI and
    direct-API callers (e.g. `scripts/score.py`) get identical fail-closed
    behavior instead of a raw `KeyError` or an opaque NumPy error.
    """
    if isinstance(raw, dict):
        if "close" not in raw:
            raise IndicatorInputError('input JSON object must contain a "close" key')
        return raw["close"]
    if isinstance(raw, list):
        return raw
    raise IndicatorInputError(
        f'input JSON must be a list of prices or an object with a "close" key, got {type(raw).__name__}'
    )


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Deterministic indicator stack (input: JSON of closes).")
    ap.add_argument("input", nargs="?", help="JSON: {'close':[...]} or [..]. If no file: self-test.")
    ap.add_argument("--slope-lookback", type=int, default=5)
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
        close = _extract_close_from_cli_input(raw)
    else:
        close = [round(100 + 18 * math.sin(i / 22) + i * 0.06, 2) for i in range(290)]
        print("[self-test: synthetic series of 290 bars]\n", file=sys.stderr)

    result = _round(compute(close, args.slope_lookback))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
