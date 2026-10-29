"""Forward shadow metrics beside the Phase 0 bands (docs/paper-testing-plan.md Phase 2).

Read-only over `strategy_signals` and `strategy_shadow_outcomes`. Each metric
is compared with a band the operator recorded in the Phase 0 go/no-go note and
gets one of WITHIN, BELOW, ABOVE or INSUFFICIENT_SAMPLE; a metric with no band
is reported as NO_BAND. Nothing here changes any stage.

Metric definitions (per share, after the slippage and fees recorded on each row):
- round_trips: CLOSED outcomes.
- fill_rate: filled / (filled + NO_FILL), where filled = CLOSED + OPEN.
- expectancy and average_return: mean return fraction of CLOSED outcomes.
- win_rate: share of CLOSED outcomes with a positive return.
- maximum_drawdown: largest peak-to-trough fall (as a positive fraction) of the
  equal-weight compounded curve of CLOSED returns in exit order.
- average_holding_sessions: mean holding sessions of CLOSED outcomes.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..backtesting.models import BacktestError
from ..storage.strategy_shadow_repositories import list_terminal_outcomes

MINIMUM_MARKET_DAYS = 20
MINIMUM_ELIGIBLE_SIGNALS = 15
METRICS = ("fill_rate", "round_trips", "expectancy", "win_rate", "maximum_drawdown", "average_holding_sessions")
WITHIN, BELOW, ABOVE, INSUFFICIENT_SAMPLE, NO_BAND = "WITHIN", "BELOW", "ABOVE", "INSUFFICIENT_SAMPLE", "NO_BAND"

CAVEATS = (
    "Hypothetical shadow outcomes; research output only, not financial advice.",
    "Bands come from the operator's Phase 0 go/no-go note; this report does not set or change them.",
)


def load_bands(path: Path | str, strategy_id: str) -> dict[str, tuple[Decimal, Decimal]]:
    """Read `{"<strategy_id>": {"<metric>": {"low": x, "high": y}}}` from `path`."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise BacktestError(f"cannot read bands file {path}: {exc}") from exc
    section = raw.get(strategy_id) if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        raise BacktestError(f"bands file has no section for {strategy_id!r}")
    bands: dict[str, tuple[Decimal, Decimal]] = {}
    for metric, band in section.items():
        if metric not in METRICS:
            raise BacktestError(f"unknown band metric {metric!r}; expected one of {METRICS}")
        try:
            low, high = Decimal(str(band["low"])), Decimal(str(band["high"]))
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise BacktestError(f"band for {metric!r} needs numeric 'low' and 'high'") from exc
        if low > high:
            raise BacktestError(f"band for {metric!r} has low above high")
        bands[metric] = (low, high)
    return bands


def _maximum_drawdown(returns: list[Decimal]) -> Decimal:
    equity, peak, worst = Decimal(1), Decimal(1), Decimal(0)
    for value in returns:
        equity *= Decimal(1) + value
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def compute_metrics(terminal: list) -> dict[str, Decimal | None]:
    closed = [r for r in terminal if r["state"] == "CLOSED"]
    no_fill = sum(1 for r in terminal if r["state"] == "NO_FILL")
    returns = [Decimal(r["return_fraction"]) for r in sorted(closed, key=lambda r: (r["exit_session"], r["outcome_id"]))]
    filled = len(closed)
    return {
        "round_trips": Decimal(filled),
        "fill_rate": Decimal(filled) / Decimal(filled + no_fill) if filled + no_fill else None,
        "expectancy": sum(returns, Decimal(0)) / filled if filled else None,
        "win_rate": Decimal(sum(1 for v in returns if v > 0)) / filled if filled else None,
        "maximum_drawdown": _maximum_drawdown(returns) if filled else None,
        "average_holding_sessions": (
            Decimal(sum(r["holding_sessions"] for r in closed)) / filled if filled else None
        ),
    }


def _status(value: Decimal | None, band: tuple[Decimal, Decimal] | None, sufficient: bool) -> str:
    if band is None:
        return NO_BAND
    if not sufficient or value is None:
        return INSUFFICIENT_SAMPLE
    low, high = band
    return BELOW if value < low else ABOVE if value > high else WITHIN


def run_shadow_report(
    *, conn, strategy_id: str, bands_path: Path | str | None = None,
    minimum_market_days: int = MINIMUM_MARKET_DAYS, minimum_eligible_signals: int = MINIMUM_ELIGIBLE_SIGNALS,
) -> dict:
    bands = load_bands(bands_path, strategy_id) if bands_path is not None else {}
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_date) FROM strategy_signals WHERE strategy_id = ?", (strategy_id,),
    ).fetchone()[0]
    eligible = conn.execute(
        "SELECT COUNT(*) FROM strategy_signals WHERE strategy_id = ? AND status = 'ELIGIBLE'", (strategy_id,),
    ).fetchone()[0]
    terminal = list_terminal_outcomes(conn, strategy_id=strategy_id)
    sufficient = sessions >= minimum_market_days and eligible >= minimum_eligible_signals
    metrics = compute_metrics(terminal)
    rows = {
        metric: {
            "value": None if metrics[metric] is None else str(metrics[metric]),
            "band": None if metric not in bands else {"low": str(bands[metric][0]), "high": str(bands[metric][1])},
            "status": _status(metrics[metric], bands.get(metric), sufficient),
        }
        for metric in METRICS
    }
    return {
        "strategy_id": strategy_id,
        "sample": {
            "evaluated_market_days": sessions, "minimum_market_days": minimum_market_days,
            "eligible_signals": eligible, "minimum_eligible_signals": minimum_eligible_signals,
            "resolved_signals": len(terminal), "status": "SUFFICIENT_SAMPLE" if sufficient else INSUFFICIENT_SAMPLE,
        },
        "metrics": rows,
        "any_below_band": any(row["status"] == BELOW for row in rows.values()),
        "caveats": list(CAVEATS),
    }
