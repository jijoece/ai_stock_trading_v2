"""SQLite schema for shadow tracking of strategy signals (docs/paper-testing-plan.md Phase 2).

`strategy_shadow_outcomes` holds hypothetical outcomes only: no order, fill or
position exists anywhere else. One PROGRESS row per (signal, update session)
while a signal is unresolved and at most one TERMINAL row per signal. Append-only.
"""
from __future__ import annotations

STRATEGY_SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS strategy_shadow_outcomes (
    outcome_id TEXT PRIMARY KEY,
    strategy_evaluation_id TEXT NOT NULL REFERENCES strategy_signals(strategy_evaluation_id),
    signal_content_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_session TEXT NOT NULL,
    as_of_session TEXT NOT NULL,
    row_kind TEXT NOT NULL CHECK (row_kind IN ('PROGRESS', 'TERMINAL')),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'OPEN', 'NO_FILL', 'REJECTED', 'CLOSED')),
    reject_reason TEXT,
    entry_session TEXT,
    entry_price TEXT,
    exit_session TEXT,
    exit_price TEXT,
    exit_reason TEXT,
    last_close TEXT,
    holding_sessions INTEGER,
    net_pnl_per_share TEXT,
    return_fraction TEXT,
    slippage_bps TEXT NOT NULL,
    fee_per_order TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK ((row_kind = 'TERMINAL') = (state IN ('NO_FILL', 'REJECTED', 'CLOSED')))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_shadow_one_terminal
    ON strategy_shadow_outcomes(strategy_evaluation_id) WHERE row_kind = 'TERMINAL';
CREATE INDEX IF NOT EXISTS idx_strategy_shadow_strategy
    ON strategy_shadow_outcomes(strategy_id, row_kind, as_of_session);

CREATE TRIGGER IF NOT EXISTS trg_strategy_shadow_outcomes_no_update
BEFORE UPDATE ON strategy_shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'strategy_shadow_outcomes is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_strategy_shadow_outcomes_no_delete
BEFORE DELETE ON strategy_shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'strategy_shadow_outcomes is append-only'); END;
"""


def apply_strategy_shadow_schema(conn) -> None:
    conn.executescript(STRATEGY_SHADOW_DDL)
