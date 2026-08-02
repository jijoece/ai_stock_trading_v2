"""Runs `src/trading_research/backtesting/engine.py` over the PR 7 fixtures.

Executed in the **main project's** environment only:

    .venv/bin/python docs/library-migration/pr7/run_legacy_engine.py <output_dir>

`backtest_runtime` is never imported here, and is never installed in this
environment (docs/adr/0009-lumibot-backtest-distribution-boundary.md
Decision 1/5). The bar-set checksum below is therefore an **independent
re-implementation** of `backtest_runtime.contract.bars_digest`, not a call
into it: if the two sides disagree about what "the same bars" means, the
checksums differ and the parity comparison says so, which is the whole point
of computing it twice.

This script only reads the engine. It changes no engine behavior, passes
`conn=None` so nothing is persisted, and touches no `paper_books` code.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from trading_research.backtesting.configuration import BacktestConfiguration  # noqa: E402
from trading_research.backtesting.data_provider import FixtureHistoricalDataProvider  # noqa: E402
from trading_research.backtesting.engine import BACKTEST_CODE_VERSION, run_backtest  # noqa: E402
from trading_research.backtesting.models import EntrySignal, HistoricalBar  # noqa: E402

SCHEMA_VERSION = "pr7.legacy_engine.result.v1"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Bars are timestamped as available after that session's close. Every bar is
# available before `run_backtest`'s `final_as_of` (end_date 23:59:59 UTC), so
# the engine's own look-ahead guard passes without any bar being withheld.
BAR_AVAILABLE_AT_UTC = time(21, 0)


def _bars_digest(raw_bars: list[dict]) -> str:
    """Independent re-implementation of `backtest_runtime.contract.bars_digest`.

    Same canonicalization (sorted keys, no whitespace, no NaN) over the same
    six fields, so an equal digest proves both engines were handed bar-for-bar
    identical input.
    """
    payload = [
        {
            "date": bar["date"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }
        for bar in raw_bars
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(value) -> Decimal:
    """Every numeric fixture value crosses into `Decimal` through `str`, never
    through `float`, so no binary-floating-point residue enters the legacy
    engine's exact arithmetic."""
    return Decimal(str(value))


def _to_bars(symbol: str, raw_bars: list[dict]) -> tuple[HistoricalBar, ...]:
    return tuple(
        HistoricalBar(
            symbol=symbol,
            session_date=date.fromisoformat(bar["date"]),
            open=_decimal(bar["open"]),
            high=_decimal(bar["high"]),
            low=_decimal(bar["low"]),
            close=_decimal(bar["close"]),
            volume=int(bar["volume"]),
            available_at=datetime.combine(
                date.fromisoformat(bar["date"]), BAR_AVAILABLE_AT_UTC, tzinfo=timezone.utc
            ),
        )
        for bar in raw_bars
    )


def _configuration(block: dict) -> BacktestConfiguration:
    return BacktestConfiguration(
        start_date=date.fromisoformat(block["start_date"]),
        end_date=date.fromisoformat(block["end_date"]),
        symbols=tuple(block["symbols"]),
        initial_cash=Decimal(block["initial_cash"]),
        atr_period=int(block["atr_period"]),
    )


def _signal(block: dict) -> EntrySignal:
    return EntrySignal(
        signal_id=block["signal_id"],
        symbol=block["symbol"],
        generated_after_session=date.fromisoformat(block["generated_after_session"]),
        limit_price=Decimal(block["limit_price"]),
        quantity_hint=Decimal(block["quantity_hint"]),
        initial_stop_reference=(
            None if block["initial_stop_reference"] is None
            else Decimal(block["initial_stop_reference"])
        ),
        target_reference=(
            None if block["target_reference"] is None else Decimal(block["target_reference"])
        ),
        maximum_holding_sessions=block["maximum_holding_sessions"],
    )


def _encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _derive_orders(fills) -> list[dict]:
    """`BacktestResult` has no order records -- the legacy engine emits fills
    only. Orders are reconstructed here from each fill's `order_id`, which the
    engine assigns deterministically, and are labelled as a comparator-side
    derivation in the emitted document so the parity report never presents
    them as something the engine itself reported.

    Order matters: the result is in **engine execution order**, keyed on each
    order's first `fill_sequence`, not sorted by `order_id`. Sorting
    lexicographically put an exit order ahead of the entry that created it
    (`bt-order-SPKE-...-STOP_GAP` sorts before `bt-order-pr7-entry`), which
    would silently align the legacy SELL against the runtime BUY.
    """
    orders: dict[str, dict] = {}
    for fill in sorted(fills, key=lambda item: (item.fill_sequence or 0)):
        row = orders.setdefault(
            fill.order_id,
            {
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": Decimal("0"),
                "order_type": "LIMIT" if fill.side == "BUY" else "MARKET_ON_EVENT",
                "status": "FILLED",
                "first_fill_sequence": fill.fill_sequence or 0,
            },
        )
        row["quantity"] += fill.quantity
    return sorted(orders.values(), key=lambda row: row["first_fill_sequence"])


def _derive_end_positions(fills) -> list[dict]:
    """Likewise reconstructed: the engine's result exposes no position records,
    so the end-of-run position is replayed from the fill sequence (BUY adds,
    SELL removes, average price weighted by opening fills)."""
    held: dict[str, dict] = {}
    for fill in sorted(fills, key=lambda item: (item.fill_sequence or 0)):
        row = held.setdefault(
            fill.symbol, {"symbol": fill.symbol, "quantity": Decimal("0"), "cost": Decimal("0")}
        )
        if fill.side == "BUY":
            row["quantity"] += fill.quantity
            row["cost"] += fill.quantity * fill.fill_price
        else:
            if row["quantity"] > 0:
                row["cost"] -= (row["cost"] / row["quantity"]) * fill.quantity
            row["quantity"] -= fill.quantity
    return [
        {
            "symbol": row["symbol"],
            "quantity": row["quantity"],
            "average_price": row["cost"] / row["quantity"],
        }
        for row in (held[key] for key in sorted(held))
        if row["quantity"] > 0
    ]


def run_case(case: dict, output_dir: Path) -> Path:
    document = json.loads((FIXTURES_DIR / case["input"]).read_text(encoding="utf-8"))
    strategy = document["strategy"]
    symbol = strategy["symbol"]
    raw_bars = document["bars"]

    legacy = case["legacy_engine"]
    configuration = _configuration(legacy["configuration"])
    signal = _signal(legacy["signal"])
    bars = _to_bars(symbol, raw_bars)

    result = run_backtest(
        configuration=configuration,
        data_provider=FixtureHistoricalDataProvider({symbol: bars}),
        signals=(signal,),
        economic_events=(),
        conn=None,
    )

    final_state = result.daily_states[-1] if result.daily_states else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "engine": "src/trading_research/backtesting/engine.py",
        "engine_code_version": BACKTEST_CODE_VERSION,
        "input_document": case["input"],
        "historical_bar_dataset_checksum": _bars_digest(raw_bars),
        "backtest_run_id": result.backtest_run_id,
        "configuration_hash": result.configuration_hash,
        "initial_cash": configuration.initial_cash,
        # Reported exactly as `BacktestResult` carries them.
        "fills": [
            {
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": fill.quantity,
                "fill_price": fill.fill_price,
                "fees": fill.fees,
                "slippage": fill.slippage,
                "market_date": fill.market_date,
                "exit_reason": fill.exit_reason,
                "fill_sequence": fill.fill_sequence,
                "position_id": fill.position_id,
            }
            for fill in result.fills
        ],
        "daily_states": [
            {
                "market_date": state.market_date,
                "cash": state.cash,
                "equity": state.equity,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl": state.unrealized_pnl,
                "drawdown_fraction": state.drawdown_fraction,
            }
            for state in result.daily_states
        ],
        "rejected_entries": list(result.rejected_entries),
        "unresolved_evaluations": list(result.unresolved_evaluations),
        "metrics": result.metrics,
        # `BacktestResult` carries neither orders nor positions; both are null
        # here rather than silently invented, with the reconstruction kept in
        # a separately labelled block.
        "orders": None,
        "positions": None,
        "derived": {
            "note": (
                "Reconstructed by run_legacy_engine.py from the engine's fills; "
                "not reported by BacktestResult itself."
            ),
            "orders_from_fills": _derive_orders(result.fills),
            "end_positions_from_fills": _derive_end_positions(result.fills),
        },
        "final_cash": final_state.cash if final_state else configuration.initial_cash,
        "final_equity": final_state.equity if final_state else configuration.initial_cash,
        "final_value": final_state.equity if final_state else configuration.initial_cash,
        "max_drawdown_fraction": result.metrics["maximum_drawdown"],
    }

    output_path = output_dir / f"{case['case_id']}.legacy_engine.json"
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_encode) + "\n", encoding="utf-8"
    )
    return output_path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: run_legacy_engine.py <output_dir>", file=sys.stderr)
        return 2
    output_dir = Path(argv[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FIXTURES_DIR / "parity_manifest.json").read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        path = run_case(case, output_dir)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
