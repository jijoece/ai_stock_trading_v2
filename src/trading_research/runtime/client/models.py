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
    normalize_side,
    normalize_time_in_force,
    normalize_timestamp_string,
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
    book_id: str | None
    symbol: str | None
    side: str | None
    limit_price: Decimal | None
    time_in_force: str
    account_fingerprint: str | None

    @classmethod
    def from_payload(cls, payload: dict) -> "RuntimeOrderSnapshot":
        """PR 9: the main process re-validates the runtime's normalized
        payload rather than trusting it, mirroring the runtime's own posture
        of not trusting the main process's validation (docs/milestone-4.md
        Step 3). The status is checked against the shared vocabulary here,
        at the boundary, instead of failing much later when the value is
        read back out of `paper_broker_submissions`.

        Milestone 11 follow-up: this now matches `OrderSnapshotPayload`'s
        behavior on the runtime side rather than a looser subset of it —
        `quantity` must be positive, `submitted_at`/`updated_at` are
        canonicalized (not just required to be present strings), and
        `book_id`/`symbol`/`side`/`limit_price`/`time_in_force`/
        `account_fingerprint` are validated and preserved instead of being
        silently dropped on the way back out through `to_dict`."""
        try:
            status = normalize_broker_reportable_status(payload.get("status"), "status")
            time_in_force = normalize_time_in_force(payload.get("time_in_force"), "time_in_force")
            submitted_at = normalize_timestamp_string(payload.get("submitted_at"), "submitted_at")
            updated_at = normalize_timestamp_string(payload.get("updated_at"), "updated_at")
            raw_side = payload.get("side")
            side = normalize_side(raw_side, "side") if raw_side is not None else None
        except NormalizationError as exc:
            raise ProtocolViolationError(str(exc)) from exc
        quantity = _exact_int(payload, "quantity")
        if quantity <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive order quantity {quantity}")
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
        limit_price = _decimal(payload, "limit_price", required=False)
        if limit_price is not None and limit_price <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive limit_price {limit_price}")
        book_id = payload.get("book_id")
        if book_id is not None and not isinstance(book_id, str):
            raise ProtocolViolationError("runtime response field 'book_id' must be a string or null")
        symbol = payload.get("symbol")
        if symbol is not None and not isinstance(symbol, str):
            raise ProtocolViolationError("runtime response field 'symbol' must be a string or null")
        account_fingerprint = payload.get("account_fingerprint")
        if account_fingerprint is not None and not isinstance(account_fingerprint, str):
            raise ProtocolViolationError("runtime response field 'account_fingerprint' must be a string or null")
        return cls(
            intent_id=_required_str(payload, "intent_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            broker_order_id=payload.get("broker_order_id"), status=status,
            raw_broker_status=payload.get("raw_broker_status"), quantity=quantity,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            submitted_at=submitted_at,
            updated_at=updated_at,
            book_id=book_id, symbol=symbol, side=side, limit_price=limit_price,
            time_in_force=time_in_force, account_fingerprint=account_fingerprint,
        )

    def to_dict(self) -> dict:
        """The canonical, re-validated wire shape `RuntimeClient` hands its
        callers — same field names as the raw runtime payload, but every
        value has passed `from_payload`'s checks."""
        return {
            "intent_id": self.intent_id, "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id, "status": self.status,
            "raw_broker_status": self.raw_broker_status, "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "average_fill_price": format(self.average_fill_price, "f") if self.average_fill_price is not None else None,
            "submitted_at": self.submitted_at, "updated_at": self.updated_at,
            "book_id": self.book_id, "symbol": self.symbol, "side": self.side,
            "limit_price": format(self.limit_price, "f") if self.limit_price is not None else None,
            "time_in_force": self.time_in_force, "account_fingerprint": self.account_fingerprint,
        }


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

    def to_dict(self) -> dict:
        return {
            "cash": format(self.cash, "f"), "equity": format(self.equity, "f"),
            "buying_power": format(self.buying_power, "f") if self.buying_power is not None else None,
            "currency": self.currency, "as_of": self.as_of,
        }


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

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "quantity": format(self.quantity, "f"),
            "average_entry_price": format(self.average_entry_price, "f"),
            "market_value": format(self.market_value, "f") if self.market_value is not None else None,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class ExternalOrderSnapshot:
    """Milestone 11: the enriched broker-order shape the external-paper wire
    ops (`GET_ORDER_BY_CLIENT_ID`, `GET_ORDER`, `CANCEL_ORDER`,
    `LIST_RECENT_ORDERS`) return (`dispatcher._external_order_dict` on the
    runtime side) — a superset of `RuntimeOrderSnapshot`'s fields carrying
    provider/environment/account scoping. `external_broker.py`'s
    `_validate_order_response` still checks these values against the
    approved intent; this parser only guarantees they are well-typed before
    that comparison runs, so a malformed nested field never reaches
    `paper_books`."""

    provider: str
    environment: str
    account_fingerprint: str
    book_id: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Decimal
    time_in_force: str
    status: str
    submitted_at: str
    updated_at: str
    filled_quantity: int
    average_fill_price: Decimal | None
    rejection_code: str | None

    @classmethod
    def from_payload(cls, payload: dict) -> "ExternalOrderSnapshot":
        if not isinstance(payload, dict):
            raise ProtocolViolationError("runtime external order response must be a mapping")
        try:
            status = normalize_broker_reportable_status(payload.get("status"), "status")
            side = normalize_side(payload.get("side"), "side")
            time_in_force = normalize_time_in_force(payload.get("time_in_force"), "time_in_force")
            submitted_at = normalize_timestamp_string(payload.get("submitted_at"), "submitted_at")
            updated_at = normalize_timestamp_string(payload.get("updated_at"), "updated_at")
        except NormalizationError as exc:
            raise ProtocolViolationError(str(exc)) from exc
        quantity = _exact_int(payload, "quantity")
        if quantity <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive order quantity {quantity}")
        filled_quantity = _exact_int(payload, "filled_quantity")
        if not 0 <= filled_quantity <= quantity:
            raise ProtocolViolationError(
                f"runtime reported filled_quantity {filled_quantity} out of range for quantity {quantity}"
            )
        limit_price = _decimal(payload, "limit_price", required=True)
        assert limit_price is not None
        if limit_price <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive limit_price {limit_price}")
        average_fill_price = _decimal(payload, "average_fill_price", required=False)
        if filled_quantity > 0 and (average_fill_price is None or average_fill_price <= 0):
            raise ProtocolViolationError(
                "runtime reported a positive filled_quantity with no positive average_fill_price"
            )
        rejection_code = payload.get("rejection_code")
        if rejection_code is not None and not isinstance(rejection_code, str):
            raise ProtocolViolationError("runtime response field 'rejection_code' must be a string or null")
        return cls(
            provider=_required_str(payload, "provider"), environment=_required_str(payload, "environment"),
            account_fingerprint=_required_str(payload, "account_fingerprint"),
            book_id=_required_str(payload, "book_id"), client_order_id=_required_str(payload, "client_order_id"),
            broker_order_id=_required_str(payload, "broker_order_id"), symbol=_required_str(payload, "symbol"),
            side=side, quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            status=status, submitted_at=submitted_at, updated_at=updated_at,
            filled_quantity=filled_quantity, average_fill_price=average_fill_price,
            rejection_code=rejection_code,
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "environment": self.environment,
            "account_fingerprint": self.account_fingerprint, "book_id": self.book_id,
            "client_order_id": self.client_order_id, "broker_order_id": self.broker_order_id,
            "symbol": self.symbol, "side": self.side, "quantity": self.quantity,
            "limit_price": format(self.limit_price, "f"), "time_in_force": self.time_in_force,
            "status": self.status, "submitted_at": self.submitted_at, "updated_at": self.updated_at,
            "filled_quantity": self.filled_quantity,
            "average_fill_price": format(self.average_fill_price, "f") if self.average_fill_price is not None else None,
            "rejection_code": self.rejection_code,
        }


@dataclass(frozen=True)
class ExternalFillSnapshot:
    """Wire shape of one entry in `LIST_ORDER_FILLS`'s `fills` list
    (`FillPayload.to_dict()` on the runtime side)."""

    fill_id: str
    broker_order_id: str
    client_order_id: str
    book_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    filled_at: str
    account_fingerprint: str

    @classmethod
    def from_payload(cls, payload: dict) -> "ExternalFillSnapshot":
        if not isinstance(payload, dict):
            raise ProtocolViolationError("runtime fill response must be a mapping")
        try:
            side = normalize_side(payload.get("side"), "side")
            filled_at = normalize_timestamp_string(payload.get("filled_at"), "filled_at")
        except NormalizationError as exc:
            raise ProtocolViolationError(str(exc)) from exc
        quantity = _decimal(payload, "quantity", required=True)
        price = _decimal(payload, "price", required=True)
        assert quantity is not None and price is not None
        if quantity <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive fill quantity {quantity}")
        if price <= 0:
            raise ProtocolViolationError(f"runtime reported a non-positive fill price {price}")
        return cls(
            fill_id=_required_str(payload, "fill_id"), broker_order_id=_required_str(payload, "broker_order_id"),
            client_order_id=_required_str(payload, "client_order_id"), book_id=_required_str(payload, "book_id"),
            symbol=_required_str(payload, "symbol"), side=side, quantity=quantity, price=price,
            filled_at=filled_at, account_fingerprint=_required_str(payload, "account_fingerprint"),
        )

    def to_dict(self) -> dict:
        return {
            "fill_id": self.fill_id, "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id, "book_id": self.book_id,
            "symbol": self.symbol, "side": self.side, "quantity": format(self.quantity, "f"),
            "price": format(self.price, "f"), "filled_at": self.filled_at,
            "account_fingerprint": self.account_fingerprint,
        }


@dataclass(frozen=True)
class ExternalPositionsSnapshot:
    """Wire shape of `GET_POSITIONS`'s response: a book/account-scoped
    envelope around a list of `RuntimePositionSnapshot`-shaped positions."""

    book_id: str
    account_fingerprint: str
    positions: tuple[RuntimePositionSnapshot, ...]

    @classmethod
    def from_payload(cls, payload: dict) -> "ExternalPositionsSnapshot":
        if not isinstance(payload, dict):
            raise ProtocolViolationError("runtime positions response must be a mapping")
        raw_positions = payload.get("positions")
        if not isinstance(raw_positions, list):
            raise ProtocolViolationError("runtime positions response field 'positions' must be a list")
        positions = []
        for entry in raw_positions:
            if not isinstance(entry, dict):
                raise ProtocolViolationError("runtime positions response entry must be a mapping")
            positions.append(RuntimePositionSnapshot.from_payload(entry))
        return cls(
            book_id=_required_str(payload, "book_id"),
            account_fingerprint=_required_str(payload, "account_fingerprint"),
            positions=tuple(positions),
        )

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id, "account_fingerprint": self.account_fingerprint,
            "positions": [position.to_dict() for position in self.positions],
        }


@dataclass(frozen=True)
class ExternalAccountSnapshot:
    """Wire shape of `GET_ACCOUNT_SNAPSHOT`'s response: `RuntimeAccountSnapshot`
    plus provider/environment/book_id/account_fingerprint scoping."""

    provider: str
    environment: str
    book_id: str
    account_fingerprint: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal | None
    currency: str
    as_of: str

    @classmethod
    def from_payload(cls, payload: dict) -> "ExternalAccountSnapshot":
        if not isinstance(payload, dict):
            raise ProtocolViolationError("runtime account response must be a mapping")
        base = RuntimeAccountSnapshot.from_payload(payload)
        return cls(
            provider=_required_str(payload, "provider"), environment=_required_str(payload, "environment"),
            book_id=_required_str(payload, "book_id"),
            account_fingerprint=_required_str(payload, "account_fingerprint"),
            cash=base.cash, equity=base.equity, buying_power=base.buying_power,
            currency=base.currency, as_of=base.as_of,
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "environment": self.environment, "book_id": self.book_id,
            "account_fingerprint": self.account_fingerprint, "cash": format(self.cash, "f"),
            "equity": format(self.equity, "f"),
            "buying_power": format(self.buying_power, "f") if self.buying_power is not None else None,
            "currency": self.currency, "as_of": self.as_of,
        }
