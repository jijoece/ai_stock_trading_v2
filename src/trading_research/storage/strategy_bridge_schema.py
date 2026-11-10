"""SQLite schema linking paper order intents to the strategy signal that produced them
(docs/paper-testing-plan.md Phase 3, decision P2).

`paper_book_strategy_links` is the strategy recommendation record: it carries
the `StrategyOrderIntentContext` fields, the signal's own exit levels, and the
identifiers that let a fill or open position be traced back to its signal. One
row per (book, intent); a signal can seed at most one intent per book.
Append-only.
"""
from __future__ import annotations

STRATEGY_BRIDGE_DDL = """
CREATE TABLE IF NOT EXISTS paper_book_strategy_links (
    book_id TEXT NOT NULL,
    paper_order_intent_id TEXT NOT NULL,
    recommendation_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_signal_id TEXT NOT NULL,
    strategy_evaluation_id TEXT NOT NULL REFERENCES strategy_signals(strategy_evaluation_id),
    entry_condition TEXT NOT NULL,
    invalidation_condition TEXT NOT NULL,
    strategy_stop TEXT NOT NULL,
    strategy_target TEXT,
    expected_holding_period INTEGER NOT NULL CHECK (expected_holding_period > 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (book_id, paper_order_intent_id),
    FOREIGN KEY (book_id, paper_order_intent_id) REFERENCES paper_book_orders(book_id, paper_order_intent_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_book_strategy_links_signal
    ON paper_book_strategy_links(book_id, strategy_signal_id);
CREATE INDEX IF NOT EXISTS idx_paper_book_strategy_links_strategy
    ON paper_book_strategy_links(strategy_id, book_id);

CREATE TRIGGER IF NOT EXISTS trg_paper_book_strategy_links_no_update
BEFORE UPDATE ON paper_book_strategy_links
BEGIN SELECT RAISE(ABORT, 'paper_book_strategy_links is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_paper_book_strategy_links_no_delete
BEFORE DELETE ON paper_book_strategy_links
BEGIN SELECT RAISE(ABORT, 'paper_book_strategy_links is append-only'); END;
"""


def apply_strategy_bridge_schema(conn) -> None:
    conn.executescript(STRATEGY_BRIDGE_DDL)
