"""Main-process-side value objects for the runtime client
(docs/milestone-4.md Step 6).

`intent_to_submit_payload` is the only place a `PaperOrderIntent`
(execution/models.py) gets serialized to the wire — everything downstream
of the client only ever sees plain dicts / these small parsed dataclasses,
never a LumiBot object (there are none on this side of the boundary).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ...execution.models import PaperOrderIntent
from ..normalization import (
    NormalizationError,
    normalize_broker_reportable_status,
    normalize_exact_int,
    parse_decimal,
)
from .errors import ProtocolViolationError


def _decimal(payload: dict, key: str, *, required: bool) -> Decimal | None:
    """Parse one numeric field of a runtime response, failing closed.

    PR 9: this used to be a bare `Decimal(payload[key])`, which raised an
    untyped `decimal.InvalidOperation` on a malformed value and accepted
    `"NaN"`/`"Infinity"` outright — `Decimal("NaN") <= 0` is `False`, so a
    non-finite price would have passed every downstream positivity guard.
    Every failure is now a `ProtocolViolationError`, the client's existing
    "the runtime broke the contract" signal.
    """
    value = payload.get(key)
    if value is None:
        if required:
            raise ProtocolViolationError(f"runtime response is missing required numeric field {key!r}")
        return None
    try:
        return parse_decimal(value, key)
    except NormalizationError as exc:
        raise ProtocolViolationError(str(exc)) from exc


def _exact_int(payload: dict, key: str) -> int:
    try:
        return normalize_exact_int(payload[key], key)
    except KeyError as exc:
        raise ProtocolViolationError(f"runtime response is missing required field {key!r}") from exc
    except NormalizationError as exc:
        raise ProtocolViolationError(str(exc)) from exc


def _required_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolViolationError(f"runtime response field {key!r} must be a non-empty string")
    return value


def derive_client_order_id(intent: PaperOrderIntent) -> str:
    """Stable, broker-safe client order id derived from `intent_id`
    (docs/milestone-4.md Step 8). `intent_id` is already
    `"intent-" + 32 hex chars` (see execution/models.py::derive_intent_id) —
    well under Alpaca's 128-character client_order_id limit and composed
    only of `[a-z0-9-]`, which every broker's client-order-id charset
    accepts."""
    return intent.intent_id


def intent_to_submit_payload(intent: PaperOrderIntent) -> dict:
    return {
        "intent_id": intent.intent_id,
        "recommendation_id": intent.recommendation_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        "reference_price": str(intent.reference_price),
        "expires_at": intent.expires_at.isoformat(),
        "idempotency_key": derive_client_order_id(intent),
    }


@dataclass(frozen=True)
class RuntimeOrderSnapshot:
    intent_id: str
    client_order_id: str
    broker_order_id: str | None
    status: str
    raw_broker_status: str | None
    quantity: int
    filled_quantity: int
    average_fill_price: Decimal | None
    submitted_at: str
    updated_at: str

    @classmethod
    def from_payload(cls, payload: dict) -> "RuntimeOrderSnapshot":
        """PR 9: the main process re-validates the runtime's normalized
        payload rather than trusting it, mirroring the runtime's own posture
        of not trusting the main process's validation (docs/milestone-4.md
        Step 3). The status is checked against the shared vocabulary here,
        at the boundary, instead of failing much later when the value is
        read back out of `paper_broker_submissions`."""
        try:
            status = normalize_broker_reportable_status(payload.get("status"), "status")
        except NormalizationError as exc:
            raise ProtocolViolationError(str(exc)) from exc
        quantity = _exact_int(payload, "quantity")
        filled_quantity = _exact_int(payload, "filled_quantity")
        if not 0 <= filled_quantity <= quantity:
            raise ProtocolViolationError(
                f"runtime reported filled_quantity {filled_quantity} out of range for quantity {quantity}"
            )
        average_fill_price = _decimal(payload, "average_fill_price", required=False)
        if filled_quantity > 0 and (average_fill_price is None or average_fill_price <= 0):
            raise ProtocolViolationError(
                "runtime reported a positive filled_quantity with no positive average_fill_price"
            )
        return cls(
            intent_id=_required_str(payload, "intent_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            broker_order_id=payload.get("broker_order_id"), status=status,
            raw_broker_status=payload.get("raw_broker_status"), quantity=quantity,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            submitted_at=_required_str(payload, "submitted_at"),
            updated_at=_required_str(payload, "updated_at"),
        )


@dataclass(frozen=True)
class RuntimeAccountSnapshot:
    cash: Decimal
    equity: Decimal
    buying_power: Decimal | None
    currency: str
    as_of: str

    @classmethod
    def from_payload(cls, payload: dict) -> "RuntimeAccountSnapshot":
        cash = _decimal(payload, "cash", required=True)
        equity = _decimal(payload, "equity", required=True)
        assert cash is not None and equity is not None  # `required=True` guarantees this
        return cls(
            cash=cash, equity=equity,
            buying_power=_decimal(payload, "buying_power", required=False),
            currency=_required_str(payload, "currency"), as_of=_required_str(payload, "as_of"),
        )


@dataclass(frozen=True)
class RuntimePositionSnapshot:
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    market_value: Decimal | None
    as_of: str

    @classmethod
    def from_payload(cls, payload: dict) -> "RuntimePositionSnapshot":
        quantity = _decimal(payload, "quantity", required=True)
        average_entry_price = _decimal(payload, "average_entry_price", required=True)
        assert quantity is not None and average_entry_price is not None
        if average_entry_price <= 0:
            raise ProtocolViolationError(
                f"runtime reported a non-positive average_entry_price {average_entry_price}"
            )
        return cls(
            symbol=_required_str(payload, "symbol"), quantity=quantity,
            average_entry_price=average_entry_price,
            market_value=_decimal(payload, "market_value", required=False),
            as_of=_required_str(payload, "as_of"),
        )
