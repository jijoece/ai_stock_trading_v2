"""Tests `runtime.lumibot.event_mapper.map_order_status` directly against
plain strings — deliberately does NOT require LumiBot to be installed (no
`pytest.importorskip`), since `map_order_status` takes a string, not a
LumiBot object. This keeps the fail-closed status-mapping behavior covered
by the default (169+N) test baseline even when the optional `paper` extra
is absent — only `test_lumibot_adapter.py` (real Order/Asset construction)
needs the importorskip guard.
"""
import pytest

from trading_research.runtime.lumibot.errors import UnknownLumiBotStatusError
from trading_research.runtime.lumibot.event_mapper import map_order_status


@pytest.mark.parametrize(
    "raw_status,expected",
    [
        ("unprocessed", "SUBMITTED"),
        ("submitted", "SUBMITTED"),
        ("new", "ACCEPTED"),
        ("open", "ACCEPTED"),
        ("partial_fill", "PARTIALLY_FILLED"),
        ("fill", "FILLED"),
        ("canceled", "CANCELLED"),
        ("cancelling", "CANCELLED"),
        ("expired", "CANCELLED"),
        ("error", "ERROR"),
        ("FILL", "FILLED"),  # case-insensitive
        ("  fill  ", "FILLED"),  # whitespace-tolerant
    ],
)
def test_known_status_mappings(raw_status, expected):
    assert map_order_status(raw_status) == expected


@pytest.mark.parametrize("raw_status", ["cash_settled", "assigned", "exercised", "unknown", "totally_bogus", ""])
def test_unknown_status_fails_closed(raw_status):
    with pytest.raises(UnknownLumiBotStatusError):
        map_order_status(raw_status)


def test_in_process_vocabulary_is_a_declared_subset_of_the_normalization_contract():
    """PR 9. The two boundaries share one contract at two conformance levels.

    LumiBot's `expired` maps to `CANCELLED` here while the isolated runtime
    gateway maps Alpaca's `expired` to `EXPIRED`, because a
    `PaperExecutionEvent` has no `EXPIRED` — `adapter.submit()` is
    synchronous and always returns a resolved outcome (docs/milestone-3.md
    Step 5). The difference is deliberate; this pins it so it stays so.
    """
    from trading_research.execution.models import EVENT_TYPES
    from trading_research.runtime.lumibot.event_mapper import _STATUS_MAP
    from trading_research.runtime.normalization import NORMALIZED_ORDER_STATUSES

    assert set(_STATUS_MAP.values()) <= set(EVENT_TYPES)
    assert set(EVENT_TYPES) <= set(NORMALIZED_ORDER_STATUSES)
    assert map_order_status("expired") == "CANCELLED"
    assert "EXPIRED" not in EVENT_TYPES
