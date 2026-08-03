"""Runtime-side declaration of the runtime normalization contract
(`docs/library-migration/MASTER_PLAN.md` PR 9).

This is the isolated-distribution half of a contract declared twice on
purpose. ADR 0002 (reaffirmed by ADR 0009) forbids this distribution and the
main `trading_research` package from importing each other — there is
deliberately no shared installable package — so the vocabulary and the
normalization rules are declared once per side:

* here, and
* `src/trading_research/runtime/normalization.py` in the main repository.

The main repository's `tests/unit/test_runtime_normalization_contract.py`
AST-parses both files and compares the declared constants literally, so the
two cannot drift apart silently. The sides share the vocabulary and the
rules; they never share a Python type, and each raises its own error class —
here, a `RuntimeOperationError` subclass, so a normalization failure reaches
the main process as an ordinary structured protocol error rather than an
unclassified internal crash.

Fail closed, always
-------------------
Every helper rejects rather than repairs. Before PR 9 this side stringified
whatever the broker happened to return: a missing fill quantity or price
became `"0"` (fabricating a zero-price fill), an unset limit price became the
literal string `"None"` (which then crashed the main process's `Decimal(...)`
parse), and any non-enum time-in-force silently became `DAY`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .errors import ErrorCode, RuntimeOperationError

# --- contract constants (mirrored verbatim on the main-repository side) ----

NORMALIZATION_CONTRACT_VERSION = "runtime-normalization.v1"

# Every normalized order/submission state that may exist anywhere on either
# side of the process boundary, in lifecycle order. This is a closed set:
# anything outside it is a bug, never a best-guess passthrough.
NORMALIZED_ORDER_STATUSES = (
    "PENDING_SUBMISSION",
    "SUBMISSION_UNKNOWN",
    "SUBMITTED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "EXPIRED",
    "REJECTED",
    "ERROR",
)

# The subset a *gateway* may report for a broker-known order.
# `PENDING_SUBMISSION` and `SUBMISSION_UNKNOWN` describe the main process's
# own knowledge of a submission, not anything a broker ever says, so a
# gateway that emits either is violating the contract.
BROKER_REPORTABLE_STATUSES = (
    "SUBMITTED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "EXPIRED",
    "REJECTED",
    "ERROR",
)

# States from which no further broker transition is expected. `EXPIRED` is
# terminal and was missing from the pre-PR-9 main-side declaration.
TERMINAL_ORDER_STATUSES = (
    "FILLED",
    "CANCELLED",
    "EXPIRED",
    "REJECTED",
    "ERROR",
)

NORMALIZED_SIDES = ("BUY", "SELL")

NORMALIZED_TIME_IN_FORCE = ("DAY", "GTC", "IOC", "FOK", "OPG", "CLS")

# --- errors ---------------------------------------------------------------


class NormalizationError(RuntimeOperationError):
    """A broker observation failed a fail-closed normalization check.

    Subclasses `RuntimeOperationError` so the dispatcher reports it to the
    main process with a structured code, not as an internal error.
    """

    def __init__(self, message: str, *, code: str = ErrorCode.MALFORMED_PAYLOAD) -> None:
        super().__init__(code, message, retryable=False)


# --- helpers (rules mirrored on the main-repository side) ------------------


def normalize_status(value: object, name: str = "status") -> str:
    """Return a canonical normalized order status, or fail closed.

    Accepts any case and surrounding whitespace, because a raw broker or
    stored value may carry either; rejects anything outside
    `NORMALIZED_ORDER_STATUSES`.
    """
    text = _require_text(value, name).upper()
    if text not in NORMALIZED_ORDER_STATUSES:
        raise NormalizationError(
            f"{name} {value!r} is not a normalized order status — fail closed "
            f"(expected one of {NORMALIZED_ORDER_STATUSES})",
            code=ErrorCode.UNKNOWN_BROKER_STATUS,
        )
    return text


def normalize_broker_reportable_status(value: object, name: str = "status") -> str:
    """Like `normalize_status`, but additionally rejects the two states no
    broker can report (`PENDING_SUBMISSION`, `SUBMISSION_UNKNOWN`)."""
    status = normalize_status(value, name)
    if status not in BROKER_REPORTABLE_STATUSES:
        raise NormalizationError(
            f"{name} {status!r} is a main-process submission state, not a broker-reportable "
            f"order status — fail closed (expected one of {BROKER_REPORTABLE_STATUSES})",
            code=ErrorCode.UNKNOWN_BROKER_STATUS,
        )
    return status


def normalize_side(value: object, name: str = "side") -> str:
    text = _require_text(value, name).upper()
    if text not in NORMALIZED_SIDES:
        raise NormalizationError(
            f"{name} {value!r} is not a normalized side — fail closed (expected one of {NORMALIZED_SIDES})"
        )
    return text


def normalize_time_in_force(value: object, name: str = "time_in_force") -> str:
    """Normalize a broker time-in-force, failing closed on anything unknown.

    Never defaults. A silent default here would report a GTC order as DAY,
    which changes what the caller believes about the order's lifetime.
    """
    text = _require_text(value, name).upper()
    if text not in NORMALIZED_TIME_IN_FORCE:
        raise NormalizationError(
            f"{name} {value!r} is not a normalized time-in-force — fail closed "
            f"(expected one of {NORMALIZED_TIME_IN_FORCE})"
        )
    return text


def normalize_decimal_string(value: object, name: str) -> str:
    """Return a canonical plain-notation decimal string, or fail closed.

    Rejects `None`, the literal strings `"None"`/`""`, non-finite values
    (`NaN`, `Infinity`), and anything `Decimal` cannot parse. Returns
    fixed-point notation so a consumer never has to handle `1E+2`.
    """
    return format(parse_decimal(value, name), "f")


def normalize_optional_decimal_string(value: object, name: str) -> str | None:
    """`None` only for a genuinely absent value — never for a malformed one.

    A real `None` (or an empty string, which brokers use for "not set")
    returns `None`; anything else must be a valid finite decimal.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return normalize_decimal_string(value, name)


def normalize_positive_decimal_string(value: object, name: str) -> str:
    text = normalize_decimal_string(value, name)
    if Decimal(text) <= 0:
        raise NormalizationError(f"{name} must be a positive decimal, got {value!r}")
    return text


def parse_decimal(value: object, name: str) -> Decimal:
    """Parse to `Decimal`, fail closed on `None`, `"None"`, or non-finite.

    `Decimal(str(x))` on its own is not safe at a broker boundary: it turns
    a `float('nan')` into `Decimal('NaN')`, which then silently passes an
    ordinary `<= 0` guard.
    """
    if value is None:
        raise NormalizationError(f"{name} is required but was None")
    if isinstance(value, bool):
        raise NormalizationError(f"{name} must be a number, got a bool: {value!r}")
    text = str(value).strip()
    if not text or text == "None":
        raise NormalizationError(f"{name} is required but was empty or the literal string 'None'")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise NormalizationError(f"{name} is not a valid decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise NormalizationError(f"{name} must be a finite decimal, got {value!r}")
    return parsed


def normalize_exact_int(value: object, name: str) -> int:
    """Return an exact whole number, never a truncation.

    A broker-reported share quantity that is fractional, non-finite, or
    unparseable fails closed rather than rounding — `int(float(...))` would
    silently turn 0.9 shares into 0.
    """
    if isinstance(value, bool):
        raise NormalizationError(f"{name} must be a whole number, got a bool: {value!r}")
    parsed = parse_decimal(value, name)
    if parsed != parsed.to_integral_value():
        raise NormalizationError(f"{name} must be a whole number, got {value!r}")
    return int(parsed)


def normalize_timestamp_string(value: object, name: str) -> str:
    """Validate an ISO 8601 timestamp and return it in canonical form.

    A naive timestamp is interpreted as UTC — brokers report UTC — rather
    than being left ambiguous for every downstream consumer to guess at.
    """
    text = _require_text(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(f"{name} is not a valid ISO 8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _require_text(value: object, name: str) -> str:
    if value is None:
        raise NormalizationError(f"{name} is required but was None")
    text = str(value).strip()
    if not text or text == "None":
        raise NormalizationError(f"{name} is required but was empty or the literal string 'None'")
    return text
