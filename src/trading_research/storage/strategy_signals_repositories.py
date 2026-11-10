"""Append-only persistence for `strategy_signals` (docs/paper-testing-plan.md Phase 1)."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields


class StrategySignalConflictError(RuntimeError):
    """The same evaluation ID is already stored with different content."""


@dataclass(frozen=True)
class StrategySignalRecord:
    strategy_evaluation_id: str
    signal_content_id: str
    strategy_id: str
    strategy_version: str
    configuration_hash: str
    symbol: str
    session_date: str
    signal_timestamp: str
    data_as_of: str | None
    status: str
    signal_strength: float
    entry_reference: str | None
    limit_reference: str | None
    invalidation_price: str | None
    initial_stop_reference: str | None
    target_reference: str | None
    expected_holding_period: int | None
    reason_codes_json: str
    factor_values_json: str
    data_quality: str
    backtest_screening_passed: bool
    backtest_screening_json: str
    production_screening_status: str
    production_screening_json: str | None
    data_provider: str
    snapshot_hash: str
    screening_profile_hash: str
    relative_strength_hash: str
    # sha256 over every field above; lets a replay detect a changed re-record.
    record_hash: str


_COLUMNS = tuple(f.name for f in fields(StrategySignalRecord)) + ("recorded_at",)
_INSERT = (
    f"INSERT OR IGNORE INTO strategy_signals ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


def insert_strategy_signal(conn: sqlite3.Connection, record: StrategySignalRecord, *, recorded_at: str) -> bool:
    """Store `record`. Returns True if a row was added, False if the identical
    record was already present. Raises if the evaluation ID exists with
    different content: history is never overwritten. Does not open a
    transaction; callers batching several records wrap them in `transaction`."""
    values = asdict(record)
    values["backtest_screening_passed"] = int(record.backtest_screening_passed)
    values["recorded_at"] = recorded_at
    cursor = conn.execute(_INSERT, tuple(values[name] for name in _COLUMNS))
    if cursor.rowcount == 1:
        return True
    stored = conn.execute(
        "SELECT record_hash FROM strategy_signals WHERE strategy_evaluation_id = ?",
        (record.strategy_evaluation_id,),
    ).fetchone()
    if stored is None or stored["record_hash"] != record.record_hash:
        raise StrategySignalConflictError(
            f"{record.strategy_evaluation_id} is already recorded with different content"
        )
    return False


def list_strategy_signals(
    conn: sqlite3.Connection, *, strategy_id: str | None = None, session_date: str | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    clauses, params = [], []
    for column, value in (("strategy_id", strategy_id), ("session_date", session_date), ("status", status)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT * FROM strategy_signals{where} ORDER BY session_date, strategy_id, symbol, strategy_evaluation_id",
        params,
    ).fetchall()
