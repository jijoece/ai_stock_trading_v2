"""SQLite schema for persisted strategy evaluations (docs/paper-testing-plan.md Phase 1).

`strategy_signals` holds one row per strategy evaluation of one symbol on one
session, whatever its status, so forward behaviour can be audited against the
backtest. It is append-only: triggers reject every UPDATE and DELETE. Applied
idempotently on every `connect()`, like the other `apply_*_schema` scripts.
"""
from __future__ import annotations

STRATEGY_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_signals (
    strategy_evaluation_id TEXT PRIMARY KEY,
    signal_content_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    signal_timestamp TEXT NOT NULL,
    data_as_of TEXT,
    status TEXT NOT NULL,
    signal_strength REAL NOT NULL,
    entry_reference TEXT,
    limit_reference TEXT,
    invalidation_price TEXT,
    initial_stop_reference TEXT,
    target_reference TEXT,
    expected_holding_period INTEGER,
    reason_codes_json TEXT NOT NULL,
    factor_values_json TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    backtest_screening_passed INTEGER NOT NULL CHECK (backtest_screening_passed IN (0, 1)),
    backtest_screening_json TEXT NOT NULL,
    production_screening_status TEXT NOT NULL
        CHECK (production_screening_status IN ('NOT_EVALUATED', 'PASSED', 'FAILED')),
    production_screening_json TEXT,
    data_provider TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    screening_profile_hash TEXT NOT NULL,
    relative_strength_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_signals_session
    ON strategy_signals(strategy_id, session_date, status);
CREATE INDEX IF NOT EXISTS idx_strategy_signals_content
    ON strategy_signals(signal_content_id);

CREATE TRIGGER IF NOT EXISTS trg_strategy_signals_no_update
BEFORE UPDATE ON strategy_signals
BEGIN SELECT RAISE(ABORT, 'strategy_signals is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_strategy_signals_no_delete
BEFORE DELETE ON strategy_signals
BEGIN SELECT RAISE(ABORT, 'strategy_signals is append-only'); END;
"""


def apply_strategy_signals_schema(conn) -> None:
    conn.executescript(STRATEGY_SIGNALS_DDL)
