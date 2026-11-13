"""What each strategy already holds or has pending in a paper book, read from the book's own records.

Feeds `allocation.AllocationInputs`: planned open risk, committed notional and symbol ownership. Open
lots and pending BUY intents both count, so two signals evaluated one after the other in a cycle see
each other (the first one's intent is committed before the second is evaluated).

A lot with no strategy link, for example from the generic paper workflow, has no stop on record: its
whole cost counts as at risk and the symbol is owned by no single strategy, so no strategy may add to it.
Ownership ends when the lots are closed and the pending intent is gone; the cycle's own reconciliation
gate (entries need a MATCHED book) is what makes "closed" mean "closed and reconciled".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .allocation import UNATTRIBUTED_OWNER, AllocationInputs, AllocationLimits, planned_risk

ZERO = Decimal("0")


@dataclass
class BookExposure:
    book_planned_open_risk_usd: Decimal = ZERO
    strategy_planned_open_risk_usd: dict[str, Decimal] = field(default_factory=dict)
    strategy_committed_notional_usd: dict[str, Decimal] = field(default_factory=dict)
    _symbol_owners: dict[str, set[str]] = field(default_factory=dict)

    def _add(self, *, owner: str, symbol: str, risk: Decimal, notional: Decimal) -> None:
        self.book_planned_open_risk_usd += risk
        self.strategy_planned_open_risk_usd[owner] = self.strategy_planned_open_risk_usd.get(owner, ZERO) + risk
        self.strategy_committed_notional_usd[owner] = self.strategy_committed_notional_usd.get(owner, ZERO) + notional
        self._symbol_owners.setdefault(symbol, set()).add(owner)

    def owner_of(self, symbol: str) -> str | None:
        owners = self._symbol_owners.get(symbol)
        if not owners:
            return None
        return next(iter(owners)) if len(owners) == 1 else UNATTRIBUTED_OWNER

    def inputs_for(
        self, *, strategy_id: str, symbol: str, stop: Decimal, limits: AllocationLimits,
        capital_budget_usd: Decimal | None, risk_budget_fraction: Decimal | None,
    ) -> AllocationInputs:
        return AllocationInputs(
            strategy_id=strategy_id, stop=stop, limits=limits,
            book_planned_open_risk_usd=self.book_planned_open_risk_usd,
            strategy_planned_open_risk_usd=self.strategy_planned_open_risk_usd.get(strategy_id, ZERO),
            strategy_committed_notional_usd=self.strategy_committed_notional_usd.get(strategy_id, ZERO),
            symbol_owner=self.owner_of(symbol),
            strategy_capital_budget_usd=capital_budget_usd, strategy_risk_budget_fraction=risk_budget_fraction,
        )


def _planned_risk(
    allocation_stop: str | None, quantity: Decimal, entry: Decimal, notional: Decimal,
) -> Decimal:
    """Planned risk at the stop frozen when the exposure was approved. Rows from before `allocation_stop`
    was recorded have no approval-time tick on record (nothing persists it), so any tick we chose now could
    later be shrunk to understate the risk. They are counted as fully at risk, like a lot with no stop on
    record, until they close."""
    if allocation_stop is None:
        return notional
    return planned_risk(quantity, entry, Decimal(allocation_stop))


def read_book_exposure(conn, book_id: str) -> BookExposure:
    """Existing exposure is risked at the stop frozen when it was approved (`allocation_stop`), so changing
    `allocation.tick_size` later cannot shrink it. Exposure without one (links from before that column
    existed) counts its whole notional as at risk, independent of any current setting."""
    exposure = BookExposure()

    lots = conn.execute(
        "SELECT l.symbol, l.quantity, l.remaining_quantity, l.cost_basis_usd, k.strategy_id, k.strategy_stop, "
        "k.allocation_stop "
        "FROM paper_book_position_lots l "
        "JOIN paper_book_fills f ON f.book_id = l.book_id AND f.fill_id = l.opening_fill_id "
        "LEFT JOIN paper_book_strategy_links k "
        "  ON k.book_id = f.book_id AND k.paper_order_intent_id = f.paper_order_intent_id "
        "WHERE l.book_id = ? AND l.remaining_quantity > '0'",
        (book_id,),
    ).fetchall()
    for lot in lots:
        remaining, original = Decimal(lot["remaining_quantity"]), Decimal(lot["quantity"])
        if remaining <= 0:
            continue  # the SQL comparison is textual; the decimal check is the real one
        unit_cost = Decimal(lot["cost_basis_usd"]) / original
        notional = unit_cost * remaining
        if lot["strategy_id"] is None:
            exposure._add(owner=UNATTRIBUTED_OWNER, symbol=lot["symbol"], risk=notional, notional=notional)
        else:
            exposure._add(
                owner=lot["strategy_id"], symbol=lot["symbol"], notional=notional,
                risk=_planned_risk(lot["allocation_stop"], remaining, unit_cost, notional),
            )

    pending = conn.execute(
        "SELECT o.symbol, o.quantity, o.limit_price, o.notional_usd, k.strategy_id, k.strategy_stop, "
        "k.allocation_stop "
        "FROM paper_book_orders o "
        "LEFT JOIN paper_book_strategy_links k "
        "  ON k.book_id = o.book_id AND k.paper_order_intent_id = o.paper_order_intent_id "
        "WHERE o.book_id = ? AND o.side = 'BUY' AND o.status = 'PENDING_SUBMISSION'",
        (book_id,),
    ).fetchall()
    for order in pending:
        notional = Decimal(order["notional_usd"])
        if order["strategy_id"] is None:
            exposure._add(owner=UNATTRIBUTED_OWNER, symbol=order["symbol"], risk=notional, notional=notional)
        else:
            exposure._add(
                owner=order["strategy_id"], symbol=order["symbol"], notional=notional,
                risk=_planned_risk(
                    order["allocation_stop"], Decimal(order["quantity"]), Decimal(order["limit_price"]), notional,
                ),
            )
    return exposure
