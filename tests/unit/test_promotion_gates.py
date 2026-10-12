"""Unit tests for research/promotion.py — Milestone 6 docs/milestone-6.md
Step 22 category K."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_research.evaluation import metrics
from trading_research.evaluation.models import RecommendationEvaluation
from trading_research.research.promotion import (
    PromotionConfigError,
    PromotionGateConfig,
    PromotionGateInputs,
    PromotionMetricInput,
    STATUS_ELIGIBLE_FOR_PAPER,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PREFERRED_FOR_PAPER,
    STATUS_REJECTED,
    STATUS_ROLLBACK_REQUIRED,
    STATUS_SHADOW_ONLY,
    evaluate_promotion,
)


def _config(**overrides) -> PromotionGateConfig:
    base = dict(
        policy_version="v1", minimum_completed_evaluations=20, minimum_market_regimes=1,
        max_incomplete_analysis_rate=0.3, max_unsupported_claim_rate=0.05, max_provider_failure_rate=0.2,
        max_retry_rate=0.5, min_reproducibility_rate=0.9, preferred_excess_return_margin=0.02,
    )
    base.update(overrides)
    return PromotionGateConfig(**base)


def _inputs(**overrides) -> PromotionGateInputs:
    base = dict(
        completed_evaluations=50, market_regimes_observed=2,
        excess_return_enhanced=PromotionMetricInput(status="OK", value=0.05),
        excess_return_baseline=PromotionMetricInput(status="OK", value=0.01),
        max_drawdown_enhanced=PromotionMetricInput(status="OK", value=-0.05),
        max_drawdown_baseline=PromotionMetricInput(status="OK", value=-0.05),
        incomplete_analysis_rate=0.0, unsupported_claim_rate=0.0, provider_failure_rate=0.0,
        retry_rate=0.0, reproducibility_rate=1.0,
    )
    base.update(overrides)
    return PromotionGateInputs(**base)


def test_allow_live_promotion_true_fails_closed_at_construction():
    with pytest.raises(PromotionConfigError):
        _config(allow_live_promotion=True)


def test_insufficient_sample_below_minimum_evaluations():
    decision = evaluate_promotion(_inputs(completed_evaluations=5), _config())
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_insufficient_sample_below_minimum_regimes():
    decision = evaluate_promotion(_inputs(market_regimes_observed=0), _config())
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_safety_regression_rejected_when_not_currently_preferred():
    decision = evaluate_promotion(_inputs(unsupported_claim_rate=0.5, currently_preferred=False), _config())
    assert decision.status == STATUS_REJECTED


def test_safety_regression_rollback_required_when_currently_preferred():
    decision = evaluate_promotion(_inputs(unsupported_claim_rate=0.5, currently_preferred=True), _config())
    assert decision.status == STATUS_ROLLBACK_REQUIRED


def test_drawdown_regression_is_a_safety_failure():
    decision = evaluate_promotion(
        _inputs(
            max_drawdown_enhanced=PromotionMetricInput(status="OK", value=-0.30),
            max_drawdown_baseline=PromotionMetricInput(status="OK", value=-0.05),
        ),
        _config(),
    )
    assert decision.status == STATUS_REJECTED


def _completed(rec_id: str, net_return: str) -> RecommendationEvaluation:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    return RecommendationEvaluation(
        recommendation_id=rec_id, horizon_trading_days=1, status="COMPLETED", evaluation_date=date(2026, 7, 14),
        benchmark_symbol="SPY", recommendation_price=Decimal("10"), execution_price=Decimal("10"),
        ending_symbol_price=Decimal("11"), ending_benchmark_price=Decimal("11"),
        benchmark_price_at_execution=Decimal("10"), gross_return=Decimal(net_return),
        net_return=Decimal(net_return), benchmark_return=Decimal("0"), excess_return=Decimal(net_return),
        evaluated_at=now,
    )


def _drawdown_input(*returns: str) -> PromotionMetricInput:
    result = metrics.max_drawdown([_completed(f"rec-{i}", r) for i, r in enumerate(returns)])
    return PromotionMetricInput(status=result.status, value=result.value)


@pytest.mark.parametrize(("currently_preferred", "regression_status"), [
    (False, STATUS_REJECTED), (True, STATUS_ROLLBACK_REQUIRED),
])
def test_equal_drawdowns_with_float_noise_are_not_a_safety_regression(currently_preferred, regression_status):
    # PR #35 review r4245830182: both curves have an exact -0.20 drawdown,
    # but empyrical's float cumulative product reports -0.2000000000000001
    # for one of them. The removed Decimal formula reported -0.20 for both.
    enhanced = _drawdown_input("0.19", "-0.20", "0.01", "0.01", "0.01")
    baseline = _drawdown_input("-0.20", "0.01", "0.01", "0.01", "0.01")
    assert enhanced.value != baseline.value  # the float noise is real
    decision = evaluate_promotion(
        _inputs(max_drawdown_enhanced=enhanced, max_drawdown_baseline=baseline, currently_preferred=currently_preferred),
        _config(),
    )
    assert decision.status == STATUS_PREFERRED_FOR_PAPER
    assert decision.status != regression_status


@pytest.mark.parametrize(("enhanced", "baseline"), [
    (-0.2000001, -0.2),
    (Decimal("-0.2000001"), Decimal("-0.2")),
])
def test_small_real_drawdown_regression_beyond_tolerance_is_still_rejected(enhanced, baseline):
    decision = evaluate_promotion(
        _inputs(
            max_drawdown_enhanced=PromotionMetricInput(status="OK", value=enhanced),
            max_drawdown_baseline=PromotionMetricInput(status="OK", value=baseline),
        ),
        _config(),
    )
    assert decision.status == STATUS_REJECTED


def test_equal_decimal_drawdowns_are_not_a_safety_regression():
    decision = evaluate_promotion(
        _inputs(
            max_drawdown_enhanced=PromotionMetricInput(status="OK", value=Decimal("-0.20")),
            max_drawdown_baseline=PromotionMetricInput(status="OK", value=Decimal("-0.20")),
        ),
        _config(),
    )
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_shadow_only_when_excess_return_not_computable():
    decision = evaluate_promotion(
        _inputs(excess_return_enhanced=PromotionMetricInput(status="INSUFFICIENT_DATA", value=None)), _config(),
    )
    assert decision.status == STATUS_SHADOW_ONLY


def test_shadow_only_when_enhanced_does_not_beat_baseline():
    decision = evaluate_promotion(
        _inputs(
            excess_return_enhanced=PromotionMetricInput(status="OK", value=0.01),
            excess_return_baseline=PromotionMetricInput(status="OK", value=0.02),
        ),
        _config(),
    )
    assert decision.status == STATUS_SHADOW_ONLY


def test_eligible_for_paper_when_margin_below_preferred_threshold():
    decision = evaluate_promotion(
        _inputs(
            excess_return_enhanced=PromotionMetricInput(status="OK", value=0.021),
            excess_return_baseline=PromotionMetricInput(status="OK", value=0.01),
        ),
        _config(preferred_excess_return_margin=0.05),
    )
    assert decision.status == STATUS_ELIGIBLE_FOR_PAPER


def test_preferred_for_paper_when_margin_meets_threshold():
    decision = evaluate_promotion(_inputs(), _config(preferred_excess_return_margin=0.02))
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_no_status_ever_implies_live_trading():
    from trading_research.research.promotion import PROMOTION_STATUSES
    for status in PROMOTION_STATUSES:
        assert "LIVE" not in status


def test_deterministic_reconstruction_same_inputs_same_decision():
    config = _config()
    inputs = _inputs()
    d1 = evaluate_promotion(inputs, config)
    d2 = evaluate_promotion(inputs, config)
    assert d1 == d2


# --- Milestone 7 additive gates (docs/milestone-7.md Step 24) -----------------


def test_unconfigured_additive_gates_do_not_change_milestone6_behavior():
    """With no Milestone 7 gate configured, behavior must be byte-for-byte
    identical to the pre-Milestone-7 decision — proves additive, not a
    behavior change."""
    decision = evaluate_promotion(_inputs(), _config())
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_min_shadow_cycle_completion_rate_below_threshold_is_insufficient_data():
    decision = evaluate_promotion(
        _inputs(shadow_cycle_completion_rate=0.5), _config(min_shadow_cycle_completion_rate=0.9),
    )
    assert decision.status == STATUS_INSUFFICIENT_DATA
    assert any("shadow_cycle_completion_rate" in r for r in decision.reasons)


def test_min_shadow_cycle_completion_rate_unknown_fails_closed():
    decision = evaluate_promotion(
        _inputs(shadow_cycle_completion_rate=None), _config(min_shadow_cycle_completion_rate=0.9),
    )
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_min_shadow_cycle_completion_rate_above_threshold_does_not_block():
    decision = evaluate_promotion(
        _inputs(shadow_cycle_completion_rate=0.95), _config(min_shadow_cycle_completion_rate=0.9),
    )
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_require_cost_known_blocks_when_cost_unknown():
    decision = evaluate_promotion(_inputs(cost_known=False), _config(require_cost_known=True))
    assert decision.status == STATUS_INSUFFICIENT_DATA
    assert any("cost" in r.lower() for r in decision.reasons)


def test_require_cost_known_blocks_when_cost_none():
    decision = evaluate_promotion(_inputs(cost_known=None), _config(require_cost_known=True))
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_require_cost_known_allows_when_cost_known_true():
    decision = evaluate_promotion(_inputs(cost_known=True), _config(require_cost_known=True))
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_min_evidence_complete_sample_size_blocks_below_threshold():
    decision = evaluate_promotion(
        _inputs(evidence_complete_sample_size=3), _config(min_evidence_complete_sample_size=10),
    )
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_min_evidence_complete_sample_size_unknown_fails_closed():
    decision = evaluate_promotion(
        _inputs(evidence_complete_sample_size=None), _config(min_evidence_complete_sample_size=10),
    )
    assert decision.status == STATUS_INSUFFICIENT_DATA


def test_min_evidence_complete_sample_size_above_threshold_does_not_block():
    decision = evaluate_promotion(
        _inputs(evidence_complete_sample_size=20), _config(min_evidence_complete_sample_size=10),
    )
    assert decision.status == STATUS_PREFERRED_FOR_PAPER


def test_additive_gates_never_override_a_safety_rejection():
    """A safety regression (unsupported_claim_rate) must still REJECT even
    when every Milestone 7 additive gate would otherwise pass — additive
    gates can only add restriction, never remove the Milestone 6 safety
    gate's authority."""
    decision = evaluate_promotion(
        _inputs(unsupported_claim_rate=0.5, shadow_cycle_completion_rate=1.0, cost_known=True, evidence_complete_sample_size=100),
        _config(min_shadow_cycle_completion_rate=0.5, require_cost_known=True, min_evidence_complete_sample_size=5),
    )
    assert decision.status == STATUS_REJECTED


def test_invalid_min_shadow_cycle_completion_rate_fails_closed_at_construction():
    with pytest.raises(PromotionConfigError):
        _config(min_shadow_cycle_completion_rate=1.5)


def test_invalid_min_evidence_complete_sample_size_fails_closed_at_construction():
    with pytest.raises(PromotionConfigError):
        _config(min_evidence_complete_sample_size=-1)
