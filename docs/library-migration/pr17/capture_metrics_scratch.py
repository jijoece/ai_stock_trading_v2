"""Library-migration PR 17 scratch script (not a test, not production code).

Prints `evaluation/metrics.py`'s status/sample size/value for the five
metrics whose custom formulas PR 17 removed, on every fixture in
`tests/unit/test_metrics_library_authority.py`.

Run from the repository root:

    .venv/bin/python docs/library-migration/pr17/capture_metrics_scratch.py

`legacy_golden_values.txt` is this script's output at `origin/main`
`3906935`, before the removal (at that commit it read the same fixture
values from PR 11's `tests/unit/test_analytics_parity.py`, plus a
`single_1` fixture). `library_backed_values.txt` is its output after the
removal. Statuses are identical; values differ only by float noise.
"""
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests/unit")

from test_metrics_library_authority import FIXTURES  # noqa: E402

from trading_research.evaluation import metrics  # noqa: E402

for name, ev in FIXTURES.items():
    rows = [
        ("cumulative_return", metrics.cumulative_return(ev)),
        ("max_drawdown", metrics.max_drawdown(ev)),
        ("calmar_ratio", metrics.calmar_ratio(ev)),
        ("sharpe_ratio[annualize=True]", metrics.sharpe_ratio(ev)),
        ("sharpe_ratio[annualize=False]", metrics.sharpe_ratio(ev, annualize=False)),
        ("sortino_ratio[annualize=True]", metrics.sortino_ratio(ev)),
        ("sortino_ratio[annualize=False]", metrics.sortino_ratio(ev, annualize=False)),
    ]
    for metric, r in rows:
        v = None if r.value is None else float(r.value)
        print(f"{name:16} {metric:31} {r.status:17} n={r.sample_size:<3} {type(r.value).__name__:8} {v!r} {r.value!s}")
for label, r in [
    ("empty min=0 cumulative", metrics.cumulative_return([], min_sample_size=0)),
    ("empty min=0 max_drawdown", metrics.max_drawdown([], min_sample_size=0)),
    ("single min=1 sharpe", metrics.sharpe_ratio(FIXTURES["single_1"], min_sample_size=1)),
    ("single min=1 sortino", metrics.sortino_ratio(FIXTURES["single_1"], min_sample_size=1)),
    ("oscillating rf=0.01 sharpe", metrics.sharpe_ratio(FIXTURES["oscillating_6"], risk_free_rate=0.01)),
    ("oscillating rf=0.01 sortino", metrics.sortino_ratio(FIXTURES["oscillating_6"], risk_free_rate=0.01)),
]:
    print(f"{label:30} {r.status:17} {r.value!r}")
