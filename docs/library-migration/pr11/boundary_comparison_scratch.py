"""Scratch comparison script for PR 11 (QuantStats/analytics migration).

Not part of the test suite and not imported by anything -- run manually
(`.venv/bin/python docs/library-migration/pr11/boundary_comparison_scratch.py`,
requires the `analytics` extra) to reproduce the numeric findings recorded
in `evaluation/analytics_parity.py`'s module docstring and `STATUS.md`'s
"Completed work (PR 11)" section: `empyrical.sharpe_ratio`/`sortino_ratio`
annualize and unannualize bit-for-bit identically to
`evaluation/metrics.py`'s custom formulas given `period="daily"` /
`annualization=1` respectively; `empyrical.max_drawdown` matches to
floating-point noise; and `empyrical.calmar_ratio` does **not** match the
custom Calmar formula under any annualization setting, because it applies a
CAGR-style annualized-return numerator instead of raw cumulative return --
see `comparison_output.txt` for the captured run this script produced.
"""
from __future__ import annotations

import math

import empyrical
import pandas as pd


def custom_sharpe(returns: list[float], risk_free_rate: float = 0.0, annualize: bool = True) -> float | None:
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0.0
    std = math.sqrt(variance)
    if math.isclose(std, 0.0, abs_tol=1e-12):
        return None
    ratio = (mean - risk_free_rate) / std
    if annualize:
        ratio *= math.sqrt(252)
    return ratio


def custom_sortino(returns: list[float], risk_free_rate: float = 0.0, annualize: bool = True) -> float | None:
    mean = sum(returns) / len(returns)
    downside = [min(0.0, r - risk_free_rate) for r in returns]
    downside_variance = sum(d ** 2 for d in downside) / len(downside)
    downside_std = math.sqrt(downside_variance)
    if math.isclose(downside_std, 0.0, abs_tol=1e-12):
        return None
    ratio = (mean - risk_free_rate) / downside_std
    if annualize:
        ratio *= math.sqrt(252)
    return ratio


def custom_cum_return(returns: list[float]) -> float:
    total = 1.0
    for r in returns:
        total *= (1 + r)
    return total - 1


def custom_max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = equity
    worst = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        dd = (equity - peak) / peak if peak != 0 else 0.0
        worst = min(worst, dd)
    return worst


def custom_calmar(returns: list[float]) -> float | None:
    cum = custom_cum_return(returns)
    dd = custom_max_drawdown(returns)
    if dd == 0:
        return None
    return cum / abs(dd)


FIXTURES = {
    "oscillating6": [0.05, -0.02, 0.03, 0.01, -0.01, 0.04],
    "increasing5": [0.10, 0.20, 0.30, 0.40, 0.50],
    "drawdown5": [0.10, -0.20, 0.05, 0.05, 0.05],
    "mixed20": [0.01, -0.02, 0.015, -0.005, 0.02, -0.03, 0.01, 0.04, -0.01, 0.02,
                -0.015, 0.005, 0.03, -0.02, 0.01, -0.01, 0.02, -0.005, 0.015, -0.02],
}


def main() -> None:
    for name, returns in FIXTURES.items():
        s = pd.Series(returns)
        print("====", name, "n=", len(returns))
        print("sharpe annualized  custom=%r  empyrical=%r" % (
            custom_sharpe(returns, annualize=True), empyrical.sharpe_ratio(s, period="daily")))
        print("sharpe raw         custom=%r  empyrical=%r" % (
            custom_sharpe(returns, annualize=False), empyrical.sharpe_ratio(s, annualization=1)))
        print("sortino annualized custom=%r  empyrical=%r" % (
            custom_sortino(returns, annualize=True), empyrical.sortino_ratio(s, period="daily")))
        print("sortino raw        custom=%r  empyrical=%r" % (
            custom_sortino(returns, annualize=False), empyrical.sortino_ratio(s, annualization=1)))
        print("cum_return         custom=%r  empyrical(cum_returns_final, sv=0)=%r" % (
            custom_cum_return(returns), empyrical.cum_returns_final(s, starting_value=0)))
        print("max_drawdown       custom=%r  empyrical=%r" % (
            custom_max_drawdown(returns), empyrical.max_drawdown(s)))
        print("calmar             custom=%r  empyrical(period=daily)=%r  empyrical(annualization=1)=%r" % (
            custom_calmar(returns), empyrical.calmar_ratio(s, period="daily"),
            empyrical.calmar_ratio(s, annualization=1)))


if __name__ == "__main__":
    main()
