"""Library-backed candidate implementation of `evaluation/metrics.py`'s
formulas, built to prove fixture parity ahead of a later removal decision
(`docs/library-migration/MASTER_PLAN.md` rows 11/17,
`docs/library-migration/REMOVAL_MANIFEST.md`).

**Not authoritative.** `evaluation/metrics.py` remains the sole production
implementation of these formulas — nothing under `src/trading_research/`
outside this file imports `empyrical` or `quantstats_lumi`
(`tests/unit/test_analytics_parity_import_boundary.py` enforces this by AST
inspection), and this module has zero production callers. Per
`REMOVAL_MANIFEST.md`'s default rule ("current custom implementation
remains authoritative" until its assigned removal PR) and
`MASTER_PLAN.md` row 17 ("execute only removal-manifest entries that passed
parity in PR 3/4/11/15"), PR 11's job is to prove parity; only PR 17
executes the removal, unlike PR 3/PR 4 which removed their custom formulas
immediately because the manifest explicitly marked those two rows closed
early. `tests/unit/test_analytics_parity.py` is the parity proof this
module exists for.

`empyrical-reloaded` is authoritative for the primitive metrics used here
(cumulative return, Sharpe, Sortino, max drawdown);  `quantstats-lumi` is
reporting/presentation only (`DEPENDENCY_MATRIX.md` Section 6: "tear
sheets, tables, charts") and `presentation_summary()` below is never
compared against `evaluation/metrics.py` for parity — two independent
authorities over the same metric is explicitly called out as a defect in
`DEPENDENCY_MATRIX.md`, not something this module reintroduces.

Deliberately preserved semantic corrections on top of the raw library
primitives, each verified numerically against
`evaluation/metrics.py` before being encoded here:

1. **Flat/near-zero return-variance Sharpe and Sortino.**
   `empyrical.sharpe_ratio`/`empyrical.sortino_ratio` do not special-case a
   zero (or floating-point-noise-near-zero) standard deviation — they
   divide by it and return an enormous finite number (observed ~1e17 for a
   flat 0.05 fixture) or `inf`, never an explicit "undefined" signal. This
   module applies the same `math.isclose(..., abs_tol=1e-12)` boundary
   `evaluation/metrics.py` already uses and reports `UNDEFINED` instead of
   propagating either value.
2. **Calmar ratio.** `empyrical.calmar_ratio` divides a CAGR-style
   annualized return — compounding across `len(returns)` periods at a
   252/year convention — by max drawdown. `evaluation/metrics.py`'s
   convention is simpler: raw (non-annualized) cumulative return over max
   drawdown, appropriate here because these are independent
   per-recommendation returns, not a fixed-frequency daily bar series a
   CAGR annualization assumes. Verified numerically that
   `empyrical.calmar_ratio`'s result diverges from the composed value
   regardless of the `annualization` parameter passed to it (e.g. a 6-bar
   oscillating fixture: composed Calmar 5.108, `empyrical.calmar_ratio`
   2922.7 with `period="daily"`, 0.817 with `annualization=1` — neither
   annualization setting recovers the composed value). `calmar_ratio_parity`
   is therefore composed from `cumulative_return_parity` and
   `max_drawdown_parity` directly, never from `empyrical.calmar_ratio()`.
   `quantstats_lumi.stats.calmar` was evaluated too and rejected for a
   sharper reason: it requires a real `DatetimeIndex` to compute elapsed
   wall-clock time (`cagr()` calls `.total_seconds()` on the index range)
   and raises `AttributeError` on the plain integer-indexed `Series` every
   other function in this module receives — not merely a different
   convention, but a shape this repository's returns do not have.
3. **Zero max drawdown.** Both this module and `evaluation/metrics.py`
   treat a zero max drawdown as `UNDEFINED` for Calmar (division by zero),
   not `inf`.
"""
from __future__ import annotations

import math

try:
    import empyrical
except ImportError as exc:
    raise ImportError(
        "evaluation/analytics_parity.py requires empyrical-reloaded. "
        "Install the analytics extra: pip install -e \".[analytics]\""
    ) from exc

try:
    import quantstats_lumi.stats as quantstats_stats
except ImportError as exc:
    raise ImportError(
        "evaluation/analytics_parity.py requires quantstats-lumi. "
        "Install the analytics extra: pip install -e \".[analytics]\""
    ) from exc

import pandas as pd

from .metrics import ANNUALIZATION_TRADING_DAYS, MIN_SAMPLE_SIZE, MetricsResult, _completed_returns, _insufficient
from .models import RecommendationEvaluation


def cumulative_return_parity(
    evaluations: list[RecommendationEvaluation], *, min_sample_size: int = 1,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    if not returns:
        # empyrical.cum_returns_final on an empty Series returns NaN;
        # evaluation/metrics.py's identity-element product over zero terms
        # yields exactly zero. Preserve that when min_sample_size permits
        # an empty sample (e.g. min_sample_size=0).
        return MetricsResult(status="OK", value=0.0, sample_size=0)
    value = float(empyrical.cum_returns_final(pd.Series([float(r) for r in returns]), starting_value=0))
    return MetricsResult(status="OK", value=value, sample_size=len(returns))


def sharpe_ratio_parity(
    evaluations: list[RecommendationEvaluation], *, risk_free_rate: float = 0.0,
    min_sample_size: int = MIN_SAMPLE_SIZE, annualize: bool = True,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    values = [float(r) for r in returns]
    mean = sum(values) / len(values)
    variance = sum((r - mean) ** 2 for r in values) / (len(values) - 1) if len(values) > 1 else 0.0
    if math.isclose(math.sqrt(variance), 0.0, abs_tol=1e-12):
        return MetricsResult(status="UNDEFINED", value=None, sample_size=len(values), reason="zero return variance")
    annualization = ANNUALIZATION_TRADING_DAYS if annualize else 1
    # empyrical-reloaded's stub types `risk_free`/`required_return` as `int`;
    # the library itself accepts a float rate at runtime without issue.
    ratio = float(
        empyrical.sharpe_ratio(pd.Series(values), risk_free=risk_free_rate, annualization=annualization)  # type: ignore[arg-type]
    )
    return MetricsResult(status="OK", value=ratio, sample_size=len(values))


def sortino_ratio_parity(
    evaluations: list[RecommendationEvaluation], *, risk_free_rate: float = 0.0,
    min_sample_size: int = MIN_SAMPLE_SIZE, annualize: bool = True,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    values = [float(r) for r in returns]
    downside = [min(0.0, r - risk_free_rate) for r in values]
    downside_variance = sum(d ** 2 for d in downside) / len(downside)
    if math.isclose(math.sqrt(downside_variance), 0.0, abs_tol=1e-12):
        return MetricsResult(
            status="UNDEFINED", value=None, sample_size=len(values), reason="no downside deviation observed",
        )
    annualization = ANNUALIZATION_TRADING_DAYS if annualize else 1
    ratio = float(
        empyrical.sortino_ratio(pd.Series(values), required_return=risk_free_rate, annualization=annualization)  # type: ignore[arg-type]
    )
    return MetricsResult(status="OK", value=ratio, sample_size=len(values))


def max_drawdown_parity(
    evaluations: list[RecommendationEvaluation], *, min_sample_size: int = 1,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    if not returns:
        # empyrical.max_drawdown on an empty Series returns NaN;
        # evaluation/metrics.py's equity curve starts flat at 1 with no
        # observed drawdown, yielding exactly zero. Preserve that when
        # min_sample_size permits an empty sample (e.g. min_sample_size=0).
        return MetricsResult(status="OK", value=0.0, sample_size=0)
    value = float(empyrical.max_drawdown(pd.Series([float(r) for r in returns])))
    return MetricsResult(status="OK", value=value, sample_size=len(returns))


def calmar_ratio_parity(
    evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE,
) -> MetricsResult:
    cumulative = cumulative_return_parity(evaluations, min_sample_size=min_sample_size)
    drawdown = max_drawdown_parity(evaluations, min_sample_size=min_sample_size)
    if cumulative.status != "OK" or drawdown.status != "OK":
        return _insufficient(cumulative.sample_size, "need sufficient data for both cumulative return and drawdown")
    # status == "OK" guarantees both .value are the float this module always
    # produces, never Decimal/None -- asserted so Pyright narrows accordingly.
    assert cumulative.value is not None and drawdown.value is not None
    cumulative_value = float(cumulative.value)
    drawdown_value = float(drawdown.value)
    if drawdown_value == 0:
        return MetricsResult(
            status="UNDEFINED", value=None, sample_size=cumulative.sample_size,
            reason="Calmar ratio is undefined with zero max drawdown",
        )
    return MetricsResult(
        status="OK", value=cumulative_value / abs(drawdown_value), sample_size=cumulative.sample_size,
    )


def presentation_summary(evaluations: list[RecommendationEvaluation]) -> dict[str, float | None]:
    """Reporting/presentation-only summary via `quantstats_lumi.stats`
    (see module docstring point 2 for why `calmar` is excluded: it requires
    a real `DatetimeIndex`, not the plain integer-indexed `Series` used
    here). Never used for a parity assertion — this repository's
    252-trading-day convention is passed explicitly via `periods=`, since
    `quantstats_lumi`'s own default (`periods=365`, calendar days) does not
    match it, but the result is still presentation-only and may legitimately
    diverge from `evaluation/metrics.py` or the `*_parity` functions above.
    """
    returns = _completed_returns(evaluations)
    if not returns:
        return {"sharpe": None, "sortino": None, "max_drawdown": None}
    series = pd.Series([float(r) for r in returns])
    prices = (1 + series).cumprod()
    return {
        "sharpe": float(quantstats_stats.sharpe(series, periods=ANNUALIZATION_TRADING_DAYS)),
        "sortino": float(quantstats_stats.sortino(series, periods=ANNUALIZATION_TRADING_DAYS)),
        "max_drawdown": float(quantstats_stats.max_drawdown(prices)),
    }
