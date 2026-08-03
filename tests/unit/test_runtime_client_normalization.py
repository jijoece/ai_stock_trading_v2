"""PR 9 — the main process re-validates the runtime's normalized payloads.

`runtime/client/models.py` is the point where a JSON dict from the isolated
runtime process becomes typed main-process state. Before PR 9 it trusted the
runtime completely: `Decimal(payload["cash"])` raised an untyped
`decimal.InvalidOperation` on a malformed value, and accepted `"NaN"` /
`"Infinity"` outright — and `Decimal("NaN") <= 0` is `False`, so a non-finite
price passed every downstream positivity guard rather than tripping one.

The runtime does not trust the main process's validation
(docs/milestone-4.md Step 3); this is the same posture in the other
direction.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_research.runtime.client.errors import ProtocolViolationError
from trading_research.runtime.client.models import (
    RuntimeAccountSnapshot,
    RuntimeOrderSnapshot,
    RuntimePositionSnapshot,
)

_NOW = "2026-08-02T15:00:00+00:00"


def _order_payload(**overrides) -> dict:
    payload = {
        "intent_id": "intent-1", "client_order_id": "intent-1", "broker_order_id": "b-1",
        "status": "ACCEPTED", "raw_broker_status": "new", "quantity": 10, "filled_quantity": 0,
        "average_fill_price": None, "submitted_at": _NOW, "updated_at": _NOW,
    }
    payload.update(overrides)
    return payload


def _account_payload(**overrides) -> dict:
    payload = {
        "cash": "1000.00", "equity": "1000.00", "buying_power": "2000.00",
        "currency": "USD", "as_of": _NOW,
    }
    payload.update(overrides)
    return payload


def _position_payload(**overrides) -> dict:
    payload = {
        "symbol": "AAPL", "quantity": "10", "average_entry_price": "101.5",
        "market_value": "1015", "as_of": _NOW,
    }
    payload.update(overrides)
    return payload


# --- happy paths still work ------------------------------------------------


def test_well_formed_payloads_parse_unchanged():
    order = RuntimeOrderSnapshot.from_payload(
        _order_payload(status="FILLED", filled_quantity=10, average_fill_price="101.50")
    )
    assert order.filled_quantity == 10 and order.average_fill_price == Decimal("101.50")

    account = RuntimeAccountSnapshot.from_payload(_account_payload())
    assert account.cash == Decimal("1000.00") and account.buying_power == Decimal("2000.00")
    assert RuntimeAccountSnapshot.from_payload(_account_payload(buying_power=None)).buying_power is None

    position = RuntimePositionSnapshot.from_payload(_position_payload())
    assert position.quantity == Decimal("10") and position.market_value == Decimal("1015")
    assert RuntimePositionSnapshot.from_payload(_position_payload(market_value=None)).market_value is None


# --- order snapshots -------------------------------------------------------


@pytest.mark.parametrize("status", ["PENDING_SUBMISSION", "SUBMISSION_UNKNOWN", "probably-fine", None, ""])
def test_order_rejects_a_status_no_broker_can_report(status):
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(_order_payload(status=status))


def test_order_accepts_every_status_the_runtime_gateway_can_emit():
    from trading_research.runtime.normalization import BROKER_REPORTABLE_STATUSES

    for status in BROKER_REPORTABLE_STATUSES:
        payload = _order_payload(status=status)
        if status in ("FILLED", "PARTIALLY_FILLED"):
            payload.update(filled_quantity=10 if status == "FILLED" else 4, average_fill_price="101.5")
        assert RuntimeOrderSnapshot.from_payload(payload).status == status


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "None", "abc"])
def test_order_rejects_a_non_finite_or_unparseable_fill_price(bad):
    """`Decimal("NaN") <= 0` is False — the pre-PR-9 parse let it through."""
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(
            _order_payload(status="FILLED", filled_quantity=10, average_fill_price=bad)
        )


def test_order_rejects_a_fill_with_no_price():
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(
            _order_payload(status="FILLED", filled_quantity=10, average_fill_price=None)
        )


def test_order_rejects_out_of_range_or_fractional_quantities():
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(_order_payload(quantity=10, filled_quantity=11))
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(_order_payload(quantity=10, filled_quantity=-1))
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(_order_payload(quantity="10.5"))


@pytest.mark.parametrize("key", ["intent_id", "client_order_id", "submitted_at", "updated_at"])
def test_order_rejects_missing_identity_fields(key):
    with pytest.raises(ProtocolViolationError):
        RuntimeOrderSnapshot.from_payload(_order_payload(**{key: None}))


# --- account and position snapshots ----------------------------------------


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "None", "", None, "abc"])
def test_account_rejects_a_malformed_cash_value(bad):
    with pytest.raises(ProtocolViolationError):
        RuntimeAccountSnapshot.from_payload(_account_payload(cash=bad))


def test_account_rejects_a_malformed_optional_buying_power():
    with pytest.raises(ProtocolViolationError):
        RuntimeAccountSnapshot.from_payload(_account_payload(buying_power="NaN"))


def test_account_requires_a_currency():
    with pytest.raises(ProtocolViolationError):
        RuntimeAccountSnapshot.from_payload(_account_payload(currency=""))


def test_position_rejects_a_non_positive_cost_basis():
    with pytest.raises(ProtocolViolationError):
        RuntimePositionSnapshot.from_payload(_position_payload(average_entry_price="0"))
    with pytest.raises(ProtocolViolationError):
        RuntimePositionSnapshot.from_payload(_position_payload(average_entry_price="-1"))


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "None", None])
def test_position_rejects_a_malformed_quantity(bad):
    with pytest.raises(ProtocolViolationError):
        RuntimePositionSnapshot.from_payload(_position_payload(quantity=bad))
