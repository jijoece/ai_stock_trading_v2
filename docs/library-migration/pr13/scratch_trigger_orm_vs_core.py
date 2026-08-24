"""PR 13 scratch reproduction (evaluation only, not merged into src/).

Empirically tests DEPENDENCY_MATRIX.md's PR 0 hypothesis: "SQLite triggers
fire at the SQL-statement level regardless of ORM/Core usage, but the ORM's
unit-of-work flush ordering and identity-map caching can mask a
trigger-rejected write."

Uses the *exact* production DDL (copy-pasted verbatim, not paraphrased) for
two representative trigger-protected tables:

* `real_orders` (`storage/trading_schema.py`) — a fully RESERVED table:
  every INSERT/UPDATE/DELETE is rejected unconditionally.
* `paper_book_cash_ledger` (`storage/paper_books_schema.py`) — an
  append-only table: INSERT is allowed, UPDATE/DELETE are rejected.

Run: /tmp/pr13_scratch_venv/bin/python scratch_trigger_orm_vs_core.py
"""
from __future__ import annotations

import sqlite3

from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Table,
    create_engine,
    delete,
    event,
    insert,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# --- exact production DDL, copy-pasted verbatim -----------------------------
# `real_orders` + its 3 triggers: storage/trading_schema.py
# (minimal parent tables added only so the FKs the real DDL declares resolve)
REAL_ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS recommendations (
    rec_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    rec_id TEXT REFERENCES recommendations(rec_id),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'expired', 'invalidated')),
    approved_at TEXT,
    approved_by TEXT
);

-- RESERVED for a later, explicitly gated phase. No writer exists in this repo.
CREATE TABLE IF NOT EXISTS real_orders (
    order_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_real_orders_reserved_insert
BEFORE INSERT ON real_orders
BEGIN
    SELECT RAISE(ABORT, 'real_orders is reserved — no execution path exists in this phase');
END;

CREATE TRIGGER IF NOT EXISTS trg_real_orders_reserved_update
BEFORE UPDATE ON real_orders
BEGIN
    SELECT RAISE(ABORT, 'real_orders is reserved — no execution path exists in this phase');
END;

CREATE TRIGGER IF NOT EXISTS trg_real_orders_reserved_delete
BEFORE DELETE ON real_orders
BEGIN
    SELECT RAISE(ABORT, 'real_orders is reserved — no execution path exists in this phase');
END;
"""

# `paper_book_cash_ledger` + its 2 triggers: storage/paper_books_schema.py
CASH_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS paper_books (
    book_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS paper_book_cash_ledger (
    book_id TEXT NOT NULL REFERENCES paper_books(book_id),
    ledger_entry_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    amount_usd TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    cycle_id TEXT,
    symbol TEXT,
    reference_id TEXT,
    operator TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (book_id, ledger_entry_id)
);

CREATE TRIGGER IF NOT EXISTS trg_paper_book_cash_ledger_no_update
BEFORE UPDATE ON paper_book_cash_ledger
BEGIN SELECT RAISE(ABORT, 'paper_book_cash_ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_paper_book_cash_ledger_no_delete
BEFORE DELETE ON paper_book_cash_ledger
BEGIN SELECT RAISE(ABORT, 'paper_book_cash_ledger is append-only'); END;
"""


def _log(title: str) -> None:
    print(f"\n=== {title} ===")


class Base(DeclarativeBase):
    pass


# Declared only so the ORM's FK-dependency sort can resolve
# `real_orders.approval_id` / `paper_book_cash_ledger.book_id` — these two
# parent tables are otherwise created solely by the raw executescript DDL
# above and never written to by the ORM in this scratch reproduction.
ApprovalsTable = Table(
    "approvals", Base.metadata,
    Column("approval_id", String, primary_key=True),
    extend_existing=True,
)
PaperBooksTable = Table(
    "paper_books", Base.metadata,
    Column("book_id", String, primary_key=True),
    extend_existing=True,
)


class RealOrderORM(Base):
    __tablename__ = "real_orders"
    order_id = Column(String, primary_key=True)
    approval_id = Column(String, ForeignKey("approvals.approval_id"), nullable=False)
    payload_hash = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    status = Column(String, nullable=False)


class CashLedgerORM(Base):
    __tablename__ = "paper_book_cash_ledger"
    book_id = Column(String, ForeignKey("paper_books.book_id"), primary_key=True)
    ledger_entry_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    amount_usd = Column(String, nullable=False)
    event_timestamp = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    cycle_id = Column(String)
    symbol = Column(String)
    reference_id = Column(String)
    operator = Column(String)
    reason = Column(String)
    created_at = Column(String, nullable=False)


def make_engine():
    # Mirrors storage/database.py: foreign_keys ON. sqlite3 DBAPI default
    # isolation_level (SQLAlchemy manages transactions itself here) is used
    # deliberately in Case 6 to test the pysqlite/SQLAlchemy transaction
    # interaction the production code's own isolation_level=None +
    # explicit-BEGIN pattern (storage/transactions.py) does not use.
    # StaticPool keeps the single in-memory DB alive across connections/engine.begin() blocks.
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", future=True, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _set_fk_pragma(dbapi_connection, connection_record):
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # Matches storage/trading_schema.py::apply_trading_schema — DDL scripts
    # are applied via the raw DBAPI connection's executescript, not
    # statement-by-statement, since sqlite3's own cursor.execute() (which
    # SQLAlchemy's Core `text()` ultimately calls) only accepts one
    # statement at a time.
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(REAL_ORDERS_DDL)
        raw_conn.executescript(CASH_LEDGER_DDL)
        raw_conn.commit()
    finally:
        raw_conn.close()
    return engine


def case_1_core_insert_rejected(engine) -> None:
    _log("Case 1: Core INSERT into real_orders (control — must be rejected)")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO approvals (approval_id, payload_json, payload_hash, created_at, expires_at) "
                "VALUES ('appr-1', '{}', 'h', 't', 't')"
            )
        )
    real_orders = Table("real_orders", Base.metadata, autoload_with=engine)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(real_orders).values(
                    order_id="ord-1", approval_id="appr-1", payload_hash="h",
                    created_at="t", status="pending",
                )
            )
        print("FAIL: Core insert into real_orders was NOT rejected")
    except IntegrityError as exc:
        print(f"PASS: Core insert rejected — {exc.orig}")


def case_2_orm_flush_insert_rejected(engine) -> None:
    _log("Case 2: ORM session.add() + flush() into real_orders (must be rejected)")
    Session = sessionmaker(bind=engine)
    session = Session()
    obj = RealOrderORM(order_id="ord-2", approval_id="appr-1", payload_hash="h", created_at="t", status="pending")
    session.add(obj)
    try:
        session.flush()
        print("FAIL: ORM flush into real_orders was NOT rejected")
    except IntegrityError as exc:
        print(f"PASS: ORM flush rejected — {exc.orig}")
        # Probe: does the identity map still report this object as
        # "pending" (about to be inserted) or worse, "persistent" (as if
        # the insert succeeded) after the failed flush but BEFORE rollback?
        from sqlalchemy import inspect as sa_inspect

        state = sa_inspect(obj)
        print(
            f"    object state pre-rollback: pending={state.pending} "
            f"persistent={state.persistent} transient={state.transient} "
            f"deleted={state.deleted}"
        )
        session.rollback()
        state = sa_inspect(obj)
        print(
            f"    object state post-rollback: pending={state.pending} "
            f"persistent={state.persistent} transient={state.transient} "
            f"deleted={state.deleted}"
        )
    session.close()


def case_3_orm_session_unusable_without_rollback(engine) -> None:
    _log("Case 3: caller forgets to rollback after a rejected flush — does the session mask further work?")
    Session = sessionmaker(bind=engine)
    session = Session()
    bad = RealOrderORM(order_id="ord-3", approval_id="appr-1", payload_hash="h", created_at="t", status="pending")
    session.add(bad)
    try:
        session.flush()
    except IntegrityError:
        pass  # deliberately NOT calling session.rollback() — simulate a caller bug
    # Now try something unrelated and legitimate: insert a cash-ledger row,
    # via the same un-rolled-back session — including the raw execute() a
    # caller might reach for, not just the ORM flush path.
    try:
        session.execute(text("INSERT INTO paper_books (book_id) VALUES ('book-1')"))
        good = CashLedgerORM(
            book_id="book-1", ledger_entry_id="le-1", event_type="DEPOSIT", amount_usd="100.00",
            event_timestamp="t", idempotency_key="k1", created_at="t",
        )
        session.add(good)
        session.flush()
        print("FAIL: SQLAlchemy allowed further work on a session with an unhandled failed flush "
              "(masking risk substantiated)")
    except PendingRollbackError as exc:
        print(
            "PASS: SQLAlchemy fails closed — a session left dirty by an unhandled flush "
            f"error refuses further work (even a raw text() execute) rather than silently "
            f"proceeding: {type(exc).__name__}"
        )
    session.close()


def case_4_batched_flush_partial_visibility(engine) -> None:
    _log("Case 4: multi-object flush, one row rejected — does any object in the batch "
         "look 'flushed'/persistent in memory before rollback (masking risk)?")
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text("INSERT OR IGNORE INTO paper_books (book_id) VALUES ('book-2')"))
    session.commit()
    good = CashLedgerORM(
        book_id="book-2", ledger_entry_id="le-2", event_type="DEPOSIT", amount_usd="50.00",
        event_timestamp="t", idempotency_key="k2", created_at="t",
    )
    bad = RealOrderORM(order_id="ord-4", approval_id="appr-1", payload_hash="h", created_at="t", status="pending")
    session.add_all([good, bad])
    from sqlalchemy import inspect as sa_inspect

    try:
        session.flush()
        print("FAIL: batched flush of a legal + illegal row did not raise")
    except IntegrityError:
        good_state = sa_inspect(good)
        bad_state = sa_inspect(bad)
        print(
            f"    pre-rollback: good.persistent={good_state.persistent} "
            f"good.pending={good_state.pending} bad.persistent={bad_state.persistent}"
        )
        with engine.connect() as check_conn:
            row = check_conn.execute(
                text("SELECT COUNT(*) FROM paper_book_cash_ledger WHERE ledger_entry_id='le-2'")
            ).scalar()
        print(f"    DB row count for 'good' row while session still open, pre-rollback: {row}")
        session.rollback()
        good_state = sa_inspect(good)
        with engine.connect() as check_conn:
            row_after = check_conn.execute(
                text("SELECT COUNT(*) FROM paper_book_cash_ledger WHERE ledger_entry_id='le-2'")
            ).scalar()
        print(
            f"    post-rollback: good.persistent={good_state.persistent} "
            f"good.transient={good_state.transient} DB row count: {row_after}"
        )
        if row_after == 0:
            print("PASS: the whole flush is one transaction — the legal row's DB write "
                  "was rolled back along with the illegal one (atomic, not masked)")
        else:
            print("FAIL: the legal row was persisted despite the batch failing (masking risk substantiated)")
    session.close()


def case_5_core_append_only_allows_insert_rejects_update_delete(engine) -> None:
    _log("Case 5: Core against paper_book_cash_ledger — INSERT allowed, UPDATE/DELETE rejected (control)")
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO paper_books (book_id) VALUES ('book-3')"))
    cash_ledger = Table("paper_book_cash_ledger", Base.metadata, autoload_with=engine, extend_existing=True)
    with engine.begin() as conn:
        conn.execute(
            insert(cash_ledger).values(
                book_id="book-3", ledger_entry_id="le-3", event_type="DEPOSIT", amount_usd="10.00",
                event_timestamp="t", idempotency_key="k3", created_at="t",
            )
        )
    print("    Core insert succeeded (expected)")
    try:
        with engine.begin() as conn:
            conn.execute(
                update(cash_ledger).where(cash_ledger.c.ledger_entry_id == "le-3").values(amount_usd="999.00")
            )
        print("FAIL: Core update of an append-only row was NOT rejected")
    except IntegrityError as exc:
        print(f"PASS: Core update rejected — {exc.orig}")
    try:
        with engine.begin() as conn:
            conn.execute(delete(cash_ledger).where(cash_ledger.c.ledger_entry_id == "le-3"))
        print("FAIL: Core delete of an append-only row was NOT rejected")
    except IntegrityError as exc:
        print(f"PASS: Core delete rejected — {exc.orig}")


def case_6_orm_relationship_cascade_hits_trigger(engine) -> None:
    _log("Case 6: ORM cascade-configured relationship issuing an implicit DELETE "
         "against an append-only child — does the cascade silently skip the DB, "
         "or does it issue a real DELETE that the trigger still blocks?")

    class Base2(DeclarativeBase):
        pass

    from sqlalchemy.orm import relationship

    class BookORM(Base2):
        __tablename__ = "paper_books"
        __table_args__ = {"extend_existing": True}
        book_id = Column(String, primary_key=True)
        ledger_entries = relationship(
            "LedgerORM", cascade="all, delete-orphan", passive_deletes=False
        )

    class LedgerORM(Base2):
        __tablename__ = "paper_book_cash_ledger"
        __table_args__ = {"extend_existing": True}
        book_id = Column(String, ForeignKey("paper_books.book_id"), primary_key=True)
        ledger_entry_id = Column(String, primary_key=True)
        event_type = Column(String, nullable=False)
        amount_usd = Column(String, nullable=False)
        event_timestamp = Column(String, nullable=False)
        idempotency_key = Column(String, nullable=False)
        created_at = Column(String, nullable=False)

    Session = sessionmaker(bind=engine)
    session = Session()
    book = session.get(BookORM, "book-3")
    if book is None:
        print("    (setup) no book-3 found — skipping cascade case")
        session.close()
        return
    print(f"    book-3 has {len(book.ledger_entries)} ledger entries in the relationship collection")
    session.delete(book)
    try:
        session.flush()
        print("FAIL: cascade delete of an append-only child row was NOT rejected "
              "(cascade silently bypassed the DB trigger — masking risk substantiated)")
    except IntegrityError as exc:
        print(f"PASS: ORM relationship cascade still issues a real DELETE that the trigger blocks — {exc.orig}")
    session.rollback()
    session.close()


if __name__ == "__main__":
    import sqlalchemy

    print(f"sqlalchemy {sqlalchemy.__version__}, sqlite3 {sqlite3.sqlite_version}")
    engine = make_engine()
    case_1_core_insert_rejected(engine)
    case_2_orm_flush_insert_rejected(engine)
    case_3_orm_session_unusable_without_rollback(engine)
    case_4_batched_flush_partial_visibility(engine)
    case_5_core_append_only_allows_insert_rejects_update_delete(engine)
    case_6_orm_relationship_cascade_hits_trigger(engine)
    print("\nDone.")
