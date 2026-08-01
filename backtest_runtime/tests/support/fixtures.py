"""Shared deterministic fixture for backtest_runtime tests.

The bars are the same fixture shape as the pre-step feasibility spike
(docs/library-migration/pre-step-06/spike_backtest.py) -- hand-written, no
fetcher, no network -- so a caller-supplied bar set is genuinely what the
strategy consumes.
"""
from __future__ import annotations

from backtest_runtime import SCHEMA_VERSION_INPUT
from backtest_runtime.contract import REFERENCE_STRATEGY_ID

BARS = [
    ("2024-01-02", 100.0, 101.0, 99.5, 100.5, 1_000_000),
    ("2024-01-03", 100.5, 102.5, 100.0, 102.0, 1_100_000),
    ("2024-01-04", 102.0, 103.0, 101.0, 101.5, 900_000),
    ("2024-01-05", 101.5, 104.0, 101.0, 103.5, 1_250_000),
    ("2024-01-08", 103.5, 105.0, 103.0, 104.5, 1_050_000),
]


def bar_dicts(bars=None) -> list[dict]:
    bars = bars or BARS
    return [
        {
            "date": date,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        for date, open_, high, low, close, volume in bars
    ]


def valid_input_document(*, symbol: str = "SPKE", quantity: int = 10, budget: float = 100_000.0, bars=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION_INPUT,
        "strategy": {
            "strategy_id": REFERENCE_STRATEGY_ID,
            "symbol": symbol,
            "quantity": quantity,
            "budget": budget,
        },
        "bars": bar_dicts(bars),
    }


def perturbed_input_document() -> dict:
    perturbed = [list(bar) for bar in BARS]
    perturbed[-1][4] += 5.0  # close
    perturbed[-1][2] = max(perturbed[-1][2], perturbed[-1][4])  # keep high >= close
    return valid_input_document(bars=[tuple(bar) for bar in perturbed])
