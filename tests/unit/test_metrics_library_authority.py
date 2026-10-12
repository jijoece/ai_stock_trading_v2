"""Removal-parity proof for library-migration PR 17
(`docs/library-migration/REMOVAL_MANIFEST.md`'s "Custom analytics formulas"
row, `DECISIONS.md` D15).

PR 17 removed `evaluation/metrics.py`'s hand-written `cumulative_return`,
`sharpe_ratio`, `sortino_ratio`, `max_drawdown`, and `calmar_ratio`
formulas; `empyrical-reloaded` is now their authority. PR 11 proved the
library agreed with those formulas by running both side by side. With the
custom formulas gone, that side-by-side comparison is impossible, so the
expected values below are the removed formulas' own outputs, captured on
these exact fixtures at `origin/main` `3906935` immediately before the
removal (`docs/library-migration/pr17/legacy_golden_values.txt`). Status
and sample size must match exactly; values must match to float noise.

The removed `cumulative_return`/`max_drawdown`/`calmar_ratio` returned
`Decimal`; the library-backed ones return `float` (D15). The golden values
are stored as the removed implementation's exact decimal strings and
compared numerically, never by type.

`empyrical-reloaded` is a base dependency as of PR 17, so this file has no
`importorskip` guard: a missing library is a real failure.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import empyrical
import pandas as pd
import pytest

from trading_research.evaluation import metrics
from trading_research.evaluation.models import RecommendationEvaluation

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _completed(rec_id: str, net_return: str) -> RecommendationEvaluation:
    return RecommendationEvaluation(
        recommendation_id=rec_id, horizon_trading_days=1, status="COMPLETED", evaluation_date=date(2026, 7, 14),
        benchmark_symbol="SPY", recommendation_price=Decimal("10"), execution_price=Decimal("10"),
        ending_symbol_price=Decimal("11"), ending_benchmark_price=Decimal("11"),
        benchmark_price_at_execution=Decimal("10"), gross_return=Decimal(net_return),
        net_return=Decimal(net_return), benchmark_return=Decimal("0"), excess_return=Decimal(net_return),
        evaluated_at=NOW,
    )


def _series(*values: str) -> list[RecommendationEvaluation]:
    return [_completed(f"rec-{i}", v) for i, v in enumerate(values)]


FIXTURES = {
    "oscillating_6": _series("0.05", "-0.02", "0.03", "0.01", "-0.01", "0.04"),
    "increasing_5": _series("0.10", "0.20", "0.30", "0.40", "0.50"),
    "drawdown_5": _series("0.10", "-0.20", "0.05", "0.05", "0.05"),
    "mixed_20": _series(
        "0.01", "-0.02", "0.015", "-0.005", "0.02", "-0.03", "0.01", "0.04", "-0.01", "0.02",
        "-0.015", "0.005", "0.03", "-0.02", "0.01", "-0.01", "0.02", "-0.005", "0.015", "-0.02",
    ),
    "flat_6": _series(*["0.05"] * 6),
    "below_minimum_3": _series(*["0.05"] * 3),
    "single_1": _series("0.05"),
    "empty": [],
}

OK, INSUFFICIENT, UNDEFINED = "OK", "INSUFFICIENT_DATA", "UNDEFINED"

# (fixture, metric) -> (status, sample_size, removed implementation's value)
LEGACY = {
    ("oscillating_6", "cumulative_return"): (OK, 6, "0.102154573520"),
    ("oscillating_6", "max_drawdown"): (OK, 6, "-0.02"),
    ("oscillating_6", "calmar_ratio"): (OK, 6, "5.1077286760"),
    ("oscillating_6", "sharpe_annualized"): (OK, 6, "9.43308278599619"),
    ("oscillating_6", "sharpe_raw"): (OK, 6, "0.5942283606769104"),
    ("oscillating_6", "sortino_annualized"): (OK, 6, "28.98275349237888"),
    ("oscillating_6", "sortino_raw"): (OK, 6, "1.8257418583505538"),
    ("increasing_5", "cumulative_return"): (OK, 5, "2.6036000000"),
    ("increasing_5", "max_drawdown"): (OK, 5, "0"),
    ("increasing_5", "calmar_ratio"): (UNDEFINED, 5, None),
    ("increasing_5", "sharpe_annualized"): (OK, 5, "30.11976095522672"),
    ("increasing_5", "sharpe_raw"): (OK, 5, "1.8973665961010275"),
    ("increasing_5", "sortino_annualized"): (UNDEFINED, 5, None),
    ("increasing_5", "sortino_raw"): (UNDEFINED, 5, None),
    ("drawdown_5", "cumulative_return"): (OK, 5, "0.0187100000"),
    ("drawdown_5", "max_drawdown"): (OK, 5, "-0.20"),
    ("drawdown_5", "calmar_ratio"): (OK, 5, "0.09355000"),
    ("drawdown_5", "sharpe_annualized"): (OK, 5, "1.3298199324087374"),
    ("drawdown_5", "sharpe_raw"): (OK, 5, "0.0837707816583391"),
    ("drawdown_5", "sortino_annualized"): (OK, 5, "1.7748239349298847"),
    ("drawdown_5", "sortino_raw"): (OK, 5, "0.11180339887498947"),
    ("mixed_20", "cumulative_return"): (OK, 20, "0.058122841252336630648612283"),
    ("mixed_20", "max_drawdown"): (OK, 20, "-0.03"),
    ("mixed_20", "calmar_ratio"): (OK, 20, "1.937428041744554354953742767"),
    ("mixed_20", "sharpe_annualized"): (OK, 20, "2.5044937777861285"),
    ("mixed_20", "sharpe_raw"): (OK, 20, "0.15776827847930378"),
    ("mixed_20", "sortino_annualized"): (OK, 20, "4.197086368015617"),
    ("mixed_20", "sortino_raw"): (OK, 20, "0.26439158954353903"),
    ("flat_6", "cumulative_return"): (OK, 6, "0.340095640625"),
    ("flat_6", "max_drawdown"): (OK, 6, "0"),
    ("flat_6", "calmar_ratio"): (UNDEFINED, 6, None),
    ("flat_6", "sharpe_annualized"): (UNDEFINED, 6, None),
    ("flat_6", "sharpe_raw"): (UNDEFINED, 6, None),
    ("flat_6", "sortino_annualized"): (UNDEFINED, 6, None),
    ("flat_6", "sortino_raw"): (UNDEFINED, 6, None),
    ("below_minimum_3", "cumulative_return"): (OK, 3, "0.157625"),
    ("below_minimum_3", "max_drawdown"): (OK, 3, "0"),
    ("below_minimum_3", "calmar_ratio"): (INSUFFICIENT, 3, None),
    ("below_minimum_3", "sharpe_annualized"): (INSUFFICIENT, 3, None),
    ("below_minimum_3", "sharpe_raw"): (INSUFFICIENT, 3, None),
    ("below_minimum_3", "sortino_annualized"): (INSUFFICIENT, 3, None),
    ("below_minimum_3", "sortino_raw"): (INSUFFICIENT, 3, None),
    ("single_1", "cumulative_return"): (OK, 1, "0.05"),
    ("single_1", "max_drawdown"): (OK, 1, "0"),
    ("single_1", "calmar_ratio"): (INSUFFICIENT, 1, None),
    ("single_1", "sharpe_annualized"): (INSUFFICIENT, 1, None),
    ("single_1", "sharpe_raw"): (INSUFFICIENT, 1, None),
    ("single_1", "sortino_annualized"): (INSUFFICIENT, 1, None),
    ("single_1", "sortino_raw"): (INSUFFICIENT, 1, None),
    ("empty", "cumulative_return"): (INSUFFICIENT, 0, None),
    ("empty", "max_drawdown"): (INSUFFICIENT, 0, None),
    ("empty", "calmar_ratio"): (INSUFFICIENT, 0, None),
    ("empty", "sharpe_annualized"): (INSUFFICIENT, 0, None),
    ("empty", "sharpe_raw"): (INSUFFICIENT, 0, None),
    ("empty", "sortino_annualized"): (INSUFFICIENT, 0, None),
    ("empty", "sortino_raw"): (INSUFFICIENT, 0, None),
}

METRICS = {
    "cumulative_return": metrics.cumulative_return,
    "max_drawdown": metrics.max_drawdown,
    "calmar_ratio": metrics.calmar_ratio,
    "sharpe_annualized": lambda e: metrics.sharpe_ratio(e, annualize=True),
    "sharpe_raw": lambda e: metrics.sharpe_ratio(e, annualize=False),
    "sortino_annualized": lambda e: metrics.sortino_ratio(e, annualize=True),
    "sortino_raw": lambda e: metrics.sortino_ratio(e, annualize=False),
}


def _assert_matches_legacy(result: metrics.MetricsResult, status: str, sample_size: int, legacy: str | None) -> None:
    assert (result.status, result.sample_size) == (status, sample_size)
    if legacy is None:
        assert result.value is None
        return
    assert isinstance(result.value, float)
    assert math.isclose(result.value, float(legacy), rel_tol=1e-9, abs_tol=1e-12), (result.value, legacy)


def test_golden_table_covers_every_fixture_and_metric():
    assert set(LEGACY) == {(f, m) for f in FIXTURES for m in METRICS}


@pytest.mark.parametrize(("fixture", "metric"), sorted(LEGACY))
def test_library_backed_metric_matches_removed_formula(fixture, metric):
    status, sample_size, legacy = LEGACY[(fixture, metric)]
    _assert_matches_legacy(METRICS[metric](FIXTURES[fixture]), status, sample_size, legacy)


@pytest.mark.parametrize(
    ("metric", "legacy"),
    [(metrics.sharpe_ratio, "3.7732331143984754"), (metrics.sortino_ratio, "7.189736274183337")],
)
def test_nonzero_risk_free_rate_matches_removed_formula(metric, legacy):
    result = metric(FIXTURES["oscillating_6"], risk_free_rate=0.01)
    _assert_matches_legacy(result, OK, 6, legacy)


@pytest.mark.parametrize("metric", [metrics.cumulative_return, metrics.max_drawdown])
def test_empty_sample_with_zero_minimum_is_exactly_zero_not_nan(metric):
    # empyrical returns NaN for an empty series; the removed formulas
    # returned an exact zero (empty product, flat equity curve).
    result = metric([], min_sample_size=0)
    assert (result.status, result.value, result.sample_size) == (OK, 0.0, 0)


@pytest.mark.parametrize("metric", [metrics.sharpe_ratio, metrics.sortino_ratio])
def test_single_return_with_minimum_one_is_undefined(metric):
    # One return at or above the required return: the removed formulas
    # found zero deviation (no variance; no downside); empyrical would
    # return NaN.
    result = metric(FIXTURES["single_1"], min_sample_size=1)
    assert (result.status, result.value, result.sample_size) == (UNDEFINED, None, 1)


@pytest.mark.parametrize(
    ("returns", "risk_free_rate", "annualize", "legacy"),
    [
        (("-0.05",), 0.0, True, "-15.874507866387544"),
        (("-0.05",), 0.0, False, "-1.0"),
        (("0.01",), 0.02, True, "-15.874507866387544"),
        (("0.01",), 0.02, False, "-1.0"),
    ],
)
def test_single_return_below_required_return_sortino_matches_removed_formula(
    returns, risk_free_rate, annualize, legacy,
):
    # One return below the required return has a nonzero downside
    # deviation, so the removed formula returned a valid ratio (captured by
    # running `sortino_ratio` from `3906935`). `empyrical.sortino_ratio`
    # returns NaN for fewer than two returns; `empyrical.downside_risk`
    # does not.
    result = metrics.sortino_ratio(
        _series(*returns), risk_free_rate=risk_free_rate, min_sample_size=1, annualize=annualize,
    )
    _assert_matches_legacy(result, OK, 1, legacy)


def test_flat_returns_sharpe_is_undefined_not_the_library_huge_finite_value():
    # The fail-closed boundary metrics.py applies before calling the library.
    raw = float(empyrical.sharpe_ratio(pd.Series([0.05] * 6)))
    assert not math.isfinite(raw) or abs(raw) > 1e6
    assert metrics.sharpe_ratio(FIXTURES["flat_6"]).status == UNDEFINED


def test_no_downside_sortino_is_undefined_not_the_library_inf():
    raw = float(empyrical.sortino_ratio(pd.Series([0.10, 0.20, 0.30, 0.40, 0.50])))
    assert math.isinf(raw)
    assert metrics.sortino_ratio(FIXTURES["increasing_5"]).status == UNDEFINED


def test_calmar_is_composed_and_diverges_from_raw_empyrical_calmar():
    # empyrical.calmar_ratio uses a CAGR numerator; the repository's
    # convention is raw cumulative return over max drawdown.
    composed = metrics.calmar_ratio(FIXTURES["oscillating_6"])
    assert composed.status == OK and composed.value is not None
    returns = pd.Series([0.05, -0.02, 0.03, 0.01, -0.01, 0.04])
    for raw in (empyrical.calmar_ratio(returns, period="daily"), empyrical.calmar_ratio(returns, annualization=1)):
        assert not math.isclose(float(raw), float(composed.value), rel_tol=1e-6)


@pytest.mark.parametrize("fixture", ["increasing_5", "flat_6", "single_1"])
def test_max_drawdown_without_a_decline_is_positive_zero(fixture):
    result = metrics.max_drawdown(FIXTURES[fixture])
    assert isinstance(result.value, float)
    assert result.value == 0.0 and math.copysign(1.0, result.value) == 1.0
