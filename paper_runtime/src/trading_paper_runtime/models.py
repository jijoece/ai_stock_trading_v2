"""Framework-neutral payload models for the isolated runtime side of the
paper-runtime.v2 protocol (docs/milestone-11-alpaca-paper-boundary.md).

These are plain dataclasses over JSON-safe primitives (str/int/bool/None) —
Decimals and datetimes cross the wire as strings, never as Python objects.
Nothing here imports LumiBot; `lumibot_gateway.py` is the only module in
this package that translates to/from real LumiBot/Alpaca types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .errors import ErrorCode, RuntimeOperationError
from .normalization import (
    NORMALIZED_ORDER_STATUSES,
    normalize_broker_reportable_status,
    normalize_decimal_string,
    normalize_exact_int,
    normalize_optional_decimal_string,
    normalize_positive_decimal_string,
    normalize_side,
    normalize_time_in_force,
    normalize_timestamp_string,
)

ORDER_TYPES = ("LIMIT",)
SIDES = ("BUY", "SELL")
ASSET_TYPES = ("equity",)

# Runtime-side submission/order states (docs/milestone-4.md Step 8).
# PR 9: this is no longer an independent literal — it is the normalization
# contract's closed vocabulary (`normalization.py`), which the main side
# declares identically. The pre-PR-9 literal omitted `EXPIRED` even though
# `lumibot_gateway._ALPACA_STATUS_MAP` could emit it.
SUBMISSION_STATES = NORMALIZED_ORDER_STATUSES


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeOperationError(ErrorCode.VALIDATION_FAILED, message)


def _parse_decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} is not a valid decimal string: {value!r}") from exc
    if not parsed.is_finite():
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} must be a finite decimal value, got {value!r}")
    return parsed


def _parse_exact_int(value: object, name: str) -> int:
    """Part 13: never truncate through `int(float(...))` or `int(Decimal(...))`
    — a broker-reported quantity must be an exact whole number or this fails
    closed, rather than silently rounding a fractional or non-finite value."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} is not a valid number: {value!r}") from exc
    if not parsed.is_finite():
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} must be finite, got {value!r}")
    if parsed != parsed.to_integral_value():
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} must be a whole number, got {value!r}")
    return int(parsed)


def _parse_dt(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"{name} is not a valid ISO8601 timestamp: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class OrderIntentPayload:
    intent_id: str
    recommendation_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: str | None
    reference_price: str
    expires_at: str
    idempotency_key: str
    asset_type: str = "equity"
    # Milestone 8 (docs/milestone-8.md Step 15): additive, optional book
    # identity. `None` preserves every pre-Milestone-8 caller's exact
    # behavior. A book-aware caller (`paper_books/execution.py`) always
    # supplies this; it is never required for the legacy Milestone 3/4
    # single-book submission path.
    book_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "OrderIntentPayload":
        required = {
            "intent_id", "recommendation_id", "symbol", "side", "quantity", "order_type",
            "limit_price", "reference_price", "expires_at", "idempotency_key",
        }
        missing = required - set(data.keys())
        if missing:
            raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"submit_order payload missing: {sorted(missing)}")
        allowed = required | {"asset_type", "book_id"}
        extra = set(data.keys()) - allowed
        if extra:
            raise RuntimeOperationError(ErrorCode.MALFORMED_PAYLOAD, f"submit_order payload has unexpected fields: {sorted(extra)}")
        return cls(
            intent_id=data["intent_id"], recommendation_id=data["recommendation_id"], symbol=data["symbol"],
            side=data["side"], quantity=data["quantity"], order_type=data["order_type"],
            limit_price=data.get("limit_price"), reference_price=data["reference_price"],
            expires_at=data["expires_at"], idempotency_key=data["idempotency_key"],
            asset_type=data.get("asset_type", "equity"), book_id=data.get("book_id"),
        )

    def validate(self, *, now: datetime) -> None:
        """Re-validate independently of whatever the main process already
        checked (docs/milestone-4.md Step 3: "The runtime must not trust
        validation from the main process alone")."""
        _require(bool(self.intent_id), "intent_id is required")
        _require(bool(self.recommendation_id), "recommendation_id is required")
        _require(bool(self.idempotency_key), "idempotency_key is required")
        _require(self.asset_type in ASSET_TYPES, f"asset_type must be one of {ASSET_TYPES} — got {self.asset_type!r}")
        _require(self.side in SIDES, f"side must be one of {SIDES} (long-only) — got {self.side!r}")
        _require(isinstance(self.quantity, int) and not isinstance(self.quantity, bool), "quantity must be an int")
        _require(self.quantity > 0, "quantity must be a positive whole number (no fractional shares)")
        _require(self.order_type in ORDER_TYPES, f"order_type must be one of {ORDER_TYPES} — got {self.order_type!r}")
        reference_price = _parse_decimal(self.reference_price, "reference_price")
        _require(reference_price > 0, "reference_price must be positive")
        _require(self.limit_price is not None, "LIMIT orders require limit_price")
        limit_price = _parse_decimal(self.limit_price, "limit_price")
        _require(limit_price > 0, "limit_price must be positive")
        expires_at = _parse_dt(self.expires_at, "expires_at")
        _require(now < expires_at, f"intent expired at {expires_at.isoformat()} (now={now.isoformat()})")


@dataclass(frozen=True)
class OrderSnapshotPayload:
    intent_id: str
    client_order_id: str
    broker_order_id: str | None
    status: str
    raw_broker_status: str | None
    quantity: int
    filled_quantity: int
    average_fill_price: str | None
    submitted_at: str
    updated_at: str
    book_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    limit_price: str | None = None
    time_in_force: str = "DAY"
    account_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """Normalize *and* validate every field at construction (PR 9).

        Doing this here rather than at each call site means both gateways —
        the credentialed `LumiBotAlpacaPaperGateway` and the deterministic
        double — are held to the same contract, and `dataclasses.replace`
        re-checks it. Fields are canonicalized in place (frozen dataclass, so
        via `object.__setattr__`); anything that cannot be canonicalized
        fails closed rather than being stringified or defaulted.
        """
        set_ = object.__setattr__
        set_(self, "status", normalize_broker_reportable_status(self.status, "order status"))
        set_(self, "quantity", normalize_exact_int(self.quantity, "order quantity"))
        set_(self, "filled_quantity", normalize_exact_int(self.filled_quantity, "order filled_quantity"))
        set_(
            self,
            "average_fill_price",
            normalize_optional_decimal_string(self.average_fill_price, "order average_fill_price"),
        )
        set_(self, "limit_price", normalize_optional_decimal_string(self.limit_price, "order limit_price"))
        set_(self, "submitted_at", normalize_timestamp_string(self.submitted_at, "order submitted_at"))
        set_(self, "updated_at", normalize_timestamp_string(self.updated_at, "order updated_at"))
        set_(self, "time_in_force", normalize_time_in_force(self.time_in_force, "order time_in_force"))
        if self.side is not None:
            set_(self, "side", normalize_side(self.side, "order side"))

        _require(bool(self.intent_id), "order intent_id is required")
        _require(bool(self.client_order_id), "order client_order_id is required")
        _require(self.quantity > 0, f"order quantity must be positive, got {self.quantity}")
        _require(
            0 <= self.filled_quantity <= self.quantity,
            f"order filled_quantity {self.filled_quantity} is out of range for quantity {self.quantity}",
        )
        # A reported fill always carries a price. Without this, a broker
        # response missing `filled_avg_price` would reconcile as shares
        # acquired at an unknown cost.
        if self.filled_quantity > 0:
            _require(
                self.average_fill_price is not None,
                "an order with filled_quantity > 0 requires an average_fill_price",
            )
            normalize_positive_decimal_string(self.average_fill_price, "order average_fill_price")

    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id, "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id, "status": self.status,
            "raw_broker_status": self.raw_broker_status, "quantity": self.quantity,
            "filled_quantity": self.filled_quantity, "average_fill_price": self.average_fill_price,
            "submitted_at": self.submitted_at, "updated_at": self.updated_at,
            "book_id": self.book_id, "symbol": self.symbol, "side": self.side,
            "limit_price": self.limit_price, "time_in_force": self.time_in_force,
            "account_fingerprint": self.account_fingerprint,
        }


@dataclass(frozen=True)
class FillPayload:
    fill_id: str
    broker_order_id: str
    client_order_id: str
    book_id: str
    symbol: str
    side: str
    quantity: str
    price: str
    filled_at: str
    account_fingerprint: str

    def __post_init__(self) -> None:
        """PR 9: a fill is the single most consequential observation this
        boundary carries — it is what `paper_books` turns into shares and
        cash. Before PR 9 a broker activity missing `qty`/`price` was
        stringified to `"0"`, fabricating a zero-price fill. Nothing is
        defaulted here."""
        set_ = object.__setattr__
        set_(self, "side", normalize_side(self.side, "fill side"))
        set_(self, "quantity", normalize_positive_decimal_string(self.quantity, "fill quantity"))
        set_(self, "price", normalize_positive_decimal_string(self.price, "fill price"))
        set_(self, "filled_at", normalize_timestamp_string(self.filled_at, "fill filled_at"))

        _require(bool(self.fill_id), "fill fill_id is required")
        _require(bool(self.broker_order_id), "fill broker_order_id is required")
        _require(bool(self.client_order_id), "fill client_order_id is required")
        _require(bool(self.symbol), "fill symbol is required")
        _require(bool(self.account_fingerprint), "fill account_fingerprint is required")

    def to_dict(self) -> dict:
        return {
            "fill_id": self.fill_id, "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id, "book_id": self.book_id,
            "symbol": self.symbol, "side": self.side, "quantity": self.quantity,
            "price": self.price, "filled_at": self.filled_at,
            "account_fingerprint": self.account_fingerprint,
        }


@dataclass(frozen=True)
class AccountSnapshotPayload:
    cash: str
    equity: str
    buying_power: str | None
    currency: str
    as_of: str

    def __post_init__(self) -> None:
        """PR 9: `cash`/`equity` were previously raw `str(...)` of whatever
        the broker returned, so a `None` became the literal `"None"` and
        crashed the main process's `Decimal(...)` parse with an untyped
        `InvalidOperation` instead of a structured protocol error."""
        set_ = object.__setattr__
        set_(self, "cash", normalize_decimal_string(self.cash, "account cash"))
        set_(self, "equity", normalize_decimal_string(self.equity, "account equity"))
        set_(
            self,
            "buying_power",
            normalize_optional_decimal_string(self.buying_power, "account buying_power"),
        )
        set_(self, "as_of", normalize_timestamp_string(self.as_of, "account as_of"))
        _require(bool(self.currency), "account currency is required")

    def to_dict(self) -> dict:
        return {
            "cash": self.cash, "equity": self.equity, "buying_power": self.buying_power,
            "currency": self.currency, "as_of": self.as_of,
        }


@dataclass(frozen=True)
class PositionSnapshotPayload:
    symbol: str
    quantity: str
    average_entry_price: str
    market_value: str | None
    as_of: str

    def __post_init__(self) -> None:
        """PR 9: position quantity and cost basis are reconciled against the
        `paper_books` ledger, so an unparseable or non-finite broker value
        must fail closed here rather than reach the comparison."""
        set_ = object.__setattr__
        set_(self, "quantity", normalize_decimal_string(self.quantity, "position quantity"))
        set_(
            self,
            "average_entry_price",
            normalize_positive_decimal_string(self.average_entry_price, "position average_entry_price"),
        )
        set_(
            self,
            "market_value",
            normalize_optional_decimal_string(self.market_value, "position market_value"),
        )
        set_(self, "as_of", normalize_timestamp_string(self.as_of, "position as_of"))
        _require(bool(self.symbol), "position symbol is required")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "quantity": self.quantity,
            "average_entry_price": self.average_entry_price, "market_value": self.market_value,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class HealthPayload:
    available: bool
    protocol_version: str
    runtime_version: str
    lumibot_version: str | None
    broker_provider: str
    broker_mode: str
    has_api_key: bool
    has_api_secret: bool
    paper_endpoint_verified: bool
    network_submission_enabled: bool
    real_money_disabled: bool = True

    def to_dict(self) -> dict:
        return {
            "available": self.available, "protocol_version": self.protocol_version,
            "runtime_version": self.runtime_version, "lumibot_version": self.lumibot_version,
            "broker_provider": self.broker_provider, "broker_mode": self.broker_mode,
            "has_api_key": self.has_api_key, "has_api_secret": self.has_api_secret,
            "paper_endpoint_verified": self.paper_endpoint_verified,
            "network_submission_enabled": self.network_submission_enabled,
            "real_money_disabled": self.real_money_disabled,
        }


@dataclass(frozen=True)
class CapabilitiesPayload:
    supported_operations: tuple = ()
    supported_asset_types: tuple = ASSET_TYPES
    supported_sides: tuple = SIDES
    supported_order_types: tuple = ORDER_TYPES
    fractional_shares: bool = False
    short_selling: bool = False
    options: bool = False
    margin: bool = False
    real_money: bool = False

    def to_dict(self) -> dict:
        return {
            "supported_operations": list(self.supported_operations),
            "supported_asset_types": list(self.supported_asset_types),
            "supported_sides": list(self.supported_sides),
            "supported_order_types": list(self.supported_order_types),
            "fractional_shares": self.fractional_shares,
            "short_selling": self.short_selling,
            "options": self.options,
            "margin": self.margin,
            "real_money": self.real_money,
        }
