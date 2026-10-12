"""Presentation-only `quantstats-lumi` summary
(`evaluation/analytics_presentation.py`, library-migration PR 17; split out
of PR 11's `analytics_parity.py`). Never a parity or authority check —
`tests/unit/test_metrics_library_authority.py` covers the authoritative
metrics.

Requires the `analytics` extra (`quantstats-lumi`); skips under
`main-tests` (`.[dev]` only) and runs for real in the `analytics-tests` CI
job, matching the `indicators-tests`/`observability-tests` precedent.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip("quantstats_lumi")

from trading_research.evaluation import analytics_presentation
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


OSCILLATING_6 = [_completed(f"rec-{i}", v) for i, v in enumerate(["0.05", "-0.02", "0.03", "0.01", "-0.01", "0.04"])]


def test_presentation_summary_returns_bounded_keys():
    summary = analytics_presentation.presentation_summary(OSCILLATING_6)
    assert set(summary) == {"sharpe", "sortino", "max_drawdown"}
    assert all(isinstance(v, float) for v in summary.values())


def test_presentation_summary_empty_evaluations():
    summary = analytics_presentation.presentation_summary([])
    assert summary == {"sharpe": None, "sortino": None, "max_drawdown": None}
