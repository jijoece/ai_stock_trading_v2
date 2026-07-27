"""Library-migration PR 5: VectorBT-backed `vector_research` adapter coverage.

`vectorbt` is not part of `.[dev]`, so the behavioral tests in this file
skip (not fail) when VectorBT is unavailable -- this keeps the ordinary
`pytest tests/ -q` suite green on a plain `.[dev]` install, while the
dedicated `research-tests` CI job (which installs the `research` extra)
runs every test in this file for real. The missing-dependency test below
is the exception: it runs unconditionally in every environment, matching
the pattern already established for TA-Lib in `tests/unit/test_indicators.py`.

See docs/library-migration/STATUS.md for the PR 5 outcome record.
"""
from __future__ import annotations

import importlib
import sys

import numpy as np
import pandas as pd
import pytest

_ADAPTER_MODULE = "trading_research.vector_research.adapter"
_PACKAGE_MODULE = "trading_research.vector_research"


def _close(n: int = 100) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(0)
    prices = 100 + np.cumsum(rng.normal(size=n))
    return pd.Series(np.clip(prices, 1.0, None), index=idx)


def _signal_frame(close: pd.Series, columns=("p1", "p2")) -> tuple[pd.DataFrame, pd.DataFrame]:
    entries = pd.DataFrame(False, index=close.index, columns=list(columns))
    exits = pd.DataFrame(False, index=close.index, columns=list(columns))
    entries.iloc[10] = True
    exits.iloc[50] = True
    return entries, exits


# ---------------------------------------------------------------------
# Missing-dependency guard -- must run in every environment, including one
# where VectorBT truly is not installed (the main-tests CI job).
# ---------------------------------------------------------------------

def test_missing_vectorbt_raises_actionable_import_error(monkeypatch):
    sys.modules.pop(_ADAPTER_MODULE, None)
    sys.modules.pop(_PACKAGE_MODULE, None)
    monkeypatch.setitem(sys.modules, "vectorbt", None)
    with pytest.raises(ImportError, match=r'pip install -e "\.\[research\]"'):
        importlib.import_module(_ADAPTER_MODULE)
    sys.modules.pop(_ADAPTER_MODULE, None)
    sys.modules.pop(_PACKAGE_MODULE, None)


# ---------------------------------------------------------------------
# Behavioral coverage -- requires VectorBT actually installed.
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def adapter():
    pytest.importorskip("vectorbt")
    return importlib.import_module(_ADAPTER_MODULE)


def test_run_parameter_sweep_returns_one_result_per_parameter_column(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    result = adapter.run_parameter_sweep(close, entries, exits)
    assert list(result.total_return.index) == ["p1", "p2"]
    assert list(result.sharpe_ratio.index) == ["p1", "p2"]
    assert list(result.max_drawdown.index) == ["p1", "p2"]


def test_result_carries_no_execution_authority_surface(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    result = adapter.run_parameter_sweep(close, entries, exits)
    for forbidden in ("submit", "order", "broker", "authorize"):
        assert not hasattr(result, forbidden)


def test_rejects_non_series_close(adapter):
    with pytest.raises(adapter.VectorResearchInputError, match="pandas Series"):
        adapter.run_parameter_sweep([1.0, 2.0], pd.DataFrame(), pd.DataFrame())


def test_rejects_empty_close(adapter):
    with pytest.raises(adapter.VectorResearchInputError, match="not be empty"):
        adapter.run_parameter_sweep(pd.Series(dtype=float), pd.DataFrame(), pd.DataFrame())


def test_rejects_nan_in_close(adapter):
    close = _close()
    close.iloc[5] = float("nan")
    entries, exits = _signal_frame(close)
    with pytest.raises(adapter.VectorResearchInputError, match="NaN"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_positive_price(adapter):
    close = _close()
    close.iloc[0] = 0.0
    entries, exits = _signal_frame(close)
    with pytest.raises(adapter.VectorResearchInputError, match="positive"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_entries_index_mismatch(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    entries = entries.iloc[:-1]
    with pytest.raises(adapter.VectorResearchInputError, match="index must exactly match"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_boolean_signal_frame(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    entries = entries.astype(int)
    with pytest.raises(adapter.VectorResearchInputError, match="boolean-valued"):
        adapter.run_parameter_sweep(close, entries, exits)


def test_rejects_non_positive_init_cash(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    with pytest.raises(adapter.VectorResearchInputError, match="init_cash"):
        adapter.run_parameter_sweep(close, entries, exits, init_cash=0.0)


def test_rejects_negative_fees(adapter):
    close = _close()
    entries, exits = _signal_frame(close)
    with pytest.raises(adapter.VectorResearchInputError, match="fees"):
        adapter.run_parameter_sweep(close, entries, exits, fees=-0.001)
