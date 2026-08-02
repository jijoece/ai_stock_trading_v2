"""Read-only probe: when does LumiBot actually book a fill, and when is it seen?

    <backtest_runtime venv>/bin/python \
        docs/library-migration/pr7/probe_lumibot_fill_timing.py \
        docs/library-migration/pr7/fixtures/case_a_buy_and_hold.input.json

Runs in the **isolated `backtest_runtime` environment only** -- it imports
`lumibot`. It applies `backtest_runtime.credential_guard` first, in the same
order `backtest_runtime/__main__.py` does (scrub, then suppress dotenv, then
import), so the probe carries the same credential-safety posture as the real
entry point. It changes nothing in `backtest_runtime/`: it re-runs an
equivalent strategy with extra instrumentation and prints what it observed.

It exists so PR 7's classifications rest on measurement rather than
inference, and specifically so that **no fill session is ever deduced from
"which bar's open equals the fill price"** -- a fill price of 100.5 is
evidence about the price, not about the session.

Three independent clocks are recorded for every fill:

  1. `broker trade-event log "time"` -- LumiBot's own record, stamped with
     `data_source._datetime` when the event is processed
     (`lumibot/brokers/broker.py`). This is the **authoritative** booking
     session and the one `backtest_runtime` now reports.
  2. `on_filled_order`'s `get_datetime()` -- when the strategy is *told*.
     Reference strategy v1 stamped fills with this; it lags (1) by one
     session whenever a later session exists.
  3. the first iteration at which `self.cash` has changed -- the earliest
     point a strategy could notice the fill without reading the broker's
     books. Recorded as the fallback observation, so that if (1) ever
     disappears the transcript still shows what is knowable.

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
    entry_after = strategy_cfg["entry_after_session"]
    asset = Asset(symbol, asset_type="stock")

    record(f"lumibot {lumibot.__version__}")
    record(f"input: {Path(argv[0]).name}")
    record(f"bars supplied: {len(raw_bars)}  ({raw_bars[0]['date']} .. {raw_bars[-1]['date']})")
    record(f"entry_after_session: {entry_after}")
    record("")

    observations: list[dict] = []
    captured: dict = {}

    class ProbeStrategy(Strategy):  # type: ignore[misc]
        def initialize(self) -> None:
            self.sleeptime = "1D"
            self.set_market("NYSE")
            self._submitted = False
            captured["broker"] = self.broker

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
            allowed = entry_after is None or now.date() > dt.date.fromisoformat(entry_after)
            if not self._submitted and allowed and price:
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
                    # Clock (2): what reference strategy v1 stamped on the fill.
                    "callback_get_datetime": self.get_datetime().isoformat(),
                    "order_id": order.identifier,
                    "fill_price": float(price),
                    "fill_quantity": float(quantity),
                    # Live brokers populate these; the backtesting broker never
                    # does, which is why the event log below is needed.
                    "order_broker_create_date": str(getattr(order, "broker_create_date", None)),
                    "order_broker_date": str(getattr(order, "broker_update_date", None)),
                    "cash_at_callback": float(self.cash),
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

    # Clock (1): LumiBot's own books.
    log = getattr(captured.get("broker"), "_trade_event_log_df", None)
    record("AUTHORITATIVE BROKER TRADE-EVENT LOG")
    authoritative: list[tuple[str, float]] = []
    if log is None or log.empty:
        record("    (unavailable -- booking session is UNRESOLVED from this run)")
    else:
        for _, row in log.iterrows():
            record(
                f"    time={row['time']} id={row['identifier']} side={row['side']} "
                f"type={row['type']} status={row['status']} price={row['price']} "
                f"filled_quantity={row['filled_quantity']}"
            )
            if str(getattr(row["status"], "value", row["status"])).lower() == "fill":
                authoritative.append((str(row["time"].date()), float(row["price"])))
    record("")

    iterations = [row for row in observations if "get_last_price" in row]
    first_iteration = iterations[0]["iteration_datetime"] if iterations else "(none)"
    budget = float(strategy_cfg["budget"])
    first_mutation = next(
        (row["iteration_datetime"] for row in iterations if row["cash"] != budget), None
    )
    callbacks = [row for row in observations if "callback_get_datetime" in row]

    record("FINDINGS")
    record(f"  first fixture bar date         : {raw_bars[0]['date']}")
    record(f"  first on_trading_iteration     : {first_iteration}")
    record(
        "  => D1: the first fixture bar is never a trading iteration, so it can "
        "never appear as a daily state, and no order can be submitted on it."
    )
    for session, price in authoritative:
        record(f"  [1] broker booked the fill on  : {session} at {price}")
    for callback in callbacks:
        record(f"  [2] strategy was told on       : {callback['callback_get_datetime']}")
    record(f"  [3] first iteration with changed cash : {first_mutation}")
    record(
        "  => D4: (1) is the booking session and is what backtest_runtime reports. "
        "(2) and (3) lag it by one session whenever a later session exists, because "
        "`_process_pandas_daily_data` runs `process_pending_orders` *after* "
        "`on_trading_iteration` within the same session. Neither lagging clock is "
        "evidence that the fill happened later."
    )
    record(
        "  => D15: the state a strategy can sample on the booking session therefore "
        "excludes that session's own fill; backtest_runtime re-applies it from (1)."
    )

    text = "\n".join(TRANSCRIPT) + "\n"
    print(text, file=_REAL_STDOUT, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
