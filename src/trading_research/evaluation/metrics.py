"""Aggregate portfolio/strategy metrics over completed evaluations
(docs/milestone-4.md Step 12).

Every metric fails safe with an explicit `MetricsResult.status` rather than
returning a misleading zero when the sample is too small or a metric is
undefined for the data available — see `MetricsResult.insufficient_data`.
Annualization uses 252 trading days/year (documented below); this is a
convention, not a measured constant.

Two arithmetic authorities, split by metric (library-migration PR 17,
`docs/library-migration/DECISIONS.md` D15):

* `empyrical-reloaded` is the sole formula authority for
  `cumulative_return`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, and
  `calmar_ratio` (composed from the first two of those). Their values are
  `float`. The hand-written formulas they replace were removed after PR 11
  proved fixture parity; `tests/unit/test_metrics_library_authority.py`
  pins every result to the values the removed formulas produced.
* Every other metric here has no library equivalent and stays
  deterministic `Decimal` arithmetic, matching this repository's convention
  of Decimal at storage/monetary boundaries and float for derived
  statistics (e.g. `analysis/scorer.py`).

Fail-closed boundaries the library does not provide are applied here,
before the library is called, never inferred from its output:

1. `empyrical.sharpe_ratio`/`sortino_ratio` do not special-case a zero (or
   floating-point-noise-near-zero) deviation — they return an enormous
   finite float (~1e17 for flat returns) or `inf`. A deviation within
   `_ZERO_DEVIATION_TOLERANCE` of zero reports `UNDEFINED` instead. Both
   also return `NaN` for one return; that is `UNDEFINED` for Sharpe (no
   variance), but a single return below the required return has a nonzero
   downside deviation, so Sortino applies the library's own formula over
   `empyrical.downside_risk` for it.
2. `empyrical.calmar_ratio` divides a CAGR-style annualized return by max
   drawdown; this module's Calmar is raw cumulative return over max
   drawdown, because these are independent per-recommendation returns, not
   a fixed-frequency bar series. `calmar_ratio` is therefore composed from
   `cumulative_return`/`max_drawdown`, and `empyrical.calmar_ratio` is
   never called. A zero max drawdown is `UNDEFINED`, never `inf`.
3. `empyrical.cum_returns_final`/`max_drawdown` return `NaN` for an empty
   series. An empty sample (reachable only with `min_sample_size=0`) is an
   exact `0.0` for both: an empty product, and a flat equity curve.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import empyrical
import pandas as pd

from .models import RecommendationEvaluation

ANNUALIZATION_TRADING_DAYS = 252
MIN_SAMPLE_SIZE = 5
_ZERO_DEVIATION_TOLERANCE = 1e-12


class InsufficientDataError(ValueError):
    pass


@dataclass(frozen=True)
class MetricsResult:
    status: str  # "OK" | "INSUFFICIENT_DATA" | "UNDEFINED"
    value: float | Decimal | None
    sample_size: int
    reason: str | None = None


def _completed_returns(evaluations: list[RecommendationEvaluation]) -> list[Decimal]:
    return [e.net_return for e in evaluations if e.status in ("COMPLETED", "PARTIALLY_FILLED") and e.net_return is not None]


def _insufficient(sample_size: int, reason: str) -> MetricsResult:
    return MetricsResult(status="INSUFFICIENT_DATA", value=None, sample_size=sample_size, reason=reason)


def _float_series(returns: list[Decimal]) -> pd.Series:
    return pd.Series([float(r) for r in returns], dtype="float64")


def _annualization(annualize: bool) -> int:
    # 252 is exactly empyrical's own `period="daily"` convention; 1 leaves
    # the ratio unannualized.
    return ANNUALIZATION_TRADING_DAYS if annualize else 1


def hit_rate(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    wins = sum(1 for r in returns if r > 0)
    return MetricsResult(status="OK", value=Decimal(wins) / Decimal(len(returns)), sample_size=len(returns))


def average_return(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    return MetricsResult(status="OK", value=sum(returns) / Decimal(len(returns)), sample_size=len(returns))


def median_return(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE) -> MetricsResult:
    returns = sorted(_completed_returns(evaluations))
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    mid = len(returns) // 2
    if len(returns) % 2 == 1:
        value = returns[mid]
    else:
        value = (returns[mid - 1] + returns[mid]) / 2
    return MetricsResult(status="OK", value=value, sample_size=len(returns))


def gain_loss_ratio(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    gains = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    if not gains or not losses:
        return MetricsResult(
            status="UNDEFINED", value=None, sample_size=len(returns),
            reason="gain/loss ratio is undefined without at least one gain and one loss",
        )
    avg_gain = sum(gains) / Decimal(len(gains))
    avg_loss = abs(sum(losses) / Decimal(len(losses)))
    return MetricsResult(status="OK", value=avg_gain / avg_loss, sample_size=len(returns))


def cumulative_return(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = 1) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    if not returns:
        return MetricsResult(status="OK", value=0.0, sample_size=0)
    value = float(empyrical.cum_returns_final(_float_series(returns), starting_value=0))
    return MetricsResult(status="OK", value=value, sample_size=len(returns))


def benchmark_relative_cumulative_return(
    evaluations: list[RecommendationEvaluation], *, min_sample_size: int = 1,
) -> MetricsResult:
    strategy = cumulative_return(evaluations, min_sample_size=min_sample_size)
    if strategy.status != "OK":
        return strategy
    excess_returns = [
        e.excess_return for e in evaluations
        if e.status in ("COMPLETED", "PARTIALLY_FILLED") and e.excess_return is not None
    ]
    if len(excess_returns) < min_sample_size:
        return _insufficient(len(excess_returns), f"need at least {min_sample_size} evaluations with excess_return")
    total = Decimal("1")
    for r in excess_returns:
        total *= (Decimal("1") + r)
    return MetricsResult(status="OK", value=total - 1, sample_size=len(excess_returns))


def sharpe_ratio(
    evaluations: list[RecommendationEvaluation], *, risk_free_rate: float = 0.0, min_sample_size: int = MIN_SAMPLE_SIZE,
    annualize: bool = True,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    series = _float_series(returns)
    # Sample standard deviation (ddof=1), the same estimator empyrical uses;
    # fewer than two returns has no variance to measure.
    if len(series) < 2 or float(series.std(ddof=1)) <= _ZERO_DEVIATION_TOLERANCE:
        return MetricsResult(status="UNDEFINED", value=None, sample_size=len(returns), reason="zero return variance")
    # empyrical-reloaded's stub types `risk_free`/`required_return` as `int`;
    # the library itself accepts a float rate at runtime without issue.
    ratio = float(
        empyrical.sharpe_ratio(series, risk_free=risk_free_rate, annualization=_annualization(annualize))  # type: ignore[arg-type]
    )
    return MetricsResult(status="OK", value=ratio, sample_size=len(returns))


def sortino_ratio(
    evaluations: list[RecommendationEvaluation], *, risk_free_rate: float = 0.0, min_sample_size: int = MIN_SAMPLE_SIZE,
    annualize: bool = True,
) -> MetricsResult:
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    series = _float_series(returns)
    # `downside_risk` is defined for one return: a single return below
    # `risk_free_rate` is a nonzero downside deviation, not an undefined one.
    downside_deviation = float(
        empyrical.downside_risk(series, required_return=risk_free_rate, annualization=1)  # type: ignore[arg-type]
    )
    if downside_deviation <= _ZERO_DEVIATION_TOLERANCE:
        return MetricsResult(
            status="UNDEFINED", value=None, sample_size=len(returns), reason="no downside deviation observed",
        )
    if len(series) < 2:
        # `empyrical.sortino_ratio` returns NaN for fewer than two returns;
        # apply its own formula (annualized mean excess return over
        # annualized `downside_risk`) for the single-return case.
        annualization = _annualization(annualize)
        annualized_downside = float(
            empyrical.downside_risk(series, required_return=risk_free_rate, annualization=annualization)  # type: ignore[arg-type]
        )
        ratio = float(series.mean() - risk_free_rate) * annualization / annualized_downside
        return MetricsResult(status="OK", value=ratio, sample_size=len(returns))
    ratio = float(
        empyrical.sortino_ratio(
            series, required_return=risk_free_rate, annualization=_annualization(annualize),  # type: ignore[arg-type]
        )
    )
    return MetricsResult(status="OK", value=ratio, sample_size=len(returns))


def max_drawdown(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = 1) -> MetricsResult:
    """Drawdown of the cumulative-return equity curve built purely from
    sequential completed-evaluation returns (ordered as given — callers
    must pass evaluations in chronological order). Zero or negative."""
    returns = _completed_returns(evaluations)
    if len(returns) < min_sample_size:
        return _insufficient(len(returns), f"need at least {min_sample_size} completed evaluations")
    if not returns:
        return MetricsResult(status="OK", value=0.0, sample_size=0)
    # empyrical reports a never-below-peak curve as -0.0; normalize to 0.0.
    value = float(empyrical.max_drawdown(_float_series(returns))) + 0.0
    return MetricsResult(status="OK", value=value, sample_size=len(returns))


def calmar_ratio(evaluations: list[RecommendationEvaluation], *, min_sample_size: int = MIN_SAMPLE_SIZE) -> MetricsResult:
    cumulative = cumulative_return(evaluations, min_sample_size=min_sample_size)
    drawdown = max_drawdown(evaluations, min_sample_size=min_sample_size)
    if cumulative.status != "OK" or drawdown.status != "OK":
        return _insufficient(cumulative.sample_size, "need sufficient data for both cumulative return and drawdown")
    # status == "OK" guarantees both are the float set above, never None.
    assert cumulative.value is not None and drawdown.value is not None
    if drawdown.value == 0:
        return MetricsResult(
            status="UNDEFINED", value=None, sample_size=cumulative.sample_size,
            reason="Calmar ratio is undefined with zero max drawdown",
        )
    return MetricsResult(
        status="OK", value=float(cumulative.value) / abs(float(drawdown.value)), sample_size=cumulative.sample_size,
    )


def recommendation_to_fill_rate(
    total_recommendations: int, evaluations: list[RecommendationEvaluation],
) -> MetricsResult:
    if total_recommendations <= 0:
        return _insufficient(0, "no recommendations to compute a fill rate from")
    filled = len({e.recommendation_id for e in evaluations if e.status in ("COMPLETED", "PARTIALLY_FILLED")})
    return MetricsResult(status="OK", value=Decimal(filled) / Decimal(total_recommendations), sample_size=total_recommendations)


def group_by(evaluations: list[RecommendationEvaluation], *, key: str) -> dict[str, list[RecommendationEvaluation]]:
    """Groups completed/partially-filled evaluations by an attribute name
    (docs/milestone-4.md Step 12: "performance by model" / "by prompt
    version" / "by configuration hash" / "by market regime")."""
    groups: dict[str, list[RecommendationEvaluation]] = {}
    for e in evaluations:
        if e.status not in ("COMPLETED", "PARTIALLY_FILLED"):
            continue
        value = getattr(e, key, None)
        if value is None:
            continue
        groups.setdefault(str(value), []).append(e)
    return groups
