"""Real LumiBot paper-execution adapter (docs/milestone-3.md Step 5).

LumiBot import boundary: this is the *only* module in this repository that
imports LumiBot's `Order`/`Asset` entities — nothing outside
`runtime/lumibot/` ever sees a LumiBot object (verified by
`tests/unit/test_lumibot_adapter.py::test_no_lumibot_import_outside_runtime_package`).

What is real here vs. what is injected
---------------------------------------
LumiBot's own architecture is a `Strategy`/`Trader` event loop bound to a
`Broker` connected to a real (paper or live) brokerage API with credentials
(Alpaca paper, Tradier, Interactive Brokers, ...). There is no bundled
"simulate a fill with no network/credentials" broker in LumiBot itself — its
backtesting broker replays historical market data from a paid/free data
provider, which is a live-network dependency this milestone's offline test
suite must not have (docs/milestone-3.md Step 10: "Do not fetch current
quotes... Do not depend on network access").

So this adapter is honestly split in two:

* `_translate_intent` builds a **real** `lumibot.entities.order.Order`
  from a `PaperOrderIntent` — genuine LumiBot objects, genuine LumiBot
  enums (`Order.OrderSide`, `Order.OrderType`), exercised in
  `tests/unit/test_lumibot_adapter.py` (guarded by
  `pytest.importorskip("lumibot")`, since LumiBot is an optional `paper`
  extra — see pyproject.toml).
* Actually *submitting* that order to a broker and receiving fill
  callbacks is delegated to an injected `PaperBrokerGateway`. In
  production this wraps a real LumiBot `Trader`/`Strategy` connected to a
  credentialed paper-trading broker; in this repository's test suite and
  CLI (absent real credentials) it is `runtime.deterministic_adapter`'s
  fixture-driven double instead. No code here ever falsely claims a live
  broker round-trip happened when it did not — see
  docs/milestone3-lumibot-paper-integration.md "Known limitations".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, Protocol

from lumibot.entities import Asset
from lumibot.entities.order import Order as LumiBotOrder

from ...execution.adapter_protocol import BrokerExecutionSnapshot
from ...execution.models import (
    EVENT_TYPES,
    RESULT_STATUSES,
    PaperExecutionEvent,
    PaperExecutionResult,
    PaperOrderIntent,
)
from ..normalization import NormalizationError, normalize_exact_int, parse_decimal
from .configuration import LumiBotAdapterConfig
from .errors import LumiBotAdapterError
from .event_mapper import map_order_status


class PaperBrokerGateway(Protocol):
    """The seam between this adapter and an actual connected paper broker.

    A real implementation wraps a LumiBot `Trader`/`Strategy` bound to a
    credentialed paper-trading broker (e.g. Alpaca paper) — out of scope to
    ship here since this environment has neither. Tests and the CLI inject
    a deterministic double.
    """

    def submit_order(
        self, order: LumiBotOrder
    ) -> Iterable[tuple[str, int, float | None, str | None]]:
        """Yield (raw_status, filled_quantity, fill_price, broker_order_id)
        tuples in chronological order for one submitted order."""
        ...

    def snapshot(self, broker_order_id: str) -> tuple[int, float, str]:
        """Return (filled_quantity, notional, raw_status) — the broker's
        current view of one order, for reconciliation."""
        ...


def _translate_intent(intent: PaperOrderIntent, config: LumiBotAdapterConfig) -> LumiBotOrder:
    asset = Asset(symbol=intent.symbol, asset_type=Asset.AssetType.STOCK)
    order_type = (
        LumiBotOrder.OrderType.MARKET if intent.order_type == "MARKET" else LumiBotOrder.OrderType.LIMIT
    )
    kwargs: dict = dict(
        strategy=config.strategy_name,
        asset=asset,
        quantity=intent.quantity,
        side=LumiBotOrder.OrderSide.BUY,  # long-only in this milestone
        order_type=order_type,
        time_in_force=config.time_in_force,
        identifier=intent.intent_id,
    )
    if intent.order_type == "LIMIT":
        # Decimal -> float: LumiBot's Order entity only accepts a plain
        # float for limit_price. This is the one unavoidable float
        # conversion at the LumiBot boundary (docs/milestone-3.md Step 5
        # requires this be explicit and tested, not silent).
        kwargs["limit_price"] = float(intent.limit_price)
    return LumiBotOrder(**kwargs)


def _normalized_fill_price(price: object, intent_id: str) -> Decimal:
    """Convert an injected gateway's fill price to `Decimal`, failing closed.

    The `float -> Decimal` step is unavoidable (`PaperBrokerGateway` yields a
    plain float, matching LumiBot's own entity types); PR 9 makes it a
    checked boundary rather than a bare `Decimal(str(price))`.
    """
    try:
        value = parse_decimal(price, "fill price")
    except NormalizationError as exc:
        raise LumiBotAdapterError(f"broker event for intent {intent_id}: {exc}") from exc
    if value <= 0:
        raise LumiBotAdapterError(
            f"broker event for intent {intent_id} reported a non-positive fill price {price!r}"
        )
    return value


def _normalized_filled_quantity(filled_qty: object, intent_id: str) -> int:
    """Whole shares only — never a truncation of a fractional quantity."""
    try:
        value = normalize_exact_int(filled_qty, "filled quantity")
    except NormalizationError as exc:
        raise LumiBotAdapterError(f"broker event for intent {intent_id}: {exc}") from exc
    if value < 0:
        raise LumiBotAdapterError(
            f"broker event for intent {intent_id} reported a negative filled quantity {filled_qty!r}"
        )
    return value


# Last-event type -> the intent's final `PaperExecutionResult` status.
# `SUBMITTED`/`ACCEPTED` mean the broker event stream ended without a
# resolution, which is an `ERROR` outcome, never an assumed fill.
#
# PR 9: this map is now checked for exhaustiveness against `EVENT_TYPES`
# at import time. It was previously a literal inside `_build_result`, so
# adding an event type would have surfaced as a `KeyError` at fill time, on
# a live order, rather than immediately.
_FINAL_STATUS_BY_EVENT_TYPE: dict[str, str] = {
    "FILLED": "FILLED",
    "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    "CANCELLED": "CANCELLED",
    "REJECTED": "REJECTED",
    "ERROR": "ERROR",
    "SUBMITTED": "ERROR",
    "ACCEPTED": "ERROR",
}

if set(_FINAL_STATUS_BY_EVENT_TYPE) != set(EVENT_TYPES):  # pragma: no cover - import-time guard
    raise LumiBotAdapterError(
        "_FINAL_STATUS_BY_EVENT_TYPE must cover exactly execution.models.EVENT_TYPES; "
        f"missing={set(EVENT_TYPES) - set(_FINAL_STATUS_BY_EVENT_TYPE)}, "
        f"unexpected={set(_FINAL_STATUS_BY_EVENT_TYPE) - set(EVENT_TYPES)}"
    )
if not set(_FINAL_STATUS_BY_EVENT_TYPE.values()) <= set(RESULT_STATUSES):  # pragma: no cover
    raise LumiBotAdapterError("_FINAL_STATUS_BY_EVENT_TYPE maps to a status outside RESULT_STATUSES")


def _build_result(intent: PaperOrderIntent, events: tuple[PaperExecutionEvent, ...]) -> PaperExecutionResult:
    if not events:
        raise LumiBotAdapterError(f"adapter produced no events for intent {intent.intent_id}")
    last = events[-1]
    final_status = _FINAL_STATUS_BY_EVENT_TYPE[last.event_type]
    fills = [e for e in events if e.filled_quantity > 0 and e.fill_price is not None]
    total_filled = sum(e.filled_quantity for e in fills)
    if total_filled > 0:
        notional = sum(e.filled_quantity * e.fill_price for e in fills)
        avg_price = notional / total_filled
    else:
        avg_price = None
    return PaperExecutionResult(
        intent_id=intent.intent_id,
        recommendation_id=intent.recommendation_id,
        final_status=final_status,
        requested_quantity=intent.quantity,
        filled_quantity=total_filled,
        average_fill_price=avg_price,
        fees=Decimal("0"),
        event_ids=tuple(e.event_id for e in events),
        completed_at=last.occurred_at,
    )


@dataclass
class LumiBotPaperExecutionAdapter:
    """Implements `execution.adapter_protocol.PaperExecutionAdapter`."""

    broker_gateway: PaperBrokerGateway
    config: LumiBotAdapterConfig = field(default_factory=LumiBotAdapterConfig)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.config.mode != "paper":
            raise LumiBotAdapterError(
                f"LumiBotPaperExecutionAdapter only operates in paper mode — got {self.config.mode!r}"
            )

    def submit(
        self, intent: PaperOrderIntent
    ) -> tuple[tuple[PaperExecutionEvent, ...], PaperExecutionResult]:
        order = _translate_intent(intent, self.config)
        events: list[PaperExecutionEvent] = []
        for raw_status, filled_qty, price, broker_order_id in self.broker_gateway.submit_order(order):
            event_type = map_order_status(raw_status)  # fail closed on unknown status
            # PR 9: `Decimal(str(price))` alone is not a boundary. It turns a
            # float `nan` into `Decimal('NaN')`, and `Decimal('NaN') <= 0` is
            # `False`, so `PaperExecutionEvent`'s "a positive filled_quantity
            # requires a positive fill_price" guard would have accepted it.
            # The gateway is an injected `Protocol` implementation, so its
            # numbers are untrusted input like any other broker observation.
            fill_price = (
                _normalized_fill_price(price, intent.intent_id) if price is not None else None
            )
            events.append(
                PaperExecutionEvent(
                    event_id=f"{intent.intent_id}-evt-{len(events) + 1}",
                    intent_id=intent.intent_id,
                    recommendation_id=intent.recommendation_id,
                    symbol=intent.symbol,
                    event_type=event_type,
                    broker_order_id=broker_order_id,
                    quantity=intent.quantity,
                    filled_quantity=_normalized_filled_quantity(filled_qty, intent.intent_id),
                    fill_price=fill_price,
                    occurred_at=self.clock(),
                    raw_status=raw_status,
                    source="LUMIBOT_PAPER",
                )
            )
        result = _build_result(intent, tuple(events))
        return tuple(events), result

    def reconcile(self, intent_id: str) -> BrokerExecutionSnapshot:
        filled_qty, notional, raw_status = self.broker_gateway.snapshot(intent_id)
        # PR 9: `BrokerExecutionSnapshot` is a plain dataclass with no
        # validation of its own, and this notional feeds reconciliation
        # comparisons — a non-finite value here would silently mismatch
        # against every ledger quantity rather than fail.
        try:
            broker_notional = parse_decimal(notional, "broker snapshot notional")
        except NormalizationError as exc:
            raise LumiBotAdapterError(f"broker snapshot for intent {intent_id}: {exc}") from exc
        if broker_notional < 0:
            raise LumiBotAdapterError(
                f"broker snapshot for intent {intent_id} reported a negative notional {notional!r}"
            )
        return BrokerExecutionSnapshot(
            intent_id=intent_id,
            broker_quantity=_normalized_filled_quantity(filled_qty, intent_id),
            broker_notional=broker_notional,
            broker_status=raw_status,
            as_of=self.clock(),
        )
