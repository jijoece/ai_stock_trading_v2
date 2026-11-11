"""Phase 3 of docs/automated-live-trading-implementation-plan.md: cross-strategy allocation checks.

Pure tests over `allocation.apply_allocation` and `risk.evaluate_paper_risk`, plus property-based tests
that replay overlapping signals through the risk engine with a small in-memory book.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_research.paper_books import risk as risk_module
from trading_research.paper_books.allocation import (
    UNATTRIBUTED_OWNER,
    AllocationConfigError,
    AllocationInputs,
    AllocationLimits,
    apply_allocation,
    check_stop,
    normalize_stop,
    planned_risk,
)
from trading_research.paper_books.config import RiskSection
from trading_research.paper_books.models import (
    APPROVED_RISK_DECISIONS,
    BOOK_STATUS_ACTIVE,
    RISK_REJECTED_DAILY_LOSS_LIMIT,
    RISK_REJECTED_INVALID_STOP,
    RISK_REJECTED_OPEN_RISK_LIMIT,
    RISK_REJECTED_STRATEGY_CAPITAL_BUDGET,
    RISK_REJECTED_STRATEGY_RISK_BUDGET,
    RISK_REJECTED_SYMBOL_OWNED,
    RISK_REJECTED_TRADE_RISK_LIMIT,
    VALUATION_COMPLETE,
    PaperPortfolioContext,
)

D = Decimal
NOW = datetime(2024, 3, 4, 21, tzinfo=timezone.utc)


def _inputs(**overrides) -> AllocationInputs:
    values = {"strategy_id": "alpha", "stop": D("92"), "limits": AllocationLimits()}
    values.update(overrides)
    for name, value in values.items():  # accept plain strings for the Decimal fields to keep the cases short
        if isinstance(value, str) and name not in ("strategy_id", "symbol_owner"):
            values[name] = D(value)
    return AllocationInputs(**values)


def _apply(quantity="100", entry="100", equity="100000", **overrides):
    return apply_allocation(quantity=D(quantity), entry=D(entry), equity=D(equity), inputs=_inputs(**overrides))


# -- stop validation -------------------------------------------------------------


def test_stop_is_rounded_down_to_the_tick_so_risk_is_never_understated():
    assert normalize_stop(D("92.057"), D("0.01")) == D("92.05")
    stop, problem = check_stop(D("100"), D("92.057"), AllocationLimits())
    assert problem is None and stop == D("92.05")
    # Sizing uses the larger risk per share, 100 - 92.05 = 7.95, not 100 - 92.059 = 7.941: a 794.5 budget
    # buys 99 shares at 7.95 but would buy 100 at 7.941.
    limits = AllocationLimits(max_risk_per_trade_fraction=D("0.007945"))
    assert _apply(quantity="125", stop="92.059", limits=limits).quantity == D("99")


@pytest.mark.parametrize("stop", ["100", "101", "0", "-1"])
def test_a_stop_at_or_above_entry_or_not_positive_is_rejected(stop):
    result = _apply(stop=stop)
    assert result.reject_code == RISK_REJECTED_INVALID_STOP and result.quantity == 0


def test_a_stop_that_rounds_to_zero_is_rejected():
    assert _apply(entry="0.05", stop="0.004").reject_code == RISK_REJECTED_INVALID_STOP


def test_stop_distance_band():
    limits = AllocationLimits(min_stop_distance_fraction=D("0.01"), max_stop_distance_fraction=D("0.20"))
    assert _apply(stop="99.5", limits=limits).reject_code == RISK_REJECTED_INVALID_STOP  # 0.5% < 1%
    assert _apply(stop="70", limits=limits).reject_code == RISK_REJECTED_INVALID_STOP  # 30% > 20%
    assert _apply(stop="90", limits=limits).reject_code is None  # 10%


def test_a_stop_inside_the_spread_is_rejected_only_when_a_spread_is_known():
    assert _apply(stop="99.90", spread=D("0.25")).reject_code == RISK_REJECTED_INVALID_STOP
    assert _apply(stop="99.00", spread=D("0.25")).reject_code is None
    assert _apply(stop="99.90").reject_code is None  # no quote, no spread: nothing to compare with


def test_limit_configuration_is_validated():
    for bad in (
        {"max_planned_open_risk_fraction": D("0")}, {"max_risk_per_trade_fraction": D("1.5")},
        {"min_stop_distance_fraction": D("1")}, {"tick_size": D("0")},
        {"min_stop_distance_fraction": D("0.3"), "max_stop_distance_fraction": D("0.2")},
    ):
        with pytest.raises(AllocationConfigError):
            AllocationLimits(**bad)


# -- symbol ownership ------------------------------------------------------------


def test_a_symbol_owned_by_another_strategy_is_rejected_before_any_sizing():
    result = _apply(symbol_owner="beta")
    assert result.reject_code == RISK_REJECTED_SYMBOL_OWNED and result.quantity == 0


def test_a_symbol_with_no_single_owner_is_rejected_for_every_strategy():
    assert _apply(symbol_owner=UNATTRIBUTED_OWNER).reject_code == RISK_REJECTED_SYMBOL_OWNED


def test_the_owning_strategy_may_continue_and_a_free_symbol_is_allowed():
    assert _apply(symbol_owner="alpha").reject_code is None
    assert _apply(symbol_owner=None).reject_code is None


# -- risk and capital budgets ----------------------------------------------------


def test_per_trade_risk_budget_shrinks_the_quantity():
    limits = AllocationLimits(max_risk_per_trade_fraction=D("0.0025"))  # 250 of 100000; one share risks 8
    result = _apply(quantity="125", limits=limits)
    assert result.quantity == D("31") and result.reject_code is None and result.reasons


def test_per_trade_budget_below_one_share_of_risk_rejects():
    limits = AllocationLimits(max_risk_per_trade_fraction=D("0.00005"))  # 5 < 8 per share
    assert _apply(limits=limits).reject_code == RISK_REJECTED_TRADE_RISK_LIMIT


def test_book_open_risk_cap_counts_what_is_already_planned():
    limits = AllocationLimits(max_planned_open_risk_fraction=D("0.01"))  # 1000
    assert _apply(quantity="125", limits=limits, book_planned_open_risk_usd=D("600")).quantity == D("50")
    assert _apply(limits=limits, book_planned_open_risk_usd=D("1000")).reject_code == RISK_REJECTED_OPEN_RISK_LIMIT
    assert _apply(limits=limits, book_planned_open_risk_usd=D("5000")).reject_code == RISK_REJECTED_OPEN_RISK_LIMIT


def test_strategy_risk_budget_is_separate_from_the_book_cap():
    result = _apply(quantity="125", strategy_risk_budget_fraction=D("0.005"), strategy_planned_open_risk_usd=D("100"))
    assert result.quantity == D("50")  # (500 - 100) / 8
    full = _apply(strategy_risk_budget_fraction=D("0.005"), strategy_planned_open_risk_usd=D("500"))
    assert full.reject_code == RISK_REJECTED_STRATEGY_RISK_BUDGET


def test_strategy_capital_budget_caps_held_plus_pending_notional():
    assert _apply(quantity="125", strategy_capital_budget_usd=D("5000")).quantity == D("50")
    result = _apply(strategy_capital_budget_usd=D("5000"), strategy_committed_notional_usd=D("4950"))
    assert result.reject_code == RISK_REJECTED_STRATEGY_CAPITAL_BUDGET


def test_the_tightest_limit_binds_and_is_reported():
    limits = AllocationLimits(max_risk_per_trade_fraction=D("0.01"), max_planned_open_risk_fraction=D("0.02"))
    result = _apply(
        quantity="125", limits=limits, book_planned_open_risk_usd=D("1900"),
        strategy_capital_budget_usd=D("100000"),
    )
    assert result.quantity == D("12")  # book room 100 / 8 is the tightest
    assert any("book planned open risk" in reason for reason in result.reasons)


def test_a_non_positive_equity_rejects_and_a_non_positive_quantity_is_a_caller_error():
    assert _apply(equity="0").reject_code == RISK_REJECTED_INVALID_STOP
    with pytest.raises(ValueError):
        _apply(quantity="0")


# -- through the risk engine -----------------------------------------------------


def _risk_config(**allocation) -> RiskSection:
    return RiskSection(
        max_position_weight=D("0.9"), max_order_notional_usd=D("1000000"), max_daily_new_notional_usd=D("1000000"),
        minimum_cash_buffer_weight=D("0"), max_open_positions=1000, max_symbol_concentration_weight=D("0.9"),
        reject_stale_market_price_seconds=999999, allocation=AllocationLimits(**allocation),
    )


def _context(cash: Decimal, equity: Decimal | None = None, positions: int = 0) -> PaperPortfolioContext:
    return PaperPortfolioContext(
        book_id="BASELINE", as_of=NOW, available_cash_usd=cash, reserved_cash_usd=D("0"),
        net_liquidation_value_usd=equity if equity is not None else cash, current_position_quantity=D("0"),
        current_position_market_value_usd=D("0"), current_position_weight=D("0"), open_position_count=positions,
        daily_new_notional_usd=D("0"), valuation_status=VALUATION_COMPLETE,
    )


def _evaluate(*, cash, entry, hint, allocation, config, equity=None, **extra):
    return risk_module.evaluate_paper_risk(
        book_status=BOOK_STATUS_ACTIVE, experiment_arm="BASELINE", expected_arm="BASELINE",
        context=_context(cash, equity), requested_quantity_hint=hint, reference_price=entry,
        reference_price_age_seconds=0, reference_price_point_in_time_safe=True, risk_config=config,
        allocation=allocation, **extra,
    )


def test_the_risk_engine_applies_allocation_after_the_book_caps_and_marks_a_cut_as_reduced():
    decision = _evaluate(
        cash=D("100000"), entry=D("100"), hint=D("125"), config=_risk_config(),
        allocation=_inputs(strategy_capital_budget_usd=D("5000")),
    )
    assert decision.approved_quantity == D("50") and decision.decision == "APPROVED_REDUCED"
    assert any("capital room" in reason for reason in decision.reasons)


def test_the_risk_engine_turns_an_allocation_rejection_into_a_rejected_decision():
    decision = _evaluate(
        cash=D("100000"), entry=D("100"), hint=D("125"), config=_risk_config(),
        allocation=_inputs(symbol_owner="beta"),
    )
    assert decision.decision == RISK_REJECTED_SYMBOL_OWNED and decision.approved_quantity is None


def test_the_loss_halt_wins_over_any_allocation_headroom():
    state = SimpleNamespace(
        as_of=NOW, valuation_status=VALUATION_COMPLETE, reconciliation_status="MATCHED",
        daily_loss_fraction=D("-0.05"), current_drawdown_fraction=D("0"),
    )
    decision = _evaluate(
        cash=D("100000"), entry=D("100"), hint=D("10"), config=_risk_config(), allocation=_inputs(),
        daily_risk_state=state, enforce_authoritative_state=True,
    )
    assert decision.decision == RISK_REJECTED_DAILY_LOSS_LIMIT


# -- properties ------------------------------------------------------------------

prices = st.decimals(min_value=D("5"), max_value=D("500"), places=2)
stop_factors = st.decimals(min_value=D("0.50"), max_value=D("1.10"), places=2)  # above 1 is an invalid stop


@given(
    quantity=st.integers(min_value=1, max_value=5000), entry=prices, stop_factor=stop_factors,
    equity=st.decimals(min_value=D("1000"), max_value=D("1000000"), places=2),
    book_risk=st.decimals(min_value=D("0"), max_value=D("50000"), places=2),
    trade_fraction=st.one_of(st.none(), st.decimals(min_value=D("0.0001"), max_value=D("0.05"), places=4)),
    open_fraction=st.one_of(st.none(), st.decimals(min_value=D("0.001"), max_value=D("0.20"), places=3)),
    capital=st.one_of(st.none(), st.decimals(min_value=D("100"), max_value=D("100000"), places=2)),
    owner=st.sampled_from([None, "alpha", "beta", UNATTRIBUTED_OWNER]),
)
@settings(max_examples=300, deadline=None)
def test_allocation_never_raises_a_quantity_and_never_exceeds_a_limit(
    quantity, entry, stop_factor, equity, book_risk, trade_fraction, open_fraction, capital, owner,
):
    stop = (entry * stop_factor).quantize(D("0.01"))
    limits = AllocationLimits(max_risk_per_trade_fraction=trade_fraction, max_planned_open_risk_fraction=open_fraction)
    inputs = AllocationInputs(
        strategy_id="alpha", stop=stop, limits=limits, book_planned_open_risk_usd=book_risk, symbol_owner=owner,
        strategy_capital_budget_usd=capital,
    )
    result = apply_allocation(quantity=D(quantity), entry=entry, equity=equity, inputs=inputs)

    assert (result.reject_code is None) == (result.quantity > 0)  # approve with shares, or reject with none
    assert result.quantity <= quantity
    if owner not in (None, "alpha"):
        assert result.reject_code == RISK_REJECTED_SYMBOL_OWNED
    if result.reject_code is not None:
        assert result.reasons
        return
    effective_stop = normalize_stop(stop, limits.tick_size)
    assert 0 < effective_stop < entry  # a stop at or above entry can never be approved
    risk = planned_risk(result.quantity, entry, effective_stop)
    if trade_fraction is not None:
        assert risk <= equity * trade_fraction
    if open_fraction is not None:
        assert book_risk + risk <= equity * open_fraction
    if capital is not None:
        assert result.quantity * entry <= capital


@dataclass
class _Book:
    """A small in-memory book standing in for the bridge's committed state."""

    cash: Decimal
    equity: Decimal
    owners: dict[str, str] = field(default_factory=dict)
    book_risk: Decimal = D("0")
    strategy_risk: dict[str, Decimal] = field(default_factory=dict)
    strategy_notional: dict[str, Decimal] = field(default_factory=dict)
    spent: Decimal = D("0")


signal_batches = st.lists(
    st.tuples(
        st.sampled_from(["alpha", "beta", "gamma"]), st.sampled_from(["AAA", "BBB", "CCC", "DDD"]),
        prices, stop_factors, st.integers(min_value=1, max_value=2000),
    ),
    min_size=1, max_size=25,
)


@given(
    batch=signal_batches,
    cash=st.decimals(min_value=D("1000"), max_value=D("200000"), places=2),
    open_fraction=st.decimals(min_value=D("0.005"), max_value=D("0.10"), places=3),
    strategy_budget=st.decimals(min_value=D("0.002"), max_value=D("0.05"), places=3),
)
@settings(max_examples=150, deadline=None)
def test_overlapping_signals_never_double_spend_cash_share_a_symbol_or_overrun_risk(
    batch, cash, open_fraction, strategy_budget,
):
    """Replays signals one after another, the way the bridge does, through the real risk engine."""
    config = _risk_config(max_planned_open_risk_fraction=open_fraction, max_risk_per_trade_fraction=D("0.01"))
    book = _Book(cash=cash, equity=cash)
    approved = 0
    for strategy_id, symbol, entry, stop_factor, hint in batch:
        stop = (entry * stop_factor).quantize(D("0.01"))
        owner = book.owners.get(symbol)
        allocation = AllocationInputs(
            strategy_id=strategy_id, stop=stop, limits=config.allocation, symbol_owner=owner,
            book_planned_open_risk_usd=book.book_risk,
            strategy_planned_open_risk_usd=book.strategy_risk.get(strategy_id, D("0")),
            strategy_committed_notional_usd=book.strategy_notional.get(strategy_id, D("0")),
            strategy_risk_budget_fraction=strategy_budget,
        )
        decision = _evaluate(
            cash=book.cash, equity=book.equity, entry=entry, hint=D(hint), config=config, allocation=allocation,
        )
        if decision.decision not in APPROVED_RISK_DECISIONS:
            continue
        approved += 1
        quantity = decision.approved_quantity
        assert quantity is not None and quantity == quantity.to_integral_value(rounding=ROUND_FLOOR)
        assert owner in (None, strategy_id), "a symbol must never be entered by a second strategy"
        effective_stop = normalize_stop(stop, config.allocation.tick_size)
        assert 0 < effective_stop < entry
        notional, risk = quantity * entry, planned_risk(quantity, entry, effective_stop)
        book.owners[symbol] = strategy_id
        book.cash -= notional
        book.spent += notional
        book.book_risk += risk
        book.strategy_risk[strategy_id] = book.strategy_risk.get(strategy_id, D("0")) + risk
        book.strategy_notional[strategy_id] = book.strategy_notional.get(strategy_id, D("0")) + notional

        assert book.cash >= 0 and book.spent <= cash, "cash must never be spent twice"
        assert book.book_risk <= book.equity * open_fraction
        assert book.strategy_risk[strategy_id] <= book.equity * strategy_budget
    assert approved <= len(batch)
