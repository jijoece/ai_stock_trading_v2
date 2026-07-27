"""Thin adapter around VectorBT for vectorized signal-matrix parameter sweeps.

This module has no execution authority. It never places orders, never
touches `paper_books` accounting, and is not wired into any scheduled or
live code path -- its output (`ParameterSweepResult`) is advisory research
data only, analogous to the existing Claude research overlay's
advisory-only boundary (ADR 0003).

VectorBT is required for this module and is imported at module scope inside
a `try`/`except ImportError` that re-raises with an actionable install
message -- there is no fallback formula, matching the pattern already
established for TA-Lib in `scripts/indicators.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

try:
    import vectorbt as vbt
except ImportError as exc:  # pragma: no cover - exercised by research-tests CI
    raise ImportError(
        "trading_research.vector_research requires VectorBT. "
        'Install the research extra: pip install -e ".[research]"'
    ) from exc


class VectorResearchInputError(ValueError):
    """Malformed or otherwise invalid research input.

    Raised at the validation boundary before any VectorBT call -- this
    module fails closed rather than passing malformed data through to
    VectorBT and trusting its own error surface.
    """


@dataclass(frozen=True)
class ParameterSweepResult:
    """Read-only summary of a vectorized parameter sweep.

    Advisory research output only. `portfolio` is the underlying
    `vectorbt.Portfolio` for callers that need deeper inspection; nothing in
    this dataclass is consumed by `paper_books`, `external_broker`, or any
    order-construction path.
    """

    total_return: pd.Series
    sharpe_ratio: pd.Series
    max_drawdown: pd.Series
    portfolio: "vbt.Portfolio"


def _validate_close(close: pd.Series) -> None:
    if not isinstance(close, pd.Series):
        raise VectorResearchInputError(f"close must be a pandas Series, got {type(close).__name__}")
    if close.empty:
        raise VectorResearchInputError("close must not be empty")
    if close.isna().any():
        raise VectorResearchInputError("close must not contain NaN values")
    if not close.map(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)).all():
        raise VectorResearchInputError("close must contain only numeric prices")
    if not (close > 0).all():
        raise VectorResearchInputError("close must contain only positive prices")


def _validate_signal_frame(name: str, signals: pd.DataFrame, close: pd.Series) -> None:
    if not isinstance(signals, pd.DataFrame):
        raise VectorResearchInputError(f"{name} must be a pandas DataFrame, got {type(signals).__name__}")
    if signals.empty:
        raise VectorResearchInputError(f"{name} must not be empty")
    if not signals.index.equals(close.index):
        raise VectorResearchInputError(f"{name} index must exactly match close's index")
    if signals.dtypes.astype(str).ne("bool").any():
        raise VectorResearchInputError(f"{name} must be boolean-valued (entry/exit signal matrix)")


def run_parameter_sweep(
    close: pd.Series,
    entries: pd.DataFrame,
    exits: pd.DataFrame,
    *,
    init_cash: float = 100_000.0,
    fees: float = 0.0,
) -> ParameterSweepResult:
    """Run a vectorized parameter sweep over a boolean signal matrix.

    `entries`/`exits` are boolean-valued DataFrames sharing `close`'s index,
    one column per parameter combination under evaluation -- VectorBT's
    signal-matrix convention for broadcasting a single price series across
    many strategy variants at once. This is research-only: the returned
    `ParameterSweepResult` is advisory and carries no execution authority.

    Raises `VectorResearchInputError` for any malformed input, before
    VectorBT is ever invoked.
    """
    _validate_close(close)
    _validate_signal_frame("entries", entries, close)
    _validate_signal_frame("exits", exits, close)
    if init_cash <= 0:
        raise VectorResearchInputError(f"init_cash must be positive, got {init_cash}")
    if fees < 0:
        raise VectorResearchInputError(f"fees must be non-negative, got {fees}")

    portfolio = vbt.Portfolio.from_signals(
        close, entries, exits, init_cash=init_cash, fees=fees, freq="1D"
    )
    return ParameterSweepResult(
        total_return=portfolio.total_return(),
        sharpe_ratio=portfolio.sharpe_ratio(),
        max_drawdown=portfolio.max_drawdown(),
        portfolio=portfolio,
    )
