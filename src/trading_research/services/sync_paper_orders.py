"""Broker event polling and recovery (docs/milestone-4.md Step 9).

Polls the isolated runtime for the current broker-side state of every
unresolved `paper_broker_submissions` row, normalizes any newly observed
fill into a `PaperExecutionEvent`, applies it to the existing `PaperLedger`
via the Milestone 3 `execution/ledger_events.py` machinery (never a second
ledger implementation), and persists a `PaperExecutionResult` once an
order reaches a terminal state.

Cumulative vs. incremental fills: a broker's `get_order` response reports
*cumulative* filled quantity, but `PaperExecutionEvent.filled_quantity` (and
the ledger's `apply_external_fill`) is defined as the *incremental* amount
for that one fill (see `execution/ledger_events.py`'s docstring). This
module tracks "previously recognized filled quantity" as the sum of
`filled_quantity` across every event already recorded for the intent, and
only applies the delta — so re-polling an unchanged order is a safe no-op
(delta is zero, no new event, nothing re-applied), and a partially-then-
fully filled order produces two correctly incremental events.

Bounded, single-pass, no busy loop: one call processes every currently
unresolved submission once and returns — the CLI/orchestrator is
responsible for any repeated invocation cadence (docs/milestone-4.md Step
9: "Use bounded polling and configurable intervals. Do not create a busy
loop.").
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable

from ..execution.broker_snapshots import TERMINAL_SUBMISSION_STATES
from ..execution.ledger_events import apply_all_new_events
from ..execution.models import EVENT_TYPES, RESULT_STATUSES, PaperExecutionEvent, PaperExecutionResult
from ..paper.ledger import PaperLedger
from ..runtime.client.errors import RuntimeOperationError, RuntimeRequestTimeoutError, RuntimeUnavailableError
from ..runtime.client.process_client import RuntimeClient
from ..runtime.normalization import BROKER_REPORTABLE_STATUSES
from ..storage import execution_repositories as exec_repo

OUTCOME_UPDATED = "UPDATED"
OUTCOME_UNCHANGED = "UNCHANGED"
OUTCOME_STILL_UNKNOWN = "STILL_UNKNOWN"
OUTCOME_RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"

# `BROKER_REPORTABLE_STATUSES` (the runtime normalization contract) is wider
# than `execution.models.EVENT_TYPES`/`RESULT_STATUSES` (the domain event/
# result vocabulary, deliberately kept the narrower, synchronous-adapter-
# compatible set — D8 ruling 3, docs/library-migration/DECISIONS.md: "two
# conformance levels, not two vocabularies"). `EXPIRED` is the one broker
# status that reaches this module but has no matching domain value: it is
# booked as `CANCELLED` for ledger/ledger-event purposes. Nothing is lost —
# the true broker status is preserved verbatim in the submission row's
# `submission_status` (which already carries the full
# BROKER_REPORTABLE_STATUSES vocabulary, PR 9) and in the event's
# `raw_status`. `CANCEL_REQUESTED` needs no entry here: it is nonterminal,
# so `_sync_one` never builds an event/result for it — the order simply
# stays on the unresolved-submissions queue and gets polled again.
_DOMAIN_STATUS_PROJECTION: dict[str, str] = {"EXPIRED": "CANCELLED"}

_unprojected = set(TERMINAL_SUBMISSION_STATES) - set(EVENT_TYPES) - set(_DOMAIN_STATUS_PROJECTION)
if _unprojected:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "sync_paper_orders._DOMAIN_STATUS_PROJECTION must cover every terminal broker status "
        f"absent from execution.models.EVENT_TYPES; missing={sorted(_unprojected)}"
    )
if not set(_DOMAIN_STATUS_PROJECTION.values()) <= (set(EVENT_TYPES) & set(RESULT_STATUSES)):  # pragma: no cover
    raise RuntimeError(
        "sync_paper_orders._DOMAIN_STATUS_PROJECTION must map only to values inside both "
        "EVENT_TYPES and RESULT_STATUSES"
    )
del _unprojected


def _project_domain_status(status: str) -> str:
    """Map a broker-reportable status onto the narrower domain event/result
    vocabulary. A no-op for every status already inside that vocabulary."""
    return _DOMAIN_STATUS_PROJECTION.get(status, status)


@dataclass(frozen=True)
class SyncOutcome:
    intent_id: str
    outcome: str
    submission_status: str | None = None
    new_events: int = 0


def sync_paper_orders(
    *, conn: sqlite3.Connection, ledger: PaperLedger, client: RuntimeClient, clock: Callable[[], datetime],
) -> list[SyncOutcome]:
    now = clock()
    outcomes: list[SyncOutcome] = []
    for submission in exec_repo.list_unresolved_submissions(conn):
        outcomes.append(_sync_one(conn, ledger, client, submission, now))
    return outcomes


def _sync_one(conn: sqlite3.Connection, ledger: PaperLedger, client: RuntimeClient, submission, now: datetime) -> SyncOutcome:
    try:
        current = client.get_order(submission.client_order_id)
    except (RuntimeUnavailableError, RuntimeRequestTimeoutError) as exc:
        exec_repo.record_failure(
            conn, recommendation_id=None, intent_id=submission.intent_id,
            stage="sync_paper_orders", reason=str(exc), now=now,
        )
        return SyncOutcome(submission.intent_id, OUTCOME_RUNTIME_UNAVAILABLE)

    if current is None:
        return SyncOutcome(submission.intent_id, OUTCOME_STILL_UNKNOWN)

    intent = exec_repo.get_intent(conn, submission.intent_id)
    status = current["status"]
    if status not in BROKER_REPORTABLE_STATUSES:
        raise RuntimeOperationError("UNKNOWN_BROKER_STATUS", f"unrecognized runtime order status {status!r}", retryable=False)

    cumulative_filled = int(current["filled_quantity"])
    previously_recognized = sum(e.filled_quantity for e in exec_repo.list_events(conn, submission.intent_id))
    delta = cumulative_filled - previously_recognized

    new_events = 0
    if delta > 0 or status in TERMINAL_SUBMISSION_STATES:
        event = _build_event(intent.intent_id, intent.recommendation_id, intent.symbol, status, delta,
                              cumulative_filled, intent.quantity, current, now)
        was_new = not exec_repo.event_exists(conn, event.event_id)
        apply_all_new_events(conn, ledger, (event,), now=now)
        new_events = 1 if was_new else 0

    exec_repo.update_submission_status(
        conn, intent_id=submission.intent_id, submission_status=status,
        broker_order_id=current.get("broker_order_id"), now=now,
    )

    if status in TERMINAL_SUBMISSION_STATES:
        _finalize_result(conn, intent, status, cumulative_filled, current, now)

    return SyncOutcome(submission.intent_id, OUTCOME_UPDATED, submission_status=status, new_events=new_events)


def _build_event(
    intent_id: str, recommendation_id: str, symbol: str, status: str, delta: int, cumulative_filled: int,
    requested_qty: int, current: dict, now: datetime,
) -> PaperExecutionEvent:
    if delta > 0:
        event_type = "FILLED" if cumulative_filled >= requested_qty else "PARTIALLY_FILLED"
        fill_price = Decimal(current["average_fill_price"]) if current.get("average_fill_price") else None
    else:
        event_type = _project_domain_status(status)
        fill_price = None
    return PaperExecutionEvent(
        event_id=f"{intent_id}-sync-{event_type}-{cumulative_filled}",
        intent_id=intent_id, recommendation_id=recommendation_id, symbol=symbol, event_type=event_type,
        broker_order_id=current.get("broker_order_id"), quantity=requested_qty,
        filled_quantity=max(delta, 0), fill_price=fill_price, occurred_at=now,
        raw_status=current.get("raw_broker_status"),
    )


def _finalize_result(conn: sqlite3.Connection, intent, status: str, filled_qty: int, current: dict, now: datetime) -> None:
    avg_price = Decimal(current["average_fill_price"]) if current.get("average_fill_price") else None
    events = exec_repo.list_events(conn, intent.intent_id)
    result = PaperExecutionResult(
        intent_id=intent.intent_id, recommendation_id=intent.recommendation_id,
        final_status=_project_domain_status(status),
        requested_quantity=intent.quantity, filled_quantity=filled_qty,
        average_fill_price=avg_price if filled_qty > 0 else None, fees=Decimal("0"),
        event_ids=tuple(e.event_id for e in events), completed_at=now,
    )
    exec_repo.save_result(conn, result)
