"""PR 9 — the runtime normalization contract.

Three things are tested here:

1. **Cross-distribution drift.** The contract is declared twice (ADR 0002 /
   ADR 0009 forbid a shared package), so both declarations are AST-parsed
   from source and compared literally. Nothing is imported from
   `trading_paper_runtime` — it is a separate distribution that is not, and
   must not be, installed in this environment.
2. **Vocabulary conformance.** Every declared consumer of a normalized
   status accepts everything a producer can emit. This is the class of
   defect PR 9 exists to close.
3. **Fail-closed helper behavior.**
"""
from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.execution.broker_snapshots import (
    SUBMISSION_STATES,
    TERMINAL_SUBMISSION_STATES,
    BrokerOrderSubmission,
    BrokerSnapshotValidationError,
)
from trading_research.execution.models import EVENT_TYPES
from trading_research.runtime import normalization as main_contract
from trading_research.runtime.normalization import (
    BROKER_REPORTABLE_STATUSES,
    NORMALIZED_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    NormalizationError,
    normalize_broker_reportable_status,
    normalize_decimal_string,
    normalize_exact_int,
    normalize_optional_decimal_string,
    normalize_side,
    normalize_status,
    normalize_time_in_force,
    normalize_timestamp_string,
    parse_decimal,
)
from trading_research.storage import execution_repositories as repos
from trading_research.storage.execution_schema import apply_execution_schema

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_CONTRACT = _REPO_ROOT / "src/trading_research/runtime/normalization.py"
_RUNTIME_CONTRACT = _REPO_ROOT / "paper_runtime/src/trading_paper_runtime/normalization.py"
_RUNTIME_GATEWAY = _REPO_ROOT / "paper_runtime/src/trading_paper_runtime/lumibot_gateway.py"

# The constants both declarations must agree on, verbatim.
_MIRRORED_CONSTANTS = (
    "NORMALIZATION_CONTRACT_VERSION",
    "NORMALIZED_ORDER_STATUSES",
    "BROKER_REPORTABLE_STATUSES",
    "TERMINAL_ORDER_STATUSES",
    "NORMALIZED_SIDES",
    "NORMALIZED_TIME_IN_FORCE",
)


def _module_constants(path: Path) -> dict[str, object]:
    """Literal module-level assignments, read without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        try:
            found[target.id] = ast.literal_eval(value)
        except ValueError:
            continue  # a non-literal assignment is not part of the mirrored contract
    return found


def _public_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def _imported_roots(path: Path) -> set[str]:
    """Top-level package name of every import in a module."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _alpaca_status_map() -> dict[str, str]:
    """`_ALPACA_STATUS_MAP` read from the runtime distribution's source.

    Read via AST rather than imported, for the same reason
    `test_lumibot_import_boundary.py` does: this test process must never
    import the isolated runtime distribution.
    """
    constants = _module_constants(_RUNTIME_GATEWAY)
    assert "_ALPACA_STATUS_MAP" in constants, "runtime gateway no longer declares _ALPACA_STATUS_MAP"
    return constants["_ALPACA_STATUS_MAP"]  # type: ignore[return-value]


# --- 1. cross-distribution drift ------------------------------------------


def test_both_distributions_declare_the_identical_contract_constants():
    main = _module_constants(_MAIN_CONTRACT)
    runtime = _module_constants(_RUNTIME_CONTRACT)
    for name in _MIRRORED_CONSTANTS:
        assert name in main, f"{_MAIN_CONTRACT.name} does not declare {name}"
        assert name in runtime, f"{_RUNTIME_CONTRACT.name} does not declare {name}"
        assert main[name] == runtime[name], (
            f"{name} has drifted between the two normalization contract declarations: "
            f"main={main[name]!r} runtime={runtime[name]!r}"
        )


def test_both_distributions_expose_the_same_public_normalization_helpers():
    assert _public_function_names(_MAIN_CONTRACT) == _public_function_names(_RUNTIME_CONTRACT)


def test_the_two_declarations_are_not_a_shared_import():
    """The contract is mirrored source, never a cross-distribution import —
    that would violate ADR 0002 / ADR 0009.

    Checked by AST, not by substring: each file's prose legitimately names
    the other distribution when explaining why the mirroring exists.
    """
    assert _imported_roots(_RUNTIME_CONTRACT).isdisjoint({"trading_research", "src"})
    assert _imported_roots(_MAIN_CONTRACT).isdisjoint({"trading_paper_runtime", "paper_runtime"})


# --- 2. vocabulary conformance --------------------------------------------


def test_contract_subsets_are_internally_consistent():
    assert set(BROKER_REPORTABLE_STATUSES) <= set(NORMALIZED_ORDER_STATUSES)
    assert set(TERMINAL_ORDER_STATUSES) <= set(BROKER_REPORTABLE_STATUSES)
    # The two states a broker can never report.
    assert set(NORMALIZED_ORDER_STATUSES) - set(BROKER_REPORTABLE_STATUSES) == {
        "PENDING_SUBMISSION",
        "SUBMISSION_UNKNOWN",
    }


def test_submission_state_machine_uses_the_contract_vocabulary():
    assert SUBMISSION_STATES == NORMALIZED_ORDER_STATUSES
    assert TERMINAL_SUBMISSION_STATES == TERMINAL_ORDER_STATUSES


def test_in_process_event_vocabulary_is_a_subset_of_the_contract():
    """The ADR 0001 in-process adapter emits a narrower vocabulary than the
    ADR 0002 process boundary. Narrower is fine; *outside* is not."""
    assert set(EVENT_TYPES) <= set(NORMALIZED_ORDER_STATUSES)


@pytest.mark.parametrize("status", sorted(set(_alpaca_status_map().values())))
def test_every_status_the_runtime_gateway_can_emit_is_accepted_by_the_submission_dataclass(status):
    """The PR 9 defect, pinned.

    `update_submission_status` writes `response["status"]` unvalidated;
    `_row_to_submission` reads it back through `BrokerOrderSubmission`. Before
    PR 9 the gateway could emit `EXPIRED` and `CANCEL_REQUESTED`, neither of
    which was in `SUBMISSION_STATES`, so those rows became permanently
    unreadable.
    """
    assert status in BROKER_REPORTABLE_STATUSES
    now = datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc)
    submission = BrokerOrderSubmission(
        intent_id="intent-1", client_order_id="intent-1", broker_order_id="b-1",
        submission_status=status, attempt_count=1, last_attempt_at=now,
        created_at=now, updated_at=now,
    )
    assert submission.submission_status == status


def test_expired_and_cancel_requested_are_the_statuses_that_previously_failed():
    """Guards the specific regression, not just the general property."""
    assert "EXPIRED" in SUBMISSION_STATES
    assert "CANCEL_REQUESTED" in SUBMISSION_STATES
    assert "EXPIRED" in TERMINAL_SUBMISSION_STATES
    assert "CANCEL_REQUESTED" not in TERMINAL_SUBMISSION_STATES


def test_a_status_outside_the_contract_still_fails_closed():
    now = datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc)
    with pytest.raises(BrokerSnapshotValidationError):
        BrokerOrderSubmission(
            intent_id="intent-1", client_order_id="intent-1", broker_order_id=None,
            submission_status="PROBABLY_FINE", attempt_count=0, last_attempt_at=None,
            created_at=now, updated_at=now,
        )


def test_expired_submission_round_trips_through_storage_and_leaves_the_work_queue():
    """End-to-end version of the poison-row defect, through real SQL."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_execution_schema(conn)
    now = datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc)
    repos.create_pending_submission(
        conn, intent_id="intent-1", client_order_id="intent-1", now=now,
    )
    assert [s.intent_id for s in repos.list_unresolved_submissions(conn)] == ["intent-1"]

    repos.update_submission_status(
        conn, intent_id="intent-1", submission_status="EXPIRED", broker_order_id="b-1", now=now,
    )
    # Previously raised BrokerSnapshotValidationError: the row was written
    # but could never be read back.
    stored = repos.get_submission(conn, "intent-1")
    assert stored is not None and stored.submission_status == "EXPIRED"
    assert stored.is_terminal()
    # ... and it must leave the polling loop's work queue, which used a
    # hardcoded SQL terminal list that omitted EXPIRED.
    assert repos.list_unresolved_submissions(conn) == []


def test_external_broker_state_mapping_coverage_is_pinned():
    """`paper_books/external_broker.py` is the safety-critical consumer.

    PR 9 does not change its state machine (that stays a deliberate,
    separately-reviewed surface); it pins exactly which normalized statuses
    it maps, so a future contract change cannot silently widen or narrow the
    gap. `ERROR` is deliberately unmapped: an order the broker reports as
    stopped/suspended has no safe automatic ledger state, so
    `_state_from_order` raises `UNKNOWN_BROKER_STATUS` and the order is left
    for manual reconciliation.
    """
    from trading_research.paper_books.external_broker import _state_from_order

    mapped, unmapped = set(), set()
    for status in BROKER_REPORTABLE_STATUSES:
        try:
            _state_from_order({"status": status})
        except Exception:
            unmapped.add(status)
        else:
            mapped.add(status)
    assert unmapped == {"ERROR"}, (
        "the set of normalized statuses external_broker cannot map has changed; "
        "this is a deliberate fail-closed boundary and needs a decision, not a silent edit"
    )
    assert mapped == set(BROKER_REPORTABLE_STATUSES) - {"ERROR"}


# --- 3. fail-closed helper behavior ---------------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", "None", "abc", float("nan"), float("inf"), float("-inf")])
def test_parse_decimal_fails_closed(bad):
    with pytest.raises(NormalizationError):
        parse_decimal(bad, "price")


def test_parse_decimal_rejects_bools():
    with pytest.raises(NormalizationError):
        parse_decimal(True, "price")


def test_normalize_decimal_string_returns_plain_notation():
    assert normalize_decimal_string("1E+2", "price") == "100"
    assert normalize_decimal_string("10.00", "price") == "10.00"
    assert normalize_decimal_string(Decimal("-3.5"), "cash") == "-3.5"


def test_normalize_optional_decimal_distinguishes_absent_from_malformed():
    assert normalize_optional_decimal_string(None, "limit_price") is None
    assert normalize_optional_decimal_string("", "limit_price") is None
    assert normalize_optional_decimal_string("12.5", "limit_price") == "12.5"
    # The exact pre-PR-9 gateway defect: str(None) is not an absent value.
    with pytest.raises(NormalizationError):
        normalize_optional_decimal_string("None", "limit_price")


@pytest.mark.parametrize("bad", ["0.5", "1.0001", float("nan"), None, True, "seven"])
def test_normalize_exact_int_never_truncates(bad):
    with pytest.raises(NormalizationError):
        normalize_exact_int(bad, "quantity")


def test_normalize_exact_int_accepts_whole_numbers_in_any_representation():
    assert normalize_exact_int("10", "quantity") == 10
    assert normalize_exact_int(10.0, "quantity") == 10
    assert normalize_exact_int(Decimal("10.000"), "quantity") == 10


def test_normalize_status_is_case_insensitive_but_closed():
    assert normalize_status(" filled ") == "FILLED"
    with pytest.raises(NormalizationError):
        normalize_status("partially-filled")


def test_broker_reportable_rejects_main_process_only_states():
    for status in ("PENDING_SUBMISSION", "SUBMISSION_UNKNOWN"):
        assert normalize_status(status) == status
        with pytest.raises(NormalizationError):
            normalize_broker_reportable_status(status)


def test_normalize_side_and_time_in_force_never_default():
    assert normalize_side("buy") == "BUY"
    assert normalize_time_in_force("gtc") == "GTC"
    with pytest.raises(NormalizationError):
        normalize_side("SHORT")
    with pytest.raises(NormalizationError):
        normalize_time_in_force("forever")


def test_normalize_timestamp_assumes_utc_for_naive_values():
    assert normalize_timestamp_string("2026-08-02T15:00:00", "as_of") == "2026-08-02T15:00:00+00:00"
    # Alpaca's str(datetime) uses a space separator, not "T".
    assert normalize_timestamp_string("2026-08-02 15:00:00+00:00", "as_of") == "2026-08-02T15:00:00+00:00"
    with pytest.raises(NormalizationError):
        normalize_timestamp_string("last Tuesday", "as_of")


def test_contract_version_is_declared():
    assert main_contract.NORMALIZATION_CONTRACT_VERSION == "runtime-normalization.v1"
