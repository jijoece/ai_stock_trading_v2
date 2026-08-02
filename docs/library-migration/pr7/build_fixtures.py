"""Builds the canonical PR 7 parity fixture set, checked in under `fixtures/`.

Run from the repository root with any Python 3.10+ interpreter:

    python docs/library-migration/pr7/build_fixtures.py

This script imports nothing from `trading_research`, `backtest_runtime`, or
`trading_paper_runtime` -- it is pure standard library, so it runs in either
environment and belongs to neither. Its output is committed; it exists to
record how the fixtures were derived, not to generate them at test time
(`pr7-prompt.md` required scope item 1).

Cases A/B/C reuse `backtest_runtime/tests/support/fixtures.py`'s bar arrays
**verbatim**, so both engines consume bar-for-bar identical input and the
`backtest_runtime` side is already covered by that distribution's own
determinism (`test_determinism.py`) and drawdown (`test_drawdown.py`) tests.
Cases D and E are synthetic and exist because the three fixtures above cannot
reach two things the parity comparison needs: D is long enough for the legacy
engine's *default* `atr_period=14`, which a five-bar fixture cannot reach, and
E gaps its opens so that each engine's fill-price model becomes observable
(every array above is gap-free, which makes two different models produce the
same number).

`parity_manifest.json` carries the legacy-engine run parameters for each case
(the Option A construction recorded in `docs/library-migration/DECISIONS.md`
D6). The `backtest_runtime` side needs no such block: its whole run
configuration is the input document's `strategy` object.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION_INPUT = "backtest_runtime.input.v1"
REFERENCE_STRATEGY_ID = "backtest_runtime.reference_strategy.v1"

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# --------------------------------------------------------------------------
# Bar arrays
# --------------------------------------------------------------------------

# Copied verbatim from backtest_runtime/tests/support/fixtures.py::BARS.
BARS = [
    ("2024-01-02", 100.0, 101.0, 99.5, 100.5, 1_000_000),
    ("2024-01-03", 100.5, 102.5, 100.0, 102.0, 1_100_000),
    ("2024-01-04", 102.0, 103.0, 101.0, 101.5, 900_000),
    ("2024-01-05", 101.5, 104.0, 101.0, 103.5, 1_250_000),
    ("2024-01-08", 103.5, 105.0, 103.0, 104.5, 1_050_000),
]

# Copied verbatim from backtest_runtime/tests/support/fixtures.py::FALLING_BARS.
FALLING_BARS = [
    ("2024-01-02", 100.0, 100.5, 99.5, 100.0, 1_000_000),
    ("2024-01-03", 100.0, 111.0, 99.5, 110.0, 1_000_000),
    ("2024-01-04", 110.0, 110.5, 94.0, 95.0, 1_000_000),
    ("2024-01-05", 95.0, 95.5, 89.0, 90.0, 1_000_000),
    ("2024-01-08", 90.0, 93.0, 89.5, 92.0, 1_000_000),
]


# Every bar array above is gap-free -- each session's open equals the previous
# session's close -- which makes two different LumiBot fill-price models
# ("previous close" vs. "open of the bar the order was submitted on")
# observationally identical. This array gaps deliberately so the two can be
# told apart, and so the legacy engine's gap handling is exercised on the same
# input. Levels are chosen so neither engine's exit paths fire: the legacy
# engine's ATR stop lands near 96, its trailing stop peaks near 105, its ATR
# target near 121, and no bar reaches any of them.
GAPPED_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.5, 100.0, 1_000_000),
    ("2024-01-03", 104.0, 105.0, 103.5, 104.5, 1_000_000),
    ("2024-01-04", 106.0, 107.0, 105.5, 106.5, 1_000_000),
    ("2024-01-05", 107.0, 108.0, 106.5, 107.5, 1_000_000),
    ("2024-01-08", 108.0, 109.0, 107.5, 108.5, 1_000_000),
]


def perturbed_bars() -> list[tuple]:
    """Reproduces backtest_runtime's `perturbed_input_document()` exactly:
    the final bar's close moves +5.0 and its high is raised if needed to keep
    high >= close."""
    perturbed: list[list] = [list(bar) for bar in BARS]
    perturbed[-1][4] = float(perturbed[-1][4]) + 5.0  # close
    perturbed[-1][2] = max(perturbed[-1][2], perturbed[-1][4])  # keep high >= close
    return [tuple(bar) for bar in perturbed]


# Case D: 30 sessions of NYSE-like weekday dates starting 2024-01-02, walked
# by a fixed repeating close-to-close delta cycle. Amplitude is deliberately
# small so that neither the legacy engine's mandatory ATR stop (entry - 2*ATR),
# its mandatory ATR target (entry + 3*ATR), its trailing stop, nor its
# 20-session maximum holding period fires inside the window -- the point of
# this case is a clean full-window hold under the engine's *default*
# configuration, not an exit-path comparison (cases A-C cover an exit).
_CASE_D_DELTAS = (0.8, -0.5, 0.3, -0.9, 0.6)
_CASE_D_SESSIONS = (
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
    "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-16",
    "2024-01-17", "2024-01-18", "2024-01-19", "2024-01-22", "2024-01-23",
    "2024-01-24", "2024-01-25", "2024-01-26", "2024-01-29", "2024-01-30",
    "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06",
    "2024-02-07", "2024-02-08", "2024-02-09", "2024-02-12", "2024-02-13",
)


def long_hold_bars() -> list[tuple]:
    bars: list[tuple] = []
    previous_close = 100.0
    for index, session in enumerate(_CASE_D_SESSIONS):
        open_price = previous_close
        close = round(previous_close + _CASE_D_DELTAS[index % len(_CASE_D_DELTAS)], 2)
        high = round(max(open_price, close) + 0.4, 2)
        low = round(min(open_price, close) - 0.4, 2)
        bars.append((session, open_price, high, low, close, 1_000_000 + index * 1_000))
        previous_close = close
    return bars


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

SYMBOL = "SPKE"
QUANTITY = 10
BUDGET = 100_000.0


def input_document(bars: list[tuple]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION_INPUT,
        "strategy": {
            "strategy_id": REFERENCE_STRATEGY_ID,
            "symbol": SYMBOL,
            "quantity": QUANTITY,
            "budget": BUDGET,
        },
        "bars": [
            {
                "date": date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
            for date, open_, high, low, close, volume in bars
        ],
    }


def legacy_parameters(bars: list[tuple], *, atr_period: int, signal_index: int) -> dict:
    """The Option A legacy-engine construction for one case.

    `signal_index` is the index of the bar used as `generated_after_session`;
    the engine then becomes eligible on the *next* session, which is the
    earliest session at which `average_true_range` has `atr_period + 1` bars
    of history available. `limit_price` is the entry bar's own high: the
    legacy engine has no market order type, and a limit at the bar's high can
    never bind, so the fill lands on the entry bar's open -- the closest
    available equivalent of the reference strategy's market buy.
    """
    entry_index = signal_index + 1
    return {
        "configuration": {
            "start_date": bars[0][0],
            "end_date": bars[-1][0],
            "symbols": [SYMBOL],
            "initial_cash": str(int(BUDGET)),
            "atr_period": atr_period,
            # Every other BacktestConfiguration field is left at its dataclass
            # default (initial_stop_multiple=2, initial_target_multiple=3,
            # risk_fraction=0.01, max_daily_loss_fraction=0.03,
            # max_drawdown_fraction=0.15, slippage_bps=0, fee_per_order=0,
            # maximum_holding_market_days=20, partial_profit disabled,
            # economic-event blackout disabled, benchmark_symbol=None).
            # slippage_bps=0 and fee_per_order=0 are the defaults and match
            # backtest_runtime's `fees: 0.0`, so no fee/slippage model has to
            # be reconciled across the two documents.
        },
        "signal": {
            "signal_id": "pr7-entry",
            "symbol": SYMBOL,
            "generated_after_session": bars[signal_index][0],
            "limit_price": str(bars[entry_index][2]),
            "quantity_hint": str(QUANTITY),
            # Option A: no initial_stop_reference, no target_reference, no
            # maximum_holding_sessions -- the narrowest expressible entry.
            "initial_stop_reference": None,
            "target_reference": None,
            "maximum_holding_sessions": None,
        },
        "expected_entry_session": bars[entry_index][0],
    }


CASES = [
    {
        "case_id": "case_a_buy_and_hold",
        "title": "Single-symbol buy-and-hold on backtest_runtime's own BARS fixture",
        "provenance": "backtest_runtime/tests/support/fixtures.py::BARS (verbatim)",
        "bars": BARS,
        "atr_period": 1,
        "signal_index": 1,
    },
    {
        "case_id": "case_b_perturbed_last_close",
        "title": "Boundary case: final close perturbed +5.0",
        "provenance": (
            "backtest_runtime/tests/support/fixtures.py::perturbed_input_document() "
            "(verbatim); the perturbation asserted by that distribution's "
            "test_determinism.py::test_changed_bar_changes_checksum_and_result"
        ),
        "bars": perturbed_bars(),
        "atr_period": 1,
        "signal_index": 1,
    },
    {
        "case_id": "case_c_falling_equity",
        "title": "Boundary case: falling equity, non-zero drawdown",
        "provenance": (
            "backtest_runtime/tests/support/fixtures.py::FALLING_BARS (verbatim); "
            "the drawdown-sign fixture asserted by that distribution's "
            "test_drawdown.py"
        ),
        "bars": FALLING_BARS,
        "atr_period": 1,
        "signal_index": 1,
    },
    {
        "case_id": "case_e_gapped_opens",
        "title": "Gapped opens: identifies each engine's fill-price model",
        "provenance": "synthetic, generated by this script (see `GAPPED_BARS`)",
        "bars": GAPPED_BARS,
        "atr_period": 1,
        "signal_index": 1,
    },
    {
        "case_id": "case_d_long_hold_default_atr",
        "title": "30-session hold at the legacy engine's default atr_period=14",
        "provenance": "synthetic, generated by this script (see `long_hold_bars`)",
        "bars": long_hold_bars(),
        "atr_period": 14,
        "signal_index": 14,
    },
]


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": "pr7.parity_manifest.v1",
        "description": (
            "Canonical PR 7 parity fixture set. Each case's `input` file is the "
            "single source of bars for BOTH engines. `legacy_engine` holds the "
            "Option A construction used to express the same run through "
            "src/trading_research/backtesting/engine.py; backtest_runtime needs "
            "no extra parameters because the input document's `strategy` object "
            "is its whole run configuration."
        ),
        "symbol": SYMBOL,
        "quantity": QUANTITY,
        "budget": BUDGET,
        "cases": [],
    }
    for case in CASES:
        bars = case["bars"]
        document = input_document(bars)
        filename = f"{case['case_id']}.input.json"
        (FIXTURES_DIR / filename).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest["cases"].append(
            {
                "case_id": case["case_id"],
                "title": case["title"],
                "provenance": case["provenance"],
                "input": filename,
                "bar_count": len(bars),
                "legacy_engine": legacy_parameters(
                    bars, atr_period=case["atr_period"], signal_index=case["signal_index"]
                ),
            }
        )
    (FIXTURES_DIR / "parity_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(CASES)} input documents + parity_manifest.json to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
