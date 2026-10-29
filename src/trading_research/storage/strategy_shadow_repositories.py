"""Append-only persistence for `strategy_shadow_outcomes` (docs/paper-testing-plan.md Phase 2)."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields


class ShadowOutcomeConflictError(RuntimeError):
    """The same outcome ID is already stored with different content."""


@dataclass(frozen=True)
class ShadowOutcomeRecord:
    outcome_id: str
    strategy_evaluation_id: str
    signal_content_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    signal_session: str
    as_of_session: str
    row_kind: str
    state: str
    reject_reason: str | None
    entry_session: str | None
    entry_price: str | None
    exit_session: str | None
    exit_price: str | None
    exit_reason: str | None
    last_close: str | None
    holding_sessions: int | None
    net_pnl_per_share: str | None
    return_fraction: str | None
    slippage_bps: str
    fee_per_order: str
    snapshot_hash: str
    record_hash: str


_COLUMNS = tuple(f.name for f in fields(ShadowOutcomeRecord)) + ("recorded_at",)
_INSERT = (
    f"INSERT OR IGNORE INTO strategy_shadow_outcomes ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


def insert_shadow_outcome(conn: sqlite3.Connection, record: ShadowOutcomeRecord, *, recorded_at: str) -> bool:
    """Store `record`; True if added, False if the identical record already exists.

    Raises if the outcome ID exists with different content. Does not open a
    transaction; callers batching several records wrap them in `transaction`."""
    values = {**asdict(record), "recorded_at": recorded_at}
    cursor = conn.execute(_INSERT, tuple(values[name] for name in _COLUMNS))
    if cursor.rowcount == 1:
        return True
    stored = conn.execute(
        "SELECT record_hash FROM strategy_shadow_outcomes WHERE outcome_id = ?", (record.outcome_id,),
    ).fetchone()
    if stored is None or stored["record_hash"] != record.record_hash:
        raise ShadowOutcomeConflictError(f"{record.outcome_id} is already recorded with different content")
    return False


def list_unresolved_eligible_signals(
    conn: sqlite3.Connection, *, as_of_session: str, strategy_id: str | None = None,
) -> list[sqlite3.Row]:
    """ELIGIBLE signals evaluated on or before `as_of_session` with no TERMINAL outcome yet."""
    params: list = [as_of_session]
    strategy_clause = ""
    if strategy_id is not None:
        strategy_clause = " AND s.strategy_id = ?"
        params.append(strategy_id)
    return conn.execute(
        "SELECT s.* FROM strategy_signals s WHERE s.status = 'ELIGIBLE' AND s.session_date <= ?"
        f"{strategy_clause} AND NOT EXISTS (SELECT 1 FROM strategy_shadow_outcomes o "
        "WHERE o.strategy_evaluation_id = s.strategy_evaluation_id AND o.row_kind = 'TERMINAL') "
        "ORDER BY s.session_date, s.strategy_id, s.symbol, s.strategy_evaluation_id",
        params,
    ).fetchall()


def list_terminal_outcomes(conn: sqlite3.Connection, *, strategy_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM strategy_shadow_outcomes WHERE strategy_id = ? AND row_kind = 'TERMINAL' "
        "ORDER BY as_of_session, exit_session, strategy_evaluation_id",
        (strategy_id,),
    ).fetchall()
