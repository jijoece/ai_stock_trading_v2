"""PR 10 (docs/library-migration/MASTER_PLAN.md row 10, DECISIONS.md D1):
broker-to-`paper_books` reconciliation parity tests.

Every existing `paper_books/external_broker.py` reconciliation test
(`tests/unit/test_external_paper_broker.py`) drives `ExternalPaperRuntime`
through `FakeRuntime`, a hand-rolled double that returns raw dicts directly —
it never goes through `RuntimeClient` (`runtime/client/process_client.py`),
so it never exercises the PR 9 normalization contract
(`ExternalOrderSnapshot`/`ExternalFillSnapshot`/`ExternalPositionsSnapshot`/
`ExternalAccountSnapshot`, `parse_client_order_lookup_response`) that sits
between the wire and `external_broker.py` in production. `RuntimeClient` is
the only production type that structurally satisfies `ExternalPaperRuntime`
(`services/reconcile_paper.py` and the Milestone 11 external-order call sites
both pass a real `RuntimeClient` as `runtime=`), so nothing previously proved
that a *normalized* broker observation — validated exactly as production
validates it — reconciles correctly into `paper_books`' real SQLite-backed
cash ledger and positions tables.

These tests close that gap: they drive `activate_external_reconciliation_
baseline`, `preview_external_paper_order`, `submit_external_paper_order`,
and `reconcile_external_paper_order` with a real `RuntimeClient` wired to a
scripted `FakeTransport` (`tests/support/runtime_client_fixtures.py`, the
same double `tests/unit/test_reconcile_paper.py` and
`tests/unit/test_runtime_client.py` use), and assert against the real
`paper_books` ledger (`cash_ledger`, `positions`) in a real SQLite
connection. D1's exact requirement — "the application reconciles those
observations into paper_books; LumiBot never mutates book state directly" —
is checked end to end, not just at the reconciliation-logic layer in
isolation. The book ledger is not removed or replaced by any of this.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_research.paper_books import cash_ledger
from trading_research.paper_books.config import (
    ExecutionSection, ExternalBrokerSection, PaperBookDefinition,
    PaperBooksConfiguration, RiskSection, ScheduledIntegrationSection, ValuationSection,
)
from trading_research.paper_books.external_broker import (
    STATE_EXPIRED, STATE_FILLED,
    activate_external_reconciliation_baseline, derive_external_order_identity,
    preview_external_paper_order, reconcile_external_paper_order, submit_external_paper_order,
)
from trading_research.paper_books.models import PaperBookOrderIntent, PaperRiskDecision, RISK_APPROVED
from trading_research.runtime.client.process_client import RuntimeClient
from trading_research.storage import paper_books_repositories as repo
from trading_research.storage.database import connect

from tests.support.runtime_client_fixtures import (
    FakeTransport, external_order_payload, fake_transport_factory, start_ready_client,
)

NOW = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
FINGERPRINT = "acct_0123456789abcdef0123456789abcdef"
BOOK_ID = "BASELINE"


def _config():
    return PaperBooksConfiguration(
        version=1, enabled=True,
        baseline=PaperBookDefinition(True, "BASELINE", Decimal("100000")),
        enhanced=PaperBookDefinition(True, "ENHANCED", Decimal("100000")),
        execution=ExecutionSection("local_simulated", False, False),
        risk=RiskSection(
            Decimal("0.10"), Decimal("1000"), Decimal("5000"), Decimal("0.10"), 20,
            Decimal("0.10"), 900,
        ),
        valuation=ValuationSection("evidence_snapshot", 900, "MARK_UNVALUED"),
        scheduled_integration=ScheduledIntegrationSection(False), config_hash="cfg-pr10", raw={},
        external_broker=ExternalBrokerSection(
            True, "alpaca_paper", True, (BOOK_ID,), True, 300,
            Decimal("100"), Decimal("300"), ("limit",), ("day",), 1,
        ),
    )


def _seed(conn):
    cash_ledger.open_book(
        conn, book_id=BOOK_ID, starting_cash_usd=Decimal("100000"), config_hash="cfg-pr10", clock=lambda: NOW,
    )
    decision = PaperRiskDecision(RISK_APPROVED, Decimal("80"), Decimal("80"), Decimal("2"), (), "risk-v1")
    repo.save_risk_decision(conn, "risk-1", BOOK_ID, "cycle-1", "rec-1", "AAPL", decision, "snap-1", NOW)
    intent = PaperBookOrderIntent(
        paper_order_intent_id="intent-1", book_id=BOOK_ID, experiment_arm="BASELINE",
        cycle_id="cycle-1", recommendation_id="rec-1", symbol="AAPL", side="BUY",
        order_type="LIMIT", quantity=Decimal("2"), limit_price=Decimal("40"),
        notional_usd=Decimal("80"), time_in_force="DAY", as_of=NOW,
        risk_decision_id="risk-1", portfolio_snapshot_id="snap-1", config_hash="cfg-pr10",
        created_at=NOW,
    )
    repo.save_order_intent(conn, intent)
    return repo.load_order_intent(conn, BOOK_ID, "intent-1")


def _client_with_fake():
    fake = FakeTransport()
    client = RuntimeClient(
        command=["python3", "-m", "trading_paper_runtime"],
        transport_factory=fake_transport_factory(fake),
        startup_timeout_seconds=1.0, request_timeout_seconds=1.0,
    )
    start_ready_client(client, fake)
    return client, fake


def _account_check_payload():
    return {
        "provider": "alpaca_paper", "environment": "paper", "book_id": BOOK_ID,
        "account_fingerprint": FINGERPRINT, "paper_endpoint_verified": True,
    }


def _positions_payload(*, quantity="0"):
    positions = []
    if Decimal(quantity) != 0:
        positions.append({
            "symbol": "AAPL", "quantity": quantity, "average_entry_price": "40",
            "market_value": str(Decimal(quantity) * Decimal("40")), "as_of": NOW.isoformat(),
        })
    return {"book_id": BOOK_ID, "account_fingerprint": FINGERPRINT, "positions": positions}


def _account_snapshot_payload(*, cash="100000"):
    return {
        "provider": "alpaca_paper", "environment": "paper", "book_id": BOOK_ID,
        "account_fingerprint": FINGERPRINT, "cash": cash, "equity": cash,
        "buying_power": cash, "currency": "USD", "as_of": NOW.isoformat(),
    }


def _activate_baseline(conn, client, cfg):
    return activate_external_reconciliation_baseline(
        conn, book_id=BOOK_ID, operator="alice", runtime=client, config=cfg, clock=lambda: NOW,
    )


def _preview(conn, client, cfg):
    return preview_external_paper_order(
        conn, book_id=BOOK_ID, paper_order_intent_id="intent-1", operator="alice",
        runtime=client, config=cfg, clock=lambda: NOW,
    )


def _submit(conn, client, cfg, preview_record):
    return submit_external_paper_order(
        conn, book_id=BOOK_ID, paper_order_intent_id="intent-1", preview_id=preview_record["preview_id"],
        operator="alice", reason="pr10 parity test", runtime=client, config=cfg, clock=lambda: NOW,
    )


def _queue_baseline_activation(fake, *, cash="100000", quantity="0"):
    fake.queue_success(_account_check_payload())
    fake.queue_success(_positions_payload(quantity=quantity))
    fake.queue_success(_account_snapshot_payload(cash=cash))


def _queue_preview(fake, client_order_id):
    fake.queue_success(_account_check_payload())
    fake.queue_success({
        "provider": "alpaca_paper", "environment": "paper", "book_id": BOOK_ID,
        "client_order_id": client_order_id, "account_fingerprint": FINGERPRINT,
        "result": "APPROVED", "reasons": [],
    })


def _order(client_order_id, *, status, filled_quantity):
    return external_order_payload(
        client_order_id=client_order_id, book_id=BOOK_ID, account_fingerprint=FINGERPRINT,
        symbol="AAPL", side="BUY", quantity=2, limit_price="40", status=status,
        filled_quantity=filled_quantity, submitted_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        average_fill_price="40" if filled_quantity else None,
    )


def _fill(client_order_id, *, quantity):
    return {
        "fill_id": "fill-1", "broker_order_id": "b-1", "client_order_id": client_order_id,
        "book_id": BOOK_ID, "symbol": "AAPL", "side": "BUY", "quantity": str(quantity),
        "price": "40", "filled_at": NOW.isoformat(), "account_fingerprint": FINGERPRINT,
    }


def _queue_submit_and_first_reconcile(fake, client_order_id, *, filled: bool):
    """Queues every runtime call `submit_external_paper_order` makes: its own
    leading account check, submit + immediate fill sweep, then the internal
    `_reconcile_locked` pass (account check, order lookup, a second fill
    sweep, positions, account)."""
    fake.queue_success(_account_check_payload())  # ACCOUNT_CHECK (submit's own preflight)
    accepted = _order(client_order_id, status="ACCEPTED", filled_quantity=0)
    fake.queue_success(accepted)  # SUBMIT_LIMIT_ORDER
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)] if filled else []})  # LIST_ORDER_FILLS (submit's own sweep)
    fake.queue_success(_account_check_payload())  # ACCOUNT_CHECK (reconciliation)
    lookup_order = _order(client_order_id, status="FILLED" if filled else "ACCEPTED", filled_quantity=2 if filled else 0)
    fake.queue_success({"found": True, "order": lookup_order})  # GET_ORDER_BY_CLIENT_ID
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)] if filled else []})  # LIST_ORDER_FILLS (reconciliation sweep)
    fake.queue_success({"orders": []})  # LIST_RECENT_ORDERS (duplicate-broker-order check)
    fake.queue_success(_positions_payload(quantity="2" if filled else "0"))  # GET_POSITIONS
    fake.queue_success(_account_snapshot_payload(cash="99920" if filled else "100000"))  # GET_ACCOUNT_SNAPSHOT


def _submit_filled_order(conn, client, fake, cfg):
    """Full happy-path flow: baseline activation, preview, and a submit whose
    broker observations (order + fill) are fully normalized `RuntimeClient`
    responses, filling the entire approved quantity."""
    intent = _seed(conn)
    client_order_id, _ = derive_external_order_identity(intent)
    _queue_baseline_activation(fake, cash="100000", quantity="0")
    _activate_baseline(conn, client, cfg)
    _queue_preview(fake, client_order_id)
    preview_record = _preview(conn, client, cfg)
    _queue_submit_and_first_reconcile(fake, client_order_id, filled=True)
    result = _submit(conn, client, cfg, preview_record)
    return client_order_id, result


def _submit_unfilled_order(conn, client, fake, cfg):
    """Same flow, but the broker never reports a fill — the order stays
    SUBMITTED, matching a book untouched by any fill."""
    intent = _seed(conn)
    client_order_id, _ = derive_external_order_identity(intent)
    _queue_baseline_activation(fake, cash="100000", quantity="0")
    _activate_baseline(conn, client, cfg)
    _queue_preview(fake, client_order_id)
    preview_record = _preview(conn, client, cfg)
    _queue_submit_and_first_reconcile(fake, client_order_id, filled=False)
    result = _submit(conn, client, cfg, preview_record)
    return client_order_id, result


def test_matched_submission_reconciles_through_real_normalized_runtime_client(tmp_path):
    conn = connect(tmp_path / "parity.sqlite3")
    cfg = _config()
    client, fake = _client_with_fake()

    _, result = _submit_filled_order(conn, client, fake, cfg)

    reconciliation = result["reconciliation"]
    assert reconciliation["status"] == "MATCHED"
    current = repo.load_order_intent(conn, BOOK_ID, "intent-1")
    assert current is not None and current["status"] == STATE_FILLED

    assert cash_ledger.settled_cash(conn, BOOK_ID) == Decimal("99920")
    local_positions = {p["symbol"]: Decimal(p["quantity"]) for p in repo.list_positions(conn, BOOK_ID)}
    assert local_positions["AAPL"] == Decimal("2")


def test_cash_mismatch_detected_through_real_normalized_runtime_client(tmp_path):
    conn = connect(tmp_path / "parity.sqlite3")
    cfg = _config()
    intent = _seed(conn)
    client_order_id, _ = derive_external_order_identity(intent)
    client, fake = _client_with_fake()

    _queue_baseline_activation(fake, cash="100000", quantity="0")
    _activate_baseline(conn, client, cfg)
    _queue_preview(fake, client_order_id)
    preview_record = _preview(conn, client, cfg)

    fake.queue_success(_account_check_payload())
    accepted = _order(client_order_id, status="ACCEPTED", filled_quantity=0)
    fake.queue_success(accepted)
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)]})
    fake.queue_success(_account_check_payload())
    lookup_order = _order(client_order_id, status="FILLED", filled_quantity=2)
    fake.queue_success({"found": True, "order": lookup_order})
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)]})
    fake.queue_success({"orders": []})
    fake.queue_success(_positions_payload(quantity="2"))
    # Broker reports a cash figure that does not match the settled delta
    # (100000 - 80 = 99920) -- e.g. it never actually deducted the trade.
    fake.queue_success(_account_snapshot_payload(cash="100000"))

    result = _submit(conn, client, cfg, preview_record)

    assert "CASH_MISMATCH" in result["reconciliation"]["statuses"]
    # The book is never silently repaired to agree with the broker.
    assert cash_ledger.settled_cash(conn, BOOK_ID) == Decimal("99920")


def test_position_mismatch_detected_through_real_normalized_runtime_client(tmp_path):
    conn = connect(tmp_path / "parity.sqlite3")
    cfg = _config()
    intent = _seed(conn)
    client_order_id, _ = derive_external_order_identity(intent)
    client, fake = _client_with_fake()

    _queue_baseline_activation(fake, cash="100000", quantity="0")
    _activate_baseline(conn, client, cfg)
    _queue_preview(fake, client_order_id)
    preview_record = _preview(conn, client, cfg)

    fake.queue_success(_account_check_payload())
    accepted = _order(client_order_id, status="ACCEPTED", filled_quantity=0)
    fake.queue_success(accepted)
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)]})
    fake.queue_success(_account_check_payload())
    lookup_order = _order(client_order_id, status="FILLED", filled_quantity=2)
    fake.queue_success({"found": True, "order": lookup_order})
    fake.queue_success({"fills": [_fill(client_order_id, quantity=2)]})
    fake.queue_success({"orders": []})
    # Broker reports only 1 share, not the 2 the normalized fill reported.
    fake.queue_success(_positions_payload(quantity="1"))
    fake.queue_success(_account_snapshot_payload(cash="99920"))

    result = _submit(conn, client, cfg, preview_record)

    assert "POSITION_MISMATCH" in result["reconciliation"]["statuses"]
    local_positions = {p["symbol"]: Decimal(p["quantity"]) for p in repo.list_positions(conn, BOOK_ID)}
    assert local_positions["AAPL"] == Decimal("2")  # never silently repaired to match the broker


def test_expired_order_round_trips_through_real_normalized_runtime_client(tmp_path):
    """The PR 9 motivating defect: an `EXPIRED` broker status used to be
    outside `execution/broker_snapshots.py::SUBMISSION_STATES`, producing a
    permanently unreadable submission row. This proves the fix reaches all
    the way through a real `RuntimeClient` into `paper_books`: an order that
    the broker later reports as `EXPIRED` reconciles cleanly, moves to the
    terminal `STATE_EXPIRED`, and leaves the book exactly as it was before
    submission -- no shares, no cash movement."""
    conn = connect(tmp_path / "parity.sqlite3")
    cfg = _config()
    client, fake = _client_with_fake()

    client_order_id, submit_result = _submit_unfilled_order(conn, client, fake, cfg)
    assert submit_result["reconciliation"]["status"] == "MATCHED"

    fake.queue_success(_account_check_payload())
    expired_order = _order(client_order_id, status="EXPIRED", filled_quantity=0)
    fake.queue_success({"found": True, "order": expired_order})
    fake.queue_success({"fills": []})
    fake.queue_success({"orders": []})
    fake.queue_success(_positions_payload(quantity="0"))
    fake.queue_success(_account_snapshot_payload(cash="100000"))

    reconciliation = reconcile_external_paper_order(
        conn, book_id=BOOK_ID, runtime=client, config=cfg, client_order_id=client_order_id, clock=lambda: NOW,
    )

    assert reconciliation["status"] == "MATCHED"
    events = repo.list_external_order_events(conn, book_id=BOOK_ID, client_order_id=client_order_id)
    assert events[-1]["new_state"] == STATE_EXPIRED
    assert cash_ledger.settled_cash(conn, BOOK_ID) == Decimal("100000")
    assert repo.list_positions(conn, BOOK_ID) == []


def test_malformed_order_lookup_fails_closed_without_corrupting_book(tmp_path):
    """A malformed `GET_ORDER_BY_CLIENT_ID` envelope (here: a non-boolean
    `found`) raises `ProtocolViolationError` at the `RuntimeClient` boundary.
    `_run_reconciliation` downgrades that to a non-authoritative `UNKNOWN`
    outcome and returns before ever calling `GET_POSITIONS`/
    `GET_ACCOUNT_SNAPSHOT` -- proving a broken/compromised broker response
    cannot silently repair or corrupt `paper_books` state."""
    conn = connect(tmp_path / "parity.sqlite3")
    cfg = _config()
    client, fake = _client_with_fake()

    client_order_id, _ = _submit_unfilled_order(conn, client, fake, cfg)

    fake.queue_success(_account_check_payload())
    fake.queue_success({"found": "not-a-boolean"})

    reconciliation = reconcile_external_paper_order(
        conn, book_id=BOOK_ID, runtime=client, config=cfg, client_order_id=client_order_id, clock=lambda: NOW,
    )

    assert "UNKNOWN" in reconciliation["statuses"]
    assert reconciliation["status"] != "MATCHED"
    assert cash_ledger.settled_cash(conn, BOOK_ID) == Decimal("100000")
    assert repo.list_positions(conn, BOOK_ID) == []
