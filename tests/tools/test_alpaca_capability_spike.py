"""Offline tests for the exit safety of spikes/alpaca_phase1/capability_spike.py.

No network, credentials or alpaca-py: the broker is an in-memory fake and the `alpaca.trading` enum and
request modules the spike imports lazily are replaced with minimal stand-ins. They prove the credentialed
`fill` run never sells on an assumed fill, flattens the position the broker reports when it is interrupted
after a fill, and cannot report a clean exit while SPY exposure or open orders remain.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "spikes" / "alpaca_phase1" / "capability_spike.py"
_spec = importlib.util.spec_from_file_location("capability_spike_under_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
spike = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spike)  # type: ignore[union-attr]


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"


class OrderClass(Enum):
    OTO = "oto"
    BRACKET = "bracket"


class QueryOrderStatus(Enum):
    OPEN = "open"


class _Request:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def model_dump(self, mode: str = "json") -> dict:
        return {key: str(value) for key, value in self.__dict__.items()}


_REQUEST_NAMES = (
    "GetOrdersRequest", "GetOrderByIdRequest", "LimitOrderRequest", "MarketOrderRequest",
    "ReplaceOrderRequest", "StopLossRequest", "TakeProfitRequest",
)
_OPEN = {"new", "accepted", "held", "partially_filled"}


class FakeBroker:
    """In-memory SPY account. `fill_labels` entries fill at once; `fill_on_cancel` ones fill when cancelled."""

    def __init__(self, *, fill_labels=(), fill_on_cancel=(), flatten_fills=True, fetch_raises_for=None,
                 cancel_fails=False, position=0):
        self.fill_labels, self.fill_on_cancel = tuple(fill_labels), tuple(fill_on_cancel)
        self.flatten_fills, self.fetch_raises_for, self.cancel_fails = flatten_fills, fetch_raises_for, cancel_fails
        self.position = position
        self.orders: list = []
        self.submitted: list[tuple[str, OrderSide, float]] = []

    # -- helpers for the tests
    def add_open_order(self, client_order_id: str):
        return self._new(client_order_id, OrderSide.BUY, 1, "new")

    def sells(self) -> list[str]:
        return [cid for cid, side, _ in self.submitted if side is OrderSide.SELL]

    def open_ids(self) -> list[str]:
        return [o.client_order_id for o in self.orders if o.status.value in _OPEN]

    def _new(self, client_order_id, side, qty, status):
        order = SimpleNamespace(id=uuid.uuid4(), client_order_id=client_order_id, symbol="SPY", side=side,
                                qty=qty, status=SimpleNamespace(value=status), legs=[])
        self.orders.append(order)
        return order

    def _fill(self, order) -> None:
        order.status = SimpleNamespace(value="filled")
        self.position += int(order.qty) if order.side is OrderSide.BUY else -int(order.qty)

    # -- the broker surface the spike uses
    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def get_all_positions(self):
        return [SimpleNamespace(symbol="SPY", qty=str(self.position))] if self.position else []

    def get_orders(self, req):
        return [o for o in self.orders if o.status.value in _OPEN]

    def get_order_by_id(self, order_id, req=None):
        order = next(o for o in self.orders if o.id == order_id)
        if self.fetch_raises_for and self.fetch_raises_for in order.client_order_id:
            raise RuntimeError("connection reset while polling the entry")
        return order

    def submit_order(self, req):
        self.submitted.append((req.client_order_id, req.side, req.qty))
        if req.side is OrderSide.SELL:
            if any(o.side is OrderSide.SELL and o.status.value in _OPEN for o in self.orders):
                raise RuntimeError("insufficient qty available: held by an open order")
            order = self._new(req.client_order_id, req.side, req.qty, "new")
            if self.flatten_fills or "flatten" not in req.client_order_id:
                self._fill(order)
            return order
        order = self._new(req.client_order_id, req.side, req.qty, "new")
        if any(label in req.client_order_id for label in self.fill_labels):
            self._fill(order)
            held = "new"
        else:
            held = "held"
        if getattr(req, "order_class", None) is not None:
            order.legs = [self._new(f"leg-{uuid.uuid4().hex[:6]}", OrderSide.SELL, req.qty, held)]
        return order

    def replace_order_by_id(self, order_id, req):
        return next(o for o in self.orders if o.id == order_id)

    def cancel_order_by_id(self, order_id):
        if self.cancel_fails:
            raise RuntimeError("cancel rejected")
        order = next(o for o in self.orders if o.id == order_id)
        if order.status.value not in _OPEN:
            return
        if any(label in order.client_order_id for label in self.fill_on_cancel):
            self._fill(order)  # the cancel lost the race: the entry filled
            return
        order.status = SimpleNamespace(value="canceled")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Fake alpaca modules, a manual clock, a temp fixture dir and a stubbed quote."""
    package, trading = ModuleType("alpaca"), ModuleType("alpaca.trading")
    enums, requests = ModuleType("alpaca.trading.enums"), ModuleType("alpaca.trading.requests")
    for enum in (OrderSide, TimeInForce, OrderClass, QueryOrderStatus):
        setattr(enums, enum.__name__, enum)
    for name in _REQUEST_NAMES:
        setattr(requests, name, type(name, (_Request,), {}))
    for module_name, module in (("alpaca", package), ("alpaca.trading", trading),
                                ("alpaca.trading.enums", enums), ("alpaca.trading.requests", requests)):
        monkeypatch.setitem(sys.modules, module_name, module)

    now = [1_000.0]
    monkeypatch.setattr(spike, "time", SimpleNamespace(
        time=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)))
    monkeypatch.setattr(spike, "FIXTURE_DIR", tmp_path / "fixtures")
    monkeypatch.setattr(spike, "last_prices", lambda data: (500.0, 500.1))
    monkeypatch.setattr(spike, "load_env_file", lambda path: {})
    monkeypatch.setattr(spike, "CREATED_IDS", set())

    def run(mode: str, broker: FakeBroker, *, confirm: bool = True) -> int:
        monkeypatch.setattr(spike, "build_clients", lambda env: (broker, None))
        flags = ["--i-understand-this-trades-paper"] if confirm else []
        return spike.main([mode, "--env-file", "unused.env", *flags])

    return run


def test_unfilled_bracket_entry_never_produces_a_sell_or_a_short(harness):
    broker = FakeBroker(fill_labels=("fill_oto",))  # the bracket entry never fills

    assert harness("fill", broker) == 0

    # only the OTO-stage sells ever existed: no bracket flatten went out against an unfilled entry
    assert not any("flatten" in cid for cid in broker.sells())
    assert broker.position == 0
    assert broker.open_ids() == []


def test_exception_after_a_filled_entry_flattens_the_reported_position(harness):
    broker = FakeBroker(fill_labels=("fill_oto", "fill_bracket"), fetch_raises_for="fill_bracket")

    with pytest.raises(RuntimeError, match="connection reset"):
        harness("fill", broker)

    assert broker.position == 0  # cancel alone would have left the long open
    assert broker.open_ids() == []
    flattens = [s for s in broker.submitted if "cleanup_flatten" in s[0]]
    assert [(side, qty) for _, side, qty in flattens] == [(OrderSide.SELL, 1.0)]


def test_entry_that_fills_during_the_cancel_is_flattened_by_its_real_quantity(harness):
    broker = FakeBroker(fill_labels=("fill_oto",), fill_on_cancel=("fill_bracket",))

    assert harness("fill", broker) == 0

    assert broker.position == 0
    assert broker.open_ids() == []
    assert [s[2] for s in broker.submitted if "cleanup_flatten" in s[0]] == [1.0]


def test_unsuccessful_flatten_is_reported_not_swallowed(harness, capsys):
    broker = FakeBroker(fill_labels=("fill_oto", "fill_bracket"), flatten_fills=False)

    with pytest.raises(spike.ExposureRemaining, match="SPY position of 1 share"):
        harness("fill", broker)

    assert broker.position == 1
    assert broker.open_ids() == []  # the unfilled flatten is cancelled rather than left to fill later
    assert "EXPOSURE REMAINS" in capsys.readouterr().out


def test_a_refused_fill_run_never_flattens_an_existing_position(harness):
    broker = FakeBroker(position=3)

    with pytest.raises(SystemExit, match="already holds"):
        harness("fill", broker)

    assert broker.submitted == []
    assert broker.position == 3


def test_run_exits_non_zero_while_a_prefixed_order_cannot_be_cancelled(harness, monkeypatch, capsys):
    broker = FakeBroker(cancel_fails=True)
    monkeypatch.setattr(spike, "stage_rest", lambda trading, data, rec: trading.add_open_order("p1spike-stuck"))

    assert harness("rest", broker) == 1

    out = capsys.readouterr().out
    assert "EXPOSURE REMAINS" in out and "p1spike-stuck" in out


def test_clean_exit_in_rest_mode_returns_zero(harness, monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(spike, "stage_rest", lambda trading, data, rec: trading.add_open_order("p1spike-rests"))

    assert harness("rest", broker) == 0
    assert broker.open_ids() == []


# -- review fix: rest mode is a mutating mode ------------------------------------


@pytest.mark.parametrize("mode", ["rest", "fill"])
def test_order_submitting_modes_refuse_to_run_without_the_confirmation_flag(harness, mode):
    broker = FakeBroker()

    with pytest.raises(SystemExit, match="--i-understand-this-trades-paper"):
        harness(mode, broker, confirm=False)

    assert broker.submitted == [] and broker.orders == []


def test_read_only_mode_needs_no_confirmation_and_submits_nothing(harness, monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(spike, "stage_read_only", lambda trading, data, rec: None)  # the fake has no account reads

    assert harness("read-only", broker, confirm=False) == 0
    assert broker.submitted == []


def test_rest_order_that_fills_is_flattened_and_the_run_exits_non_zero(harness, capsys):
    broker = FakeBroker(fill_labels=("oto_day",))  # a "far from the market" entry fills anyway (gap)

    assert harness("rest", broker) == 1

    assert broker.position == 0  # cleanup restored a known-flat account
    assert broker.open_ids() == []
    flattens = [s for s in broker.submitted if "cleanup_flatten" in s[0]]
    assert [(side, qty) for _, side, qty in flattens] == [(OrderSide.SELL, 1.0)]
    out = capsys.readouterr().out
    assert "EXPOSURE REMAINS" in out and "filled unexpectedly" in out


def test_rest_short_sell_that_fills_is_covered_not_left_short(harness, monkeypatch):
    broker = FakeBroker(fill_labels=("cleanup_flatten",))  # the cover order (a BUY) fills

    def short_sell_fills(trading, data, rec):
        spike.require_no_symbol_exposure(trading, rec)  # the same preflight stage_rest runs first
        order = trading._new("p1spike-short_no_position-x", OrderSide.SELL, 1, "new")
        trading._fill(order)  # the broker fills the far-above-market sell: the account is now short one share

    monkeypatch.setattr(spike, "stage_rest", short_sell_fills)

    assert harness("rest", broker) == 1
    assert broker.position == 0  # the short was bought back, not left open
    assert any(side is OrderSide.BUY and "cleanup_flatten" in cid for cid, side, _ in broker.submitted)


@pytest.mark.parametrize("existing", ["position", "open_order"])
def test_rest_mode_refuses_to_start_on_pre_existing_exposure_and_never_touches_it(harness, existing):
    broker = FakeBroker(position=3 if existing == "position" else 0)
    if existing == "open_order":
        broker.add_open_order("someone-elses-order")

    with pytest.raises(SystemExit, match="refusing"):
        harness("rest", broker)

    assert broker.submitted == []
    assert broker.position == (3 if existing == "position" else 0)
    assert broker.open_ids() == (["someone-elses-order"] if existing == "open_order" else [])


def test_a_clean_rest_run_with_no_fill_still_exits_zero(harness):
    broker = FakeBroker()

    assert harness("rest", broker) == 0
    assert broker.position == 0 and broker.open_ids() == []
