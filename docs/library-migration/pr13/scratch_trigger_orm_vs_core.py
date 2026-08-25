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

import re
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
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
from sqlalchemy.orm import DeclarativeBase, registry, sessionmaker

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
    # Mirrors storage/database.py: file-backed SQLite (storage/database.py's
    # own sqlite3.connect(str(db_path)) never uses ":memory:"), foreign_keys
    # ON. sqlite3 DBAPI default isolation_level (SQLAlchemy manages
    # transactions itself here) is used deliberately in Case 6 to test the
    # pysqlite/SQLAlchemy transaction interaction the production code's own
    # isolation_level=None + explicit-BEGIN pattern (storage/transactions.py)
    # does not use.
    #
    # A file-backed DB (default QueuePool, no StaticPool) is required, not
    # cosmetic: Case 4's masking check needs `engine.connect()` to open a
    # DBAPI connection genuinely independent of the ORM session's own
    # connection. An in-memory `:memory:` URL is a distinct, empty database
    # per DBAPI connection unless pinned to a single shared connection via
    # StaticPool — which would make `engine.connect()` silently reuse the
    # *same* connection the session is using, defeating the independent-
    # observer check (review finding: "Use a separate DB connection for the
    # visibility check"). A file-backed DB lets every connection, including
    # the session's and the check's, see the same on-disk data through
    # genuinely separate DBAPI connections, like production.
    db_path = Path(tempfile.mkdtemp(prefix="pr13_scratch_trigger_")) / "scratch.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)

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
        # Genuinely independent observer: `engine` is file-backed (make_engine()),
        # so `engine.connect()` opens a second, distinct DBAPI connection from
        # the session's own — not the same connection reused, as an in-memory
        # `:memory:` + StaticPool engine would give (review finding: "Use a
        # separate DB connection for the visibility check"). Per Case 3's
        # `PendingRollbackError`, SQLAlchemy has already rolled back the failed
        # flush's transaction internally by the time this `except` block runs,
        # before `session.rollback()` is called below — so this check proves
        # the post-failure end-state is clean via true cross-connection
        # visibility, not that a still-pending, not-yet-rolled-back write was
        # momentarily invisible mid-transaction (no such observable window is
        # claimed).
        with engine.connect() as check_conn:
            row = check_conn.execute(
                text("SELECT COUNT(*) FROM paper_book_cash_ledger WHERE ledger_entry_id='le-2'")
            ).scalar()
        print(
            f"    DB row count for 'good' row from a genuinely independent "
            f"connection, checked immediately after the caught IntegrityError "
            f"(after SQLAlchemy's internal rollback, before this test's "
            f"explicit session.rollback()): {row}"
        )
        session.rollback()
        good_state = sa_inspect(good)
        with engine.connect() as check_conn:
            row_after = check_conn.execute(
                text("SELECT COUNT(*) FROM paper_book_cash_ledger WHERE ledger_entry_id='le-2'")
            ).scalar()
        print(
            f"    post-rollback: good.persistent={good_state.persistent} "
            f"good.transient={good_state.transient} DB row count from an "
            f"independent connection: {row_after}"
        )
        if row == 0 and row_after == 0:
            print(
                "PASS: a truly independent connection never observed the legal "
                "row at either checkpoint — the whole flush is one atomic "
                "transaction, and its failure left no trace once SQLAlchemy's "
                "own fail-closed rollback ran (post-failure state, not a "
                "mid-transaction pending-write leak)"
            )
        else:
            print(
                f"FAIL: an independent connection observed the legal row "
                f"(pre-rollback count={row}, post-rollback count={row_after}) "
                f"despite the batch failing (masking risk substantiated)"
            )
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


def case_7_orm_update_existing_row_masking(engine) -> None:
    _log(
        "Case 7: ORM UPDATE — load an existing paper_book_cash_ledger row "
        "through the identity map, mutate a protected field, flush (must be "
        "rejected) — is the trigger-rejected UPDATE masked by the "
        "already-persistent object's in-memory state, before and after "
        "rollback?"
    )
    from sqlalchemy import inspect as sa_inspect

    Session = sessionmaker(bind=engine)
    session = Session()
    row = session.get(CashLedgerORM, ("book-3", "le-3"))
    if row is None:
        print("    (setup) no book-3/le-3 row found — skipping ORM update case")
        session.close()
        return
    original_amount = row.amount_usd
    print(f"    loaded existing row via ORM identity map: amount_usd={original_amount!r}")
    mutated_amount = "999.00"
    row.amount_usd = mutated_amount
    state = sa_inspect(row)
    print(
        f"    object state pre-flush: persistent={state.persistent} "
        f"dirty(in session.dirty)={row in session.dirty} in-memory amount_usd={mutated_amount!r}"
    )
    try:
        session.flush()
        print("FAIL: ORM UPDATE of an append-only row was NOT rejected")
    except IntegrityError as exc:
        print(f"PASS: ORM update rejected — {exc.orig}")
        state = sa_inspect(row)
        print(
            f"    object state pre-rollback: persistent={state.persistent} "
            f"expired={state.expired} (in-memory value this test assigned "
            f"before the rejected flush: {mutated_amount!r})"
        )
        # Regression for review finding "Actually execute the pre-rollback
        # attribute read": actually access the expired attribute here,
        # rather than only asserting what it would do. SQLAlchemy expires an
        # object's attributes after a failed UPDATE flush, and re-reading an
        # expired attribute issues a reload query on this still-dirty
        # session — which must raise PendingRollbackError (the same
        # fail-closed behavior Case 3 pins), not silently return a value.
        try:
            leaked_value = row.amount_usd
            print(
                f"FAIL: reading the expired attribute after a rejected flush "
                f"did not raise PendingRollbackError — returned {leaked_value!r} "
                f"instead (masking risk substantiated for ORM UPDATE)"
            )
        except PendingRollbackError:
            print(
                "PASS: reading the expired attribute pre-rollback itself "
                "raised PendingRollbackError, proving the session refuses to "
                "serve a possibly-stale value rather than masking one"
            )
        with engine.connect() as check_conn:
            db_value = check_conn.execute(
                text("SELECT amount_usd FROM paper_book_cash_ledger WHERE ledger_entry_id='le-3'")
            ).scalar()
        print(f"    DB value from a genuinely independent connection, pre-rollback: {db_value!r}")
        session.rollback()
        session.expire(row)
        state = sa_inspect(row)
        refreshed_amount = row.amount_usd  # triggers a re-SELECT since row was expired
        print(
            f"    object state post-rollback: persistent={state.persistent} "
            f"re-fetched-from-DB amount_usd={refreshed_amount!r}"
        )
        if db_value == original_amount and refreshed_amount == original_amount:
            print(
                "PASS: the rejected UPDATE's in-memory mutation never became "
                "durable — an independent connection saw the original value "
                "throughout, and the identity map itself reverts to the true "
                "DB value on refresh after rollback (no masking of the "
                "rejected ORM UPDATE)"
            )
        else:
            print(
                f"FAIL: the append-only row's value diverged from the original "
                f"{original_amount!r} (db_value={db_value!r}, "
                f"refreshed={refreshed_amount!r}) — masking risk substantiated "
                f"for ORM UPDATE"
            )
    session.close()


class TriggerProtectedTableORMGuard(Exception):
    """Raised by the Core-only architectural guard, before any SQL is
    emitted, when an ORM session attempts to flush a change against a
    trigger-protected table. MASTER_PLAN.md row 13 question (a) requires
    proving trigger-protected tables can be constrained to Core-only
    statements, never ORM-session flush/unit-of-work — this is the
    representative enforcement mechanism that proves it, not just a
    recommendation."""


def discover_trigger_protected_tables_from_production_schema() -> frozenset[str]:
    """Derives the Core-only guard's table policy from the *actual*
    production schema DDL — the centralized, authoritative source review
    finding "Enforce Core-only access for every trigger-protected table"
    required — instead of a hand-maintained allowlist that silently omits
    tables. A hand-maintained set (the original `{"real_orders",
    "paper_book_cash_ledger"}`) covered only 2 of the 50 tables production
    actually protects with a write-rejecting trigger, including
    `paper_book_fills`, `research_attempts`, `research_attempt_failures`,
    and `research_cycle_provider_provenance_links` cited in that finding.

    Scans every `src/trading_research/storage/*_schema.py` module for a
    `CREATE TRIGGER ... BEFORE {INSERT,UPDATE,DELETE} ON <table> ... END;`
    block (unconditional or `WHEN`-conditional, e.g. `recommendations`'
    frozen-row guard) whose body contains `RAISE(ABORT`, and collects
    `<table>`. Because this re-derives the set from the same production
    files on every run, a future table gaining a write-rejecting trigger is
    picked up automatically — the guard cannot go stale by omission the way
    a hardcoded set could.
    """
    schema_dir = (
        Path(__file__).resolve().parents[3] / "src" / "trading_research" / "storage"
    )
    trigger_block = re.compile(
        r"CREATE TRIGGER.*?BEFORE\s+(?:INSERT|UPDATE|DELETE)\s+ON\s+(\w+).*?END;",
        re.DOTALL,
    )
    tables: set[str] = set()
    for schema_file in sorted(schema_dir.glob("*_schema.py")):
        source = schema_file.read_text(encoding="utf-8")
        for match in trigger_block.finditer(source):
            if "RAISE(ABORT" in match.group(0):
                tables.add(match.group(1))
    return frozenset(tables)


TRIGGER_PROTECTED_TABLES = discover_trigger_protected_tables_from_production_schema()
assert {"real_orders", "paper_book_cash_ledger"} <= TRIGGER_PROTECTED_TABLES, (
    "discovery regressed: lost the two tables cases 1-7 exercise against real DDL"
)


def _reject_protected_table_orm_writes(session, flush_context, instances) -> None:
    """Regression for review findings "Cover unit-of-work writes without
    mapped objects", "Inspect relationship-generated writes in the ORM
    guard", and "Block cascades through unloaded secondary relationships":
    the original guard inspected only each changed object's own `__table__`,
    which misses two real unit-of-work write paths — (1) a
    joined-table-inheritance (or any multi-table) mapper, whose flush writes
    every table in `mapper.tables`, not just the object's own local table;
    and (2) a `relationship(..., secondary=...)` collection, whose flush
    emits association-table INSERT/DELETE against `secondary` while neither
    endpoint object's own `__table__` is that secondary table. A later
    refinement of (2) also covers deleting the relationship's owning object
    itself: when that object is loaded without its collection (or with it
    loaded but untouched) and then deleted, SQLAlchemy still emits a DELETE
    against the secondary table for every existing association row, driven
    by current membership rather than by any local Python-side mutation —
    `get_history(..., passive=PASSIVE_NO_FETCH)` reports no `added`/
    `deleted` history in that case, so a deleted object's relationship must
    be force-fetched (`PASSIVE_OFF`) and its `unchanged` membership treated
    as relevant too. Case 10 below is the adversarial regression for all
    three paths.
    """
    from sqlalchemy.orm import class_mapper
    from sqlalchemy.orm.attributes import get_history
    from sqlalchemy.orm.base import PASSIVE_NO_FETCH, PASSIVE_OFF

    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        mapper = class_mapper(type(obj))
        for table in mapper.tables:
            if table.name in TRIGGER_PROTECTED_TABLES:
                raise TriggerProtectedTableORMGuard(
                    f"ORM session flush blocked before any SQL was emitted: "
                    f"{table.name!r} is a trigger-protected table mapped by "
                    f"{type(obj).__name__} — use Core statements only"
                )
        is_deleted = obj in session.deleted
        for prop in mapper.relationships:
            secondary = prop.secondary
            secondary_name = getattr(secondary, "name", None)
            if secondary_name is None or secondary_name not in TRIGGER_PROTECTED_TABLES:
                continue
            # Deleting the owning object can cascade-delete secondary-table
            # rows even when the collection was never mutated (or never
            # loaded) in Python, so force a fetch and also treat existing
            # (`unchanged`) membership as relevant in that case — not just
            # `added`/`deleted` history, which only reflects local mutation.
            passive = PASSIVE_OFF if is_deleted else PASSIVE_NO_FETCH
            history = get_history(obj, prop.key, passive=passive)
            relevant = history.added or history.deleted
            if is_deleted:
                relevant = relevant or history.unchanged
            if relevant:
                raise TriggerProtectedTableORMGuard(
                    f"ORM session flush blocked before any SQL was emitted: "
                    f"relationship {type(obj).__name__}.{prop.key} writes "
                    f"through secondary table {secondary_name!r}, a "
                    f"trigger-protected table — use Core statements only"
                )


def case_8_core_only_guard_blocks_every_orm_session_construction_path(engine) -> None:
    _log(
        "Case 8: architectural Core-only guard — does a `before_flush` guard "
        "registered on the ORM `Session` class block every permitted session "
        "construction path *before* any SQL reaches the trigger, while Core "
        "access keeps working unmodified?"
    )
    from sqlalchemy.orm import Session as SASession

    event.listen(SASession, "before_flush", _reject_protected_table_orm_writes)
    try:
        # Path 1: sessionmaker()-produced session — the construction path
        # every other case in this file uses.
        Session = sessionmaker(bind=engine)
        session = Session()
        obj = RealOrderORM(
            order_id="ord-7a", approval_id="appr-1", payload_hash="h", created_at="t", status="pending"
        )
        session.add(obj)
        try:
            session.flush()
            print("FAIL: sessionmaker()-constructed session was not blocked by the guard")
        except TriggerProtectedTableORMGuard as exc:
            print(f"PASS: sessionmaker() session blocked pre-SQL by TriggerProtectedTableORMGuard — {exc}")
        except IntegrityError:
            print(
                "FAIL: the guard did not fire before the trigger — the trigger caught it "
                "instead, so the guard itself is not proven to act pre-SQL"
            )
        session.close()

        # Path 2: Session(bind=...) constructed directly, with no
        # sessionmaker in between — a distinct, equally permitted
        # construction path that must go through the same class-level event.
        session2 = SASession(bind=engine)
        obj2 = CashLedgerORM(
            book_id="book-3", ledger_entry_id="le-guard", event_type="DEPOSIT", amount_usd="1.00",
            event_timestamp="t", idempotency_key="kguard", created_at="t",
        )
        session2.add(obj2)
        try:
            session2.flush()
            print("FAIL: directly-constructed Session(bind=...) was not blocked by the guard")
        except TriggerProtectedTableORMGuard as exc:
            print(f"PASS: Session(bind=...) session blocked pre-SQL by TriggerProtectedTableORMGuard — {exc}")
        except IntegrityError:
            print(
                "FAIL: the guard did not fire before the trigger — the trigger caught it "
                "instead, so the guard itself is not proven to act pre-SQL"
            )
        session2.close()

        # Core access must still work unmodified with the guard installed —
        # the guard only listens on the ORM Session class, never on Core
        # connections/engines.
        with engine.begin() as conn:
            conn.execute(text("INSERT OR IGNORE INTO paper_books (book_id) VALUES ('book-guard')"))
            conn.execute(
                text(
                    "INSERT INTO paper_book_cash_ledger (book_id, ledger_entry_id, event_type, "
                    "amount_usd, event_timestamp, idempotency_key, created_at) VALUES "
                    "('book-guard', 'le-core-with-guard', 'DEPOSIT', '5.00', 't', 'kcore', 't')"
                )
            )
        with engine.connect() as check_conn:
            row = check_conn.execute(
                text(
                    "SELECT COUNT(*) FROM paper_book_cash_ledger WHERE "
                    "ledger_entry_id='le-core-with-guard'"
                )
            ).scalar()
        if row == 1:
            print(
                "PASS: Core statements still work with the guard installed — the guard "
                "is ORM-session-flush-only and never intercepts Core"
            )
        else:
            print(f"FAIL: Core insert did not persist with the guard installed (row count={row})")
    finally:
        event.remove(SASession, "before_flush", _reject_protected_table_orm_writes)


def case_9_core_only_guard_covers_every_discovered_protected_table() -> None:
    """Regression for review finding: "Enforce Core-only access for every
    trigger-protected table". Cases 1-8 only ever exercise the guard against
    `real_orders`/`paper_book_cash_ledger` — the two tables with real DDL
    reproduced in this file. This case proves the guard's *coverage*, not
    its blocking mechanism (already proven pre-SQL by case 8): every one of
    the `TRIGGER_PROTECTED_TABLES` derived from the real production schema
    (currently 50 tables, not 2) is rejected by
    `_reject_protected_table_orm_writes` before any SQL is emitted. Uses a
    disposable, single-column synthetic table per discovered name — full
    production DDL/triggers for 50 tables is out of scope here and
    unnecessary, since the guard inspects each mapper's tables (and any
    relationship `secondary` table, per case 10) and fires before any SQL
    reaches a real trigger.
    """
    _log(
        "Case 9: does the Core-only guard block ORM writes against *every* "
        "trigger-protected table discovered from the production schema, not "
        "just the two representative tables cases 1-8 exercise?"
    )
    print(
        f"    discovered {len(TRIGGER_PROTECTED_TABLES)} trigger-protected "
        f"tables from src/trading_research/storage/*_schema.py"
    )
    from sqlalchemy.orm import Session as SASession
    from sqlalchemy.pool import StaticPool

    synthetic_engine = create_engine("sqlite:///:memory:", future=True, poolclass=StaticPool)
    synthetic_metadata = MetaData()
    synthetic_tables = {
        name: Table(name, synthetic_metadata, Column("id", Integer, primary_key=True))
        for name in sorted(TRIGGER_PROTECTED_TABLES)
    }
    synthetic_metadata.create_all(synthetic_engine)

    synthetic_registry = registry()
    mapped_classes = {}
    for name, table in synthetic_tables.items():
        mapped_cls = type(f"Synthetic_{name}", (), {})
        synthetic_registry.map_imperatively(mapped_cls, table)
        mapped_classes[name] = mapped_cls

    event.listen(SASession, "before_flush", _reject_protected_table_orm_writes)
    blocked: list[str] = []
    not_blocked: list[str] = []
    try:
        for name, mapped_cls in mapped_classes.items():
            session = SASession(bind=synthetic_engine)
            session.add(mapped_cls())
            try:
                session.flush()
                not_blocked.append(name)
            except TriggerProtectedTableORMGuard:
                blocked.append(name)
            except Exception as exc:  # pragma: no cover - diagnostic only
                not_blocked.append(f"{name} ({type(exc).__name__})")
            session.close()
    finally:
        event.remove(SASession, "before_flush", _reject_protected_table_orm_writes)

    if not_blocked:
        print(
            f"FAIL: guard did not block {len(not_blocked)} discovered "
            f"protected table(s): {sorted(not_blocked)}"
        )
    else:
        previously_omitted = [
            "paper_book_fills",
            "research_attempts",
            "research_attempt_failures",
            "research_cycle_provider_provenance_links",
        ]
        assert all(name in blocked for name in previously_omitted)
        print(
            f"PASS: guard blocked ORM writes pre-SQL for all {len(blocked)} "
            f"discovered trigger-protected tables, including the tables the "
            f"prior 2-table allowlist omitted: {previously_omitted}"
        )


def case_10_guard_covers_relationship_secondary_and_multi_table_mapper() -> None:
    """Adversarial regression for review findings "Cover unit-of-work writes
    without mapped objects", "Inspect relationship-generated writes in the
    ORM guard", and "Block cascades through unloaded secondary
    relationships": cases 8-9 only ever add directly mapped objects whose own
    `__table__` is the protected table, so they could not expose the
    unit-of-work write paths a single-`__table__` check misses:

    (a) a `relationship(..., secondary=protected_table)` collection — a
        many-to-many mutation dirties only the unprotected parent/child
        endpoint objects, and the flush emits association-table INSERT/
        DELETE with no corresponding mapped object in `session.new`/
        `dirty`/`deleted` whose own `__table__` is the secondary table;
    (b) a joined-table-inheritance mapper spanning more than one table —
        the object's own `__table__` is its most-derived local table, but
        the flush also writes every ancestor table in `mapper.tables`;
    (c) deleting a relationship's owning object without its many-to-many
        collection ever loaded into the session — `get_history(...,
        passive=PASSIVE_NO_FETCH)` reports no local `added`/`deleted`
        mutation, yet SQLAlchemy's unit-of-work still emits a DELETE
        against the secondary table for every existing association row
        once the parent is deleted.

    Uses `research_cycle_provider_provenance_links` — the exact
    association table this finding's evidence cited as a discovered
    protected table with no directly mapped class — as the `secondary=`
    table, and a disposable synthetic base/child pair for the
    joined-table-inheritance case.
    """
    _log(
        "Case 10: does the Core-only guard block ORM writes that reach a "
        "protected table only through a relationship's secondary table, "
        "only through an ancestor table in a multi-table mapper, or only "
        "through deleting the relationship owner without its collection "
        "ever loaded — not through the changed object's own __table__?"
    )
    from sqlalchemy.orm import Session as SASession, relationship
    from sqlalchemy.pool import StaticPool

    secondary_protected_name = "research_cycle_provider_provenance_links"
    assert secondary_protected_name in TRIGGER_PROTECTED_TABLES, (
        "discovery regressed: lost the table this case's secondary= adversarial probe needs"
    )
    base_protected_name = sorted(TRIGGER_PROTECTED_TABLES - {secondary_protected_name})[0]

    synthetic_engine = create_engine("sqlite:///:memory:", future=True, poolclass=StaticPool)
    metadata = MetaData()
    parent_t = Table("scratch_m2m_parent", metadata, Column("id", Integer, primary_key=True))
    child_t = Table("scratch_m2m_child", metadata, Column("id", Integer, primary_key=True))
    secondary_t = Table(
        secondary_protected_name,
        metadata,
        Column("parent_id", Integer, ForeignKey("scratch_m2m_parent.id"), primary_key=True),
        Column("child_id", Integer, ForeignKey("scratch_m2m_child.id"), primary_key=True),
    )
    base_t = Table(
        base_protected_name, metadata, Column("id", Integer, primary_key=True), Column("type", String)
    )
    derived_t = Table(
        "scratch_joined_child_unprotected",
        metadata,
        Column("id", Integer, ForeignKey(f"{base_protected_name}.id"), primary_key=True),
        Column("extra", String),
    )
    metadata.create_all(synthetic_engine)

    class Base10(DeclarativeBase):
        pass

    class ParentORM(Base10):
        __table__ = parent_t
        children = relationship("ChildORM", secondary=secondary_t)

    class ChildORM(Base10):
        __table__ = child_t

    class BaseORM(Base10):
        __table__ = base_t
        __mapper_args__ = {"polymorphic_identity": "base", "polymorphic_on": "type"}

    class DerivedORM(BaseORM):
        __table__ = derived_t
        __mapper_args__ = {"polymorphic_identity": "derived"}

    # (c) setup: a parent/child pair linked through the protected secondary
    # table, committed so a later session can load the parent fresh without
    # its collection.
    setup_session = SASession(bind=synthetic_engine)
    setup_parent = ParentORM(id=2)
    setup_child = ChildORM(id=2)
    setup_session.add_all([setup_parent, setup_child])
    setup_session.flush()
    setup_parent.children.append(setup_child)
    setup_session.flush()
    setup_session.commit()
    setup_session.close()

    event.listen(SASession, "before_flush", _reject_protected_table_orm_writes)
    try:
        # (a) relationship secondary write, no directly mapped object for
        # the protected association table itself.
        session = SASession(bind=synthetic_engine)
        parent = ParentORM(id=1)
        child = ChildORM(id=1)
        session.add_all([parent, child])
        session.flush()  # establish both endpoint rows; collection still empty
        parent.children.append(child)  # dirties only ParentORM/ChildORM, not the secondary table
        try:
            session.flush()
            print(
                "FAIL: relationship secondary write to a protected association "
                "table was NOT blocked pre-SQL (bypass substantiated)"
            )
        except TriggerProtectedTableORMGuard as exc:
            print(f"PASS: relationship secondary write blocked pre-SQL — {exc}")
        session.rollback()
        session.close()

        # (b) multi-table (joined-table-inheritance) mapper write, where the
        # changed object's own __table__ is the unprotected derived table.
        session2 = SASession(bind=synthetic_engine)
        derived = DerivedORM(id=1, extra="x")
        assert derived.__table__.name not in TRIGGER_PROTECTED_TABLES, (
            "test setup bug: the object's own __table__ must NOT be the "
            "protected table, or this case cannot distinguish the fix from "
            "the original single-__table__ check"
        )
        session2.add(derived)
        try:
            session2.flush()
            print(
                "FAIL: joined-table-inheritance write to an ancestor protected "
                "table was NOT blocked pre-SQL (bypass substantiated)"
            )
        except TriggerProtectedTableORMGuard as exc:
            print(f"PASS: multi-table mapper write blocked pre-SQL — {exc}")
        session2.rollback()
        session2.close()

        # (c) delete the relationship owner without ever loading its m2m
        # collection into this session — the unit-of-work still needs to
        # remove the existing secondary-table row(s) for the deleted parent.
        session3 = SASession(bind=synthetic_engine)
        parent_to_delete = session3.get(ParentORM, 2)
        assert "children" not in parent_to_delete.__dict__, (
            "test setup bug: the collection must still be unloaded at delete "
            "time, or this case cannot distinguish the fix from a history-"
            "only check"
        )
        session3.delete(parent_to_delete)
        try:
            session3.flush()
            print(
                "FAIL: deleting a relationship owner with an unloaded "
                "many-to-many collection was NOT blocked pre-SQL — the "
                "secondary-table cascade bypasses the guard (bypass "
                "substantiated)"
            )
        except TriggerProtectedTableORMGuard as exc:
            print(f"PASS: unloaded-delete cascade blocked pre-SQL — {exc}")
        session3.rollback()
        session3.close()
    finally:
        event.remove(SASession, "before_flush", _reject_protected_table_orm_writes)


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
    case_7_orm_update_existing_row_masking(engine)
    case_8_core_only_guard_blocks_every_orm_session_construction_path(engine)
    case_9_core_only_guard_covers_every_discovered_protected_table()
    case_10_guard_covers_relationship_secondary_and_multi_table_mapper()
    print("\nDone.")
