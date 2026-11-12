"""Bridge from stored strategy signals into the local paper book
(docs/paper-testing-plan.md Phase 3, decision P2).

A stored ELIGIBLE signal becomes a strategy recommendation (an id derived from
the signal's content id plus a `paper_book_strategy_links` row carrying the
`StrategyOrderIntentContext` fields and the signal's own exit levels). It then
goes through the unchanged `risk.evaluate_paper_risk` and
`order_intent.build_order_intent`; nothing here approves, sizes or fills an
order on its own. The intent is left PENDING_SUBMISSION and is filled by the
existing lifecycle on a later session. Local simulated, BASELINE book only:
this module never touches an external broker and refuses to run for a book
that is enabled for external submission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Callable, Mapping

from ..evidence_providers.economic_calendar import evaluate_economic_event_blackout
from ..research import experiment_policy as ep
from ..storage import paper_books_repositories as pb_repo
from ..storage.paper_books_repositories import EXECUTION_NAMESPACE_LOCAL
from ..storage.strategy_bridge_repositories import (
    StrategyLink,
    get_link_for_signal,
    insert_strategy_link,
    latest_linked_data_as_of,
    save_decision_controls,
)
from ..storage.transactions import transaction
from ..strategies.config import ACTIVATION_STAGES
from ..strategies.controls import (
    MINIMUM_PAPER_STAGE,
    STRATEGY_MODE_PAPER,
    StrategyControls,
    signal_expires_at,
)
from ..strategies.selector import signal_rank_key
from ..strategies.contracts import (
    StrategySignal,
    StrategyStatus,
    derive_strategy_evaluation_id,
    derive_strategy_signal_content_id,
)
from ..strategies.execution_boundary import StrategyExecutionBoundaryError, build_strategy_order_intent_context
from . import cash_ledger, order_intent, reconciliation, safety_pause, valuation
from . import daily_risk as daily_risk_module
from . import risk as risk_module
from .allocation import normalize_stop
from .config import PaperBooksConfiguration
from .strategy_exposure import read_book_exposure
from .models import (
    APPROVED_RISK_DECISIONS,
    INTENT_STATUS_PENDING_SUBMISSION,
    ORDER_SIDE_BUY,
    RISK_REJECTED_DAILY_LOSS_LIMIT,
    RISK_REJECTED_DRAWDOWN_LIMIT,
)

RECOMMENDATION_VERSION = "strategy-recommendation-v1"
# The share of net liquidation value put at risk between the signal's limit and
# its own stop. The same fraction `scheduled_integration` sizes ATR plans with.
RISK_FRACTION = Decimal("0.01")
MINIMUM_PENDING_EXPIRY_MARKET_DAYS = 2
# How many times one signal's risk decision is recomputed after another writer changed the book's
# risk-relevant state between the decision and the write lock. Exhausting it skips the signal.
MAXIMUM_STALE_RISK_STATE_ATTEMPTS = 3

OUTCOME_INTENT_CREATED_PENDING_FILL = "INTENT_CREATED_PENDING_FILL"
OUTCOME_REJECTED_BY_RISK = "REJECTED_BY_RISK"
OUTCOME_SKIPPED_ALREADY_PROCESSED = "SKIPPED_ALREADY_PROCESSED"
OUTCOME_SKIPPED_BOUNDARY = "SKIPPED_BOUNDARY_VIOLATION"
OUTCOME_SKIPPED_FUTURE_SIGNAL = "SKIPPED_SIGNAL_AFTER_AS_OF"
OUTCOME_SKIPPED_STALE_RISK_STATE = "SKIPPED_RISK_STATE_CHANGED"
OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED = "SKIPPED_STRATEGY_NOT_ENABLED_FOR_PAPER"
OUTCOME_SKIPPED_SYMBOL_NOT_ALLOWED = "SKIPPED_SYMBOL_NOT_IN_STRATEGY_ALLOWLIST"
OUTCOME_SKIPPED_EXPIRED = "SKIPPED_SIGNAL_EXPIRED"
OUTCOME_SKIPPED_OUT_OF_ORDER = "SKIPPED_SIGNAL_OUT_OF_ORDER"


class StrategyBridgeError(RuntimeError):
    """The strategy cycle cannot run (configuration, policy, or a failed integrity check)."""


class _StaleRiskState(Exception):
    """The book's risk-relevant state changed after the risk decision was computed."""


@dataclass(frozen=True)
class StrategyEntryOutcome:
    symbol: str
    strategy_id: str
    strategy_signal_id: str
    outcome: str
    reasons: tuple[str, ...] = ()
    risk_decision_id: str | None = None
    paper_order_intent_id: str | None = None
    # Identity of the strategy controls the persisted risk decision was evaluated under; see
    # `paper_book_decision_strategy_controls`. None when no decision was persisted.
    strategy_controls_hash: str | None = None


def _controls_for(controls: Mapping[str, StrategyControls] | None, strategy_id: str) -> StrategyControls | None:
    """The strategy's controls. No mapping means the paper-mode defaults; a mapping that lacks the strategy
    means the strategy is not enabled (fails closed)."""
    if controls is None:
        return StrategyControls(strategy_id=strategy_id, mode=STRATEGY_MODE_PAPER, activation_stage=MINIMUM_PAPER_STAGE)
    return controls.get(strategy_id)


def derive_strategy_recommendation_id(strategy_signal_id: str) -> str:
    """Stable per signal content, so a retried run resolves to the same recommendation and intent."""
    digest = hashlib.sha256(f"{RECOMMENDATION_VERSION}:{strategy_signal_id}".encode()).hexdigest()[:32]
    return f"strat-rec-{digest}"


def _optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def signal_from_row(row) -> StrategySignal:
    """Rebuild the `StrategySignal` behind a `strategy_signals` row and prove it is the same one.

    Fails closed if the rebuilt signal's content or evaluation id differs from
    the stored one: a bridge must never act on a row that does not describe
    the signal it claims to."""
    signal = StrategySignal(
        strategy_id=row["strategy_id"], strategy_version=row["strategy_version"], symbol=row["symbol"],
        signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
        data_as_of=datetime.fromisoformat(row["data_as_of"]) if row["data_as_of"] else None,
        status=StrategyStatus(row["status"]), signal_strength=row["signal_strength"],
        entry_reference=_optional_decimal(row["entry_reference"]),
        limit_reference=_optional_decimal(row["limit_reference"]),
        invalidation_price=_optional_decimal(row["invalidation_price"]),
        initial_stop_reference=_optional_decimal(row["initial_stop_reference"]),
        target_reference=_optional_decimal(row["target_reference"]),
        expected_holding_period=row["expected_holding_period"],
        reason_codes=tuple(json.loads(row["reason_codes_json"])), factor_values=json.loads(row["factor_values_json"]),
        data_quality=row["data_quality"], configuration_hash=row["configuration_hash"],
    )
    if derive_strategy_signal_content_id(signal) != row["signal_content_id"] \
            or derive_strategy_evaluation_id(signal) != row["strategy_evaluation_id"]:
        raise StrategyBridgeError(
            f"stored signal {row['strategy_evaluation_id']} does not reproduce its recorded identity"
        )
    return signal


def validate_strategy_cycle_config(cfg: PaperBooksConfiguration, experiment_policy: str) -> str:
    """Fail closed unless `cfg` describes a local simulated BASELINE-only strategy cycle; returns the book id.

    Callable before anything is fetched or written: the lifecycle that runs ahead of entries processes
    every enabled book and enqueues external submissions, so the whole configuration, not just the entry
    step, must be acceptable before it starts."""
    if not cfg.enabled:
        raise StrategyBridgeError("paper_books.enabled is false — the strategy cycle fails closed")
    if not cfg.lifecycle.enabled:
        raise StrategyBridgeError("paper_books.lifecycle.enabled is false — the strategy cycle fails closed")
    if experiment_policy != ep.BASELINE_ONLY:
        raise StrategyBridgeError(
            f"experiment policy {experiment_policy!r} is not supported for strategy cycles; "
            f"only {ep.BASELINE_ONLY} (the strategy alone) is"
        )
    if cfg.lifecycle.pending_orders.expire_after_market_days < MINIMUM_PENDING_EXPIRY_MARKET_DAYS:
        raise StrategyBridgeError(
            "paper_books.lifecycle.pending_orders.expire_after_market_days must be at least "
            f"{MINIMUM_PENDING_EXPIRY_MARKET_DAYS}: a strategy intent is created after the close and fills in the "
            "next session's lifecycle, so a shorter expiry would cancel it before it could fill"
        )
    if not cfg.lifecycle.exits.enabled:
        raise StrategyBridgeError(
            "paper_books.lifecycle.exits.enabled is false — the lifecycle would never evaluate exits, so a "
            "signal's stop, target and maximum holding period would go unenforced; the strategy cycle fails closed"
        )
    state_policies = [
        name for name, section in (
            ("trailing_stop", cfg.lifecycle.trailing_stop), ("breakeven", cfg.lifecycle.breakeven),
            ("partial_profit", cfg.lifecycle.partial_profit),
        ) if section.enabled
    ]
    if state_policies:
        raise StrategyBridgeError(
            f"paper_books.lifecycle.{'/'.join(state_policies)} enabled — those policies run on per-position "
            "lifecycle state that only the scheduled-integration path creates, and strategy fills do not get one, "
            "so they would silently not apply; a strategy position is exited by its own signal's stop, target and "
            "holding period. Disable them for strategy cycles"
        )
    book_id = cfg.baseline.book_id
    if not cfg.is_book_enabled(book_id):
        raise StrategyBridgeError(f"paper book {book_id!r} is not enabled in config/paper_books.yaml")
    if cfg.external_broker.enabled and book_id in cfg.external_broker.enabled_book_ids:
        raise StrategyBridgeError(
            f"paper book {book_id!r} is enabled for external submission; the strategy cycle is local simulated only"
        )
    if cfg.is_book_enabled(cfg.enhanced.book_id):
        raise StrategyBridgeError(
            f"paper book {cfg.enhanced.book_id!r} (ENHANCED) is enabled; the lifecycle processes every enabled book, "
            "so a BASELINE_ONLY strategy cycle would fill, exit and reconcile it too. Disable it for strategy cycles"
        )
    return book_id


def _committed_daily_new_notional(conn, book_id: str, market_date: date) -> Decimal:
    """BUY notional already committed for the book on `market_date`, whichever process created it."""
    return sum(
        (
            Decimal(order["notional_usd"]) for order in pb_repo.list_order_intents(conn, book_id)
            if order["side"] == ORDER_SIDE_BUY and datetime.fromisoformat(order["as_of"]).date() == market_date
        ),
        Decimal("0"),
    )


def _pending_new_position_count(conn, book_id: str) -> int:
    """Distinct symbols with a pending BUY that will open a position the book does not hold yet.

    `build_portfolio_context` counts only filled positions, and the later lifecycle fill does not re-check
    `max_open_positions`, so without this a batch of approvals can fill past the limit."""
    held = {position["symbol"] for position in pb_repo.list_positions(conn, book_id, open_only=True)}
    return len({
        order["symbol"] for order in pb_repo.list_order_intents(conn, book_id)
        if order["side"] == ORDER_SIDE_BUY and order["status"] == INTENT_STATUS_PENDING_SUBMISSION
        and order["symbol"] not in held
    })


def _pending_symbol_notional(conn, book_id: str, symbol: str) -> Decimal:
    """Notional reserved by pending BUYs of `symbol`, which no filled position reflects yet.

    The position-weight and symbol-concentration caps read only the filled position value, and the later
    lifecycle fill does not re-check them, so successive approvals for one symbol would each get a full allowance."""
    return sum(
        (
            Decimal(order["notional_usd"]) for order in pb_repo.list_order_intents(conn, book_id)
            if order["side"] == ORDER_SIDE_BUY and order["status"] == INTENT_STATUS_PENDING_SUBMISSION
            and order["symbol"] == symbol
        ),
        Decimal("0"),
    )


def _risk_state_fingerprint(conn, book_id: str) -> tuple:
    """Everything a risk decision reads that another writer can change: cash movements, orders, fills, pauses."""
    counts = tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table} WHERE book_id = ?", (book_id,)).fetchone()[0]
        for table in ("paper_book_cash_ledger", "paper_book_orders", "paper_book_fills")
    )
    return (*counts, safety_pause.is_paused(conn, book_id))


def create_strategy_entry_intents(
    conn, *, strategy_rows: list, as_of: datetime, cfg: PaperBooksConfiguration, price_provider: Any,
    clock: Callable[[], datetime], experiment_policy: str = ep.BASELINE_ONLY, economic_events=None,
    controls: Mapping[str, StrategyControls] | None = None,
) -> list[StrategyEntryOutcome]:
    """Turn stored ELIGIBLE signal rows into risk-gated, pending paper order intents.

    Each approval is registered under `BEGIN IMMEDIATE`, and only if the book's risk-relevant state is
    exactly what the decision was computed from; otherwise the decision is recomputed from the committed
    state. Two concurrent cycles can therefore never both approve against the same stale cash, position
    count or daily-notional state.

    Signals are handled in one deterministic order, strongest first (`selector.signal_rank_key`, then
    strategy, symbol and evaluation id), so when signals compete for the same cash or symbol the same
    one wins on every run. Each strategy's `controls` (mode, stage, symbol allowlist, budgets, signal
    validity) gate its signals; with `controls=None` every strategy gets the paper-mode defaults, and
    with a mapping a strategy missing from it is not enabled. A signal past its validity window, or
    older than a signal of the same strategy and symbol the book already acted on, creates no intent."""
    book_id = validate_strategy_cycle_config(cfg, experiment_policy)
    book_def = cfg.book(book_id)
    book = cash_ledger.open_book(
        conn, book_id=book_id, starting_cash_usd=book_def.starting_cash_usd, config_hash=cfg.config_hash, clock=clock,
    )
    authoritative_enabled = "max_daily_loss_fraction" in cfg.raw.get("paper_books", {}).get("risk", {})
    outcomes: list[StrategyEntryOutcome] = []

    # Rebuild and verify every signal before anything is written, so a row that does not reproduce its
    # recorded identity stops the batch instead of leaving it half done.
    prepared = sorted(
        ((row, signal_from_row(row)) for row in strategy_rows),
        key=lambda pair: (
            signal_rank_key(pair[1]), pair[1].strategy_id, pair[1].symbol, pair[0]["strategy_evaluation_id"],
        ),
    )

    for row, signal in prepared:

        def outcome(code: str, *reasons: str, **extra) -> StrategyEntryOutcome:
            return StrategyEntryOutcome(
                symbol=signal.symbol, strategy_id=signal.strategy_id,
                strategy_signal_id=derive_strategy_signal_content_id(signal), outcome=code,
                reasons=tuple(reasons), **extra,
            )

        try:
            context = build_strategy_order_intent_context(signal)
        except StrategyExecutionBoundaryError as exc:
            outcomes.append(outcome(OUTCOME_SKIPPED_BOUNDARY, str(exc)))
            continue
        recommendation_id = derive_strategy_recommendation_id(context.strategy_signal_id)
        cycle_id = f"strategy:{row['session_date']}"
        risk_decision_id = "pb-risk-" + hashlib.sha256(
            f"{book_id}:{cycle_id}:{recommendation_id}:{signal.symbol}".encode()
        ).hexdigest()[:32]

        def already_processed() -> bool:
            return get_link_for_signal(conn, book_id, context.strategy_signal_id) is not None \
                or pb_repo.load_risk_decision(conn, risk_decision_id) is not None

        def attempt() -> StrategyEntryOutcome:
            """One decide-then-register pass; raises `_StaleRiskState` if the book changed underneath it."""
            if already_processed():
                return outcome(OUTCOME_SKIPPED_ALREADY_PROCESSED, "this signal was already evaluated for the book")
            if signal.data_as_of is None or signal.data_as_of > as_of:
                return outcome(OUTCOME_SKIPPED_FUTURE_SIGNAL, "signal data is after the cycle as_of")
            strategy_controls = _controls_for(controls, signal.strategy_id)
            if strategy_controls is None:
                return outcome(OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED, "the strategy has no controls entry")
            if strategy_controls.mode != STRATEGY_MODE_PAPER or ACTIVATION_STAGES.index(
                strategy_controls.activation_stage
            ) < ACTIVATION_STAGES.index(MINIMUM_PAPER_STAGE):
                return outcome(
                    OUTCOME_SKIPPED_STRATEGY_NOT_ENABLED,
                    f"strategy mode is {strategy_controls.mode!r} at {strategy_controls.activation_stage}; "
                    f"the paper book needs mode 'paper' at {MINIMUM_PAPER_STAGE} or later",
                )
            if not strategy_controls.allows_symbol(signal.symbol):
                return outcome(OUTCOME_SKIPPED_SYMBOL_NOT_ALLOWED, f"{signal.symbol} is not in the strategy's allowlist")
            expires_at = signal_expires_at(date.fromisoformat(row["session_date"]), strategy_controls.signal_validity_sessions)
            if as_of > expires_at:
                return outcome(OUTCOME_SKIPPED_EXPIRED, f"signal expired at {expires_at.isoformat()}")
            newest_acted_on = latest_linked_data_as_of(conn, book_id, signal.strategy_id, signal.symbol)
            if newest_acted_on is not None and signal.data_as_of < newest_acted_on:
                return outcome(
                    OUTCOME_SKIPPED_OUT_OF_ORDER,
                    f"the book already acted on a {signal.strategy_id} signal for {signal.symbol} "
                    f"from {newest_acted_on.isoformat()}, which is later than this one",
                )

            reference_price = signal.limit_reference
            assert reference_price is not None  # guaranteed by build_strategy_order_intent_context / the contract
            observed_state = _risk_state_fingerprint(conn, book_id)
            snap = valuation.build_portfolio_snapshot(
                conn, book_id, as_of, price_provider=price_provider,
                maximum_price_age_seconds=cfg.valuation.maximum_price_age_seconds,
            )
            portfolio = risk_module.build_portfolio_context(
                conn, book_id, as_of, snap, signal.symbol, _committed_daily_new_notional(conn, book_id, as_of.date()),
            )
            portfolio = replace(
                portfolio, open_position_count=portfolio.open_position_count + _pending_new_position_count(conn, book_id),
                current_position_market_value_usd=(portfolio.current_position_market_value_usd or Decimal("0"))
                + _pending_symbol_notional(conn, book_id, signal.symbol),
            )

            # Risk-based size from the book's equity and the signal's own stop distance.
            # The signal never sets a size; risk.py below caps whatever this proposes.
            # A stop at or above the limit price has no risk distance to size from; leaving the hint unset
            # makes the risk gate reject (and persist) the decision instead of dividing by zero mid-batch.
            quantity_hint = None
            risk_per_share = reference_price - context.strategy_stop
            if portfolio.net_liquidation_value_usd is not None and portfolio.net_liquidation_value_usd > 0 \
                    and risk_per_share > 0:
                quantity_hint = (
                    portfolio.net_liquidation_value_usd * RISK_FRACTION / risk_per_share
                ).to_integral_value(rounding=ROUND_FLOOR)

            daily_state = None
            if authoritative_enabled:
                try:
                    reconciliation.reconcile_book(conn, book_id, as_of)
                    daily_state = daily_risk_module.calculate_and_persist_daily_risk_state(
                        conn, book_id=book_id, market_date=as_of.date(), as_of=as_of, config_hash=cfg.config_hash,
                        require_reconciled=cfg.risk.require_reconciled_risk_state,
                    )
                except daily_risk_module.DailyRiskStateError:
                    daily_state = None
            blackout_decision = evaluate_economic_event_blackout(
                as_of=as_of, events=economic_events, configuration=cfg.lifecycle.economic_event_blackout,
            )
            if authoritative_enabled:
                pb_repo.save_economic_blackout_decision(
                    conn, book_id=book_id, order_evaluation_id=recommendation_id, as_of=as_of,
                    decision=blackout_decision, created_at=clock(),
                )

            decision = risk_module.evaluate_paper_risk(
                book_status="PAUSED" if safety_pause.is_paused(conn, book_id) else book.status,
                experiment_arm=book.experiment_arm, expected_arm=book.experiment_arm, context=portfolio,
                requested_quantity_hint=quantity_hint, reference_price=reference_price,
                reference_price_age_seconds=int((as_of - signal.data_as_of).total_seconds()),
                reference_price_point_in_time_safe=True, risk_config=cfg.risk, daily_risk_state=daily_state,
                economic_blackout_decision=blackout_decision, enforce_authoritative_state=authoritative_enabled,
                # Symbol ownership, stop validity and the risk and capital budgets. Read from the same
                # committed state the fingerprint above guards, so a change underneath forces a recompute.
                allocation=read_book_exposure(conn, book_id).inputs_for(
                    strategy_id=signal.strategy_id, symbol=signal.symbol, stop=context.strategy_stop,
                    limits=cfg.risk.allocation, capital_budget_usd=strategy_controls.capital_budget_usd,
                    risk_budget_fraction=strategy_controls.risk_budget_fraction,
                ),
            )
            approved = decision.decision in APPROVED_RISK_DECISIONS
            # A rejection is final and is persisted on its own. An approved decision
            # is persisted in the same transaction as its intent below, so an
            # interruption can never leave an approved decision without an intent
            # (the retry check above would otherwise skip the signal forever).
            # Because a stored rejection is never recomputed, it gets the same guard as an approval: under
            # the write lock it is stored only if the committed state it was decided from is unchanged,
            # otherwise a rejection on exposure that has since closed would suppress a valid signal forever.
            persisted_risk_id = risk_decision_id
            if not approved:
                with transaction(conn):
                    if already_processed():
                        return outcome(OUTCOME_SKIPPED_ALREADY_PROCESSED, "this signal was already evaluated for the book")
                    if _risk_state_fingerprint(conn, book_id) != observed_state:
                        raise _StaleRiskState
                    order_intent.persist_risk_decision(
                        conn, book_id, cycle_id, recommendation_id, signal.symbol, decision, snap.snapshot_id, clock,
                        commit=False,
                    )
                    save_decision_controls(
                        conn, risk_decision_id=persisted_risk_id, book_id=book_id, controls=strategy_controls,
                        created_at=clock().isoformat(),
                    )
            if daily_state is not None and decision.decision in (
                RISK_REJECTED_DAILY_LOSS_LIMIT, RISK_REJECTED_DRAWDOWN_LIMIT,
            ):
                safety_pause.pause_for_risk_state(
                    conn, book_id=book_id, reason_code=decision.decision, risk_state_id=daily_state.risk_state_id,
                    reason="; ".join(decision.reasons), at=clock(),
                )
            if not approved:
                return outcome(
                    OUTCOME_REJECTED_BY_RISK, decision.decision, *decision.reasons, risk_decision_id=persisted_risk_id,
                    strategy_controls_hash=strategy_controls.controls_hash,
                )

            intent = order_intent.build_order_intent(
                book_id=book_id, experiment_arm=book.experiment_arm, cycle_id=cycle_id,
                recommendation_id=recommendation_id, symbol=signal.symbol, risk_decision=decision,
                risk_decision_id=persisted_risk_id, portfolio_snapshot_id=snap.snapshot_id,
                config_hash=cfg.config_hash, as_of=as_of, clock=clock,
            )
            assert intent is not None  # decision.decision is one of APPROVED_RISK_DECISIONS
            link = StrategyLink(
                book_id=book_id, paper_order_intent_id=intent.paper_order_intent_id, recommendation_id=recommendation_id,
                strategy_id=context.strategy_id, strategy_signal_id=context.strategy_signal_id,
                strategy_evaluation_id=row["strategy_evaluation_id"], entry_condition=context.entry_condition,
                invalidation_condition=context.invalidation_condition, strategy_stop=str(context.strategy_stop),
                strategy_target=None if signal.target_reference is None else str(signal.target_reference),
                expected_holding_period=context.expected_holding_period,
                # Freeze the stop the decision was risked at, so a later tick_size change cannot re-risk it.
                allocation_stop=str(normalize_stop(context.strategy_stop, cfg.risk.allocation.tick_size)),
            )
            # The same atomic registration `execution.submit_and_simulate` performs for a
            # new intent (local namespace claim, intent row, cash reservation), plus the
            # strategy link. The later lifecycle fill requires the claim and a reservation
            # equal to the frozen notional, so a bare intent row would never fill.
            #
            # BEGIN IMMEDIATE takes the database write lock; once held, no other writer can change
            # the book, so the decision is only registered if the state it was computed from is still
            # the committed state.
            with transaction(conn):
                if already_processed():
                    return outcome(OUTCOME_SKIPPED_ALREADY_PROCESSED, "this signal was already evaluated for the book")
                if _risk_state_fingerprint(conn, book_id) != observed_state:
                    raise _StaleRiskState
                order_intent.persist_risk_decision(
                    conn, book_id, cycle_id, recommendation_id, signal.symbol, decision, snap.snapshot_id, clock,
                    commit=False,
                )
                save_decision_controls(
                    conn, risk_decision_id=persisted_risk_id, book_id=book_id, controls=strategy_controls,
                    created_at=clock().isoformat(),
                )
                pb_repo.claim_execution_namespace(
                    conn, book_id, intent.paper_order_intent_id, EXECUTION_NAMESPACE_LOCAL, clock(),
                    "local_simulator", commit=False,
                )
                pb_repo.save_order_intent(conn, intent, commit=False)
                cash_ledger.reserve_for_order(
                    conn, book_id, intent.paper_order_intent_id, intent.notional_usd, clock(), commit=False,
                )
                insert_strategy_link(conn, link, created_at=clock().isoformat())
            return outcome(
                OUTCOME_INTENT_CREATED_PENDING_FILL, risk_decision_id=persisted_risk_id,
                paper_order_intent_id=intent.paper_order_intent_id,
                strategy_controls_hash=strategy_controls.controls_hash,
            )

        result = None
        for _ in range(MAXIMUM_STALE_RISK_STATE_ATTEMPTS):
            try:
                result = attempt()
            except _StaleRiskState:
                continue
            break
        outcomes.append(result or outcome(
            OUTCOME_SKIPPED_STALE_RISK_STATE,
            f"the book's risk state kept changing across {MAXIMUM_STALE_RISK_STATE_ATTEMPTS} attempts; "
            "no intent was created",
        ))
    return outcomes
