"""Real, credentialed Alpaca-paper `BrokerGateway` (docs/milestone-4.md Step 7).

What is genuinely LumiBot here vs. what is not
-----------------------------------------------
This gateway constructs a real `lumibot.brokers.Alpaca` broker instance
(the same class LumiBot's own `Strategy`/`Trader` would use) purely to reuse
LumiBot's own credential wiring and to prove the connection is paper mode
before anything else happens, and it builds a real `lumibot.entities.Asset`
for every order to validate the symbol against LumiBot's own asset model.

LumiBot's `Broker.submit_order`/`get_order`/`get_tracked_positions` API is
designed around a `Strategy` instance tracking its own orders inside a
`Trader` event loop — the exact shape ADR 0001 (Milestone 3) and this
milestone's ADR 0002 deliberately do not adopt (see
"docs/adr/0001-lumibot-paper-runtime.md" Decision 1, reaffirmed by
"docs/adr/0002-isolated-lumibot-runtime.md"). Actual order transmission,
status lookup, cancellation, account, and position reads therefore go
through the same underlying `alpaca-py` `TradingClient` LumiBot's `Alpaca`
broker itself wraps (`broker.api`) — not a second, competing broker
integration. Every credential and endpoint check still runs through the
LumiBot-constructed broker object first.

No credentials are available in this repository's development environment
(confirmed absent from `.env` at implementation time), so this module has
not been exercised against a live paper-broker connection — see
`docs/milestone4-isolated-paper-broker.md` "Known limitations".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib

from .configuration import RuntimeConfiguration
from .errors import ErrorCode, RuntimeOperationError
from .models import (
    AccountSnapshotPayload, FillPayload, OrderIntentPayload, OrderSnapshotPayload, PositionSnapshotPayload,
)
from .normalization import (
    normalize_broker_reportable_status,
    normalize_exact_int,
    normalize_optional_decimal_string,
    normalize_side,
    normalize_time_in_force,
    normalize_timestamp_string,
)

# Alpaca (alpaca-py) raw order statuses -> internal runtime status
# (docs/milestone-4.md Step 8). Fail closed on anything not explicitly
# mapped, mirroring the main repo's runtime/lumibot/event_mapper.py posture.
_ALPACA_STATUS_MAP: dict[str, str] = {
    "new": "ACCEPTED",
    "accepted": "ACCEPTED",
    "pending_new": "SUBMITTED",
    "accepted_for_bidding": "ACCEPTED",
    "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED",
    "done_for_day": "CANCELLED",
    "canceled": "CANCELLED",
    "expired": "EXPIRED",
    "pending_cancel": "CANCEL_REQUESTED",
    "stopped": "ERROR",
    "rejected": "REJECTED",
    "suspended": "ERROR",
    "calculated": "ACCEPTED",
    "replaced": "ACCEPTED",
    "pending_replace": "ACCEPTED",
    "held": "ACCEPTED",
}


def _map_status(raw: str) -> str:
    key = str(raw).strip().lower()
    if key not in _ALPACA_STATUS_MAP:
        raise RuntimeOperationError(
            ErrorCode.UNKNOWN_BROKER_STATUS, f"unrecognized Alpaca order status {raw!r} — fail closed"
        )
    # PR 9: assert the mapped value is inside the shared normalization
    # vocabulary. Without this, adding a row to `_ALPACA_STATUS_MAP` with a
    # status the main process does not know about would only surface much
    # later — as an unreadable persisted row, not as a mapping bug here.
    return normalize_broker_reportable_status(_ALPACA_STATUS_MAP[key], "mapped Alpaca order status")


@dataclass
class LumiBotAlpacaPaperGateway:
    """Implements `broker_gateway.BrokerGateway` against a real, credentialed
    Alpaca paper-trading connection, constructed via LumiBot's `Alpaca`
    broker for credential handling and paper-mode verification."""

    config: RuntimeConfiguration
    broker_provider: str = "alpaca"
    lumibot_version: str | None = field(default=None, init=False)

    _broker: object | None = field(default=None, init=False, repr=False)
    _api: object | None = field(default=None, init=False, repr=False)
    _paper_verified: bool = field(default=False, init=False)
    _init_error: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            import lumibot

            self.lumibot_version = getattr(lumibot, "__version__", "unknown")
        except ImportError:
            self.lumibot_version = None
            self._init_error = "lumibot is not installed in this environment"
            return

        if not self.config.has_credentials:
            self._init_error = "ALPACA_API_KEY / ALPACA_API_SECRET are not both set"
            return
        if not self.config.alpaca_is_paper_flag:
            self._init_error = "ALPACA_IS_PAPER is not exactly 'true' — refusing to assume paper mode"
            return
        if not self.config.paper_endpoint_configured:
            self._init_error = "Alpaca base URL is not the exact paper endpoint"
            return

        try:
            from lumibot.brokers import Alpaca

            broker_config = {
                "API_KEY": self.config.alpaca_api_key,
                "API_SECRET": self.config.alpaca_api_secret,
                "OAUTH_TOKEN": None,
                "PAPER": True,
            }
            self._broker = Alpaca(
                broker_config, connect_stream=False, start_orders_thread=False,
            )
            self._api = self._broker.api
        except Exception:  # broker construction/auth failures must not crash health checks
            self._init_error = "failed to construct Alpaca broker"
            self._broker = None
            self._api = None
            return

        self._paper_verified = self._verify_paper_endpoint()
        if not self._paper_verified:
            self._init_error = "Alpaca TradingClient base URL did not verify as the paper endpoint"

    def _verify_paper_endpoint(self) -> bool:
        try:
            from alpaca.common.enums import BaseURL

            base_url = getattr(self._api, "_base_url", None)
            return base_url == BaseURL.TRADING_PAPER or str(base_url).rstrip("/") == self.config.alpaca_base_url
        except Exception:
            return False

    def is_paper_mode_verified(self) -> bool:
        return self._paper_verified and self._api is not None

    def _require_verified(self) -> None:
        if not self.is_paper_mode_verified():
            raise RuntimeOperationError(
                ErrorCode.NOT_PAPER_MODE,
                self._init_error or "Alpaca paper broker connection is not verified",
            )

    def account_fingerprint(self) -> str:
        self._require_verified()
        try:
            account = self._api.get_account()
            account_id = str(account.id)
        except Exception as exc:
            raise RuntimeOperationError(ErrorCode.BROKER_ERROR, "account verification failed") from exc
        return "acct_" + hashlib.sha256(f"alpaca-paper:{account_id}".encode()).hexdigest()[:32]

    def _validate_asset(self, symbol: str) -> None:
        # Genuine LumiBot entity construction — validates the symbol against
        # LumiBot's own Asset model before anything is sent to Alpaca.
        from lumibot.entities import Asset

        Asset(symbol=symbol, asset_type=Asset.AssetType.STOCK)

    def submit_order(self, intent: OrderIntentPayload) -> OrderSnapshotPayload:
        self._require_verified()
        self._validate_asset(intent.symbol)

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        client_order_id = intent.idempotency_key

        existing = self.get_order(client_order_id)
        if existing is not None:
            return existing  # idempotent replay — never resubmit (docs/milestone-4.md Step 8)

        common_kwargs = dict(
            symbol=intent.symbol, qty=intent.quantity,
            side=OrderSide.BUY if intent.side == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=client_order_id,
        )
        order_request = LimitOrderRequest(limit_price=float(intent.limit_price), **common_kwargs)

        try:
            order = self._api.submit_order(order_data=order_request)
        except Exception as exc:
            # Ambiguous outcome: the broker may or may not have received the
            # order. Never fabricate acknowledgement and never blind-retry —
            # look the order up by client_order_id before deciding anything.
            recovered = self.get_order(client_order_id)
            if recovered is not None:
                return recovered
            raise RuntimeOperationError(
                ErrorCode.SUBMISSION_UNKNOWN,
                "submit_order outcome is unknown after the broker call; "
                "query get_order with the same client_order_id before retrying — do not resubmit",
                retryable=False,
            ) from exc

        return self._order_to_snapshot(order)

    def get_order(self, client_order_id: str) -> OrderSnapshotPayload | None:
        self._require_verified()
        try:
            order = self._api.get_order_by_client_id(client_order_id)
        except Exception as exc:
            if _is_authoritative_not_found(exc):
                return None
            raise RuntimeOperationError(ErrorCode.BROKER_ERROR, "get_order failed") from exc
        return self._order_to_snapshot(order)

    def get_order_by_broker_id(self, broker_order_id: str) -> OrderSnapshotPayload | None:
        self._require_verified()
        try:
            order = self._api.get_order_by_id(broker_order_id)
        except Exception as exc:
            if _is_authoritative_not_found(exc):
                return None
            raise RuntimeOperationError(ErrorCode.BROKER_ERROR, "get_order_by_id failed") from exc
        return self._order_to_snapshot(order)

    def list_order_fills(self, client_order_id: str) -> list[FillPayload]:
        self._require_verified()
        order = self.get_order(client_order_id)
        if order is None:
            raise RuntimeOperationError(ErrorCode.UNKNOWN_ORDER, f"no known order for {client_order_id!r}")
        activities_api = getattr(self._api, "get_account_activities", None)
        if activities_api is None:
            if not order.filled_quantity or not order.average_fill_price or not order.broker_order_id:
                return []
            return [FillPayload(
                fill_id=f"alpaca-cumulative-{order.broker_order_id}-{order.filled_quantity}",
                broker_order_id=order.broker_order_id, client_order_id=client_order_id,
                book_id=order.book_id or "", symbol=order.symbol or "", side=order.side or "",
                quantity=str(order.filled_quantity), price=order.average_fill_price,
                filled_at=order.updated_at, account_fingerprint=self.account_fingerprint(),
            )]
        try:
            activities = activities_api(activity_types=["FILL"])
        except Exception as exc:
            raise RuntimeOperationError(ErrorCode.BROKER_ERROR, "list fills failed") from exc
        fingerprint = self.account_fingerprint()
        result = []
        for activity in activities:
            if str(getattr(activity, "order_id", "")) != str(order.broker_order_id):
                continue
            # PR 9: no `"0"` defaults. A FILL activity missing its quantity
            # or price is a malformed observation, not a zero-price fill —
            # the payload's `__post_init__` rejects it, and the whole read
            # fails closed rather than returning a fabricated fill that
            # `paper_books` would book as free shares.
            result.append(FillPayload(
                fill_id=str(getattr(activity, "id", "")), broker_order_id=str(order.broker_order_id),
                client_order_id=client_order_id, book_id=order.book_id or "",
                symbol=str(getattr(activity, "symbol", None) or order.symbol or ""),
                side=getattr(activity, "side", None) or order.side,
                quantity=getattr(activity, "qty", None), price=getattr(activity, "price", None),
                filled_at=getattr(activity, "transaction_time", None) or order.updated_at,
                account_fingerprint=fingerprint,
            ))
        return result

    def list_open_orders(self) -> list[OrderSnapshotPayload]:
        self._require_verified()
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._api.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return [self._order_to_snapshot(o) for o in orders]

    def list_recent_orders(self, limit: int) -> list[OrderSnapshotPayload]:
        self._require_verified()
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._api.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit, direction="desc")
        )
        return [self._order_to_snapshot(o) for o in orders]

    def get_account(self) -> AccountSnapshotPayload:
        self._require_verified()
        account = self._api.get_account()
        # PR 9: raw values are handed to the payload, whose `__post_init__`
        # is the single normalization boundary. Previously `str(account.cash)`
        # turned a missing value into the literal `"None"`, and a missing
        # `currency` was silently defaulted to `"USD"` rather than rejected
        # — a broker that stops reporting its account currency is a
        # malformed observation, not evidence the account is USD.
        return AccountSnapshotPayload(
            cash=getattr(account, "cash", None), equity=getattr(account, "equity", None),
            buying_power=getattr(account, "buying_power", None),
            currency=getattr(account, "currency", None),
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    def list_positions(self) -> list[PositionSnapshotPayload]:
        self._require_verified()
        positions = self._api.get_all_positions()
        now = datetime.now(timezone.utc).isoformat()
        # PR 9: quantity/cost-basis normalization happens in the payload's
        # `__post_init__`, so an unparseable broker value fails closed here
        # instead of reaching ledger reconciliation as a string.
        return [
            PositionSnapshotPayload(
                symbol=str(getattr(p, "symbol", "")),
                quantity=getattr(p, "qty", None),
                average_entry_price=getattr(p, "avg_entry_price", None),
                market_value=getattr(p, "market_value", None),
                as_of=now,
            )
            for p in positions
        ]

    def cancel_order(self, client_order_id: str) -> OrderSnapshotPayload:
        self._require_verified()
        existing = self.get_order(client_order_id)
        if existing is None:
            raise RuntimeOperationError(ErrorCode.UNKNOWN_ORDER, f"no known order for {client_order_id!r}")
        if existing.broker_order_id:
            try:
                self._api.cancel_order_by_id(existing.broker_order_id)
            except Exception as exc:
                raise RuntimeOperationError(ErrorCode.BROKER_ERROR, "cancel_order failed") from exc
        updated = self.get_order(client_order_id)
        result = updated if updated is not None else existing
        if result.status in ("ACCEPTED", "SUBMITTED"):
            result = replace(
                result, status="CANCEL_REQUESTED",
                raw_broker_status=f"cancel_requested:{result.raw_broker_status or 'unknown'}",
            )
        return result

    def _order_to_snapshot(self, order: object) -> OrderSnapshotPayload:
        """Translate one raw `alpaca-py` order into the normalized contract.

        PR 9: every field goes through `normalization.py` rather than a bare
        `str(...)`. Three concrete defects this closes:

        * `limit_price` used `str(getattr(order, "limit_price", "")) or None`,
          which yields the *string* `"None"` for a market order — truthy, so
          it survived the `or None`, and then crashed the main process's
          `Decimal(...)` parse.
        * `time_in_force` read `getattr(<enum-or-str>, "value", "day")`, so a
          broker reporting a plain string `"gtc"` silently normalized to
          `DAY`, misstating the order's lifetime.
        * nothing asserted the mapped status was in the shared vocabulary.

        Also no longer fabricated: a missing `filled_qty` used to become the
        integer `0` via `order.filled_qty or 0` — indistinguishable from a
        broker genuinely reporting zero shares filled — and a missing
        `submitted_at`/`updated_at` used to become *this process's* current
        clock reading, fabricating a broker timestamp that was never
        observed. Both now fail closed through `normalize_exact_int`/
        `normalize_timestamp_string` instead.
        """
        raw_status = getattr(order.status, "value", order.status)
        status = _map_status(raw_status)
        raw_side = getattr(order, "side", None)
        raw_tif = getattr(order, "time_in_force", None)
        return OrderSnapshotPayload(
            intent_id=str(order.client_order_id), client_order_id=str(order.client_order_id),
            broker_order_id=str(order.id) if getattr(order, "id", None) else None,
            status=status, raw_broker_status=str(raw_status),
            quantity=normalize_exact_int(order.qty, "broker order quantity"),
            filled_quantity=normalize_exact_int(
                getattr(order, "filled_qty", None), "broker order filled_qty"
            ),
            average_fill_price=normalize_optional_decimal_string(
                getattr(order, "filled_avg_price", None), "broker order filled_avg_price"
            ),
            # A naive (tzinfo-less) broker timestamp is treated as UTC by
            # `normalize_timestamp_string` — Alpaca's paper API reports UTC —
            # rather than silently defaulted from this process's own clock.
            # This is a documented contract rule (docs/library-migration/
            # DECISIONS.md D8), not an unstated coercion.
            submitted_at=normalize_timestamp_string(
                getattr(order, "submitted_at", None), "broker order submitted_at"
            ),
            updated_at=normalize_timestamp_string(
                getattr(order, "updated_at", None), "broker order updated_at"
            ),
            book_id=_book_from_client_order_id(str(order.client_order_id)),
            symbol=str(getattr(order, "symbol", "")) or None,
            side=normalize_side(getattr(raw_side, "value", raw_side), "broker order side"),
            limit_price=normalize_optional_decimal_string(
                getattr(order, "limit_price", None), "broker order limit_price"
            ),
            # This is the one deliberate default in this method (re-checked
            # against D8's fail-closed posture, docs/library-migration/
            # DECISIONS.md): this runtime only ever *submits* DAY LIMIT
            # orders (its own capability advertisement restricts it to
            # that), so a broker order with no `time_in_force` attribute at
            # all can only be one this runtime itself created as DAY — it is
            # a documented, tested contract rule, not a repair of an
            # ambiguous broker value. A *present but unrecognized* value
            # still fails closed rather than defaulting.
            time_in_force=normalize_time_in_force(
                getattr(raw_tif, "value", raw_tif) if raw_tif is not None else "DAY",
                "broker order time_in_force",
            ),
            account_fingerprint=self.account_fingerprint(),
        )


def _book_from_client_order_id(client_order_id: str) -> str | None:
    if client_order_id.startswith("epb-baseline-"):
        return "BASELINE"
    if client_order_id.startswith("epb-enhanced-"):
        return "ENHANCED"
    return None


def _is_authoritative_not_found(exc: Exception) -> bool:
    """Only an explicit HTTP 404 is authoritative broker absence."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status == 404
