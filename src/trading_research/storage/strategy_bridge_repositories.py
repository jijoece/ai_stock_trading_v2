"""Persistence for `paper_book_strategy_links` (docs/paper-testing-plan.md Phase 3)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime

from ..strategies.controls import StrategyControls


@dataclass(frozen=True)
class StrategyLink:
    book_id: str
    paper_order_intent_id: str
    recommendation_id: str
    strategy_id: str
    strategy_signal_id: str
    strategy_evaluation_id: str
    entry_condition: str
    invalidation_condition: str
    strategy_stop: str
    strategy_target: str | None
    expected_holding_period: int


_COLUMNS = tuple(f.name for f in fields(StrategyLink)) + ("created_at",)


def insert_strategy_link(conn: sqlite3.Connection, link: StrategyLink, *, created_at: str) -> bool:
    """True if added, False if this exact link already exists. Raises if the
    intent or signal is already linked to something different. Does not open a
    transaction: the caller persists the intent and its link together."""
    values = {**asdict(link), "created_at": created_at}
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO paper_book_strategy_links ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
        tuple(values[name] for name in _COLUMNS),
    )
    if cursor.rowcount == 1:
        return True
    stored = get_strategy_link(conn, link.book_id, link.paper_order_intent_id)
    if stored != link:
        raise ValueError(f"{link.paper_order_intent_id} is already linked to different strategy content")
    return False


def _row_to_link(row: sqlite3.Row | None) -> StrategyLink | None:
    if row is None:
        return None
    return StrategyLink(**{f.name: row[f.name] for f in fields(StrategyLink)})


def get_strategy_link(conn: sqlite3.Connection, book_id: str, paper_order_intent_id: str) -> StrategyLink | None:
    return _row_to_link(conn.execute(
        "SELECT * FROM paper_book_strategy_links WHERE book_id = ? AND paper_order_intent_id = ?",
        (book_id, paper_order_intent_id),
    ).fetchone())


def get_link_for_signal(conn: sqlite3.Connection, book_id: str, strategy_signal_id: str) -> StrategyLink | None:
    return _row_to_link(conn.execute(
        "SELECT * FROM paper_book_strategy_links WHERE book_id = ? AND strategy_signal_id = ?",
        (book_id, strategy_signal_id),
    ).fetchone())


def latest_linked_data_as_of(
    conn: sqlite3.Connection, book_id: str, strategy_id: str, symbol: str,
) -> datetime | None:
    """The newest `data_as_of` among signals of this strategy and symbol the book has already acted on.

    A signal older than this is out of order: a later one for the same strategy and symbol was already
    turned into an intent, so acting on the older one would reverse the book's own chronology."""
    rows = conn.execute(
        "SELECT s.data_as_of FROM paper_book_strategy_links k "
        "JOIN strategy_signals s ON s.strategy_evaluation_id = k.strategy_evaluation_id "
        "WHERE k.book_id = ? AND k.strategy_id = ? AND s.symbol = ? AND s.data_as_of IS NOT NULL",
        (book_id, strategy_id, symbol),
    ).fetchall()
    return max((datetime.fromisoformat(row["data_as_of"]) for row in rows), default=None)


def exclusive_link_for_open_position(conn: sqlite3.Connection, book_id: str, symbol: str) -> StrategyLink | None:
    """The one strategy link behind *every* open lot of the symbol, else None.

    Exits are evaluated against the symbol-level position, so a signal's own
    stop, target and holding period may only govern it when that signal opened
    all of its open quantity. A symbol with an unlinked lot (e.g. from the
    generic paper workflow) or lots from different signals has mixed
    provenance and returns None, so the generic exit rules apply to the whole
    position rather than one signal's rules liquidating another's quantity."""
    rows = conn.execute(
        "SELECT k.* FROM paper_book_position_lots l "
        "JOIN paper_book_fills f ON f.book_id = l.book_id AND f.fill_id = l.opening_fill_id "
        "LEFT JOIN paper_book_strategy_links k ON k.book_id = f.book_id AND k.paper_order_intent_id = f.paper_order_intent_id "
        "WHERE l.book_id = ? AND l.symbol = ? AND l.remaining_quantity > '0'",
        (book_id, symbol),
    ).fetchall()
    if not rows:
        return None
    links = {row["paper_order_intent_id"] for row in rows}
    if len(links) != 1 or None in links:
        return None
    return _row_to_link(rows[0])


def link_for_open_position(conn: sqlite3.Connection, book_id: str, symbol: str) -> StrategyLink | None:
    """The strategy link behind a symbol's oldest open lot (via lot -> opening fill -> intent), if any."""
    return _row_to_link(conn.execute(
        "SELECT k.* FROM paper_book_position_lots l "
        "JOIN paper_book_fills f ON f.book_id = l.book_id AND f.fill_id = l.opening_fill_id "
        "JOIN paper_book_strategy_links k ON k.book_id = f.book_id AND k.paper_order_intent_id = f.paper_order_intent_id "
        "WHERE l.book_id = ? AND l.symbol = ? AND l.remaining_quantity > '0' "
        "ORDER BY l.opened_at, l.lot_id LIMIT 1",
        (book_id, symbol),
    ).fetchone())


def save_decision_controls(
    conn: sqlite3.Connection, *, risk_decision_id: str, book_id: str, controls: StrategyControls, created_at: str,
) -> bool:
    """Record the controls a risk decision was evaluated under. True if added, False if the identical
    record exists; raises if the decision is already recorded under different controls. Does not open a
    transaction: the caller persists it with the decision it describes."""
    record = json.dumps(controls.audit_record(), sort_keys=True)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO paper_book_decision_strategy_controls "
        "(risk_decision_id, book_id, strategy_id, controls_hash, controls_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (risk_decision_id, book_id, controls.strategy_id, controls.controls_hash, record, created_at),
    )
    if cursor.rowcount == 1:
        return True
    stored = get_decision_controls(conn, risk_decision_id)
    if stored is None or stored["controls_hash"] != controls.controls_hash:
        raise ValueError(f"{risk_decision_id} is already recorded under different strategy controls")
    return False


def get_decision_controls(conn: sqlite3.Connection, risk_decision_id: str) -> dict | None:
    """The stored controls for a risk decision: strategy id, hash, and the full settings as `controls`."""
    row = conn.execute(
        "SELECT * FROM paper_book_decision_strategy_controls WHERE risk_decision_id = ?", (risk_decision_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["controls"] = json.loads(result.pop("controls_json"))
    return result
