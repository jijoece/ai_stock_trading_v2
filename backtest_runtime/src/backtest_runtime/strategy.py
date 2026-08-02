"""Runs the deterministic reference strategy against caller-supplied bars.

This is the only module in this distribution that imports `lumibot`. It
never imports `trading_research` or `trading_paper_runtime` (see
docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 1/Allowed
imports), and no LumiBot object (`Order`, `Asset`, `Position`, ...) is ever
returned to a caller -- every value handed back is a JSON-serializable
primitive (Decision 3: "LumiBot objects never cross the process boundary").

The reference strategy itself is intentionally minimal: buy the
caller-specified whole-share quantity of one symbol on the first bar with a
price, then hold. This is the same shape as the pre-step feasibility spike's
`SpikeStrategy` (docs/library-migration/pre-step-06/spike_backtest.py) --
deterministic, no execution authority, no scheduler, no data fetcher.

PR 7 added exactly one control to it, `strategy.entry_after_session`
(reference strategy v2, `docs/library-migration/DECISIONS.md` D6): it moves
*when* that same single buy is submitted, and nothing else. It adds no sell,
no stop, no target, no second order, no order type, no scheduler, no data
fetcher and no broker interaction, and it does not touch
`benchmark_asset=None`, `analyze_backtest=False`, or any credential- or
network-safety guarantee below. It exists so PR 7 can construct one case in
which this strategy and `backtesting/engine.py` enter on the same session --
which is otherwise impossible, because that engine cannot enter before its
third bar.

`benchmark_asset=None` and `analyze_backtest=False` are hardcoded below, not
caller-configurable -- ADR 0009 Decision 3 requires both as a condition of the
no-network-access guarantee, not a default a caller could accidentally
disable.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import LUMIBOT_PINNED_VERSION, SCHEMA_VERSION_RESULT
from .contract import BacktestInput, StrategyConfig, bars_digest, strategy_digest


class BacktestExecutionError(RuntimeError):
    """Raised when the underlying LumiBot backtest fails or mis-resolves."""


def _build_frame(bars):
    import pandas as pd

    rows = [
        {
            "datetime": bar.session_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    frame = pd.DataFrame(rows)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame.set_index("datetime")


def run_backtest(backtest_input: BacktestInput) -> dict[str, Any]:
    import lumibot

    if lumibot.__version__ != LUMIBOT_PINNED_VERSION:
        raise BacktestExecutionError(
            f"installed lumibot=={lumibot.__version__}, expected {LUMIBOT_PINNED_VERSION}"
        )

    from lumibot.backtesting.pandas_backtesting import PandasDataBacktesting
    from lumibot.entities import Asset, Data
    from lumibot.strategies.strategy import Strategy

    strategy_cfg = backtest_input.strategy
    bars = backtest_input.bars
    asset = Asset(strategy_cfg.symbol, asset_type="stock")

    orders: dict[str, dict[str, Any]] = {}
    fills: list[dict[str, Any]] = []
    raw_daily_states: list[dict[str, Any]] = []

    def _enum_value(member: object) -> str:
        return member.value if hasattr(member, "value") else str(member)

    class ReferenceStrategy(Strategy):  # type: ignore[misc]
        def initialize(self) -> None:
            self.sleeptime = "1D"
            self.set_market("NYSE")
            self._submitted = False

        def on_trading_iteration(self) -> None:
            current_date = self.get_datetime().date()
            # The only control this strategy has. `None` reproduces v1
            # exactly: submit on the first iteration with a resolvable price.
            # A date defers that same single buy to the first iteration
            # strictly after it. There is no second order either way.
            entry_allowed = (
                strategy_cfg.entry_after_session is None
                or current_date > strategy_cfg.entry_after_session
            )
            price = self.get_last_price(asset)
            if not self._submitted and entry_allowed and price:
                order = self.create_order(asset, strategy_cfg.quantity, "buy")
                submitted = self.submit_order(order)
                orders[submitted.identifier] = {
                    "order_id": submitted.identifier,
                    "symbol": strategy_cfg.symbol,
                    "side": _enum_value(submitted.side),
                    "quantity": float(submitted.quantity),
                    "order_type": _enum_value(submitted.order_type),
                    "status": _enum_value(submitted.status),
                }
                self._submitted = True

            equity = float(self.portfolio_value)
            cash = float(self.cash)
            position = self.get_position(asset)
            filled_quantity = float(position.quantity) if position else 0.0
            avg_fill_price = (
                float(position.avg_fill_price) if position and position.avg_fill_price else 0.0
            )
            unrealized_pnl = (equity - cash) - (filled_quantity * avg_fill_price)
            raw_daily_states.append(
                {
                    "market_date": current_date.isoformat(),
                    "cash": cash,
                    "equity": equity,
                    "unrealized_pnl": unrealized_pnl,
                    "position_quantity": filled_quantity,
                    "position_avg_price": avg_fill_price,
                }
            )

        def on_filled_order(self, position, order, price, quantity, multiplier) -> None:
            fills.append(
                {
                    "fill_id": f"{order.identifier}-fill-{len(fills) + 1}",
                    "order_id": order.identifier,
                    "symbol": strategy_cfg.symbol,
                    "side": _enum_value(order.side),
                    "quantity": float(quantity),
                    "fill_price": float(price),
                    "fees": 0.0,
                    "market_date": self.get_datetime().date().isoformat(),
                }
            )
            if order.identifier in orders:
                orders[order.identifier]["status"] = _enum_value(order.status)

    frame = _build_frame(bars)
    pandas_data = {asset: Data(asset, frame, timestep="day", timezone="America/New_York")}
    start = dt.datetime.combine(bars[0].session_date, dt.time.min)
    end = dt.datetime.combine(bars[-1].session_date, dt.time.min)

    try:
        ReferenceStrategy.run_backtest(
            PandasDataBacktesting,
            backtesting_start=start,
            backtesting_end=end,
            pandas_data=pandas_data,
            budget=strategy_cfg.budget,
            benchmark_asset=None,
            analyze_backtest=False,
            save_tearsheet=False,
            show_tearsheet=False,
            show_plot=False,
            show_indicators=False,
            quiet_logs=True,
            save_logfile=False,
        )
    except Exception as exc:  # noqa: BLE001 - normalized into a contract-level failure
        raise BacktestExecutionError(f"lumibot backtest failed: {exc}") from exc

    return _normalize_result(strategy_cfg, bars, orders, fills, raw_daily_states, lumibot.__version__)


def _normalize_result(
    strategy_cfg: StrategyConfig,
    bars,
    orders: dict[str, dict[str, Any]],
    fills: list[dict[str, Any]],
    raw_daily_states: list[dict[str, Any]],
    lumibot_version: str,
) -> dict[str, Any]:
    peak_equity = 0.0
    max_drawdown = 0.0
    daily_states: list[dict[str, Any]] = []
    for state in raw_daily_states:
        peak_equity = max(peak_equity, state["equity"])
        drawdown_fraction = (
            0.0 if peak_equity <= 0 else min(0.0, (state["equity"] - peak_equity) / peak_equity)
        )
        max_drawdown = min(max_drawdown, drawdown_fraction)
        daily_states.append(
            {
                "market_date": state["market_date"],
                "cash": state["cash"],
                "equity": state["equity"],
                "realized_pnl": 0.0,
                "unrealized_pnl": state["unrealized_pnl"],
                "drawdown_fraction": drawdown_fraction,
            }
        )

    last_raw_state = raw_daily_states[-1] if raw_daily_states else None
    positions = []
    if last_raw_state and last_raw_state["position_quantity"] > 0:
        positions.append(
            {
                "symbol": strategy_cfg.symbol,
                "quantity": last_raw_state["position_quantity"],
                "average_price": last_raw_state["position_avg_price"],
            }
        )

    final_state = daily_states[-1] if daily_states else None
    final_cash = final_state["cash"] if final_state else strategy_cfg.budget
    final_equity = final_state["equity"] if final_state else strategy_cfg.budget

    return {
        "schema_version": SCHEMA_VERSION_RESULT,
        "historical_bar_dataset_checksum": bars_digest(bars),
        "run_configuration_checksum": strategy_digest(strategy_cfg),
        "strategy_identity": strategy_cfg.strategy_id,
        "lumibot_version": lumibot_version,
        "orders": [orders[key] for key in sorted(orders)],
        "fills": fills,
        "daily_states": daily_states,
        "positions": positions,
        "final_cash": final_cash,
        "final_equity": final_equity,
        "final_value": final_equity,
        "max_drawdown_fraction": max_drawdown,
    }
