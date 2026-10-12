"""Presentation-only performance summary via `quantstats-lumi`.

**Not authoritative.** `evaluation/metrics.py` is the sole authority for
every reported, compared, or audited performance figure; since
library-migration PR 17 its Sharpe/Sortino/drawdown/cumulative-return
formulas are `empyrical-reloaded` (`docs/library-migration/DECISIONS.md`
D15). `quantstats-lumi` is reporting/presentation only
(`docs/library-migration/DEPENDENCY_MATRIX.md` Section 6: "two independent
authorities over the same metric is a defect, not a feature"), so nothing
here is ever compared against `evaluation/metrics.py` or fed to a decision.

This module was split out of PR 11's `evaluation/analytics_parity.py` when
PR 17 deleted that module's parity candidates, which would otherwise have
become a second empyrical-backed copy of `evaluation/metrics.py`. It has
zero production callers and is the only module that imports
`quantstats_lumi` (`tests/unit/test_analytics_import_boundary.py`).
"""
from __future__ import annotations

try:
    import quantstats_lumi.stats as quantstats_stats
except ImportError as exc:
    raise ImportError(
        "evaluation/analytics_presentation.py requires quantstats-lumi. "
        "Install the analytics extra: pip install -e \".[analytics]\""
    ) from exc

import pandas as pd

from .metrics import ANNUALIZATION_TRADING_DAYS, _completed_returns
from .models import RecommendationEvaluation


def presentation_summary(evaluations: list[RecommendationEvaluation]) -> dict[str, float | None]:
    """Presentation-only Sharpe/Sortino/max-drawdown summary.

    `quantstats_lumi.stats.calmar` is excluded: it requires a real
    `DatetimeIndex` (its `cagr()` calls `.total_seconds()` on the index
    range) and raises `AttributeError` on the plain integer-indexed
    `Series` used here. This repository's 252-trading-day convention is
    passed explicitly via `periods=`, since `quantstats_lumi`'s own default
    (`periods=365`, calendar days) does not match it, but the result may
    still legitimately diverge from `evaluation/metrics.py`.
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
