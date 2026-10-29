"""Backtest screening profile (docs/historical-backtesting-plan.md D1).

Production screening (`analysis/screener.py`) needs fundamentals, quotes and
status flags that have no point-in-time history here. Rather than let those
gates pass silently or fail every symbol, the backtest evaluates only the gates
computable from daily bars, with the production thresholds, and records every
other gate as NOT_EVALUATED in the profile description. The production screener
is untouched.
"""
from __future__ import annotations

from datetime import datetime
from statistics import pstdev

from ..analysis.screener import GateResult, ScreeningConfig, ScreeningResult
from ..hashing import hash_config
from .models import HistoricalBar

PROFILE_ID = "backtest-screening/1"
EVALUATED_GATES = (
    "max_share_price", "min_avg_daily_dollar_volume", "max_realized_volatility",
    "max_data_staleness_seconds",
)
DOLLAR_VOLUME_WINDOW = 20
VOLATILITY_WINDOW = 20

_DEFINITIONS = {
    "max_share_price": "latest close",
    "min_avg_daily_dollar_volume": f"mean of close*volume over the last {DOLLAR_VOLUME_WINDOW} sessions",
    "max_realized_volatility": f"population stdev of the last {VOLATILITY_WINDOW} simple daily close returns",
    "max_data_staleness_seconds": "seconds from the latest bar's available_at to now",
}

# Production gates the backtest cannot evaluate, with the config flag that
# switches each on (None = always emitted by the production screener).
_NOT_EVALUATED = (
    ("min_market_cap", None),
    ("min_operating_history_years", None),
    ("exclude_otc", "exclude_otc"),
    ("exclude_inactive", "exclude_inactive"),
    ("exclude_bankruptcy_or_distress", "exclude_bankruptcy_or_distress"),
    ("exclude_going_concern_warning", "exclude_going_concern_warning"),
    ("exclude_shell_company", "exclude_shell_company"),
    ("exclude_recent_reverse_split", "exclude_recent_reverse_split"),
    ("max_dilution_share_growth_yoy", None),
    ("min_cash_runway_quarters", None),
    ("earnings_blackout_days", None),
    ("exclude_recent_halt", "exclude_recent_halt"),
    ("max_bid_ask_spread_bps", None),
)


def not_evaluated_gates(config: ScreeningConfig) -> tuple[str, ...]:
    return tuple(gate for gate, flag in _NOT_EVALUATED if flag is None or getattr(config, flag))


def describe_profile(config: ScreeningConfig) -> dict:
    """Deterministic description for the run identity and the report header."""
    return {
        "profile": PROFILE_ID,
        "source_config_hash": config.config_hash,
        "source_config_version": config.version,
        "evaluated": [
            {"gate": gate, "status": "EVALUATED", "threshold": getattr(config, gate), "definition": _DEFINITIONS[gate]}
            for gate in EVALUATED_GATES
        ],
        "not_evaluated": [{"gate": gate, "status": "NOT_EVALUATED"} for gate in not_evaluated_gates(config)],
    }


def profile_hash(config: ScreeningConfig) -> str:
    return hash_config(describe_profile(config))


def _gate(gate: str, passed: bool, threshold: float, observed: float | None, reason: str, stamp: str | None) -> GateResult:
    return GateResult(
        gate=gate, passed=passed, hard_failure=not passed, threshold=threshold,
        observed=observed, reason=reason, data_timestamp=stamp,
    )


def backtest_screening_result(
    symbol: str, bars: tuple[HistoricalBar, ...], *, config: ScreeningConfig, now: datetime,
) -> ScreeningResult:
    """Screen `symbol` using only bars known at `now`; unknown inputs fail closed."""
    known = tuple(bar for bar in bars if bar.available_at <= now)
    stamp = known[-1].available_at.isoformat() if known else None
    gates: list[GateResult] = []

    price = float(known[-1].close) if known else None
    gates.append(_gate(
        "max_share_price", price is not None and price <= config.max_share_price,
        config.max_share_price, price,
        "price unknown" if price is None else f"price {price} vs cap {config.max_share_price}", stamp,
    ))

    dollar_volume = None
    if len(known) >= DOLLAR_VOLUME_WINDOW:
        window = known[-DOLLAR_VOLUME_WINDOW:]
        dollar_volume = sum(float(bar.close) * bar.volume for bar in window) / DOLLAR_VOLUME_WINDOW
    gates.append(_gate(
        "min_avg_daily_dollar_volume",
        dollar_volume is not None and dollar_volume >= config.min_avg_daily_dollar_volume,
        config.min_avg_daily_dollar_volume, dollar_volume,
        "avg daily dollar volume unknown" if dollar_volume is None
        else f"avg daily dollar volume {dollar_volume:.0f} vs floor {config.min_avg_daily_dollar_volume}", stamp,
    ))

    volatility = None
    if len(known) >= VOLATILITY_WINDOW + 1:
        closes = [float(bar.close) for bar in known[-(VOLATILITY_WINDOW + 1):]]
        volatility = pstdev([closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))])
    gates.append(_gate(
        "max_realized_volatility", volatility is not None and volatility <= config.max_realized_volatility,
        config.max_realized_volatility, volatility,
        "realized volatility unknown" if volatility is None
        else f"realized volatility {volatility:.2%} vs cap {config.max_realized_volatility:.2%}", stamp,
    ))

    age = (now - known[-1].available_at).total_seconds() if known else None
    gates.append(_gate(
        "max_data_staleness_seconds", age is not None and age <= config.max_data_staleness_seconds,
        config.max_data_staleness_seconds, age,
        "no bars known" if age is None else ("data fresh" if age <= config.max_data_staleness_seconds else "data stale"),
        stamp,
    ))

    return ScreeningResult(
        symbol=symbol, passed=all(g.passed for g in gates), gate_results=tuple(gates),
        config_hash=profile_hash(config), config_version=config.version, screened_at=now.isoformat(),
    )
