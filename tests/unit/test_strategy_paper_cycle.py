"""Phase 3 of docs/paper-testing-plan.md: strategy signals into the local simulated paper book."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_research.analysis.screener import load_screening_config
from trading_research.backtesting.alpaca_history import AlpacaHistoricalDataProvider, warmup_start
from trading_research.backtesting.fixture_history import build_fixture_snapshot
from trading_research.backtesting.models import BacktestError
from trading_research.backtesting.signal_replay import replay_signals
from trading_research.backtesting.strategy_backtest_runner import build_strategy
from trading_research.evaluation.market_calendar import add_trading_days, regular_session_close
from trading_research.evaluation.price_provider import DeterministicPriceProvider
from trading_research.forward_testing.candidates import run_strategy_candidates
from trading_research.forward_testing.paper_cycle import run_strategy_paper_cycle
from trading_research.paper_books import cash_ledger, execution, order_intent, risk as risk_module, valuation
from trading_research.paper_books.config import (
    ExecutionSection,
    ExitsSection,
    LifecycleSection,
    PaperBookDefinition,
    PaperBooksConfiguration,
    PendingOrdersSection,
    RiskSection,
    ScheduledIntegrationSection,
    SoakSection,
    ValuationSection,
)
from trading_research.paper_books.lifecycle import run_paper_book_lifecycle
from trading_research.paper_books.strategy_bridge import (
    OUTCOME_INTENT_CREATED_PENDING_FILL,
    OUTCOME_REJECTED_BY_RISK,
    OUTCOME_SKIPPED_ALREADY_PROCESSED,
    OUTCOME_SKIPPED_BOUNDARY,
    StrategyBridgeError,
    create_strategy_entry_intents,
    signal_from_row,
)
from trading_research.storage import paper_books_repositories as pb_repo
from trading_research.storage.database import connect
from trading_research.storage.strategy_bridge_repositories import (
    StrategyLink,
    get_strategy_link,
    insert_strategy_link,
    link_for_open_position,
)
from trading_research.storage.strategy_signals_repositories import list_strategy_signals
from trading_research.strategies.config import DEFAULT_STRATEGY_CONFIG_PATH

SYMBOLS = ("AAA", "BBB", "CCC")
START, END = date(2023, 1, 3), date(2024, 12, 31)
NOW = datetime(2025, 3, 1, tzinfo=timezone.utc)
UNIVERSE = ("AAA", "BBB", "CCC", "SPY")
BOOK = "BASELINE"


def _paper_config(*, max_order_notional="50000", stop_loss_percent="0.08", profit_target_percent="0.15",
                  maximum_holding_market_days=20, external=None) -> PaperBooksConfiguration:
    kwargs = {} if external is None else {"external_broker": external}
    return PaperBooksConfiguration(
        version=1, enabled=True,
        baseline=PaperBookDefinition(enabled=True, book_id=BOOK, starting_cash_usd=Decimal("100000")),
        enhanced=PaperBookDefinition(enabled=False, book_id="ENHANCED", starting_cash_usd=Decimal("100000")),
        execution=ExecutionSection(provider="local_simulated", allow_external_paper_broker=False, allow_live_broker=False),
        risk=RiskSection(
            max_position_weight=Decimal("0.9"), max_order_notional_usd=Decimal(max_order_notional),
            max_daily_new_notional_usd=Decimal("50000"), minimum_cash_buffer_weight=Decimal("0.02"),
            max_open_positions=20, max_symbol_concentration_weight=Decimal("0.9"),
            reject_stale_market_price_seconds=999999,
        ),
        valuation=ValuationSection(price_source="persisted_market_bar", maximum_price_age_seconds=999999, missing_price_policy="MARK_UNVALUED"),
        scheduled_integration=ScheduledIntegrationSection(enabled=False),
        lifecycle=LifecycleSection(
            enabled=True, pending_orders=PendingOrdersSection(expire_after_market_days=5),
            exits=ExitsSection(
                enabled=True, stop_loss_percent=Decimal(stop_loss_percent),
                profit_target_percent=Decimal(profit_target_percent),
                maximum_holding_market_days=maximum_holding_market_days, exit_on_recommendation_reversal=False,
            ),
            soak=SoakSection(minimum_completed_cycles=1, minimum_market_days=1),
        ),
        config_hash="strategy-cycle-test-hash", raw={}, **kwargs,
    )


@pytest.fixture()
def stage5_config(tmp_path):
    text = DEFAULT_STRATEGY_CONFIG_PATH.read_text()
    path = tmp_path / "strategies.yaml"
    path.write_text(re.sub(r"activation_stage: \w+", "activation_stage: STAGE_5_LOCAL_PAPER_BOOK", text))
    return path


@pytest.fixture(scope="module")
def full_replay():
    snapshot = build_fixture_snapshot(
        symbols=UNIVERSE, start=START, end=END, adjustment="split",
        as_of=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    return replay_signals(
        strategy=build_strategy("momentum_breakout", None), provider=AlpacaHistoricalDataProvider(snapshot),
        symbols=SYMBOLS, start=START, end=END, history_start=warmup_start(START),
        screening_config=load_screening_config(None),
    )


@pytest.fixture()
def stored(tmp_path, stage5_config, full_replay):
    conn = connect(tmp_path / "db.sqlite3")
    day = full_replay.signals[0].data_as_of.date()
    run_strategy_candidates(
        strategy_ids=("momentum_breakout",), symbols=SYMBOLS, as_of=day, data_source="fixture",
        snapshot_path=tmp_path / "cand.snapshot.json", conn=conn, strategy_config_path=stage5_config, now=NOW,
    )
    return conn, day


def _eligible(conn):
    return [r for r in list_strategy_signals(conn) if r["status"] == "ELIGIBLE"]


def _entry(conn, rows, day, cfg=None):
    return create_strategy_entry_intents(
        conn, strategy_rows=rows, as_of=regular_session_close(day), cfg=cfg or _paper_config(),
        price_provider=None, clock=lambda: regular_session_close(day),
    )


def _orders(conn):
    return pb_repo.list_order_intents(conn, BOOK)


# -- bridge ----------------------------------------------------------------------


def test_eligible_signal_becomes_a_pending_intent_with_a_strategy_link(stored):
    conn, day = stored
    rows = _eligible(conn)
    outcomes = _entry(conn, rows, day)
    assert {o.outcome for o in outcomes} == {OUTCOME_INTENT_CREATED_PENDING_FILL}
    orders = _orders(conn)
    assert len(orders) == len(rows) and {o["status"] for o in orders} == {"PENDING_SUBMISSION"}
    for order in orders:
        link = get_strategy_link(conn, BOOK, order["paper_order_intent_id"])
        assert link is not None and link.strategy_id == "momentum_breakout"
        assert order["recommendation_id"] == link.recommendation_id.replace("", "")
        assert Decimal(link.strategy_stop) > 0 and link.expected_holding_period > 0
        assert Decimal(order["limit_price"]) > Decimal(link.strategy_stop)


def test_quantity_is_set_by_risk_not_by_the_signal(stored):
    conn, day = stored
    _entry(conn, _eligible(conn), day)
    for order in _orders(conn):
        link = get_strategy_link(conn, BOOK, order["paper_order_intent_id"])
        per_share_risk = Decimal(order["limit_price"]) - Decimal(link.strategy_stop)
        # Never more than the 1% equity-risk proposal, and inside every risk.py cap.
        assert Decimal(order["quantity"]) <= Decimal("100000") * Decimal("0.01") / per_share_risk
        assert Decimal(order["notional_usd"]) <= Decimal("50000")


def test_a_non_eligible_signal_cannot_create_an_intent(stored):
    conn, day = stored
    others = [r for r in list_strategy_signals(conn) if r["status"] != "ELIGIBLE"]
    assert others, "fixture day should hold at least one non-eligible evaluation"
    outcomes = _entry(conn, others, day)
    assert {o.outcome for o in outcomes} == {OUTCOME_SKIPPED_BOUNDARY}
    assert _orders(conn) == []


def test_a_retried_run_does_not_duplicate_intents(stored):
    conn, day = stored
    rows = _eligible(conn)
    _entry(conn, rows, day)
    count = len(_orders(conn))
    again = _entry(conn, rows, day)
    assert {o.outcome for o in again} == {OUTCOME_SKIPPED_ALREADY_PROCESSED}
    assert len(_orders(conn)) == count
    assert conn.execute("SELECT COUNT(*) FROM paper_book_strategy_links").fetchone()[0] == count


def test_a_risk_rejection_creates_no_intent_but_keeps_the_decision(stored):
    conn, day = stored
    outcomes = _entry(conn, _eligible(conn), day, cfg=_paper_config(max_order_notional="1"))
    assert {o.outcome for o in outcomes} == {OUTCOME_REJECTED_BY_RISK}
    assert _orders(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM paper_book_strategy_links").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_book_risk_decisions").fetchone()[0] == len(outcomes)
    assert all(o.risk_decision_id for o in outcomes)


def test_a_tampered_stored_signal_fails_closed(stored):
    conn, _ = stored
    row = dict(_eligible(conn)[0])
    row["limit_reference"] = str(Decimal(row["limit_reference"]) + 1)
    with pytest.raises(StrategyBridgeError, match="identity"):
        signal_from_row(row)


def test_strategy_cycle_refuses_unsupported_policy_disabled_books_and_external_books(stored):
    from trading_research.paper_books.config import ExternalBrokerSection

    conn, day = stored
    rows = _eligible(conn)
    clock = lambda: regular_session_close(day)  # noqa: E731
    kwargs = dict(strategy_rows=rows, as_of=clock(), price_provider=None, clock=clock)
    with pytest.raises(StrategyBridgeError, match="BASELINE_ONLY"):
        create_strategy_entry_intents(conn, cfg=_paper_config(), experiment_policy="SHADOW_ENHANCED", **kwargs)
    short = replace(
        _paper_config(), lifecycle=replace(_paper_config().lifecycle, pending_orders=PendingOrdersSection(expire_after_market_days=1)),
    )
    with pytest.raises(StrategyBridgeError, match="expire_after_market_days"):
        create_strategy_entry_intents(conn, cfg=short, **kwargs)
    with pytest.raises(StrategyBridgeError, match="paper_books.enabled"):
        create_strategy_entry_intents(conn, cfg=replace(_paper_config(), enabled=False), **kwargs)
    external = ExternalBrokerSection.__new__(ExternalBrokerSection)
    object.__setattr__(external, "enabled", True)
    object.__setattr__(external, "enabled_book_ids", (BOOK,))
    with pytest.raises(StrategyBridgeError, match="external submission"):
        create_strategy_entry_intents(conn, cfg=replace(_paper_config(), external_broker=external), **kwargs)
    assert _orders(conn) == []


# -- exits (gap 5) ---------------------------------------------------------------

DAY1 = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)  # Monday


def _link_open_position(conn, cfg, *, stop, target, holding, symbol="AAPL", price="100", quantity="100", link=True):
    """Open a long via the normal intent path, then (unless `link` is False) attach a strategy link to its intent."""
    book = cash_ledger.open_book(conn, book_id=BOOK, starting_cash_usd=Decimal("100000"), config_hash=cfg.config_hash, clock=lambda: DAY1)
    snap = valuation.build_portfolio_snapshot(conn, BOOK, DAY1, maximum_price_age_seconds=999999)
    portfolio = risk_module.build_portfolio_context(conn, BOOK, DAY1, snap, symbol, Decimal("0"))
    decision = risk_module.evaluate_paper_risk(
        book_status=book.status, experiment_arm=book.experiment_arm, expected_arm=BOOK, context=portfolio,
        requested_quantity_hint=Decimal(quantity), reference_price=Decimal(price), reference_price_age_seconds=0,
        reference_price_point_in_time_safe=True, risk_config=cfg.risk,
    )
    risk_id = order_intent.persist_risk_decision(conn, BOOK, "c", "rec-x", symbol, decision, snap.snapshot_id, lambda: DAY1)
    intent = order_intent.build_order_intent(
        book_id=BOOK, experiment_arm=BOOK, cycle_id="c", recommendation_id="rec-x", symbol=symbol,
        risk_decision=decision, risk_decision_id=risk_id, portfolio_snapshot_id=snap.snapshot_id,
        config_hash=cfg.config_hash, as_of=DAY1, clock=lambda: DAY1,
    )
    ref = Decimal(price)
    result = execution.submit_and_simulate(
        conn, intent, execution.MarketSimulationInput(bid=ref * Decimal("0.97"), ask=ref * Decimal("0.975")), DAY1,
    )
    assert result["status"] == "FILLED"
    if not link:
        return Decimal(result["fill"]["fill_price"])
    evaluation_id = _eligible_evaluation_id(conn)
    insert_strategy_link(conn, StrategyLink(
        book_id=BOOK, paper_order_intent_id=intent.paper_order_intent_id, recommendation_id="rec-x",
        strategy_id="momentum_breakout", strategy_signal_id="sig-x", strategy_evaluation_id=evaluation_id,
        entry_condition="e", invalidation_condition="i", strategy_stop=stop, strategy_target=target,
        expected_holding_period=holding,
    ), created_at=DAY1.isoformat())
    return Decimal(result["fill"]["fill_price"])


def _eligible_evaluation_id(conn):
    return conn.execute("SELECT strategy_evaluation_id FROM strategy_signals LIMIT 1").fetchone()[0]


def _prices(**by_day):
    provider = DeterministicPriceProvider()
    for day, price in by_day.items():
        provider.register("AAPL", date.fromisoformat(day), Decimal(price))
    return provider


def _exit_decision(conn, cfg, as_of, provider):
    result = run_paper_book_lifecycle(conn, as_of=as_of, paper_books_config=cfg, price_provider=provider)
    return next((d["decision"] for d in result.exit_decisions if d["symbol"] == "AAPL"), None)


def test_generic_position_is_still_stopped_at_the_fixed_percent(stored):
    conn, _ = stored
    cfg = _paper_config()
    _link_open_position(conn, cfg, stop="1", target=None, holding=1, link=False)
    assert _exit_decision(conn, cfg, datetime(2026, 1, 6, 20, tzinfo=timezone.utc), _prices(**{"2026-01-06": "85"})) \
        == "EXIT_STOP_LOSS"


def test_strategy_stop_is_not_tightened_by_the_generic_stop(stored):
    conn, _ = stored
    cfg = _paper_config(stop_loss_percent="0.08")
    _link_open_position(conn, cfg, stop="80", target=None, holding=20)
    provider = _prices(**{"2026-01-06": "90", "2026-01-07": "79"})
    assert _exit_decision(conn, cfg, datetime(2026, 1, 6, 20, tzinfo=timezone.utc), provider) == "HOLD"
    assert _exit_decision(conn, cfg, datetime(2026, 1, 7, 20, tzinfo=timezone.utc), provider) == "EXIT_STOP_LOSS"


def test_strategy_holding_period_replaces_the_generic_one(stored):
    conn, _ = stored
    cfg = _paper_config(maximum_holding_market_days=20)
    _link_open_position(conn, cfg, stop="50", target=None, holding=2)
    provider = _prices(**{"2026-01-06": "100", "2026-01-07": "100"})
    assert _exit_decision(conn, cfg, datetime(2026, 1, 6, 20, tzinfo=timezone.utc), provider) == "HOLD"
    assert _exit_decision(conn, cfg, datetime(2026, 1, 7, 20, tzinfo=timezone.utc), provider) == "EXIT_MAX_HOLDING_PERIOD"


def test_strategy_target_replaces_the_generic_one(stored):
    conn, _ = stored
    cfg = _paper_config(profit_target_percent="0.15")
    _link_open_position(conn, cfg, stop="50", target="130", holding=20)
    provider = _prices(**{"2026-01-06": "120", "2026-01-07": "131"})
    assert _exit_decision(conn, cfg, datetime(2026, 1, 6, 20, tzinfo=timezone.utc), provider) == "HOLD"
    assert _exit_decision(conn, cfg, datetime(2026, 1, 7, 20, tzinfo=timezone.utc), provider) == "EXIT_PROFIT_TARGET"


def test_strategy_identity_survives_to_the_fill_and_the_open_position(stored):
    conn, _ = stored
    cfg = _paper_config()
    _link_open_position(conn, cfg, stop="50", target=None, holding=5)
    fill = pb_repo.list_fills(conn, BOOK)[0] if hasattr(pb_repo, "list_fills") else None
    link = link_for_open_position(conn, BOOK, "AAPL")
    assert link is not None and link.strategy_id == "momentum_breakout"
    if fill is not None:
        assert get_strategy_link(conn, BOOK, fill["paper_order_intent_id"]) == link
    assert link_for_open_position(conn, BOOK, "MSFT") is None


def test_links_are_append_only(stored):
    import sqlite3

    conn, day = stored
    _entry(conn, _eligible(conn), day)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE paper_book_strategy_links SET strategy_stop = '1'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM paper_book_strategy_links")


# -- end to end ------------------------------------------------------------------


def _cycle(tmp_path, conn, day, stage5_config, cfg=None, **overrides):
    kwargs = dict(
        as_of=day, data_source="fixture", snapshot_path=tmp_path / f"cycle-{day}.snapshot.json", conn=conn,
        cfg=cfg or _paper_config(), strategy_config_path=stage5_config, now=NOW,
    )
    kwargs.update(overrides)
    return run_strategy_paper_cycle(**kwargs)


def test_cycle_creates_intents_then_the_next_session_fills_them(tmp_path, stored, stage5_config):
    conn, day = stored
    first = _cycle(tmp_path, conn, day, stage5_config)
    assert first["entries"]["outcome_counts"] == {OUTCOME_INTENT_CREATED_PENDING_FILL: len(_eligible(conn))}
    assert first["lifecycle"]["failure_reasons"] == []
    assert {o["status"] for o in _orders(conn)} == {"PENDING_SUBMISSION"}

    second = _cycle(tmp_path, conn, add_trading_days(day, 1), stage5_config)
    assert second["lifecycle"]["failure_reasons"] == []
    assert set(second["lifecycle"]["reconciliation_statuses"]) == {BOOK}
    filled_orders = [o for o in _orders(conn) if o["status"] == "FILLED"]
    assert second["lifecycle"]["pending_orders_filled"] == len(filled_orders) >= 1
    assert second["lifecycle"]["reconciliation_statuses"][BOOK] == "MATCHED"
    for order in filled_orders:
        assert get_strategy_link(conn, BOOK, order["paper_order_intent_id"]) is not None
        link = link_for_open_position(conn, BOOK, order["symbol"])
        assert link is not None and link.paper_order_intent_id == order["paper_order_intent_id"]
    assert all(d["decision"] == "HOLD" for d in second["lifecycle"]["exit_decisions"])


def test_cycle_rerun_for_the_same_session_adds_nothing(tmp_path, stored, stage5_config):
    conn, day = stored
    _cycle(tmp_path, conn, day, stage5_config)
    orders, links = len(_orders(conn)), conn.execute("SELECT COUNT(*) FROM paper_book_strategy_links").fetchone()[0]
    again = _cycle(tmp_path, conn, day, stage5_config)
    assert set(again["entries"]["outcome_counts"]) == {OUTCOME_SKIPPED_ALREADY_PROCESSED}
    assert len(_orders(conn)) == orders
    assert conn.execute("SELECT COUNT(*) FROM paper_book_strategy_links").fetchone()[0] == links


def test_cycle_refuses_before_stage_5(tmp_path, stored):
    conn, day = stored
    with pytest.raises(BacktestError, match="STAGE_5"):
        _cycle(tmp_path, conn, day, DEFAULT_STRATEGY_CONFIG_PATH)
    assert _orders(conn) == []


def test_cycle_refuses_an_unclosed_session(tmp_path, stored, stage5_config):
    conn, day = stored
    with pytest.raises(BacktestError, match="not closed"):
        _cycle(tmp_path, conn, day, stage5_config, now=regular_session_close(day).replace(hour=1))


def test_cycle_refuses_while_the_shadow_system_is_paused(tmp_path, stored, stage5_config, monkeypatch):
    from types import SimpleNamespace

    from trading_research.forward_testing import paper_cycle

    conn, day = stored
    monkeypatch.setattr(paper_cycle.pause_mod, "current_state", lambda c: SimpleNamespace(state="PAUSED", reason="test"))
    with pytest.raises(BacktestError, match="pause state"):
        _cycle(tmp_path, conn, day, stage5_config)
    assert _orders(conn) == []


# -- boundary --------------------------------------------------------------------


def test_the_strategy_paper_path_adds_no_broker_or_runtime_module_to_the_lifecycle_baseline():
    """`paper_books.config` already pulls in `runtime.client`; the strategy modules must add nothing on top."""
    def loaded(imports: str) -> set[str]:
        code = f"import sys, json\n{imports}\nprint(json.dumps(sorted(sys.modules)))\n"
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
        import json

        return set(json.loads(out.stdout))

    baseline = loaded("import trading_research.paper_books.lifecycle")
    extra = loaded(
        "import trading_research.paper_books.lifecycle, trading_research.paper_books.strategy_bridge, "
        "trading_research.forward_testing.paper_cycle, trading_research.forward_testing.price_provider"
    ) - baseline
    risky = sorted(
        m for m in extra
        if "runtime" in m or "external" in m or "alpaca_paper" in m or "broker" in m
    )
    assert risky == []


# -- CLI -------------------------------------------------------------------------


def _enabled_paper_books_yaml(tmp_path):
    import yaml

    from trading_research.paper_books.config import DEFAULT_PAPER_BOOKS_CONFIG_PATH

    raw = yaml.safe_load(DEFAULT_PAPER_BOOKS_CONFIG_PATH.read_text())
    raw["paper_books"]["enabled"] = True
    raw["paper_books"]["lifecycle"]["enabled"] = True
    raw["paper_books"]["lifecycle"]["exits"]["enabled"] = True
    raw["paper_books"]["lifecycle"]["pending_orders"]["expire_after_market_days"] = 5
    path = tmp_path / "paper_books.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_cli_runs_the_cycle_and_fails_closed_on_the_shipped_config(tmp_path, stored, stage5_config, capsys):
    import json

    from trading_research.cli import main

    _, day = stored
    db = str(tmp_path / "db.sqlite3")
    base = ["paper-book-run-strategy-cycle", "--as-of", day.isoformat(), "--data-source", "fixture",
            "--strategy-config", str(stage5_config), "--db", db]

    assert main(base) == 2  # config/paper_books.yaml ships disabled
    assert "paper_books.enabled is false" in json.loads(capsys.readouterr().out)["error"]

    assert main([*base, "--paper-books-config", str(_enabled_paper_books_yaml(tmp_path))]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["book_id"] == "BASELINE"
    # The shipped risk section enforces the authoritative daily-loss state, which needs a prior
    # session's snapshot as its baseline: the very first session fails closed, creating no intent.
    assert out["entries"]["outcome_counts"] == {OUTCOME_REJECTED_BY_RISK: 1}
    assert "RISK_REJECTED_RISK_STATE_UNAVAILABLE" in out["entries"]["outcomes"][0]["reasons"]
    assert out["entries"]["outcomes"][0]["paper_order_intent_id"] is None


def test_cli_rejects_other_experiment_policies():
    from trading_research.cli import main

    with pytest.raises(SystemExit):
        main(["paper-book-run-strategy-cycle", "--as-of", "2024-01-02", "--data-source", "fixture",
              "--experiment-policy", "BOTH_SEPARATE_PAPER_BOOKS"])
