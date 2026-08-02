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
which is otherwise impossible, because LumiBot cannot submit before its
second bar and that engine cannot enter before its third.

Timing is read from LumiBot's own books, not from the strategy callbacks
=======================================================================
`lumibot/strategies/strategy_executor.py::_process_pandas_daily_data` runs
one session as::

    broker._update_datetime(session)   # the broker's clock is now `session`
    self._on_trading_iteration()       # the strategy submits; state sampled
    broker.process_pending_orders(...) # the order fills, clock still `session`

so a fill is booked in the *submission* session, and the strategy sees it
only at the following iteration -- `on_filled_order` is dispatched one
session late, and `self.cash`/`self.portfolio_value` inside
`on_trading_iteration` are always sampled before that session's fills.
Reference-strategy v1 stamped fills with the callback's clock and reported
the sampled state verbatim, which mis-dated every fill by one session and
left the entry session's state showing no position.

v2 reads the broker's own trade-event log instead, whose rows are stamped
with `data_source._datetime` at the moment the event is processed
(`lumibot/brokers/broker.py`, the `"time"` column). That is LumiBot's
authoritative record of when it booked the fill, is independent of callback
delivery, and is what `_normalize_result` below re-aligns the daily state
onto. The log is a private attribute, which is why `LUMIBOT_PINNED_VERSION`
is asserted before it is read and why its absence is a hard error rather
than a fallback to the callback clock -- a silently wrong date is worse than
a failed run.

`benchmark_asset=None` and `analyze_backtest=False` are hardcoded below, not
caller-configurable -- ADR 0009 Decision 3 requires both as a condition of the
no-network-access guarantee, not a default a caller could accidentally
disable.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from . import LUMIBOT_PINNED_VERSION, SCHEMA_VERSION_RESULT
from .contract import BacktestInput, StrategyConfig, bars_digest, strategy_digest


class BacktestExecutionError(RuntimeError):
    """Raised when the underlying LumiBot backtest fails or mis-resolves."""


# Every reconstructed quantity below is checked against the value LumiBot
# itself reported, so the tolerance only has to absorb double-rounding in
# `cash + quantity * price`, not any modelling difference.
_LEDGER_TOLERANCE = 1e-6


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


def _enum_value(member: object) -> str:
    return member.value if hasattr(member, "value") else str(member)


def _finite_float(value: object, default: float = 0.0) -> float:
    """LumiBot leaves numeric event-log cells unset as NaN. NaN is not
    representable in the JSON contract, so it becomes the documented default
    rather than propagating into a document `contract.py` would reject."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _authoritative_fill_events(broker: object, symbol: str) -> list[dict[str, Any]]:
    """Return LumiBot's own fill records, in the order its broker booked them.

    Read from the broker's trade-event log rather than from `on_filled_order`,
    because that callback is dispatched a session after the broker books the
    fill (see the module docstring). Each row's `"time"` is the broker clock at
    the moment the event was processed, which is the session the fill belongs
    to.
    """
    log = getattr(broker, "_trade_event_log_df", None)
    if log is None:
        raise BacktestExecutionError(
            f"lumibot=={LUMIBOT_PINNED_VERSION} broker exposed no trade-event log; "
            "refusing to fall back to the strategy callback clock, which mis-dates "
            "fills by one session"
        )
    required = {"time", "identifier", "side", "status", "price", "filled_quantity"}
    missing = required - set(getattr(log, "columns", ()))
    if missing:
        raise BacktestExecutionError(
            f"lumibot trade-event log is missing columns {sorted(missing)}"
        )

    events: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for _, row in log.iterrows():
        if str(_enum_value(row["status"])).lower() != "fill":
            continue
        order_id = str(row["identifier"])
        counters[order_id] = counters.get(order_id, 0) + 1
        booked_at = row["time"]
        if not hasattr(booked_at, "date"):
            raise BacktestExecutionError(
                f"lumibot trade-event log stamped fill {order_id} with a "
                f"non-datetime time {booked_at!r}"
            )
        events.append(
            {
                "fill_id": f"{order_id}-fill-{counters[order_id]}",
                "order_id": order_id,
                "symbol": symbol,
                "side": str(_enum_value(row["side"])).lower(),
                "quantity": _finite_float(row["filled_quantity"]),
                "fill_price": _finite_float(row["price"]),
                "fees": _finite_float(row.get("trade_cost")),
                "market_date": booked_at.date().isoformat(),
            }
        )
    return events


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
    observations: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}

    class ReferenceStrategy(Strategy):  # type: ignore[misc]
        def initialize(self) -> None:
            self.sleeptime = "1D"
            self.set_market("NYSE")
            self._submitted = False
            # Kept only to read the broker's event log once the run is over.
            # Nothing is ever submitted through this reference.
            captured["broker"] = self.broker

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

            # Sampled *before* this session's fills are processed, which is
            # the only point in the session a strategy gets to run. These are
            # therefore last session's closing balances marked at this
            # session's price; `_normalize_result` checks them against the
            # reconstruction and then adds this session's booked fills.
            position = self.get_position(asset)
            observations.append(
                {
                    "market_date": current_date.isoformat(),
                    "observed_cash": float(self.cash),
                    "observed_equity": float(self.portfolio_value),
                    "observed_quantity": float(position.quantity) if position else 0.0,
                    "mark_price": float(price) if price else 0.0,
                }
            )

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

    broker = captured.get("broker")
    if broker is None:
        raise BacktestExecutionError("lumibot never initialized the reference strategy")
    fills = _authoritative_fill_events(broker, strategy_cfg.symbol)
    for fill in fills:
        if fill["order_id"] in orders:
            orders[fill["order_id"]]["status"] = "fill"

    return _normalize_result(
        strategy_cfg, bars, orders, fills, observations, lumibot.__version__
    )


def _require_agrees(actual: float, expected: float, what: str) -> None:
    if abs(actual - expected) > _LEDGER_TOLERANCE * max(1.0, abs(expected)):
        raise BacktestExecutionError(
            f"reconstructed {what} ({expected}) disagrees with the value lumibot "
            f"reported ({actual}); the daily-state realignment cannot be trusted"
        )


def _normalize_result(
    strategy_cfg: StrategyConfig,
    bars,
    orders: dict[str, dict[str, Any]],
    fills: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    lumibot_version: str,
) -> dict[str, Any]:
    """Turn per-iteration samples into end-of-session states.

    Each observation is what LumiBot reported *before* that session's fills
    were booked. Walking the sessions in order and applying the fills the
    broker stamped with each session yields the end-of-session balances, which
    is the state a daily series is normally understood to carry.

    Two invariants keep this from drifting away from LumiBot's own accounting,
    and both are hard errors rather than warnings:

      * each session's observation must equal the reconstruction as of the
        *previous* reported session -- if the one-session lag documented above
        ever stops holding, the realignment is wrong; and
      * `observed_cash + observed_quantity * mark_price` must equal the
        `portfolio_value` LumiBot reported -- if it does not, `mark_price` is
        not the price LumiBot marked at and every reconstructed equity would
        be wrong.
    """
    for fill in fills:
        if fill["side"] != "buy":
            raise BacktestExecutionError(
                f"reference strategy is buy-only but lumibot booked a "
                f"{fill['side']!r} fill; realized P&L is not modelled"
            )

    fills_by_session: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        fills_by_session.setdefault(fill["market_date"], []).append(fill)

    cash = float(strategy_cfg.budget)
    quantity = 0.0
    cost_basis = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0
    daily_states: list[dict[str, Any]] = []
    expected_cash = float(strategy_cfg.budget)
    expected_quantity = 0.0

    for observation in observations:
        session = observation["market_date"]
        _require_agrees(observation["observed_cash"], expected_cash, f"cash before {session}")
        _require_agrees(
            observation["observed_quantity"], expected_quantity, f"quantity before {session}"
        )
        _require_agrees(
            observation["observed_equity"],
            observation["observed_cash"]
            + observation["observed_quantity"] * observation["mark_price"],
            f"mark price on {session}",
        )

        for fill in fills_by_session.pop(session, ()):
            cash -= fill["quantity"] * fill["fill_price"] + fill["fees"]
            quantity += fill["quantity"]
            cost_basis += fill["quantity"] * fill["fill_price"]

        equity = cash + quantity * observation["mark_price"]
        peak_equity = max(peak_equity, equity)
        drawdown_fraction = (
            0.0 if peak_equity <= 0 else min(0.0, (equity - peak_equity) / peak_equity)
        )
        max_drawdown = min(max_drawdown, drawdown_fraction)
        daily_states.append(
            {
                "market_date": session,
                "cash": cash,
                "equity": equity,
                "realized_pnl": 0.0,
                "unrealized_pnl": quantity * observation["mark_price"] - cost_basis,
                "drawdown_fraction": drawdown_fraction,
            }
        )
        expected_cash = cash
        expected_quantity = quantity

    if fills_by_session:
        # A fill stamped with a session the strategy never ran on would be
        # dropped from the daily series while still appearing under `fills`,
        # leaving a document that contradicts itself.
        raise BacktestExecutionError(
            f"lumibot booked fills on sessions with no trading iteration: "
            f"{sorted(fills_by_session)}"
        )

    positions = []
    if quantity > 0:
        positions.append(
            {
                "symbol": strategy_cfg.symbol,
                "quantity": quantity,
                "average_price": cost_basis / quantity,
            }
        )

    final_state = daily_states[-1] if daily_states else None
    final_cash = final_state["cash"] if final_state else float(strategy_cfg.budget)
    final_equity = final_state["equity"] if final_state else float(strategy_cfg.budget)

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
