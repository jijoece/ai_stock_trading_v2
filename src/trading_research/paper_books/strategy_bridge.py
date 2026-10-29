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
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Callable

from ..evidence_providers.economic_calendar import evaluate_economic_event_blackout
from ..research import experiment_policy as ep
from ..storage import paper_books_repositories as pb_repo
from ..storage.paper_books_repositories import EXECUTION_NAMESPACE_LOCAL
from ..storage.strategy_bridge_repositories import StrategyLink, get_link_for_signal, insert_strategy_link
from ..storage.transactions import transaction
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
from .config import PaperBooksConfiguration
from .models import (
    APPROVED_RISK_DECISIONS,
    RISK_REJECTED_DAILY_LOSS_LIMIT,
    RISK_REJECTED_DRAWDOWN_LIMIT,
)

RECOMMENDATION_VERSION = "strategy-recommendation-v1"
# The share of net liquidation value put at risk between the signal's limit and
# its own stop. The same fraction `scheduled_integration` sizes ATR plans with.
RISK_FRACTION = Decimal("0.01")
MINIMUM_PENDING_EXPIRY_MARKET_DAYS = 2

OUTCOME_INTENT_CREATED_PENDING_FILL = "INTENT_CREATED_PENDING_FILL"
OUTCOME_REJECTED_BY_RISK = "REJECTED_BY_RISK"
OUTCOME_SKIPPED_ALREADY_PROCESSED = "SKIPPED_ALREADY_PROCESSED"
OUTCOME_SKIPPED_BOUNDARY = "SKIPPED_BOUNDARY_VIOLATION"
OUTCOME_SKIPPED_FUTURE_SIGNAL = "SKIPPED_SIGNAL_AFTER_AS_OF"


class StrategyBridgeError(RuntimeError):
    """The strategy cycle cannot run (configuration, policy, or a failed integrity check)."""


@dataclass(frozen=True)
class StrategyEntryOutcome:
    symbol: str
    strategy_id: str
    strategy_signal_id: str
    outcome: str
    reasons: tuple[str, ...] = ()
    risk_decision_id: str | None = None
    paper_order_intent_id: str | None = None


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


def _require_runnable(cfg: PaperBooksConfiguration, experiment_policy: str) -> str:
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
    book_id = cfg.baseline.book_id
    if not cfg.is_book_enabled(book_id):
        raise StrategyBridgeError(f"paper book {book_id!r} is not enabled in config/paper_books.yaml")
    if cfg.external_broker.enabled and book_id in cfg.external_broker.enabled_book_ids:
        raise StrategyBridgeError(
            f"paper book {book_id!r} is enabled for external submission; the strategy cycle is local simulated only"
        )
    return book_id


def create_strategy_entry_intents(
    conn, *, strategy_rows: list, as_of: datetime, cfg: PaperBooksConfiguration, price_provider: Any,
    clock: Callable[[], datetime], experiment_policy: str = ep.BASELINE_ONLY, economic_events=None,
) -> list[StrategyEntryOutcome]:
    """Turn stored ELIGIBLE signal rows into risk-gated, pending paper order intents."""
    book_id = _require_runnable(cfg, experiment_policy)
    book_def = cfg.book(book_id)
    book = cash_ledger.open_book(
        conn, book_id=book_id, starting_cash_usd=book_def.starting_cash_usd, config_hash=cfg.config_hash, clock=clock,
    )
    authoritative_enabled = "max_daily_loss_fraction" in cfg.raw.get("paper_books", {}).get("risk", {})
    outcomes: list[StrategyEntryOutcome] = []
    committed_notional = Decimal("0")

    for row in strategy_rows:
        signal = signal_from_row(row)

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

        if get_link_for_signal(conn, book_id, context.strategy_signal_id) is not None \
                or pb_repo.load_risk_decision(conn, risk_decision_id) is not None:
            outcomes.append(outcome(OUTCOME_SKIPPED_ALREADY_PROCESSED, "this signal was already evaluated for the book"))
            continue
        if signal.data_as_of is None or signal.data_as_of > as_of:
            outcomes.append(outcome(OUTCOME_SKIPPED_FUTURE_SIGNAL, "signal data is after the cycle as_of"))
            continue

        reference_price = signal.limit_reference
        assert reference_price is not None  # guaranteed by build_strategy_order_intent_context / the contract
        snap = valuation.build_portfolio_snapshot(
            conn, book_id, as_of, price_provider=price_provider,
            maximum_price_age_seconds=cfg.valuation.maximum_price_age_seconds,
        )
        portfolio = risk_module.build_portfolio_context(conn, book_id, as_of, snap, signal.symbol, committed_notional)

        # Risk-based size from the book's equity and the signal's own stop distance.
        # The signal never sets a size; risk.py below caps whatever this proposes.
        quantity_hint = None
        if portfolio.net_liquidation_value_usd is not None and portfolio.net_liquidation_value_usd > 0:
            risk_per_share = reference_price - context.strategy_stop
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
        )
        approved = decision.decision in APPROVED_RISK_DECISIONS
        # A rejection is final and is persisted on its own. An approved decision
        # is persisted in the same transaction as its intent below, so an
        # interruption can never leave an approved decision without an intent
        # (the retry check above would otherwise skip the signal forever).
        persisted_risk_id = risk_decision_id
        if not approved:
            order_intent.persist_risk_decision(
                conn, book_id, cycle_id, recommendation_id, signal.symbol, decision, snap.snapshot_id, clock,
            )
        if daily_state is not None and decision.decision in (
            RISK_REJECTED_DAILY_LOSS_LIMIT, RISK_REJECTED_DRAWDOWN_LIMIT,
        ):
            safety_pause.pause_for_risk_state(
                conn, book_id=book_id, reason_code=decision.decision, risk_state_id=daily_state.risk_state_id,
                reason="; ".join(decision.reasons), at=clock(),
            )
        if not approved:
            outcomes.append(outcome(OUTCOME_REJECTED_BY_RISK, decision.decision, *decision.reasons, risk_decision_id=persisted_risk_id))
            continue

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
        )
        # The same atomic registration `execution.submit_and_simulate` performs for a
        # new intent (local namespace claim, intent row, cash reservation), plus the
        # strategy link. The later lifecycle fill requires the claim and a reservation
        # equal to the frozen notional, so a bare intent row would never fill.
        with transaction(conn):
            order_intent.persist_risk_decision(
                conn, book_id, cycle_id, recommendation_id, signal.symbol, decision, snap.snapshot_id, clock,
                commit=False,
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
        committed_notional += intent.notional_usd
        outcomes.append(outcome(
            OUTCOME_INTENT_CREATED_PENDING_FILL, risk_decision_id=persisted_risk_id,
            paper_order_intent_id=intent.paper_order_intent_id,
        ))
    return outcomes
