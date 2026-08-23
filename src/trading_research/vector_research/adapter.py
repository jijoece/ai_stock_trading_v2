"""Thin adapter around VectorBT for vectorized signal-matrix parameter sweeps.

This module has no execution authority. It never places orders, never
touches `paper_books` accounting, and is not wired into any scheduled or
live code path -- its output (`ParameterSweepResult`) is advisory research
data only, analogous to the existing Claude research overlay's
advisory-only boundary (ADR 0003). No production module outside this
package may import it (enforced by
`tests/unit/test_vector_research_import_boundary.py`); a future consumer
requires an explicit architecture decision and a test update, not a silent
import.

VectorBT is required for this module and is imported at module scope inside
a `try`/`except ImportError` that re-raises with an actionable install
message -- there is no fallback formula, matching the pattern already
established for TA-Lib in `scripts/indicators.py`.

## Signal timing contract (fail-closed, non-negotiable)

`entry_signals`/`exit_signals` represent the bar on which a signal was
*generated* -- e.g. `entry_signals.iloc[t]` is `True` because bar `t`'s own
close crossed some threshold. VectorBT's `Portfolio.from_signals` executes
a `True` entry/exit at that *same* bar's close (verified directly against
VectorBT 1.1.0: an entry set at index 5 of a 10-bar series fills at index 5,
not index 6). Passed straight through, that lets a signal derived from bar
`t`'s own close trade at that identical close -- look-ahead bias for any
signal computed from same-bar data.

This module never does that. Before calling VectorBT, `entry_signals`/
`exit_signals` are shifted forward by exactly one bar
(`.shift(1, fill_value=False)`), so a signal generated at bar `t` becomes
eligible for execution no earlier than bar `t + 1`'s close -- matching this
repository's existing backtest convention that a signal generated during a
session becomes eligible only on the next session. A signal on the final
bar of the input has no later bar to execute on and therefore produces no
fill; this is intentional, not a bug. There is no parameter to disable this
shift -- a caller with already execution-ready (pre-shifted) signals must
shift them back by one bar before calling this function, not bypass the
contract.

## Daily-session, timezone-aware temporal contract

`close.index` must be a timezone-aware, strictly increasing, duplicate-free
`DatetimeIndex` with daily-session spacing: each gap `>= 1` day and
`<= 10` days (tolerating weekends/holiday clusters but rejecting intraday
or monthly/irregular bar spacing), **and** a median bar-to-bar gap of
exactly one day. Gaps are measured in local calendar days (via
`index.date`), not raw elapsed duration between the tz-aware timestamps
themselves -- elapsed wall-clock duration between two consecutive local
calendar days is not fixed at 24 hours across a DST transition (23h in
spring, 25h in fall in `America/New_York`), so a valid daily series that
crosses a DST boundary is never misclassified as intraday. The
median-gap check exists because a systematically wider cadence -- most
notably weekly (`freq="7D"`) data -- has a uniform gap that falls
entirely within the `[1, 10]` day bound and would otherwise silently
pass the min/max check alone while this adapter still executes with
`freq="1D"`, corrupting every annualized statistic. A genuine daily
session calendar (including weekend/holiday gaps) always has a majority
of one-day gaps, so its median gap is one day; a uniform weekly (or
coarser) cadence never does. Requiring
timezone-awareness mirrors `evaluation/market_calendar.py`'s existing
fail-closed convention (`is_market_open` "requires a timezone-aware
datetime"); this module does not guess a timezone for tz-naive input.
`entry_signals`/`exit_signals` must share `close`'s index exactly, including
timezone -- `pandas.Index.equals` treats two same-instant indexes with
different timezone labels as unequal, so this boundary also catches a
timezone mismatch, not only a raw value mismatch.

## Exploratory-only analytics contract

`total_return`/`sharpe_ratio`/`max_drawdown` are VectorBT's own vectorized
statistics -- fast, useful for coarse relative ranking across a parameter
sweep, but **not** this repository's authoritative performance-metrics
implementation. `evaluation/metrics.py` remains authoritative for any
reported, compared, or audited performance figure -- PR 11 proved fixture
parity against `empyrical-reloaded` in a new, additive
`evaluation/analytics_parity.py` (zero production callers), but did not
replace `metrics.py`'s formulas; removal is deferred to PR 17
(`docs/library-migration/DECISIONS.md` D9). VectorBT's Sharpe uses a
`year_freq` annualization assumption that need not match either
implementation's convention. Every
`ParameterSweepResult` carries `metric_source = "VECTORBT_EXPLORATORY"`
plus the exact `frequency`/`year_freq` assumption used, so a caller (or a
future automated check) can never mistake one for the other. Each metric
is wrapped in an `ExploratoryMetric(value, status)`: `status` is `"ok"`,
`"no_trades"` (VectorBT reports a column with zero trades), or
`"zero_variance"`/`"non_finite"` for a non-finite VectorBT-reported result
attributable to zero return variance or another cause respectively.
Whenever `status != "ok"`, `value` is `None` -- a raw NaN/inf value is
never returned for ranking or selection logic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
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


METRIC_SOURCE = "VECTORBT_EXPLORATORY"

_MIN_SESSION_GAP = pd.Timedelta("1D")
_MAX_SESSION_GAP = pd.Timedelta("10D")
_EXPECTED_MEDIAN_SESSION_GAP = pd.Timedelta("1D")
_MIN_BARS_FOR_SWEEP = 10
_BAR_FREQ = "1D"
_YEAR_FREQ = "365 days"


@dataclass(frozen=True)
class ExploratoryMetric:
    """One exploratory ranking value, explicitly not authoritative.

    `status` is one of `"ok"`, `"no_trades"`, `"zero_variance"`, or
    `"non_finite"`. `value` is `None` whenever `status != "ok"` -- callers
    must branch on `status` before using `value` for ranking or selection.
    """

    value: Optional[float]
    status: str


@dataclass(frozen=True)
class ParameterSweepResult:
    """Read-only summary of a vectorized parameter sweep.

    Advisory, exploratory research output only -- see this module's
    docstring for the exact analytics-authority boundary. `portfolio` is
    the underlying `vectorbt.Portfolio`, a raw analysis artifact for
    deeper inspection; it must never be passed to `paper_books`,
    `execution`, `runtime`, or any broker/scheduling interface without an
    explicit, reviewed conversion into a framework-neutral research DTO --
    no such conversion exists yet because no consumer exists yet.
    """

    metric_source: str
    frequency: str
    year_freq: str
    total_return: dict[str, ExploratoryMetric]
    sharpe_ratio: dict[str, ExploratoryMetric]
    max_drawdown: dict[str, ExploratoryMetric]
    trade_count: dict[str, int]
    portfolio: "vbt.Portfolio"


def _validate_daily_session_spacing(index: pd.DatetimeIndex) -> None:
    if len(index) < 2:
        return
    # Diff local calendar dates, not raw elapsed duration on the tz-aware
    # timestamps themselves -- a DST transition changes the elapsed wall-clock
    # duration between two consecutive local calendar days (23h in spring,
    # 25h in fall in America/New_York) without changing the number of
    # calendar days that elapsed. `index.date` extracts each timestamp's own
    # local calendar date (already resolved in the index's timezone);
    # rewrapping that in a tz-naive DatetimeIndex and diffing gives exact,
    # DST-independent whole-day gaps.
    local_days = pd.DatetimeIndex(index.date)
    diffs = local_days[1:] - local_days[:-1]
    if (diffs < _MIN_SESSION_GAP).any():
        raise VectorResearchInputError(
            "close.index spacing is finer than one day -- this adapter supports "
            "daily-session data only, not intraday bars"
        )
    if (diffs > _MAX_SESSION_GAP).any():
        raise VectorResearchInputError(
            f"close.index contains a gap larger than {_MAX_SESSION_GAP} -- this adapter "
            "supports daily-session data only, not weekly/monthly or irregular bars"
        )
    median_gap = pd.Series(diffs).median()
    if median_gap != _EXPECTED_MEDIAN_SESSION_GAP:
        raise VectorResearchInputError(
            f"close.index's median bar-to-bar gap is {median_gap}, not "
            f"{_EXPECTED_MEDIAN_SESSION_GAP} -- this adapter requires a genuine daily-session "
            "cadence (occasional weekend/holiday gaps tolerated, but the typical gap must be "
            "one day), not a systematically wider cadence such as weekly or monthly bars that "
            "would otherwise pass the min/max gap bound alone"
        )


def _validate_close(close: pd.Series) -> None:
    if not isinstance(close, pd.Series):
        raise VectorResearchInputError(f"close must be a pandas Series, got {type(close).__name__}")
    if close.empty:
        raise VectorResearchInputError("close must not be empty")
    if not isinstance(close.index, pd.DatetimeIndex):
        raise VectorResearchInputError("close.index must be a pandas DatetimeIndex")
    if close.index.tz is None:
        raise VectorResearchInputError(
            "close.index must be timezone-aware -- this adapter requires an explicit "
            "timezone policy, matching evaluation/market_calendar.py's tz-aware convention; "
            "localize the index (e.g. close.tz_localize(...)) before calling run_parameter_sweep"
        )
    if not close.index.is_unique:
        raise VectorResearchInputError("close.index must not contain duplicate timestamps")
    if not close.index.is_monotonic_increasing:
        raise VectorResearchInputError("close.index must be strictly increasing (sorted ascending)")
    _validate_daily_session_spacing(close.index)
    if len(close) < _MIN_BARS_FOR_SWEEP:
        raise VectorResearchInputError(
            f"close must contain at least {_MIN_BARS_FOR_SWEEP} bars for a meaningful "
            f"parameter sweep, got {len(close)}"
        )
    if pd.api.types.is_bool_dtype(close.dtype):
        raise VectorResearchInputError(
            "close must contain numeric prices, got boolean dtype -- boolean values are not "
            "valid prices"
        )
    if close.isna().any():
        raise VectorResearchInputError("close must not contain NaN values")
    try:
        values = close.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise VectorResearchInputError(
            f"close could not be converted to numeric prices: {exc}"
        ) from exc
    if not pd.api.types.is_numeric_dtype(close.dtype):
        raise VectorResearchInputError(
            f"close must have a numeric dtype, got {close.dtype} -- numeric-looking strings "
            "are not accepted as prices, even when individually convertible to float"
        )
    if not np.isfinite(values).all():
        raise VectorResearchInputError("close must contain only finite values (no +/-infinity)")
    if not (values > 0).all():
        raise VectorResearchInputError("close must contain only positive prices")


def _validate_signal_frame(name: str, signals: pd.DataFrame, close: pd.Series) -> None:
    if not isinstance(signals, pd.DataFrame):
        raise VectorResearchInputError(f"{name} must be a pandas DataFrame, got {type(signals).__name__}")
    if signals.empty:
        raise VectorResearchInputError(f"{name} must not be empty")
    if not signals.columns.is_unique:
        raise VectorResearchInputError(f"{name} columns must be unique")
    if not signals.index.equals(close.index):
        raise VectorResearchInputError(
            f"{name}.index must exactly match close's index, including timezone"
        )
    if signals.dtypes.astype(str).ne("bool").any():
        raise VectorResearchInputError(f"{name} must be boolean-valued (entry/exit signal matrix)")
    if signals.isna().any().any():
        raise VectorResearchInputError(f"{name} must not contain missing values")


def _validate_init_cash(init_cash: float) -> None:
    if isinstance(init_cash, bool) or not isinstance(init_cash, (int, float)):
        raise VectorResearchInputError(
            f"init_cash must be numeric (not bool), got {type(init_cash).__name__}"
        )
    if not math.isfinite(init_cash):
        raise VectorResearchInputError(f"init_cash must be finite, got {init_cash!r}")
    if init_cash <= 0:
        raise VectorResearchInputError(f"init_cash must be positive, got {init_cash}")


def _validate_fees(fees: float) -> None:
    if isinstance(fees, bool) or not isinstance(fees, (int, float)):
        raise VectorResearchInputError(f"fees must be numeric (not bool), got {type(fees).__name__}")
    if not math.isfinite(fees):
        raise VectorResearchInputError(f"fees must be finite, got {fees!r}")
    if not (0.0 <= fees < 1.0):
        raise VectorResearchInputError(f"fees must be within [0.0, 1.0) as a fraction, got {fees}")


def _shift_to_execution_bar(signals: pd.DataFrame) -> pd.DataFrame:
    """Shift a signal-generation matrix forward one bar (see module docstring)."""
    return signals.shift(1, fill_value=False)


def _classify(raw: float, *, trade_count: int, return_std: float) -> ExploratoryMetric:
    if trade_count == 0:
        return ExploratoryMetric(None, "no_trades")
    if not math.isfinite(raw):
        if return_std == 0.0:
            return ExploratoryMetric(None, "zero_variance")
        return ExploratoryMetric(None, "non_finite")
    return ExploratoryMetric(float(raw), "ok")


def run_parameter_sweep(
    close: pd.Series,
    entry_signals: pd.DataFrame,
    exit_signals: pd.DataFrame,
    *,
    init_cash: float = 100_000.0,
    fees: float = 0.0,
) -> ParameterSweepResult:
    """Run a vectorized parameter sweep over a boolean signal matrix.

    `entry_signals`/`exit_signals` are boolean-valued DataFrames sharing
    `close`'s index and columns, one column per parameter combination under
    evaluation -- VectorBT's signal-matrix convention for broadcasting a
    single price series across many strategy variants at once. See this
    module's docstring for the signal-timing, temporal, and
    exploratory-analytics contracts this function enforces. The returned
    `ParameterSweepResult` is advisory and carries no execution authority.

    Raises `VectorResearchInputError` for any malformed input, before
    VectorBT is ever invoked.
    """
    _validate_close(close)
    _validate_signal_frame("entry_signals", entry_signals, close)
    _validate_signal_frame("exit_signals", exit_signals, close)
    if list(entry_signals.columns) != list(exit_signals.columns):
        raise VectorResearchInputError(
            "entry_signals and exit_signals must have identical columns in identical order"
        )
    _validate_init_cash(init_cash)
    _validate_fees(fees)

    entries = _shift_to_execution_bar(entry_signals)
    exits = _shift_to_execution_bar(exit_signals)

    portfolio = vbt.Portfolio.from_signals(
        close, entries, exits, init_cash=init_cash, fees=fees, freq=_BAR_FREQ
    )

    trade_counts = portfolio.trades.count()
    return_stds = portfolio.returns().std()
    raw_total_return = portfolio.total_return()
    raw_sharpe_ratio = portfolio.sharpe_ratio(year_freq=_YEAR_FREQ)
    raw_max_drawdown = portfolio.max_drawdown()

    columns = list(entry_signals.columns)
    total_return: dict[str, ExploratoryMetric] = {}
    sharpe_ratio: dict[str, ExploratoryMetric] = {}
    max_drawdown: dict[str, ExploratoryMetric] = {}
    trade_count: dict[str, int] = {}
    for col in columns:
        tc = int(trade_counts.get(col, 0))
        std = float(return_stds.get(col, 0.0))
        trade_count[col] = tc
        total_return[col] = _classify(float(raw_total_return[col]), trade_count=tc, return_std=std)
        sharpe_ratio[col] = _classify(float(raw_sharpe_ratio[col]), trade_count=tc, return_std=std)
        max_drawdown[col] = _classify(float(raw_max_drawdown[col]), trade_count=tc, return_std=std)

    return ParameterSweepResult(
        metric_source=METRIC_SOURCE,
        frequency=_BAR_FREQ,
        year_freq=_YEAR_FREQ,
        total_return=total_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
        portfolio=portfolio,
    )
