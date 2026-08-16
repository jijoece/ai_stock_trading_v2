"""PR 9 — runtime-side normalization contract and payload boundary.

No network call, no credentials, no LumiBot import: the payload models and
the normalization helpers are plain Python. The gateway translation tests
build fake `alpaca-py`-shaped order objects and drive `_order_to_snapshot`
directly, with paper-mode verification and the account fingerprint stubbed,
so nothing reaches a broker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_paper_runtime.errors import ErrorCode, RuntimeOperationError
from trading_paper_runtime.models import (
    SUBMISSION_STATES,
    AccountSnapshotPayload,
    FillPayload,
    OrderSnapshotPayload,
    PositionSnapshotPayload,
)
from trading_paper_runtime.normalization import (
    BROKER_REPORTABLE_STATUSES,
    NORMALIZED_ORDER_STATUSES,
    NormalizationError,
    normalize_broker_reportable_status,
    normalize_exact_int,
    normalize_optional_decimal_string,
    normalize_time_in_force,
    parse_decimal,
)

_NOW = "2026-08-02T15:00:00+00:00"


def _order(**overrides) -> OrderSnapshotPayload:
    defaults = dict(
        intent_id="intent-1", client_order_id="intent-1", broker_order_id="b-1",
        status="ACCEPTED", raw_broker_status="new", quantity=10, filled_quantity=0,
        average_fill_price=None, submitted_at=_NOW, updated_at=_NOW,
        book_id="book-1", symbol="AAPL", side="BUY", limit_price="101.50",
    )
    defaults.update(overrides)
    return OrderSnapshotPayload(**defaults)  # type: ignore[arg-type]


def _fill(**overrides) -> FillPayload:
    defaults = dict(
        fill_id="f-1", broker_order_id="b-1", client_order_id="intent-1", book_id="book-1",
        symbol="AAPL", side="BUY", quantity="10", price="101.50", filled_at=_NOW,
        account_fingerprint="acct_x",
    )
    defaults.update(overrides)
    return FillPayload(**defaults)  # type: ignore[arg-type]


# --- contract vocabulary ---------------------------------------------------


def test_submission_states_is_the_contract_vocabulary():
    assert SUBMISSION_STATES == NORMALIZED_ORDER_STATUSES
    assert "EXPIRED" in SUBMISSION_STATES  # absent before PR 9


def test_normalization_errors_are_structured_runtime_errors():
    """A normalization failure must reach the main process as a protocol
    error with a code, not as an unclassified internal crash."""
    with pytest.raises(RuntimeOperationError) as excinfo:
        parse_decimal("nope", "price")
    assert isinstance(excinfo.value, NormalizationError)
    assert excinfo.value.code == ErrorCode.MALFORMED_PAYLOAD
    assert excinfo.value.retryable is False

    with pytest.raises(RuntimeOperationError) as excinfo:
        normalize_broker_reportable_status("PENDING_SUBMISSION")
    assert excinfo.value.code == ErrorCode.UNKNOWN_BROKER_STATUS


# --- OrderSnapshotPayload boundary ----------------------------------------


def test_order_snapshot_canonicalizes_its_fields():
    order = _order(submitted_at="2026-08-02 15:00:00", limit_price="1E+2", side="buy")
    assert order.submitted_at == "2026-08-02T15:00:00+00:00"
    assert order.limit_price == "100"
    assert order.side == "BUY"
    assert order.time_in_force == "DAY"


def test_order_snapshot_rejects_the_literal_string_none_as_a_limit_price():
    """The exact pre-PR-9 gateway defect: `str(None)` is `"None"`, which is
    truthy and survived `... or None`, then crashed the consumer's
    `Decimal(...)`."""
    with pytest.raises(NormalizationError):
        _order(limit_price="None")
    assert _order(limit_price=None).limit_price is None


@pytest.mark.parametrize("status", sorted(set(NORMALIZED_ORDER_STATUSES) - set(BROKER_REPORTABLE_STATUSES)))
def test_order_snapshot_rejects_main_process_only_states(status):
    with pytest.raises(NormalizationError):
        _order(status=status)


def test_order_snapshot_rejects_an_unknown_status():
    with pytest.raises(NormalizationError):
        _order(status="PROBABLY_FINE")


def test_order_snapshot_enforces_quantity_invariants():
    with pytest.raises(RuntimeOperationError):
        _order(quantity=0)
    with pytest.raises(RuntimeOperationError):
        _order(quantity=10, filled_quantity=11)
    with pytest.raises(NormalizationError):
        _order(filled_quantity="2.5")
    assert _order(quantity="10", filled_quantity=Decimal("4"), average_fill_price="9").quantity == 10


def test_order_snapshot_requires_a_price_for_a_reported_fill():
    with pytest.raises(RuntimeOperationError):
        _order(status="FILLED", filled_quantity=10, average_fill_price=None)
    with pytest.raises(NormalizationError):
        _order(status="FILLED", filled_quantity=10, average_fill_price="0")


def test_order_snapshot_rejects_non_finite_prices():
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(NormalizationError):
            _order(filled_quantity=10, average_fill_price=bad)


# --- FillPayload boundary --------------------------------------------------


def test_fill_rejects_a_missing_quantity_or_price_instead_of_defaulting_to_zero():
    """Before PR 9 a FILL activity missing `qty`/`price` was stringified to
    `"0"`, fabricating a zero-price fill that `paper_books` would have booked
    as free shares."""
    for field in ("quantity", "price"):
        with pytest.raises(NormalizationError):
            _fill(**{field: None})
        with pytest.raises(NormalizationError):
            _fill(**{field: "0"})


def test_fill_requires_identity_fields_and_a_normalized_side():
    with pytest.raises(RuntimeOperationError):
        _fill(fill_id="")
    with pytest.raises(RuntimeOperationError):
        _fill(symbol="")
    with pytest.raises(NormalizationError):
        _fill(side="SHORT")
    assert _fill(side="sell").side == "SELL"


def test_fill_canonicalizes_its_timestamp():
    assert _fill(filled_at="2026-08-02 15:00:00").filled_at == "2026-08-02T15:00:00+00:00"


# --- account and position boundaries --------------------------------------


def test_account_snapshot_rejects_unparseable_values():
    ok = AccountSnapshotPayload(cash="1000.00", equity="1000.00", buying_power=None, currency="USD", as_of=_NOW)
    assert ok.cash == "1000.00" and ok.buying_power is None
    with pytest.raises(NormalizationError):
        AccountSnapshotPayload(cash=None, equity="1", buying_power=None, currency="USD", as_of=_NOW)
    with pytest.raises(NormalizationError):
        AccountSnapshotPayload(cash="NaN", equity="1", buying_power=None, currency="USD", as_of=_NOW)
    with pytest.raises(RuntimeOperationError):
        AccountSnapshotPayload(cash="1", equity="1", buying_power=None, currency="", as_of=_NOW)


def test_position_snapshot_requires_a_positive_cost_basis():
    ok = PositionSnapshotPayload(
        symbol="AAPL", quantity="10", average_entry_price="101.5", market_value="1015", as_of=_NOW,
    )
    assert ok.average_entry_price == "101.5"
    with pytest.raises(NormalizationError):
        PositionSnapshotPayload(
            symbol="AAPL", quantity="10", average_entry_price="0", market_value=None, as_of=_NOW,
        )
    with pytest.raises(NormalizationError):
        PositionSnapshotPayload(
            symbol="AAPL", quantity="oops", average_entry_price="1", market_value=None, as_of=_NOW,
        )


# --- gateway translation ---------------------------------------------------


@pytest.fixture
def gateway():
    pytest.importorskip("lumibot")
    from trading_paper_runtime.configuration import RuntimeConfiguration
    from trading_paper_runtime.lumibot_gateway import LumiBotAlpacaPaperGateway

    gw = LumiBotAlpacaPaperGateway(
        config=RuntimeConfiguration(
            broker_provider="alpaca", alpaca_api_key=None, alpaca_api_secret=None, alpaca_is_paper_flag=True,
        )
    )
    # No broker connection is made or needed: only the pure translation
    # method is exercised, with the fingerprint (an API read) stubbed.
    object.__setattr__(gw, "account_fingerprint", lambda: "acct_test")
    return gw


def _raw_order(**overrides) -> SimpleNamespace:
    defaults = dict(
        client_order_id="intent-1", id="b-1", status=SimpleNamespace(value="new"),
        qty="10", filled_qty="0", filled_avg_price=None,
        submitted_at=datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc),
        symbol="AAPL", side=SimpleNamespace(value="buy"), limit_price="101.5",
        time_in_force=SimpleNamespace(value="day"),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_gateway_translates_a_market_order_with_no_limit_price(gateway):
    """`limit_price=None` must stay `None` — not become the string
    `"None"`, which is what the pre-PR-9 `str(...) or None` produced."""
    snapshot = gateway._order_to_snapshot(_raw_order(limit_price=None))
    assert snapshot.limit_price is None
    assert snapshot.status == "ACCEPTED"
    assert snapshot.side == "BUY"


def test_gateway_does_not_silently_default_a_plain_string_time_in_force(gateway):
    """A broker reporting `time_in_force` as a plain string (not an enum)
    previously fell through `getattr(str, "value", "day")` and was reported
    as DAY regardless of what it actually said."""
    assert gateway._order_to_snapshot(_raw_order(time_in_force="gtc")).time_in_force == "GTC"
    assert gateway._order_to_snapshot(_raw_order(time_in_force=None)).time_in_force == "DAY"
    with pytest.raises(NormalizationError):
        gateway._order_to_snapshot(_raw_order(time_in_force="forever"))


def test_gateway_canonicalizes_alpaca_timestamps(gateway):
    snapshot = gateway._order_to_snapshot(_raw_order())
    assert snapshot.submitted_at == "2026-08-02T15:00:00+00:00"


def test_gateway_fails_closed_on_a_fractional_broker_quantity(gateway):
    with pytest.raises(NormalizationError):
        gateway._order_to_snapshot(_raw_order(qty="10.5"))


def test_gateway_fails_closed_on_a_fill_with_no_price(gateway):
    with pytest.raises(RuntimeOperationError):
        gateway._order_to_snapshot(_raw_order(status=SimpleNamespace(value="filled"), filled_qty="10"))


# --- item 4: no remaining silent repairs -----------------------------------


def test_gateway_fails_closed_on_a_filled_order_with_no_filled_qty_reported(gateway):
    """Before this fix, `order.filled_qty or 0` turned a genuinely missing
    `filled_qty` into the same value as a broker legitimately reporting zero
    shares filled — the two are not the same observation and must not be
    conflated, especially for a status claiming shares were filled."""
    with pytest.raises(RuntimeOperationError):
        gateway._order_to_snapshot(
            _raw_order(status=SimpleNamespace(value="filled"), filled_qty=None, filled_avg_price="101.5")
        )


def test_gateway_fails_closed_on_a_missing_submitted_at_instead_of_using_the_clock(gateway):
    """Before this fix, a missing `submitted_at`/`updated_at` was replaced
    with `datetime.now(timezone.utc)` — fabricating a broker timestamp this
    process never observed."""
    with pytest.raises(NormalizationError):
        gateway._order_to_snapshot(_raw_order(submitted_at=None))


def test_gateway_fails_closed_on_a_missing_updated_at_instead_of_using_the_clock(gateway):
    with pytest.raises(NormalizationError):
        gateway._order_to_snapshot(_raw_order(updated_at=None))


def test_get_account_fails_closed_on_a_missing_currency_instead_of_defaulting_to_usd(gateway):
    """Before this fix, a missing `currency` attribute was silently
    defaulted to `"USD"` — a broker that stops reporting account currency is
    a malformed observation, not evidence the account is denominated in
    dollars."""
    object.__setattr__(
        gateway, "_api",
        SimpleNamespace(get_account=lambda: SimpleNamespace(cash="1000", equity="1000", buying_power="2000")),
    )
    with pytest.raises(RuntimeOperationError):
        gateway.get_account()


def test_every_mapped_alpaca_status_is_inside_the_contract():
    from trading_paper_runtime.lumibot_gateway import _ALPACA_STATUS_MAP, _map_status

    for raw, expected in _ALPACA_STATUS_MAP.items():
        assert _map_status(raw) == expected
        assert expected in BROKER_REPORTABLE_STATUSES
    with pytest.raises(RuntimeOperationError) as excinfo:
        _map_status("teleported")
    assert excinfo.value.code == ErrorCode.UNKNOWN_BROKER_STATUS


# --- helper edge cases -----------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "None", "abc", float("nan"), float("inf"), True])
def test_parse_decimal_fails_closed(bad):
    with pytest.raises(NormalizationError):
        parse_decimal(bad, "price")


@pytest.mark.parametrize("bad", ["0.5", float("nan"), None, True])
def test_normalize_exact_int_never_truncates(bad):
    with pytest.raises(NormalizationError):
        normalize_exact_int(bad, "quantity")


def test_normalize_optional_decimal_distinguishes_absent_from_malformed():
    assert normalize_optional_decimal_string(None, "x") is None
    assert normalize_optional_decimal_string("  ", "x") is None
    with pytest.raises(NormalizationError):
        normalize_optional_decimal_string("None", "x")


def test_normalize_time_in_force_never_defaults():
    with pytest.raises(NormalizationError):
        normalize_time_in_force(None, "time_in_force")
