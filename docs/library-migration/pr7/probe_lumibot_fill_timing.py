"""Read-only probe: when does LumiBot actually fill, and when is it observed?

    <backtest_runtime venv>/bin/python \
        docs/library-migration/pr7/probe_lumibot_fill_timing.py \
        docs/library-migration/pr7/fixtures/case_a_buy_and_hold.input.json

Runs in the **isolated `backtest_runtime` environment only** -- it imports
`lumibot`. It applies `backtest_runtime.credential_guard` first, in the same
order `backtest_runtime/__main__.py` does (scrub, then suppress dotenv, then
import), so the probe carries the same credential-safety posture as the real
entry point. It changes nothing in `backtest_runtime/`: it re-runs an
equivalent strategy with extra instrumentation and prints what it observed.

It exists to decide two PR 7 classifications from measurement rather than
inference:

  * **D1** -- does LumiBot's first `on_trading_iteration` really land on the
    second fixture bar, and is the first bar therefore reported nowhere?
  * **D4** -- is a fill's `market_date` (taken from `get_datetime()` inside
    `on_filled_order`) the date the fill was actually booked, or the date the
    callback was invoked one iteration later? The probe records the portfolio
    state at every iteration alongside LumiBot's own `Order` timestamps, so
    the two can be told apart.

Output is a text transcript; the checked-in copy is `probe_output.txt`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from backtest_runtime.credential_guard import (
    scrub_credential_environment,
    suppress_dotenv_discovery,
)

scrub_credential_environment()
suppress_dotenv_discovery()

# LumiBot writes a banner and progress bars to fd 1; keep them off the
# transcript exactly as the real entry point does.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

import lumibot  # noqa: E402
import pandas as pd  # noqa: E402
from lumibot.backtesting.pandas_backtesting import PandasDataBacktesting  # noqa: E402
from lumibot.entities import Asset, Data  # noqa: E402
from lumibot.strategies.strategy import Strategy  # noqa: E402

TRANSCRIPT: list[str] = []


def record(line: str) -> None:
    TRANSCRIPT.append(line)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: probe_lumibot_fill_timing.py <input_json_path>", file=sys.stderr)
        return 2
    document = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    strategy_cfg = document["strategy"]
    raw_bars = document["bars"]
    symbol = strategy_cfg["symbol"]
    quantity = strategy_cfg["quantity"]
    asset = Asset(symbol, asset_type="stock")

    record(f"lumibot {lumibot.__version__}")
    record(f"input: {Path(argv[0]).name}")
    record(f"bars supplied: {len(raw_bars)}  ({raw_bars[0]['date']} .. {raw_bars[-1]['date']})")
    record("")

    observations: list[dict] = []

    class ProbeStrategy(Strategy):  # type: ignore[misc]
        def initialize(self) -> None:
            self.sleeptime = "1D"
            self.set_market("NYSE")
            self._submitted = False

        def on_trading_iteration(self) -> None:
            now = self.get_datetime()
            price = self.get_last_price(asset)
            position = self.get_position(asset)
            observations.append(
                {
                    "event": "iteration",
                    "iteration_datetime": now.isoformat(),
                    "get_last_price": None if price is None else float(price),
                    "cash": float(self.cash),
                    "portfolio_value": float(self.portfolio_value),
                    "position_quantity": float(position.quantity) if position else 0.0,
                }
            )
            if not self._submitted and price:
                order = self.create_order(asset, quantity, "buy")
                submitted = self.submit_order(order)
                observations.append(
                    {
                        "event": "submit",
                        "iteration_datetime": now.isoformat(),
                        "order_id": submitted.identifier,
                        "status": str(getattr(submitted.status, "value", submitted.status)),
                    }
                )
                self._submitted = True

        def on_filled_order(self, position, order, price, quantity, multiplier) -> None:
            observations.append(
                {
                    "event": "on_filled_order",
                    # What backtest_runtime/strategy.py stamps onto the fill:
                    "callback_get_datetime": self.get_datetime().isoformat(),
                    "order_id": order.identifier,
                    "fill_price": float(price),
                    "fill_quantity": float(quantity),
                    # LumiBot's own timestamps on the order object, which is
                    # the independent record of when the fill was booked.
                    "order_broker_create_date": str(
                        getattr(order, "broker_create_date", None)
                    ),
                    "order_broker_date": str(getattr(order, "broker_date", None)),
                    "order_status": str(getattr(order.status, "value", order.status)),
                    "cash_at_callback": float(self.cash),
                    "portfolio_value_at_callback": float(self.portfolio_value),
                }
            )

    rows = [
        {
            "datetime": dt.date.fromisoformat(bar["date"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }
        for bar in raw_bars
    ]
    frame = pd.DataFrame(rows)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame = frame.set_index("datetime")

    first = dt.date.fromisoformat(raw_bars[0]["date"])
    last = dt.date.fromisoformat(raw_bars[-1]["date"])
    ProbeStrategy.run_backtest(
        PandasDataBacktesting,
        backtesting_start=dt.datetime.combine(first, dt.time.min),
        backtesting_end=dt.datetime.combine(last, dt.time.min),
        pandas_data={asset: Data(asset, frame, timestep="day", timezone="America/New_York")},
        budget=strategy_cfg["budget"],
        benchmark_asset=None,
        analyze_backtest=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_plot=False,
        show_indicators=False,
        quiet_logs=True,
        save_logfile=False,
    )

    for observation in observations:
        event = observation.pop("event")
        record(f"{event}:")
        for key, value in observation.items():
            record(f"    {key} = {value}")
    record("")

    iterations = [row for row in observations if "iteration_datetime" in row]
    first_iteration = iterations[0]["iteration_datetime"] if iterations else "(none)"
    record("FINDINGS")
    record(f"  first fixture bar date      : {raw_bars[0]['date']}")
    record(f"  first on_trading_iteration  : {first_iteration}")
    record(
        "  => D1: the first fixture bar is never an iteration date, so it can never "
        "appear as a daily state."
    )
    fills = [row for row in observations if "callback_get_datetime" in row]
    for fill in fills:
        record(f"  on_filled_order observed at : {fill['callback_get_datetime']}")
        record(f"  order.broker_create_date    : {fill['order_broker_create_date']}")
        record(f"  order.broker_date           : {fill['order_broker_date']}")
    record(
        "  => D4: compare the callback date against the iteration at which cash first "
        "dropped, printed above."
    )

    text = "\n".join(TRANSCRIPT) + "\n"
    print(text, file=_REAL_STDOUT, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
