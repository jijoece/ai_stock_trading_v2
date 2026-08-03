"""Maps LumiBot's `Order.OrderStatus` values to this project's internal
`PaperExecutionEvent.event_type` (docs/milestone-3.md Step 5).

Only imports LumiBot for the `OrderStatus` enum reference in the module
docstring/tests — `map_order_status` itself takes a plain string, so it can
be unit-tested (including the fail-closed branch) without LumiBot installed.

LumiBot 4.5.74's `Order.OrderStatus` enum (confirmed via
`[s.value for s in Order.OrderStatus]` in this environment):
unprocessed, submitted, open, new, cancelling, canceled, fill, partial_fill,
cash_settled, assigned, exercised, error, expired, unknown.

Only equity, long-only, market/limit statuses relevant to this milestone are
mapped. `cash_settled`, `assigned`, `exercised` are options-settlement
concepts (options are an explicit non-goal, docs/milestone-3.md "Non-goals")
and `unknown` is LumiBot's own escape hatch — all three, and anything not
in `_STATUS_MAP`, raise `UnknownLumiBotStatusError` rather than being
silently mapped. Note LumiBot 4.5.74 has no distinct "rejected" status in
this enum; `error` is the closest available raw status and is mapped to our
internal `ERROR` (not `REJECTED` — `REJECTED` is reachable only through the
deterministic test adapter, which can script it directly). See
docs/milestone3-lumibot-paper-integration.md "Known limitations".
"""
from __future__ import annotations

from ...execution.models import EVENT_TYPES
from ..normalization import NORMALIZED_ORDER_STATUSES
from .errors import UnknownLumiBotStatusError

_STATUS_MAP: dict[str, str] = {
    "unprocessed": "SUBMITTED",
    "submitted": "SUBMITTED",
    "new": "ACCEPTED",
    "open": "ACCEPTED",
    "partial_fill": "PARTIALLY_FILLED",
    "fill": "FILLED",
    "canceled": "CANCELLED",
    "cancelling": "CANCELLED",
    "expired": "CANCELLED",
    "error": "ERROR",
}

# PR 9: the two boundaries in this repository do not share one vocabulary —
# they share one *contract*, at two conformance levels, and that relationship
# is now enforced at import time instead of being coincidental.
#
# `NORMALIZED_ORDER_STATUSES` (runtime/normalization.py) is the full closed
# set for the ADR 0002/0009 process boundary. This in-process ADR 0001
# adapter emits `execution.models.EVENT_TYPES`, a strict *subset* of it: a
# `PaperExecutionEvent` has no `EXPIRED`, `CANCEL_REQUESTED`,
# `PENDING_SUBMISSION` or `SUBMISSION_UNKNOWN`, because `adapter.submit()`
# is synchronous and always returns a resolved outcome
# (docs/milestone-3.md Step 5). That is why LumiBot's `expired` maps to
# `CANCELLED` here while the runtime gateway maps Alpaca's `expired` to
# `EXPIRED` — the same broker concept, expressed at the conformance level
# each boundary supports. The difference is deliberate; what was missing
# before PR 9 was anything asserting it stayed deliberate.
if not set(_STATUS_MAP.values()) <= set(EVENT_TYPES):  # pragma: no cover - import-time guard
    raise UnknownLumiBotStatusError(
        "_STATUS_MAP maps a LumiBot status to a value outside execution.models.EVENT_TYPES: "
        f"{sorted(set(_STATUS_MAP.values()) - set(EVENT_TYPES))}"
    )
if not set(EVENT_TYPES) <= set(NORMALIZED_ORDER_STATUSES):  # pragma: no cover - import-time guard
    raise UnknownLumiBotStatusError(
        "execution.models.EVENT_TYPES must be a subset of the runtime normalization contract: "
        f"{sorted(set(EVENT_TYPES) - set(NORMALIZED_ORDER_STATUSES))}"
    )


def map_order_status(raw_status: str) -> str:
    """Return the internal `event_type` for a raw LumiBot order status.

    Raises `UnknownLumiBotStatusError` for anything not explicitly mapped —
    fail closed, never a best-guess default.
    """
    key = str(raw_status).strip().lower()
    if key not in _STATUS_MAP:
        raise UnknownLumiBotStatusError(
            f"unrecognized LumiBot order status {raw_status!r} — fail closed, requires reconciliation"
        )
    return _STATUS_MAP[key]
