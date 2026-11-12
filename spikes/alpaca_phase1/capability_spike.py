"""THROWAWAY Phase 1 broker capability spike (docs/automated-live-trading-implementation-plan.md).

Records how the Alpaca PAPER account treats attached orders, with captured request/response fixtures.
It is not part of the application: nothing imports it, it is outside pyright, and it touches neither
`paper_runtime` nor the main process. Only its exit-safety logic has a credential-free fake-broker test
(tests/tools/test_alpaca_capability_spike.py).

Safety, enforced in code:
* paper only. The client is built with paper=True and the env file must say ALPACA_IS_PAPER=true and the
  paper URL, or the script refuses to start;
* credentials come only from a dedicated env file passed with --env-file. That file may contain only the
  allowlisted ALPACA_* keys, so the repository's main `.env` (other services' secrets) is rejected;
* one share of SPY at most, every order carries the client-order-id prefix `p1spike-`, and everything
  still open under that prefix is cancelled on exit;
* exit is reconciled to flat, not just cancelled: once `fill` has verified the account held no SPY and no
  SPY orders, cleanup cancels the open orders, waits until none remain, reads the real position and
  flattens exactly that signed quantity. A flatten is never sent on the strength of an assumed fill. If
  exposure or open orders still remain, the run prints them and exits non-zero;
* the default mode makes no network call. `read-only` reads. `rest` places orders far from the market
  that cannot fill. `fill` places marketable one-share orders and is refused outside regular hours or if
  the account already holds or has orders in the symbol.

    python spikes/alpaca_phase1/capability_spike.py plan
    python spikes/alpaca_phase1/capability_spike.py read-only --env-file PATH
    python spikes/alpaca_phase1/capability_spike.py rest --env-file PATH
    python spikes/alpaca_phase1/capability_spike.py fill --env-file PATH --i-understand-this-trades-paper

Not financial advice.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

PAPER_URL = "https://paper-api.alpaca.markets"
ALLOWED_ENV_KEYS = frozenset({
    "ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_IS_PAPER", "ALPACA_BASE_URL",
    "ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET",
})
SYMBOL = "SPY"
PREFIX = "p1spike-"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "alpaca-capability" / "fixtures"
REDACT_KEYS = {"id_account", "account_number", "id"}  # `id` only redacted on account objects, see _redact_account

PLAN = """Phase 1 spike plan (no calls are made in this mode)

read-only : account, clock, account configuration, the asset record, current positions and open orders.
rest      : one-share SPY orders priced far below the market so they cannot fill:
            OTO day / OTO gtc / bracket gtc / stop-market vs stop-limit leg types / invalid stop above entry /
            duplicate client order id / lookup by client order id / replace the parent and a held leg /
            cancel the parent and read its legs / a short sell with no position (ADR A9).
fill      : (regular hours only, one share) marketable OTO: parent fills, stop leg activates; a competing sell
            while the stop rests (ADR A4); stop replaced upward then downward; exit by cancel-confirm-sell;
            marketable bracket fill and its sibling leg; then flat.
Not measurable on paper, recorded as not demonstrated: partial parent fills, a gap through the stop, GTC age
expiry, split and dividend handling, rate-limit headers. They stay open in the capability matrix."""


# -- credentials -----------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    """Only the allowlisted keys are read; every other line is skipped without its value being kept.

    The operator chose to point the spike at the shared repo `.env`, so other settings (model, Reddit, ...)
    may be present. They are never stored, passed on or printed.
    """
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if not sep or key not in ALLOWED_ENV_KEYS:
            continue
        values[key] = value.strip().strip("'\"")
    if values.get("ALPACA_IS_PAPER", "").lower() != "true":
        raise SystemExit("ALPACA_IS_PAPER must be exactly 'true'")
    if values.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/") != PAPER_URL:
        raise SystemExit(f"ALPACA_BASE_URL must be {PAPER_URL}")
    if not values.get("ALPACA_API_KEY") or not values.get("ALPACA_API_SECRET"):
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    return values


# -- fixtures --------------------------------------------------------------------


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def _redact_account(data):
    if isinstance(data, dict):
        return {k: ("<redacted>" if k in {"account_number", "id"} else _redact_account(v)) for k, v in data.items()}
    return data


class Recorder:
    def __init__(self, stage: str):
        self.stage, self.steps = stage, []
        # Set only after `fill` has verified the account held no SPY position and no SPY order, so a SPY
        # position found later is this run's and may be flattened. Never true for a refused or other run.
        self.owns_symbol = False
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    def run(self, name: str, request, call, *, account: bool = False):
        """Run one broker call and store request, response or error; never raises for a broker error."""
        entry = {"step": name, "request": _jsonable(request)}
        try:
            result = call()
            entry["response"] = _redact_account(_jsonable(result)) if account else _jsonable(result)
            outcome = result
        except Exception as exc:  # recording the broker's error taxonomy is the point
            entry["error"] = {
                "type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
                "code": getattr(exc, "code", None), "message": str(exc),
            }
            outcome = None
        self.steps.append(entry)
        (FIXTURE_DIR / f"{self.stage}_{len(self.steps):02d}_{name}.json").write_text(
            json.dumps(entry, indent=2, sort_keys=True) + "\n")
        status = "ERROR " + entry["error"]["message"][:100] if "error" in entry else "ok"
        print(f"  [{self.stage}] {name}: {status}")
        return outcome

    def save(self):
        (FIXTURE_DIR / f"{self.stage}_run.json").write_text(json.dumps(self.steps, indent=2, sort_keys=True) + "\n")


# -- helpers ---------------------------------------------------------------------


def cid(label: str) -> str:
    return f"{PREFIX}{label}-{uuid.uuid4().hex[:8]}"


def build_clients(env: dict[str, str]):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    trading = TradingClient(env["ALPACA_API_KEY"], env["ALPACA_API_SECRET"], paper=True)
    data = StockHistoricalDataClient(
        env.get("ALPACA_MARKET_DATA_API_KEY", env["ALPACA_API_KEY"]),
        env.get("ALPACA_MARKET_DATA_API_SECRET", env["ALPACA_API_SECRET"]),
    )
    return trading, data


def last_prices(data) -> tuple[float, float]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestQuoteRequest

    quote = data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=SYMBOL, feed=DataFeed.IEX))[SYMBOL]
    return float(quote.bid_price or 0), float(quote.ask_price or 0)


CREATED_IDS: set[str] = set()  # order ids made by a replace (their client_order_id has no prefix)


def cancel_prefixed(trading, rec: Recorder | None = None) -> None:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    for order in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL], nested=True)):
        if (order.client_order_id or "").startswith(PREFIX) or str(order.id) in CREATED_IDS:
            try:
                trading.cancel_order_by_id(order.id)
                print(f"  cleanup: cancelled {order.client_order_id}")
            except Exception as exc:
                print(f"  cleanup: could not cancel {order.client_order_id}: {exc}")


def fetch(trading, order_id):
    from alpaca.trading.requests import GetOrderByIdRequest

    return trading.get_order_by_id(order_id, GetOrderByIdRequest(nested=True))


# -- stages ----------------------------------------------------------------------


def stage_read_only(trading, data, rec: Recorder) -> None:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    rec.run("account", {}, trading.get_account, account=True)
    rec.run("clock", {}, trading.get_clock)
    rec.run("account_configurations", {}, trading.get_account_configurations)
    rec.run("asset", {"symbol": SYMBOL}, lambda: trading.get_asset(SYMBOL))
    rec.run("positions", {}, trading.get_all_positions)
    rec.run("open_orders", {}, lambda: trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)))
    rec.run("latest_quote", {"symbol": SYMBOL}, lambda: last_prices(data))


def stage_rest(trading, data, rec: Recorder) -> None:
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, ReplaceOrderRequest, StopLossRequest, TakeProfitRequest

    bid, ask = last_prices(data)
    reference = ask or bid
    if reference <= 0:
        raise SystemExit("no usable quote; cannot price orders away from the market")
    entry = round(reference * 0.80, 2)  # 20% below the market: rests, cannot fill
    stop, target = round(entry * 0.97, 2), round(entry * 1.05, 2)
    print(f"  reference {reference}, resting entry {entry}, stop {stop}, target {target}")

    def submit(label, **kw):
        req = LimitOrderRequest(symbol=SYMBOL, qty=1, side=OrderSide.BUY, limit_price=entry,
                                client_order_id=kw.pop("client_order_id", cid(label)), **kw)
        return req, rec.run(f"submit_{label}", req.model_dump(mode="json"), lambda: trading.submit_order(req))

    # ADR A3: one time in force for every leg? Read the legs' own fields back.
    _, oto_day = submit("oto_day", time_in_force=TimeInForce.DAY, order_class=OrderClass.OTO,
                        stop_loss=StopLossRequest(stop_price=stop))
    if oto_day is not None:
        rec.run("oto_day_with_legs", {"id": str(oto_day.id)}, lambda: fetch(trading, oto_day.id))
        # A replace creates a NEW order id with an auto-generated client_order_id (no prefix); the old id
        # becomes `replaced`. Track the new id so it is cancelled and cleaned up.
        replaced = rec.run("oto_day_replace_parent_price", {"limit_price": entry - 0.05},
                           lambda: trading.replace_order_by_id(oto_day.id, ReplaceOrderRequest(limit_price=round(entry - 0.05, 2))))
        live_parent_id = getattr(replaced, "id", None) or oto_day.id
        CREATED_IDS.add(str(live_parent_id))
        legs = getattr(fetch(trading, live_parent_id), "legs", None) or []
        if legs:
            held = rec.run("oto_day_replace_held_leg", {"leg": str(legs[0].id)},
                           lambda: trading.replace_order_by_id(legs[0].id, ReplaceOrderRequest(stop_price=round(stop + 0.05, 2))))
            if held is not None:
                CREATED_IDS.add(str(held.id))
        rec.run("oto_day_cancel_parent", {"id": str(live_parent_id)}, lambda: trading.cancel_order_by_id(live_parent_id))
        time.sleep(1)
        rec.run("oto_day_after_cancel", {"id": str(live_parent_id)}, lambda: fetch(trading, live_parent_id))
        rec.run("oto_day_replaced_original", {"id": str(oto_day.id)}, lambda: fetch(trading, oto_day.id))

    _, oto_gtc = submit("oto_gtc", time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
                        stop_loss=StopLossRequest(stop_price=stop))
    if oto_gtc is not None:
        rec.run("oto_gtc_with_legs", {"id": str(oto_gtc.id)}, lambda: fetch(trading, oto_gtc.id))

    _, bracket = submit("bracket_gtc", time_in_force=TimeInForce.GTC, order_class=OrderClass.BRACKET,
                        stop_loss=StopLossRequest(stop_price=stop), take_profit=TakeProfitRequest(limit_price=target))
    if bracket is not None:
        rec.run("bracket_gtc_with_legs", {"id": str(bracket.id)}, lambda: fetch(trading, bracket.id))

    submit("bracket_day", time_in_force=TimeInForce.DAY, order_class=OrderClass.BRACKET,
           stop_loss=StopLossRequest(stop_price=stop), take_profit=TakeProfitRequest(limit_price=target))
    submit("oto_stop_limit_leg", time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
           stop_loss=StopLossRequest(stop_price=stop, limit_price=round(stop - 0.10, 2)))
    submit("invalid_stop_above_entry", time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
           stop_loss=StopLossRequest(stop_price=round(entry + 1, 2)))

    fixed = cid("duplicate")
    submit("duplicate_first", time_in_force=TimeInForce.GTC, client_order_id=fixed)
    submit("duplicate_second", time_in_force=TimeInForce.GTC, client_order_id=fixed)
    rec.run("lookup_by_client_order_id", {"client_order_id": fixed}, lambda: trading.get_order_by_client_id(fixed))

    # A short-sell test only means something with no buy order open (an open buy makes the broker refuse it for
    # that reason alone), so clear the book first. The limit is 25% ABOVE the market so it cannot fill.
    cancel_prefixed(trading)
    time.sleep(2)
    short = LimitOrderRequest(symbol=SYMBOL, qty=1, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                              limit_price=round(reference * 1.25, 2), client_order_id=cid("short_no_position"))
    rec.run("short_sell_without_position", short.model_dump(mode="json"), lambda: trading.submit_order(short))


TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"})
SETTLE_SECONDS = 45


class ExposureRemaining(RuntimeError):
    """Cleanup could not prove the account is flat of this run's SPY exposure and orders."""


def _wait(trading, order_id, wanted: set[str], seconds: int = 45):
    """Poll until the order reaches a wanted status, any other terminal one, or the deadline."""
    deadline = time.time() + seconds
    order = fetch(trading, order_id)
    while str(order.status.value) not in wanted | TERMINAL_STATUSES and time.time() < deadline:
        time.sleep(1.5)
        order = fetch(trading, order_id)
    return order


def _open_orders(trading, *, owns_symbol: bool) -> list:
    """Open SPY orders (legs listed flat) that this run is responsible for."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL]))
    # After the fill preflight there were no SPY orders at all, so every open one is this run's (including
    # legs, whose client order ids carry no prefix). Otherwise only the prefix and replaced ids are ours.
    return [o for o in orders
            if owns_symbol or (o.client_order_id or "").startswith(PREFIX) or str(o.id) in CREATED_IDS]


def _position_qty(trading) -> Decimal:
    return sum((Decimal(str(p.qty)) for p in trading.get_all_positions() if p.symbol == SYMBOL), Decimal(0))


def settle_to_flat(trading, rec: Recorder, seconds: int = SETTLE_SECONDS) -> list[str]:
    """Cancel this run's open orders, then flatten the SPY position the broker reports; return what remains.

    The flatten side and quantity come from the confirmed position, never from an assumed fill: an entry
    that did not fill produces no sell, and one that filled late (even during the cancel) is flattened.
    A position is only flattened when `rec.owns_symbol` says the run began with none. The result is empty
    only when no matching open order and no SPY position (when owned) remain. Never raises.
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    owns = rec.owns_symbol
    problems: list[str] = []

    def remaining_orders() -> list:
        deadline = time.time() + seconds
        while True:
            mine = _open_orders(trading, owns_symbol=owns)
            if not mine or time.time() >= deadline:
                return mine
            for order in mine:
                try:
                    trading.cancel_order_by_id(order.id)
                    print(f"  cleanup: cancelled {order.client_order_id}")
                except Exception as exc:
                    print(f"  cleanup: could not cancel {order.client_order_id}: {exc}")
            time.sleep(1.5)

    try:
        stuck = remaining_orders()  # cancelling first frees shares held by a resting stop or target
        if stuck:
            problems.append(f"{len(stuck)} open {SYMBOL} order(s) could not be cancelled: "
                            + ", ".join(str(o.client_order_id or o.id) for o in stuck))
        if owns:
            qty = _position_qty(trading)
            if qty != 0:
                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                flat = MarketOrderRequest(symbol=SYMBOL, qty=float(abs(qty)), side=side, time_in_force=TimeInForce.DAY,
                                          client_order_id=cid("cleanup_flatten"))
                order = rec.run("cleanup_flatten", flat.model_dump(mode="json"), lambda: trading.submit_order(flat))
                if order is not None:
                    _wait(trading, order.id, {"filled"}, seconds)
                stuck = remaining_orders()  # a flatten that did not fill is cancelled, never left to fill later
                if stuck:
                    problems.append(f"{len(stuck)} open {SYMBOL} order(s) remain after the flatten: "
                                    + ", ".join(str(o.client_order_id or o.id) for o in stuck))
                qty = _position_qty(trading)  # re-read last: a cancel can race a fill
            if qty != 0:
                problems.append(f"{SYMBOL} position of {qty} share(s) is still open")
    except Exception as exc:  # cleanup must report, not mask the failure it is cleaning up after
        problems.append(f"cleanup failed ({type(exc).__name__}: {exc}); check {SYMBOL} exposure manually")
    return problems


def require_flat(trading, rec: Recorder) -> None:
    problems = settle_to_flat(trading, rec)
    if problems:
        raise ExposureRemaining("; ".join(problems))


def stage_fill(trading, data, rec: Recorder) -> None:
    from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import (
        GetOrdersRequest, LimitOrderRequest, MarketOrderRequest, ReplaceOrderRequest, StopLossRequest, TakeProfitRequest,
    )

    clock = trading.get_clock()
    if not clock.is_open:
        raise SystemExit("the market is closed; the fill stage runs only in regular hours")
    if trading.get_all_positions() and any(p.symbol == SYMBOL for p in trading.get_all_positions()):
        raise SystemExit(f"the account already holds {SYMBOL}; refusing to mix with an existing position")
    if trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL])):
        raise SystemExit(f"there are open {SYMBOL} orders; refusing to start")
    bid, ask = last_prices(data)
    if bid <= 0 or ask <= 0:
        raise SystemExit("no usable quote")
    marketable = round(ask + 0.50, 2)
    stop = round(bid * 0.97, 2)
    print(f"  bid {bid} ask {ask}; marketable limit {marketable}; stop {stop}")
    rec.owns_symbol = True  # the account was verified free of SPY: from here any SPY position is this run's

    req =LimitOrderRequest(symbol=SYMBOL, qty=1, side=OrderSide.BUY, limit_price=marketable, time_in_force=TimeInForce.GTC,
                            order_class=OrderClass.OTO, stop_loss=StopLossRequest(stop_price=stop), client_order_id=cid("fill_oto"))
    parent = rec.run("fill_submit_oto", req.model_dump(mode="json"), lambda: trading.submit_order(req))
    if parent is None:
        return
    filled = _wait(trading, parent.id, {"filled"})
    rec.run("fill_oto_after_parent_fill", {"id": str(parent.id)}, lambda: fetch(trading, parent.id))
    if str(filled.status.value) != "filled":
        print("  parent did not fill in time; stopping the fill stage")
        return
    leg = (fetch(trading, parent.id).legs or [None])[0]
    time.sleep(2)
    rec.run("fill_oto_leg_state", {"leg": str(getattr(leg, "id", None))}, lambda: fetch(trading, parent.id))

    # ADR A4: can the shares a resting stop holds be sold by a second order?
    competing = MarketOrderRequest(symbol=SYMBOL, qty=1, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                                   client_order_id=cid("fill_competing_sell"))
    rec.run("fill_competing_sell_while_stop_rests", competing.model_dump(mode="json"), lambda: trading.submit_order(competing))
    cancel_prefixed_sells = [o for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL]))
                             if (o.client_order_id or "").startswith(f"{PREFIX}fill_competing")]
    for stray in cancel_prefixed_sells:
        trading.cancel_order_by_id(stray.id)

    if leg is not None:
        rec.run("fill_replace_stop_tighten", {"stop_price": stop + 0.25},
                lambda: trading.replace_order_by_id(leg.id, ReplaceOrderRequest(stop_price=round(stop + 0.25, 2))))
        rec.run("fill_replace_stop_loosen", {"stop_price": stop - 0.25},
                lambda: trading.replace_order_by_id(leg.id, ReplaceOrderRequest(stop_price=round(stop - 0.25, 2))))
        rec.run("fill_stop_state_after_replace", {"id": str(parent.id)}, lambda: fetch(trading, parent.id))

    # ADR A4 option 2: cancel the stop, confirm, sell, confirm.
    open_stops = [o for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL]))
                  if (o.client_order_id or "").startswith(PREFIX) or o.side == OrderSide.SELL]
    for stop_order in open_stops:
        rec.run("fill_exit_cancel_stop", {"id": str(stop_order.id)}, lambda s=stop_order: trading.cancel_order_by_id(s.id))
    time.sleep(2)
    exit_req = MarketOrderRequest(symbol=SYMBOL, qty=1, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                                  client_order_id=cid("fill_exit"))
    exit_order = None
    if _position_qty(trading) > 0:  # the competing sell above may already have taken the share; never sell short
        exit_order = rec.run("fill_exit_sell", exit_req.model_dump(mode="json"), lambda: trading.submit_order(exit_req))
    else:
        print("  no long SPY position left to exit; skipping the exit sell")
    if exit_order is not None:
        rec.run("fill_exit_confirmed", {"id": str(exit_order.id)}, lambda: _wait(trading, exit_order.id, {"filled"}))
    require_flat(trading, rec)  # the bracket test starts only from a confirmed-flat account

    # Bracket: marketable entry, then the stop and target legs.
    bracket = LimitOrderRequest(
        symbol=SYMBOL, qty=1, side=OrderSide.BUY, limit_price=round(ask + 0.50, 2), time_in_force=TimeInForce.GTC,
        order_class=OrderClass.BRACKET, stop_loss=StopLossRequest(stop_price=stop),
        take_profit=TakeProfitRequest(limit_price=round(ask * 1.05, 2)), client_order_id=cid("fill_bracket"))
    bparent = rec.run("fill_submit_bracket", bracket.model_dump(mode="json"), lambda: trading.submit_order(bracket))
    if bparent is not None:
        entry = _wait(trading, bparent.id, {"filled"})
        if str(entry.status.value) != "filled":
            print("  bracket entry did not fill in time; cancelling it and flattening only what the broker reports")
        time.sleep(2)
        rec.run("fill_bracket_after_fill", {"id": str(bparent.id)}, lambda: fetch(trading, bparent.id))
        # Cancel the legs and entry, then flatten the position the broker reports (none if the entry never
        # filled, so no sell can open a short).
        require_flat(trading, rec)
        rec.run("fill_bracket_after_cancel_legs", {"id": str(bparent.id)}, lambda: fetch(trading, bparent.id))
    require_flat(trading, rec)
    rec.run("final_positions", {}, trading.get_all_positions)


# -- main ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("plan", "read-only", "rest", "fill"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--i-understand-this-trades-paper", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "plan":
        print(PLAN)
        return 0
    if args.env_file is None:
        raise SystemExit("--env-file is required")
    if args.mode == "fill" and not args.i_understand_this_trades_paper:
        raise SystemExit("fill mode submits and fills one-share paper orders; pass --i-understand-this-trades-paper")

    trading, data = build_clients(load_env_file(args.env_file))
    rec = Recorder(args.mode.replace("-", "_"))
    stage = {"read-only": stage_read_only, "rest": stage_rest, "fill": stage_fill}[args.mode]
    problems: list[str] = []
    try:
        stage(trading, data, rec)
    finally:
        if args.mode in ("rest", "fill"):
            problems = settle_to_flat(trading, rec)
            if problems:
                print("!! EXPOSURE REMAINS - reconcile the paper account manually:")
                for problem in problems:
                    print(f"!!   {problem}")
        rec.save()
        print(f"fixtures written to {FIXTURE_DIR}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
