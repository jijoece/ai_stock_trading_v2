"""Stage 5 local simulated paper cycle for strategy signals (docs/paper-testing-plan.md Phase 3).

One closed session, in this order:

1. the existing paper lifecycle runs against snapshot prices: pending entries
   from earlier sessions are filled or expired, open positions are exited by
   their signal's own stop, target and holding period, and each book is
   snapshotted, reconciled and checked against the daily-loss and drawdown
   breakers;
2. the session's stored ELIGIBLE signals go through the strategy bridge and the
   unchanged risk and intent code, creating pending intents that the next
   session's lifecycle will fill.

Local simulated only. No broker, scheduler or model is involved, and the
BASELINE book is the only book a strategy can reach.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path

from ..backtesting.models import BacktestError
from ..backtesting.strategy_backtest_runner import DATA_SOURCES, acquire_snapshot
from ..evaluation.market_calendar import regular_session_close
from ..paper_books import lifecycle, reconciliation, strategy_bridge
from ..paper_books.config import PaperBooksConfiguration
from ..research import experiment_policy as ep
from ..shadow import pause as pause_mod
from ..storage import paper_books_repositories as pb_repo
from ..strategies.config import ACTIVATION_STAGES, load_strategy_config
from .price_provider import SnapshotPriceProvider

MINIMUM_ACTIVATION_STAGE = "STAGE_5_LOCAL_PAPER_BOOK"

CAVEATS = (
    "Local simulated paper book only; no order reaches any broker. Not financial advice.",
    "Entries fill on a later session at a simulated price near that session's close, which is not the shadow "
    "tracker's next-session limit fill; compare the two before trusting either.",
    "A signal without its own target uses the paper book's generic profit target, where the backtest engine "
    "uses an ATR-based one.",
)


def _unprocessed_rows(conn, *, session: date, strategy_id: str | None) -> list:
    params: list = [session.isoformat()]
    clause = ""
    if strategy_id is not None:
        clause, params = " AND strategy_id = ?", [*params, strategy_id]
    return conn.execute(
        "SELECT * FROM strategy_signals WHERE status = 'ELIGIBLE' AND session_date = ?"
        f"{clause} ORDER BY strategy_id, symbol, strategy_evaluation_id",
        params,
    ).fetchall()


def _book_symbols(conn, book_id: str) -> set[str]:
    held = {p["symbol"] for p in pb_repo.list_positions(conn, book_id, open_only=True)}
    pending = {o["symbol"] for o in pb_repo.list_order_intents(conn, book_id) if o["status"] == "PENDING_SUBMISSION"}
    return held | pending


def _entry_block_reasons(lifecycle_result, book_id: str) -> tuple[str, ...]:
    reasons = [f"lifecycle failure: {reason}" for reason in lifecycle_result.failure_reasons]
    if book_id not in lifecycle_result.books_processed:
        reasons.append(f"book {book_id!r} was not fully processed by the lifecycle")
    status = lifecycle_result.reconciliation_statuses.get(book_id)
    if status != reconciliation.STATUS_MATCHED:
        reasons.append(f"book {book_id!r} reconciliation status is {status!r}, not {reconciliation.STATUS_MATCHED}")
    return tuple(reasons)


def run_strategy_paper_cycle(
    *, as_of: date, data_source: str, snapshot_path: Path | str | None, conn, cfg: PaperBooksConfiguration,
    strategy_id: str | None = None, experiment_policy: str = ep.BASELINE_ONLY, adjustment: str = "split",
    strategy_config_path: Path | str | None = None, env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    if data_source not in DATA_SOURCES:
        raise BacktestError(f"unknown data source {data_source!r}; expected one of {DATA_SOURCES}")
    strategy_config = load_strategy_config(strategy_config_path)
    if ACTIVATION_STAGES.index(strategy_config.activation_stage) < ACTIVATION_STAGES.index(MINIMUM_ACTIVATION_STAGE):
        raise BacktestError(
            f"activation_stage is {strategy_config.activation_stage}; the paper cycle needs {MINIMUM_ACTIVATION_STAGE} "
            "or later. Only the operator moves the stage, by a reviewed change to config/strategies.yaml."
        )
    clock_now = now or datetime.now(timezone.utc)
    try:
        close = regular_session_close(as_of)
    except Exception as exc:
        raise BacktestError(f"{as_of} is not a trading session: {exc}") from exc
    if close > clock_now:
        raise BacktestError(f"session {as_of} has not closed yet (closes {close.isoformat()})")
    state = pause_mod.current_state(conn)
    if state.state != pause_mod.STATE_ACTIVE:
        raise BacktestError(f"shadow pause state is {state.state} ({state.reason}); the paper cycle fails closed")

    book_id = cfg.baseline.book_id
    rows = _unprocessed_rows(conn, session=as_of, strategy_id=strategy_id)
    symbols = tuple(sorted({row["symbol"] for row in rows} | _book_symbols(conn, book_id)))
    snapshot = None
    price_provider = None
    if symbols:
        snapshot = acquire_snapshot(
            data_source=data_source, snapshot_path=snapshot_path, symbols=symbols, start=as_of, end=as_of,
            adjustment=adjustment, env=env, now=clock_now,
        )
        price_provider = SnapshotPriceProvider(snapshot)

    clock = lambda: close  # noqa: E731  every timestamp is the session close, never wall-clock time
    lifecycle_result = lifecycle.run_paper_book_lifecycle(
        conn, as_of=close, paper_books_config=cfg, price_provider=price_provider, clock=clock,
    )
    # Lifecycle-before-entry is a safety boundary: new exposure is only created on top of a lifecycle
    # that processed, persisted and reconciled the BASELINE book without any failure.
    blocked_reasons = _entry_block_reasons(lifecycle_result, book_id)
    outcomes = [] if blocked_reasons else strategy_bridge.create_strategy_entry_intents(
        conn, strategy_rows=rows, as_of=close, cfg=cfg, price_provider=price_provider, clock=clock,
        experiment_policy=experiment_policy,
    )
    counts: dict[str, int] = {}
    for item in outcomes:
        counts[item.outcome] = counts.get(item.outcome, 0) + 1
    report = {
        "as_of": as_of.isoformat(), "activation_stage": strategy_config.activation_stage, "book_id": book_id,
        "experiment_policy": experiment_policy,
        "data": None if snapshot is None else {
            "provider": snapshot.provider, "price_adjustment": snapshot.adjustment, "snapshot_hash": snapshot.content_hash,
        },
        "lifecycle": {
            "lifecycle_run_id": lifecycle_result.lifecycle_run_id,
            "pending_orders_filled": lifecycle_result.pending_orders_filled,
            "pending_orders_expired": lifecycle_result.pending_orders_expired,
            "exit_decisions": list(lifecycle_result.exit_decisions),
            "exit_orders_created": lifecycle_result.exit_orders_created,
            "exit_orders_filled": lifecycle_result.exit_orders_filled,
            "reconciliation_statuses": dict(lifecycle_result.reconciliation_statuses),
            "failure_reasons": list(lifecycle_result.failure_reasons),
        },
        "entries": {
            "signals": len(rows), "blocked": bool(blocked_reasons), "blocked_reasons": list(blocked_reasons),
            "outcome_counts": dict(sorted(counts.items())),
            "outcomes": [
                {"symbol": o.symbol, "strategy_id": o.strategy_id, "strategy_signal_id": o.strategy_signal_id,
                 "outcome": o.outcome, "reasons": list(o.reasons), "risk_decision_id": o.risk_decision_id,
                 "paper_order_intent_id": o.paper_order_intent_id}
                for o in outcomes
            ],
        },
        "caveats": list(CAVEATS),
    }
    if blocked_reasons:
        # A top-level error makes the CLI exit non-zero while the report still shows what the lifecycle did.
        report["error"] = "no entry intents were created: " + "; ".join(blocked_reasons)
    return report
