"""Cross-strategy allocation checks (docs/automated-live-trading-implementation-plan.md Phase 3).

Pure functions, no I/O. They run inside `risk.evaluate_paper_risk`, after the book-level caps have
proposed a quantity, and can only keep or shrink it or reject the entry:

* one strategy owns a symbol at a time;
* the signal's stop must be valid (below entry, tick-aligned downward, inside the distance band, and
  not inside the spread when a spread is known);
* planned risk, `shares x (entry - stop)`, is capped per trade, per strategy and across the whole book;
* a strategy's own capital budget caps the notional it holds plus has pending.

The risk uses the stop rounded *down* to the tick, which is the larger loss, so rounding never
understates risk. A stop is an order instruction, not a guarantee of an exit price.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from .models import (
    RISK_REJECTED_INVALID_STOP,
    RISK_REJECTED_OPEN_RISK_LIMIT,
    RISK_REJECTED_STRATEGY_CAPITAL_BUDGET,
    RISK_REJECTED_STRATEGY_RISK_BUDGET,
    RISK_REJECTED_SYMBOL_OWNED,
    RISK_REJECTED_TRADE_RISK_LIMIT,
)

ZERO = Decimal("0")


class AllocationConfigError(ValueError):
    """An allocation limit is out of range."""


@dataclass(frozen=True)
class AllocationLimits:
    """Limits expressed against book equity. None means that limit is not enforced.

    Symbol ownership and stop-below-entry are not parameters: they are always enforced."""

    max_planned_open_risk_fraction: Decimal | None = None
    max_risk_per_trade_fraction: Decimal | None = None
    min_stop_distance_fraction: Decimal | None = None
    max_stop_distance_fraction: Decimal | None = None
    tick_size: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        for name in ("max_planned_open_risk_fraction", "max_risk_per_trade_fraction"):
            value = getattr(self, name)
            if value is not None and not (ZERO < value <= Decimal("1")):
                raise AllocationConfigError(f"allocation.{name} must be within (0, 1]")
        for name in ("min_stop_distance_fraction", "max_stop_distance_fraction"):
            value = getattr(self, name)
            if value is not None and not (ZERO < value < Decimal("1")):
                raise AllocationConfigError(f"allocation.{name} must be within (0, 1)")
        low, high = self.min_stop_distance_fraction, self.max_stop_distance_fraction
        if low is not None and high is not None and low > high:
            raise AllocationConfigError("allocation.min_stop_distance_fraction must not exceed max_stop_distance_fraction")
        if self.tick_size <= 0:
            raise AllocationConfigError("allocation.tick_size must be > 0")


@dataclass(frozen=True)
class AllocationInputs:
    """What the allocation checks need about the book and the strategy.

    `symbol_owner` is the strategy that already holds or has pending the symbol, None when it is free,
    or UNATTRIBUTED_OWNER when the book holds it without a single strategy behind it."""

    strategy_id: str
    stop: Decimal
    limits: AllocationLimits
    book_planned_open_risk_usd: Decimal = ZERO
    strategy_planned_open_risk_usd: Decimal = ZERO
    strategy_committed_notional_usd: Decimal = ZERO
    symbol_owner: str | None = None
    strategy_capital_budget_usd: Decimal | None = None
    strategy_risk_budget_fraction: Decimal | None = None
    spread: Decimal | None = None


UNATTRIBUTED_OWNER = "<unattributed>"


@dataclass(frozen=True)
class AllocationResult:
    quantity: Decimal
    reject_code: str | None
    reasons: tuple[str, ...]


def normalize_stop(stop: Decimal, tick_size: Decimal) -> Decimal:
    """Round the stop down to the tick: never a tighter stop (smaller loss) than the strategy asked for."""
    return (stop / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


def planned_risk(quantity: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """`quantity x (entry - stop)`, never negative."""
    return quantity * max(entry - stop, ZERO)


def check_stop(entry: Decimal, stop: Decimal, limits: AllocationLimits, spread: Decimal | None = None) -> tuple[Decimal | None, str | None]:
    """The normalised stop, or a reason the stop is unusable."""
    if entry <= 0:
        return None, "entry price is not positive"
    if stop <= 0:
        return None, f"stop {stop} is not positive"
    normalized = normalize_stop(stop, limits.tick_size)
    if normalized <= 0:
        return None, f"stop {stop} rounds to zero at tick size {limits.tick_size}"
    if normalized >= entry:
        return None, f"stop {stop} (tick-aligned {normalized}) is at or above entry {entry}"
    distance = (entry - normalized) / entry
    if limits.min_stop_distance_fraction is not None and distance < limits.min_stop_distance_fraction:
        return None, f"stop distance {distance:.6f} of entry is below the minimum {limits.min_stop_distance_fraction}"
    if limits.max_stop_distance_fraction is not None and distance > limits.max_stop_distance_fraction:
        return None, f"stop distance {distance:.6f} of entry is above the maximum {limits.max_stop_distance_fraction}"
    if spread is not None and (spread < 0 or (entry - normalized) <= spread):
        return None, f"stop is inside the spread ({entry - normalized} from entry, spread {spread})"
    return normalized, None


def _shares_within(capacity_usd: Decimal, risk_per_share: Decimal) -> Decimal:
    return (max(capacity_usd, ZERO) / risk_per_share).to_integral_value(rounding=ROUND_FLOOR)


def apply_allocation(*, quantity: Decimal, entry: Decimal, equity: Decimal, inputs: AllocationInputs) -> AllocationResult:
    """Keep, shrink or reject a proposed whole-share quantity. Never raises it."""
    if quantity <= 0:
        raise ValueError("apply_allocation needs a positive proposed quantity")

    def reject(code: str, *reasons: str) -> AllocationResult:
        return AllocationResult(quantity=ZERO, reject_code=code, reasons=tuple(reasons))

    if inputs.symbol_owner is not None and inputs.symbol_owner != inputs.strategy_id:
        owner = "a position with no single strategy behind it" if inputs.symbol_owner == UNATTRIBUTED_OWNER \
            else f"strategy {inputs.symbol_owner!r}"
        return reject(RISK_REJECTED_SYMBOL_OWNED, f"the symbol is held or pending under {owner}, not {inputs.strategy_id!r}")
    if equity <= 0:
        return reject(RISK_REJECTED_INVALID_STOP, "book equity is not positive, so risk cannot be sized")

    stop, problem = check_stop(entry, inputs.stop, inputs.limits, inputs.spread)
    if stop is None:
        return reject(RISK_REJECTED_INVALID_STOP, problem or "invalid stop")
    risk_per_share = entry - stop

    # (maximum shares, rejection code if that cap binds to zero, reason)
    caps: list[tuple[Decimal, str, str]] = []
    limits = inputs.limits
    if limits.max_risk_per_trade_fraction is not None:
        budget = equity * limits.max_risk_per_trade_fraction
        caps.append((_shares_within(budget, risk_per_share), RISK_REJECTED_TRADE_RISK_LIMIT,
                     f"per-trade planned risk budget {budget} (one share risks {risk_per_share})"))
    if limits.max_planned_open_risk_fraction is not None:
        room = equity * limits.max_planned_open_risk_fraction - inputs.book_planned_open_risk_usd
        caps.append((_shares_within(room, risk_per_share), RISK_REJECTED_OPEN_RISK_LIMIT,
                     f"book planned open risk room {max(room, ZERO)} after {inputs.book_planned_open_risk_usd} already planned"))
    if inputs.strategy_risk_budget_fraction is not None:
        room = equity * inputs.strategy_risk_budget_fraction - inputs.strategy_planned_open_risk_usd
        caps.append((_shares_within(room, risk_per_share), RISK_REJECTED_STRATEGY_RISK_BUDGET,
                     f"strategy {inputs.strategy_id!r} planned risk room {max(room, ZERO)} "
                     f"after {inputs.strategy_planned_open_risk_usd} already planned"))
    if inputs.strategy_capital_budget_usd is not None:
        room = inputs.strategy_capital_budget_usd - inputs.strategy_committed_notional_usd
        caps.append((max(room, ZERO) // entry, RISK_REJECTED_STRATEGY_CAPITAL_BUDGET,
                     f"strategy {inputs.strategy_id!r} capital room {max(room, ZERO)} "
                     f"after {inputs.strategy_committed_notional_usd} already committed"))

    allowed = quantity
    binding: tuple[Decimal, str, str] | None = None
    for cap in caps:
        if cap[0] < allowed:
            allowed, binding = cap[0], cap
    if allowed <= 0:
        assert binding is not None  # quantity was positive, so only a cap can bring it to zero
        return reject(binding[1], binding[2], "approved quantity rounds down to zero whole shares")
    reasons = () if binding is None else (binding[2],)
    return AllocationResult(quantity=allowed, reject_code=None, reasons=reasons)
