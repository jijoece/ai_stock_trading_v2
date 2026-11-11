"""Phases 2 and 3 of docs/automated-live-trading-implementation-plan.md at the strategy bridge.

Per-strategy controls (mode, stage, allowlist, budgets, signal validity), expired and out-of-order signals,
one strategy per symbol, and the cross-strategy risk limits, driven with synthetic stored signals so each
case states exactly the numbers it depends on. Local simulated paper book only; nothing here reaches a broker.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from tests.unit.test_strategy_paper_cycle import BOOK, _paper_config
from trading_research.evaluation.market_calendar import regular_session_close
from trading_research.hashing import hash_config
from trading_research.paper_books import cash_ledger
from trading_research.paper_books.allocation import AllocationLimits
from trading_research.paper_books.strategy_bridge import (
    OUTCOME_INTENT_CREATED_PENDING_FILL,
    OUTCOME_REJECTED_BY_RISK,
    OUTCOME_SKIPPED_ALREADY_PROCESSED,
    OUTCOME_SKIPPED_EXPIRED,
    OUTCOME_SKIPPED_OUT_OF_ORDER,
    OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED,
    OUTCOME_SKIPPED_SYMBOL_NOT_ALLOWED,
    create_strategy_entry_intents,
)
from trading_research.storage import paper_books_repositories as pb_repo
from trading_research.storage.database import connect
from trading_research.storage.strategy_bridge_repositories import get_decision_controls, save_decision_controls
from trading_research.storage.strategy_signals_repositories import (
    StrategySignalRecord,
    insert_strategy_signal,
    list_strategy_signals,
)
from trading_research.strategies.config import StrategyConfigError, load_strategy_config
from trading_research.strategies.contracts import (
    StrategySignal,
    StrategyStatus,
    derive_strategy_evaluation_id,
    derive_strategy_signal_content_id,
)
from trading_research.strategies.controls import (
    DEFAULT_SIGNAL_VALIDITY_SESSIONS,
    StrategyControls,
    StrategyControlsError,
    signal_expires_at,
)

D = Decimal
MON, TUE, WED = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)
STAGE_5 = "STAGE_5_LOCAL_PAPER_BOOK"


# -- helpers ---------------------------------------------------------------------


def _store(conn, strategy_id, symbol, session, *, strength=0.5, entry="100", stop="92", limit=None) -> None:
    limit = limit or entry
    close = regular_session_close(session)
    signal = StrategySignal(
        strategy_id=strategy_id, strategy_version="v1", symbol=symbol, signal_timestamp=close, data_as_of=close,
        status=StrategyStatus.ELIGIBLE, signal_strength=strength, entry_reference=D(entry), limit_reference=D(limit),
        invalidation_price=D(stop), initial_stop_reference=D(stop), target_reference=None,
        expected_holding_period=10, reason_codes=("test",), factor_values={}, data_quality="complete",
        configuration_hash="cfg",
    )
    evaluation_id = derive_strategy_evaluation_id(signal)
    insert_strategy_signal(conn, StrategySignalRecord(
        strategy_evaluation_id=evaluation_id, signal_content_id=derive_strategy_signal_content_id(signal),
        strategy_id=strategy_id, strategy_version="v1", configuration_hash="cfg", symbol=symbol,
        session_date=session.isoformat(), signal_timestamp=close.isoformat(), data_as_of=close.isoformat(),
        status="ELIGIBLE", signal_strength=strength, entry_reference=entry, limit_reference=limit,
        invalidation_price=stop, initial_stop_reference=stop, target_reference=None, expected_holding_period=10,
        reason_codes_json=json.dumps(["test"]), factor_values_json="{}", data_quality="complete",
        backtest_screening_passed=True, backtest_screening_json="{}", production_screening_status="NOT_EVALUATED",
        production_screening_json=None, data_provider="test", snapshot_hash="s", screening_profile_hash="p",
        relative_strength_hash="r", record_hash=f"test-{evaluation_id}",
    ), recorded_at=close.isoformat())


@pytest.fixture()
def conn(tmp_path):
    return connect(tmp_path / "controls.sqlite3")


def _rows(conn, *, session=None, strategy_id=None, symbol=None):
    return [
        r for r in list_strategy_signals(conn)
        if (session is None or r["session_date"] == session.isoformat())
        and (strategy_id is None or r["strategy_id"] == strategy_id) and (symbol is None or r["symbol"] == symbol)
    ]


def _cfg(*, allocation=None, cash="100000", **kwargs):
    cfg = _paper_config(**kwargs)
    if allocation is not None:
        cfg = replace(cfg, risk=replace(cfg.risk, allocation=allocation))
    return replace(cfg, baseline=replace(cfg.baseline, starting_cash_usd=D(cash)))


def _controls(**per_strategy):
    """Paper-mode controls at stage 5 for alpha/beta/gamma, with optional per-strategy overrides."""
    return {
        name: StrategyControls(strategy_id=name, mode="paper", activation_stage=STAGE_5, **per_strategy.get(name, {}))
        for name in ("alpha", "beta", "gamma")
    }


_DEFAULT_CONTROLS: Any = object()  # distinguishes "not given" from an explicit None


def _enter(conn, rows, *, session=MON, as_of_session=None, cfg=None, controls: Any = _DEFAULT_CONTROLS):
    close = regular_session_close(as_of_session or session)
    return create_strategy_entry_intents(
        conn, strategy_rows=rows, as_of=close, cfg=cfg or _cfg(), price_provider=None, clock=lambda: close,
        controls=_controls() if controls is _DEFAULT_CONTROLS else controls,
    )


def _orders(conn):
    return pb_repo.list_order_intents(conn, BOOK)


def _by_strategy(outcomes):
    return {(o.strategy_id, o.symbol): o.outcome for o in outcomes}


# -- Phase 2: loader -------------------------------------------------------------


def _write(tmp_path, extra: str, stage="STAGE_5_LOCAL_PAPER_BOOK"):
    from trading_research.strategies.config import DEFAULT_STRATEGY_CONFIG_PATH

    text = DEFAULT_STRATEGY_CONFIG_PATH.read_text()
    text = text.replace("activation_stage: STAGE_2_HISTORICAL_BACKTEST", f"activation_stage: {stage}", 1)
    start = text.index("strategy_controls:")
    end = text.index("\nmomentum_breakout:", start)
    path = tmp_path / "strategies.yaml"
    path.write_text(text[:start] + extra + text[end:])
    return path


def test_the_shipped_config_loads_with_a_controls_entry_for_every_strategy():
    config = load_strategy_config()
    assert set(config.controls) == {"momentum_breakout", "mean_reversion", "event_catalyst"}
    for controls in config.controls.values():
        assert controls.mode == "paper" and controls.activation_stage == config.activation_stage
        assert controls.signal_validity_sessions == DEFAULT_SIGNAL_VALIDITY_SESSIONS


def test_a_missing_controls_section_gives_defaults_at_the_global_stage(tmp_path):
    config = load_strategy_config(_write(tmp_path, ""))
    assert config.controls_for("mean_reversion") == StrategyControls(
        strategy_id="mean_reversion", mode="paper", activation_stage=STAGE_5,
    )
    with pytest.raises(StrategyConfigError):
        config.controls_for("nope")


def test_controls_parse_budgets_allowlist_mode_and_stage(tmp_path):
    config = load_strategy_config(_write(tmp_path, """strategy_controls:
  momentum_breakout:
    mode: shadow
    activation_stage: STAGE_4_SHADOW_RECOMMENDATION_TRACKING
    symbol_allowlist: [AAPL, MSFT]
    capital_budget_usd: "5000"
    risk_budget_fraction: "0.004"
    signal_validity_sessions: 2
"""))
    controls = config.controls_for("momentum_breakout")
    assert controls.mode == "shadow" and controls.activation_stage == "STAGE_4_SHADOW_RECOMMENDATION_TRACKING"
    assert controls.symbol_allowlist == ("AAPL", "MSFT") and controls.allows_symbol("AAPL")
    assert not controls.allows_symbol("TSLA")
    assert controls.capital_budget_usd == D("5000") and controls.risk_budget_fraction == D("0.004")
    assert controls.signal_validity_sessions == 2
    assert config.controls_for("mean_reversion").mode == "paper"  # a neighbour is unaffected


@pytest.mark.parametrize("body, fragment", [
    ("  momentum_breakout: {mode: live}", "not available"),
    ("  momentum_breakout: {mode: turbo}", "mode"),
    ("  momentum_breakout: {activation_stage: STAGE_7_MULTI_DAY_PAPER_SOAK}", "ceiling"),
    ("  momentum_breakout: {activation_stage: STAGE_9}", "not a known stage"),
    ("  momentum_breakout: {capital_budget_usd: 0}", "capital_budget_usd"),
    ("  momentum_breakout: {capital_budget_usd: abc}", "must be a number"),
    ("  momentum_breakout: {risk_budget_fraction: 1.5}", "risk_budget_fraction"),
    ("  momentum_breakout: {signal_validity_sessions: 0}", "signal_validity_sessions"),
    ("  momentum_breakout: {signal_validity_sessions: 1.5}", "integer"),
    ("  momentum_breakout: {symbol_allowlist: [aapl]}", "upper-case"),
    ("  momentum_breakout: {symbol_allowlist: [AAPL, AAPL]}", "duplicates"),
    ("  momentum_breakout: {surprise: 1}", "unknown keys"),
    ("  not_a_strategy: {mode: paper}", "unknown strategies"),
])
def test_invalid_controls_fail_closed(tmp_path, body, fragment):
    with pytest.raises(StrategyConfigError, match=fragment):
        load_strategy_config(_write(tmp_path, f"strategy_controls:\n{body}\n", stage="STAGE_5_LOCAL_PAPER_BOOK"))


def test_live_mode_cannot_be_built_even_in_code():
    with pytest.raises(StrategyControlsError, match="ADR 0010"):
        StrategyControls(strategy_id="alpha", mode="live", activation_stage=STAGE_5)


def test_signal_expiry_is_the_close_of_the_nth_session_after_the_signal():
    assert signal_expires_at(MON, 1) == regular_session_close(TUE)
    assert signal_expires_at(MON, 2) == regular_session_close(WED)
    assert signal_expires_at(date(2024, 3, 8), 1) == regular_session_close(date(2024, 3, 11))  # over the weekend
    with pytest.raises(StrategyControlsError):
        signal_expires_at(MON, 0)


# -- Phase 2: the bridge gates on controls, expiry and order ---------------------


def test_only_paper_mode_strategies_at_stage_5_or_later_reach_the_book(conn):
    for name in ("alpha", "beta", "gamma"):
        _store(conn, name, name.upper(), MON)
    controls = {
        "alpha": StrategyControls(strategy_id="alpha", mode="paper", activation_stage=STAGE_5),
        "beta": StrategyControls(strategy_id="beta", mode="shadow", activation_stage=STAGE_5),
        "gamma": StrategyControls(
            strategy_id="gamma", mode="paper", activation_stage="STAGE_4_SHADOW_RECOMMENDATION_TRACKING"),
    }
    outcomes = _by_strategy(_enter(conn, _rows(conn), controls=controls))
    assert outcomes == {
        ("alpha", "ALPHA"): OUTCOME_INTENT_CREATED_PENDING_FILL,
        ("beta", "BETA"): OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED,
        ("gamma", "GAMMA"): OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED,
    }
    assert [o["symbol"] for o in _orders(conn)] == ["ALPHA"]


def test_a_strategy_missing_from_the_controls_mapping_is_not_enabled(conn):
    _store(conn, "alpha", "AAA", MON)
    outcomes = _enter(conn, _rows(conn), controls={})
    assert [o.outcome for o in outcomes] == [OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED] and _orders(conn) == []


def test_no_controls_mapping_means_paper_defaults(conn):
    _store(conn, "alpha", "AAA", MON)
    assert [o.outcome for o in _enter(conn, _rows(conn), controls=None)] == [OUTCOME_INTENT_CREATED_PENDING_FILL]


def test_the_symbol_allowlist_is_enforced_per_strategy(conn):
    _store(conn, "alpha", "AAA", MON)
    _store(conn, "alpha", "BBB", MON)
    controls = _controls(alpha={"symbol_allowlist": ("AAA",)})
    outcomes = _by_strategy(_enter(conn, _rows(conn), controls=controls))
    assert outcomes == {
        ("alpha", "AAA"): OUTCOME_INTENT_CREATED_PENDING_FILL, ("alpha", "BBB"): OUTCOME_SKIPPED_SYMBOL_NOT_ALLOWED,
    }


def test_an_expired_signal_cannot_produce_an_intent(conn):
    _store(conn, "alpha", "AAA", MON)
    late = _enter(conn, _rows(conn), as_of_session=WED)  # valid until Tuesday's close, run Wednesday
    assert [o.outcome for o in late] == [OUTCOME_SKIPPED_EXPIRED] and _orders(conn) == []
    on_time = _enter(conn, _rows(conn), as_of_session=TUE)  # Tuesday's close is still inside the window
    assert [o.outcome for o in on_time] == [OUTCOME_INTENT_CREATED_PENDING_FILL]


def test_a_longer_validity_window_keeps_the_signal_alive(conn):
    _store(conn, "alpha", "AAA", MON)
    controls = _controls(alpha={"signal_validity_sessions": 2})
    assert [o.outcome for o in _enter(conn, _rows(conn), as_of_session=WED, controls=controls)] == [
        OUTCOME_INTENT_CREATED_PENDING_FILL
    ]


def test_an_out_of_order_signal_is_skipped_once_a_later_one_was_acted_on(conn):
    _store(conn, "alpha", "AAA", TUE, strength=0.6)
    _store(conn, "alpha", "AAA", MON, strength=0.4)
    newer, older = _rows(conn, session=TUE), _rows(conn, session=MON)
    assert [o.outcome for o in _enter(conn, newer, as_of_session=TUE)] == [OUTCOME_INTENT_CREATED_PENDING_FILL]
    outcomes = _enter(conn, older, as_of_session=TUE)  # Monday's signal, still inside its window
    assert [o.outcome for o in outcomes] == [OUTCOME_SKIPPED_OUT_OF_ORDER]
    assert len(_orders(conn)) == 1


def test_another_strategys_or_symbols_later_signal_does_not_make_this_one_out_of_order(conn):
    _store(conn, "beta", "AAA", TUE)
    _store(conn, "alpha", "BBB", TUE)
    _store(conn, "alpha", "AAA", MON)
    _enter(conn, _rows(conn, session=TUE), as_of_session=TUE)
    outcome = _enter(conn, _rows(conn, session=MON), as_of_session=TUE)[0]
    assert outcome.outcome != OUTCOME_SKIPPED_OUT_OF_ORDER  # beta owns AAA, so it is rejected for that instead


# -- Phase 3: one strategy per symbol, ordering, cash ----------------------------


def test_two_strategies_on_the_same_symbol_and_bar_get_one_entry_for_the_stronger_signal(conn):
    _store(conn, "alpha", "AAA", MON, strength=0.4)
    _store(conn, "beta", "AAA", MON, strength=0.9)
    outcomes = _by_strategy(_enter(conn, _rows(conn)))
    assert outcomes[("beta", "AAA")] == OUTCOME_INTENT_CREATED_PENDING_FILL
    assert outcomes[("alpha", "AAA")] == OUTCOME_REJECTED_BY_RISK
    (order,) = _orders(conn)
    assert order["symbol"] == "AAA"
    reasons = [o for o in _enter(conn, _rows(conn, strategy_id="alpha"))]  # a retry stays rejected, never a second entry
    assert len(_orders(conn)) == 1 and reasons


def test_the_ordering_rule_follows_signal_strength_not_strategy_name(conn):
    _store(conn, "alpha", "AAA", MON, strength=0.9)
    _store(conn, "beta", "AAA", MON, strength=0.4)
    outcomes = _by_strategy(_enter(conn, _rows(conn)))
    assert outcomes[("alpha", "AAA")] == OUTCOME_INTENT_CREATED_PENDING_FILL
    assert outcomes[("beta", "AAA")] == OUTCOME_REJECTED_BY_RISK


def test_equal_strength_ties_break_deterministically_by_strategy_id(conn):
    _store(conn, "beta", "AAA", MON, strength=0.5)
    _store(conn, "alpha", "AAA", MON, strength=0.5)
    first = _by_strategy(_enter(conn, list(reversed(_rows(conn)))))
    assert first[("alpha", "AAA")] == OUTCOME_INTENT_CREATED_PENDING_FILL  # input order does not matter


def test_the_rejected_strategy_reason_names_the_symbol_ownership(conn):
    _store(conn, "alpha", "AAA", MON, strength=0.9)
    _store(conn, "beta", "AAA", MON, strength=0.4)
    outcomes = _enter(conn, _rows(conn))
    rejected = next(o for o in outcomes if o.strategy_id == "beta")
    assert rejected.reasons[0] == "REJECTED_SYMBOL_OWNED_BY_OTHER_STRATEGY"
    assert "alpha" in " ".join(rejected.reasons)


def test_a_symbol_is_free_again_once_its_position_is_gone(conn):
    _store(conn, "alpha", "AAA", MON)
    _enter(conn, _rows(conn))
    (order,) = _orders(conn)
    conn.execute("UPDATE paper_book_orders SET status = 'CANCELLED' WHERE paper_order_intent_id = ?",
                 (order["paper_order_intent_id"],))
    conn.commit()
    _store(conn, "beta", "AAA", TUE)
    outcome = _enter(conn, _rows(conn, session=TUE, strategy_id="beta"), as_of_session=TUE)[0]
    assert outcome.outcome == OUTCOME_INTENT_CREATED_PENDING_FILL


def test_two_strategies_cannot_spend_the_same_cash(conn):
    """20,000 of cash and a 1-dollar stop: each signal is sized at 1% of equity, 200 shares, which is all the cash."""
    for name, symbol in (("alpha", "AAA"), ("beta", "BBB"), ("gamma", "CCC")):
        _store(conn, name, symbol, MON, strength={"alpha": 0.9, "beta": 0.8, "gamma": 0.7}[name], stop="99")
    _enter(conn, _rows(conn), cfg=_cfg(cash="20000"))
    orders = {o["symbol"]: o for o in _orders(conn)}
    assert sum(D(o["notional_usd"]) for o in orders.values()) <= D("20000")
    assert cash_ledger.reserved_cash(conn, BOOK) == sum(D(o["notional_usd"]) for o in orders.values())
    # The strongest signal is sized first and takes what the 90% position-weight cap allows (18,000);
    # the later ones only get what is left after it, never the same cash again.
    assert D(orders["AAA"]["notional_usd"]) == D("18000")
    assert D(orders["AAA"]["notional_usd"]) > sum(D(o["notional_usd"]) for s, o in orders.items() if s != "AAA")


# -- Phase 3: risk limits and stops ----------------------------------------------


def test_book_open_risk_cap_sizes_later_signals_down_and_then_rejects(conn):
    for name, symbol, strength in (("alpha", "AAA", 0.9), ("beta", "BBB", 0.8), ("gamma", "CCC", 0.7)):
        _store(conn, name, symbol, MON, strength=strength)
    cfg = _cfg(allocation=AllocationLimits(max_planned_open_risk_fraction=D("0.015")))
    outcomes = _enter(conn, _rows(conn), cfg=cfg)
    quantities = {o["symbol"]: D(o["quantity"]) for o in _orders(conn)}
    assert quantities == {"AAA": D("125"), "BBB": D("62")}  # 1,000 planned, then 500 / 8 per share
    gamma = next(o for o in outcomes if o.strategy_id == "gamma")
    assert gamma.outcome == OUTCOME_REJECTED_BY_RISK and gamma.reasons[0] == "REJECTED_OPEN_RISK_LIMIT"


def test_per_trade_risk_budget_is_its_own_limit(conn):
    _store(conn, "alpha", "AAA", MON)
    cfg = _cfg(allocation=AllocationLimits(max_risk_per_trade_fraction=D("0.0025")))
    _enter(conn, _rows(conn), cfg=cfg)
    (order,) = _orders(conn)
    assert D(order["quantity"]) == D("31")  # 250 / 8


def test_a_strategys_capital_and_risk_budgets_apply_to_it_alone(conn):
    _store(conn, "alpha", "AAA", MON, strength=0.9)
    _store(conn, "beta", "BBB", MON, strength=0.8)
    controls = _controls(alpha={"capital_budget_usd": D("5000")}, beta={"risk_budget_fraction": D("0.004")})
    _enter(conn, _rows(conn), controls=controls)
    quantities = {o["symbol"]: D(o["quantity"]) for o in _orders(conn)}
    assert quantities == {"AAA": D("50"), "BBB": D("50")}  # 5000 / 100 shares; 400 / 8 per share


def test_a_stop_outside_the_distance_band_creates_no_intent(conn):
    _store(conn, "alpha", "TIGHT", MON, stop="99.9")  # 0.1% from entry
    _store(conn, "alpha", "WIDE", MON, stop="60")  # 40% from entry
    _store(conn, "alpha", "FINE", MON, stop="92")
    cfg = _cfg(allocation=AllocationLimits(min_stop_distance_fraction=D("0.005"), max_stop_distance_fraction=D("0.25")))
    outcomes = {o.symbol: o for o in _enter(conn, _rows(conn), cfg=cfg)}
    assert outcomes["TIGHT"].reasons[0] == outcomes["WIDE"].reasons[0] == "REJECTED_INVALID_STOP"
    assert outcomes["FINE"].outcome == OUTCOME_INTENT_CREATED_PENDING_FILL
    assert [o["symbol"] for o in _orders(conn)] == ["FINE"]


def test_unattributed_exposure_blocks_every_strategy_and_counts_as_fully_at_risk(conn):
    """An open lot with no strategy link has no stop on record: nobody may add to it."""
    from trading_research.paper_books.strategy_exposure import read_book_exposure

    _store(conn, "alpha", "AAA", MON)
    _enter(conn, _rows(conn))
    (linked,) = _orders(conn)
    # A pending BUY of another symbol that the generic paper workflow placed: same row, but no strategy link.
    unlinked = {**linked, "paper_order_intent_id": "generic-intent", "recommendation_id": "generic-rec", "symbol": "ZZZ"}
    conn.execute(
        f"INSERT INTO paper_book_orders ({', '.join(unlinked)}) VALUES ({', '.join('?' for _ in unlinked)})",
        tuple(unlinked.values()),
    )
    conn.commit()

    exposure = read_book_exposure(conn, BOOK)
    assert exposure.owner_of("AAA") == "alpha" and exposure.owner_of("ZZZ") == "<unattributed>"
    # Alpha's own pending entry risks 8 a share; the unlinked one has no stop on record, so all of it is at risk.
    assert exposure.book_planned_open_risk_usd == D(linked["quantity"]) * D("8") + D(linked["notional_usd"])

    _store(conn, "alpha", "ZZZ", TUE)
    outcome = _enter(conn, _rows(conn, session=TUE), as_of_session=TUE)[0]
    assert outcome.reasons[0] == "REJECTED_SYMBOL_OWNED_BY_OTHER_STRATEGY"


def test_a_stale_reference_price_is_still_rejected(conn):
    """The stale-quote policy for the price an order is sized on: `reject_stale_market_price_seconds`."""
    _store(conn, "alpha", "AAA", MON)
    cfg = _cfg()
    cfg = replace(cfg, risk=replace(cfg.risk, reject_stale_market_price_seconds=60))
    outcome = _enter(conn, _rows(conn), as_of_session=TUE, cfg=cfg)[0]  # a day old at the next close
    assert outcome.outcome == OUTCOME_REJECTED_BY_RISK and outcome.reasons[0] == "REJECTED_STALE_PRICE"


def test_the_config_loader_reads_the_allocation_section(tmp_path):
    from trading_research.paper_books.config import PaperBooksConfigError, load_paper_books_config

    shipped = load_paper_books_config()
    allocation = shipped.risk.allocation
    assert allocation.max_risk_per_trade_fraction == D("0.01")
    assert allocation.max_planned_open_risk_fraction == D("0.10")
    assert allocation.tick_size == D("0.01")

    from trading_research.paper_books.config import DEFAULT_PAPER_BOOKS_CONFIG_PATH as shipped_path

    text = shipped_path.read_text()
    bad = tmp_path / "bad.yaml"
    bad.write_text(text.replace('tick_size: "0.01"', 'tick_size: "0.01"\n      surprise: "1"', 1))
    with pytest.raises(PaperBooksConfigError, match="unknown keys"):
        load_paper_books_config(bad)
    bad.write_text(text.replace('max_risk_per_trade_fraction: "0.01"', 'max_risk_per_trade_fraction: "2"', 1))
    with pytest.raises(PaperBooksConfigError, match="max_risk_per_trade_fraction"):
        load_paper_books_config(bad)


# -- review finding: the controls a decision was made under are part of its audit record ------------------


def _decision_controls(conn, outcome) -> tuple[dict, dict]:
    decision = pb_repo.load_risk_decision(conn, outcome.risk_decision_id)
    stored = get_decision_controls(conn, outcome.risk_decision_id)
    assert decision is not None and stored is not None
    return decision, stored


@pytest.mark.parametrize("change", [
    {"mode": "shadow"},
    {"activation_stage": "STAGE_6_SUPERVISED_ALPACA_PAPER"},
    {"symbol_allowlist": ("AAA",)},
    {"capital_budget_usd": D("5000")},
    {"risk_budget_fraction": D("0.004")},
    {"signal_validity_sessions": 2},
])
def test_every_control_setting_changes_the_controls_hash(change):
    base: dict[str, Any] = {"strategy_id": "alpha", "mode": "paper", "activation_stage": STAGE_5}
    assert StrategyControls(**base).controls_hash != StrategyControls(**{**base, **change}).controls_hash


def test_the_controls_hash_ignores_representation_only_differences():
    base: dict[str, Any] = {"strategy_id": "alpha", "mode": "paper", "activation_stage": STAGE_5}
    first = StrategyControls(**base, symbol_allowlist=("AAA", "BBB"), capital_budget_usd=D("5000"))
    second = StrategyControls(**base, symbol_allowlist=("BBB", "AAA"), capital_budget_usd=D("5000.00"))
    assert first.controls_hash == second.controls_hash


def test_an_approved_decision_records_the_controls_it_was_evaluated_under(conn):
    _store(conn, "alpha", "AAA", MON)
    controls = _controls(alpha={
        "symbol_allowlist": ("AAA", "BBB"), "capital_budget_usd": D("5000"), "risk_budget_fraction": D("0.004"),
        "signal_validity_sessions": 2,
    })
    (outcome,) = _enter(conn, _rows(conn), controls=controls)
    decision, stored = _decision_controls(conn, outcome)
    assert outcome.outcome == OUTCOME_INTENT_CREATED_PENDING_FILL and decision is not None
    assert stored["controls_hash"] == outcome.strategy_controls_hash == controls["alpha"].controls_hash
    assert stored["controls"] == {
        "strategy_id": "alpha", "mode": "paper", "activation_stage": STAGE_5, "symbol_allowlist": ["AAA", "BBB"],
        "capital_budget_usd": "5000", "risk_budget_fraction": "0.004", "signal_validity_sessions": 2,
    }
    assert hash_config(stored["controls"]) == stored["controls_hash"]
    (order,) = _orders(conn)
    assert order["risk_decision_id"] == outcome.risk_decision_id  # intent -> decision -> controls


def test_a_rejected_decision_records_the_controls_it_was_evaluated_under(conn):
    _store(conn, "alpha", "AAA", MON)
    cfg = replace(_cfg(), risk=replace(_cfg().risk, reject_stale_market_price_seconds=60))
    controls = _controls(alpha={"capital_budget_usd": D("5000")})
    (outcome,) = _enter(conn, _rows(conn), as_of_session=TUE, cfg=cfg, controls=controls)
    _, stored = _decision_controls(conn, outcome)
    assert outcome.outcome == OUTCOME_REJECTED_BY_RISK and _orders(conn) == []
    assert stored["controls_hash"] == outcome.strategy_controls_hash == controls["alpha"].controls_hash


def test_identical_signals_under_different_controls_leave_distinguishable_records(tmp_path):
    """Same signal, same paper_books config, only `strategies.yaml` controls differ: the records must differ."""
    results = {}
    for name, budget in (("small", D("2000")), ("large", D("8000"))):
        db = connect(tmp_path / f"{name}.sqlite3")
        _store(db, "alpha", "AAA", MON)
        (outcome,) = _enter(db, _rows(db), controls=_controls(alpha={"capital_budget_usd": budget}))
        _, stored = _decision_controls(db, outcome)
        (order,) = _orders(db)
        results[name] = (D(order["quantity"]), outcome.risk_decision_id, stored["controls_hash"])
    assert results["small"][0] == D("20") and results["large"][0] == D("80")  # the budget changed the outcome...
    assert results["small"][1] == results["large"][1]  # ...under the same decision id...
    assert results["small"][2] != results["large"][2]  # ...so the stored controls hash is what tells them apart


def test_recorded_controls_are_append_only(conn):
    _store(conn, "alpha", "AAA", MON)
    (outcome,) = _enter(conn, _rows(conn))
    assert outcome.risk_decision_id is not None
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE paper_book_decision_strategy_controls SET controls_hash = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM paper_book_decision_strategy_controls")
    assert get_decision_controls(conn, outcome.risk_decision_id) is not None


def test_recording_a_decision_under_different_controls_is_refused(conn):
    _store(conn, "alpha", "AAA", MON)
    (outcome,) = _enter(conn, _rows(conn))
    assert outcome.risk_decision_id is not None
    other = StrategyControls(strategy_id="alpha", mode="paper", activation_stage=STAGE_5, capital_budget_usd=D("1"))
    with pytest.raises(ValueError, match="different strategy controls"):
        save_decision_controls(
            conn, risk_decision_id=outcome.risk_decision_id, book_id=BOOK, controls=other, created_at="2024-03-04T21:00:00+00:00",
        )


# -- review finding: a stop at or above the limit price must reject, not abort the batch ------------------


@pytest.mark.parametrize("stop", ["90", "95"], ids=["stop-equals-limit", "stop-above-limit"])
def test_a_stop_at_or_above_the_limit_is_rejected_without_aborting_the_batch(conn, stop):
    # entry 100 passes the contract (stop < entry) but the order's limit is 90, the price risk is sized from.
    _store(conn, "alpha", "AAA", MON, strength=0.9, entry="100", limit="90", stop=stop)
    _store(conn, "beta", "BBB", MON, strength=0.1)
    outcomes = _enter(conn, _rows(conn))
    by_symbol = {o.symbol: o for o in outcomes}
    assert len(outcomes) == 2
    bad, good = by_symbol["AAA"], by_symbol["BBB"]
    assert bad.outcome == OUTCOME_REJECTED_BY_RISK and bad.paper_order_intent_id is None
    assert good.outcome == OUTCOME_INTENT_CREATED_PENDING_FILL
    assert [o["symbol"] for o in _orders(conn)] == ["BBB"]
    decision, stored = _decision_controls(conn, bad)
    assert decision["decision"].startswith("REJECTED") and stored["controls_hash"] == bad.strategy_controls_hash


def test_every_persisted_risk_decision_has_exactly_one_controls_record(conn):
    _store(conn, "alpha", "AAA", MON, strength=0.9)  # approved in full
    _store(conn, "beta", "BBB", MON, strength=0.5)  # shrunk by the strategy's capital budget
    _store(conn, "gamma", "CCC", MON, strength=0.1, entry="100", limit="90", stop="90")  # rejected
    outcomes = _enter(conn, _rows(conn), controls=_controls(beta={"capital_budget_usd": D("5000")}))
    decisions = {o.symbol: pb_repo.load_risk_decision(conn, o.risk_decision_id)["decision"] for o in outcomes}
    assert decisions["AAA"] == "APPROVED" and decisions["BBB"] == "APPROVED_REDUCED"
    assert decisions["CCC"].startswith("REJECTED")
    risk_ids = {row[0] for row in conn.execute("SELECT risk_decision_id FROM paper_book_risk_decisions")}
    controls_ids = [row[0] for row in conn.execute("SELECT risk_decision_id FROM paper_book_decision_strategy_controls")]
    assert risk_ids == {o.risk_decision_id for o in outcomes}
    assert sorted(controls_ids) == sorted(risk_ids)


def test_a_retried_cycle_keeps_exactly_one_controls_record_per_decision(conn):
    _store(conn, "alpha", "AAA", MON)
    (first,) = _enter(conn, _rows(conn))
    (retry,) = _enter(conn, _rows(conn))
    assert first.outcome == OUTCOME_INTENT_CREATED_PENDING_FILL and retry.outcome == OUTCOME_SKIPPED_ALREADY_PROCESSED
    rows = conn.execute("SELECT risk_decision_id, controls_hash FROM paper_book_decision_strategy_controls").fetchall()
    assert [tuple(r) for r in rows] == [(first.risk_decision_id, first.strategy_controls_hash)]
    assert len(_orders(conn)) == 1


def test_no_controls_record_survives_a_failed_intent_registration(conn, monkeypatch):
    from trading_research.paper_books import strategy_bridge

    def fail(*args, **kwargs):
        raise RuntimeError("registration failed")

    _store(conn, "alpha", "AAA", MON)
    monkeypatch.setattr(strategy_bridge, "insert_strategy_link", fail)
    with pytest.raises(RuntimeError, match="registration failed"):
        _enter(conn, _rows(conn))
    for table in ("paper_book_risk_decisions", "paper_book_decision_strategy_controls"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert _orders(conn) == []
