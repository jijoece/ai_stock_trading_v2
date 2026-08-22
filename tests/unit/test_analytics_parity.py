"""Fixture-parity proof for library-migration PR 11
(`docs/library-migration/MASTER_PLAN.md` row 11,
`docs/library-migration/REMOVAL_MANIFEST.md`'s "Custom analytics formulas"
row): every `evaluation/analytics_parity.py` function must agree with its
`evaluation/metrics.py` counterpart on status (`OK`/`INSUFFICIENT_DATA`/
`UNDEFINED`) always, and on value whenever both are `OK` — the exact
condition `REMOVAL_MANIFEST.md` requires before PR 17 may remove the custom
formulas. This file proves parity; it does not wire `analytics_parity`
into any production caller (see that module's docstring and
`test_analytics_parity_import_boundary.py`).

Requires the `analytics` extra (`empyrical-reloaded`, `quantstats-lumi`);
skips under `main-tests` (`.[dev]` only) via the `pytest.importorskip`
below and runs for real in the dedicated `analytics-tests` CI job
(`.github/workflows/ci.yml`), matching the `indicators-tests`/
`research-tests` precedent.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip("empyrical")
pytest.importorskip("quantstats_lumi")

from trading_research.evaluation import analytics_parity, metrics
from trading_research.evaluation.models import RecommendationEvaluation

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _completed(rec_id: str, net_return: str, **overrides) -> RecommendationEvaluation:
    base = dict(
        recommendation_id=rec_id, horizon_trading_days=1, status="COMPLETED", evaluation_date=date(2026, 7, 14),
        benchmark_symbol="SPY", recommendation_price=Decimal("10"), execution_price=Decimal("10"),
        ending_symbol_price=Decimal("11"), ending_benchmark_price=Decimal("11"),
        benchmark_price_at_execution=Decimal("10"), gross_return=Decimal(net_return),
        net_return=Decimal(net_return), benchmark_return=Decimal("0"), excess_return=Decimal(net_return),
        evaluated_at=NOW,
    )
    base.update(overrides)
    return RecommendationEvaluation(**base)


OSCILLATING_6 = [_completed(f"rec-{i}", v) for i, v in enumerate(
    ["0.05", "-0.02", "0.03", "0.01", "-0.01", "0.04"]
)]
INCREASING_5 = [_completed(f"rec-{i}", v) for i, v in enumerate(
    ["0.10", "0.20", "0.30", "0.40", "0.50"]
)]
DRAWDOWN_5 = [_completed(f"rec-{i}", v) for i, v in enumerate(
    ["0.10", "-0.20", "0.05", "0.05", "0.05"]
)]
MIXED_20 = [_completed(f"rec-{i}", v) for i, v in enumerate(
    ["0.01", "-0.02", "0.015", "-0.005", "0.02", "-0.03", "0.01", "0.04", "-0.01", "0.02",
     "-0.015", "0.005", "0.03", "-0.02", "0.01", "-0.01", "0.02", "-0.005", "0.015", "-0.02"]
)]
FLAT_6 = [_completed(f"rec-{i}", "0.05") for i in range(6)]
BELOW_MINIMUM_3 = [_completed(f"rec-{i}", "0.05") for i in range(3)]

FIXTURES = {
    "oscillating_6": OSCILLATING_6,
    "increasing_5": INCREASING_5,
    "drawdown_5": DRAWDOWN_5,
    "mixed_20": MIXED_20,
    "flat_6": FLAT_6,
    "below_minimum_3": BELOW_MINIMUM_3,
    "empty": [],
}


def _assert_parity(custom, parity, *, rel_tol: float = 1e-9) -> None:
    assert parity.status == custom.status, (parity.status, custom.status)
    assert parity.sample_size == custom.sample_size
    if custom.status != "OK":
        assert parity.value is None and custom.value is None
        return
    assert math.isclose(float(parity.value), float(custom.value), rel_tol=rel_tol, abs_tol=1e-9)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_cumulative_return_parity(name):
    evaluations = FIXTURES[name]
    _assert_parity(metrics.cumulative_return(evaluations), analytics_parity.cumulative_return_parity(evaluations))


@pytest.mark.parametrize("name", list(FIXTURES))
def test_max_drawdown_parity(name):
    evaluations = FIXTURES[name]
    _assert_parity(metrics.max_drawdown(evaluations), analytics_parity.max_drawdown_parity(evaluations))


@pytest.mark.parametrize("name", list(FIXTURES))
@pytest.mark.parametrize("annualize", [True, False])
def test_sharpe_ratio_parity(name, annualize):
    evaluations = FIXTURES[name]
    _assert_parity(
        metrics.sharpe_ratio(evaluations, annualize=annualize),
        analytics_parity.sharpe_ratio_parity(evaluations, annualize=annualize),
    )


@pytest.mark.parametrize("name", list(FIXTURES))
@pytest.mark.parametrize("annualize", [True, False])
def test_sortino_ratio_parity(name, annualize):
    evaluations = FIXTURES[name]
    _assert_parity(
        metrics.sortino_ratio(evaluations, annualize=annualize),
        analytics_parity.sortino_ratio_parity(evaluations, annualize=annualize),
    )


@pytest.mark.parametrize("name", list(FIXTURES))
def test_calmar_ratio_parity(name):
    evaluations = FIXTURES[name]
    _assert_parity(metrics.calmar_ratio(evaluations), analytics_parity.calmar_ratio_parity(evaluations))


def test_sharpe_ratio_undefined_on_flat_returns_not_a_huge_finite_number():
    # empyrical.sharpe_ratio itself does not special-case ~zero variance: it
    # returns a huge finite float (~1e17 observed) rather than UNDEFINED.
    # This is the boundary correction analytics_parity.py documents.
    result = analytics_parity.sharpe_ratio_parity(FLAT_6)
    assert result.status == "UNDEFINED"


def test_sortino_ratio_undefined_with_no_downside_not_inf():
    result = analytics_parity.sortino_ratio_parity(INCREASING_5)
    assert result.status == "UNDEFINED"


def test_calmar_ratio_undefined_with_zero_drawdown_not_inf():
    result = analytics_parity.calmar_ratio_parity(INCREASING_5)
    assert result.status == "UNDEFINED"


def test_calmar_ratio_parity_diverges_from_raw_empyrical_calmar_ratio():
    # Documents why calmar_ratio_parity is composed from cum_returns_final/
    # max_drawdown rather than calling empyrical.calmar_ratio() directly.
    import empyrical
    import pandas as pd

    returns = pd.Series([0.05, -0.02, 0.03, 0.01, -0.01, 0.04])
    composed = analytics_parity.calmar_ratio_parity(OSCILLATING_6)
    assert composed.status == "OK"
    raw_daily = float(empyrical.calmar_ratio(returns, period="daily"))
    raw_unannualized = float(empyrical.calmar_ratio(returns, annualization=1))
    assert not math.isclose(raw_daily, float(composed.value), rel_tol=1e-6)
    assert not math.isclose(raw_unannualized, float(composed.value), rel_tol=1e-6)


def test_presentation_summary_returns_bounded_keys():
    summary = analytics_parity.presentation_summary(OSCILLATING_6)
    assert set(summary) == {"sharpe", "sortino", "max_drawdown"}
    assert all(isinstance(v, float) for v in summary.values())


def test_presentation_summary_empty_evaluations():
    summary = analytics_parity.presentation_summary([])
    assert summary == {"sharpe": None, "sortino": None, "max_drawdown": None}
