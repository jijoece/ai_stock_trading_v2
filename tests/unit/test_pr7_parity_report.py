"""Regression tests for the PR 7 parity artifacts.

These guard the four properties the PR 7 review round found missing, against
the **committed** artifacts under `docs/library-migration/pr7/` rather than
against a freshly computed run — so a regenerated result set that quietly
loses one of them fails here:

1. order alignment pairs BUY with BUY, and no vocabulary rule can conceal a
   BUY/SELL or a market/limit difference;
2. the cross-case legacy run-identity collision is detected and classified;
3. the exact-parity case really is exact on every promised dimension;
4. classification categories are valid, and "unsupported requirement" is not
   used for a capability only one side lacks.

Plus the point-in-time-safety property of the fixture set itself: no signal
parameter may be derived from a bar the signal could not have seen.

Nothing here imports `backtest_runtime` (the main environment must never
install it) or runs either engine; it reads the checked-in JSON and the
comparator module.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

PR7 = Path(__file__).resolve().parents[2] / "docs" / "library-migration" / "pr7"
RESULTS = PR7 / "results"
FIXTURES = PR7 / "fixtures"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"pr7_{name}", PR7 / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def comparison() -> dict:
    return json.loads((RESULTS / "comparison.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURES / "parity_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compare_parity():
    return _load_module("compare_parity")


def _differences(comparison: dict) -> list[dict]:
    return [row for case in comparison["cases"] for row in case["differences"]]


# --- 1. order alignment ----------------------------------------------------


def test_orders_are_paired_by_economic_role_never_by_position(comparison):
    """Every paired-order difference must name the role it was matched under,
    so a BUY can never be silently aligned against a SELL."""
    paired = [
        row for row in _differences(comparison) if row["dimension"].startswith("orders[")
    ]
    assert paired, "expected at least one paired-order difference"
    for row in paired:
        assert row["dimension"].startswith(("orders[BUY#", "orders[SELL#")), row["dimension"]


def test_unmatched_legacy_sell_is_the_mandatory_exit_difference(comparison):
    """Cases B and C are the ones where the legacy engine exits. The extra
    SELL must show up as an unmatched order, not as a BUY mismatch."""
    for case_id in ("case_b_perturbed_last_close", "case_c_falling_equity"):
        case = next(row for row in comparison["cases"] if row["case_id"] == case_id)
        unmatched = [
            row
            for row in case["differences"]
            if row["dimension"] == "orders: SELL count"
        ]
        assert len(unmatched) == 1, f"{case_id}: expected one unmatched SELL difference"
        assert unmatched[0]["backtest_runtime"] == 0
        assert unmatched[0]["legacy_engine"] == 1
        assert unmatched[0]["difference_id"] == "D9-mandatory-risk-exit"
        # ... and the BUY still pairs with the BUY.
        assert any(
            "orders[BUY#0]: side (BUY) and quantity" in agreement
            for agreement in case["agreements"]
        ), f"{case_id}: BUY/BUY pairing missing"


def test_vocabulary_differences_cannot_conceal_an_economic_difference(comparison, compare_parity):
    """A D5 difference must be the same fact spelled two ways -- both values
    normalizing to one token. Sides and order types are not eligible."""
    normalize = compare_parity._normalize_token
    for row in _differences(comparison):
        if row["difference_id"] != "D5-enum-vocabulary":
            continue
        assert ": side" not in row["dimension"], row["dimension"]
        assert ": order_type" not in row["dimension"], row["dimension"]
        assert normalize(row["backtest_runtime"]) == normalize(row["legacy_engine"]), row


def test_market_versus_limit_is_an_economic_difference_not_vocabulary(comparison):
    order_types = [
        row for row in _differences(comparison) if row["dimension"].endswith(": order_type")
    ]
    assert order_types, "expected the market-vs-limit difference to be reported"
    for row in order_types:
        assert row["difference_id"] == "D16-order-type-model"


def test_normalize_token_never_equates_buy_and_sell(compare_parity):
    normalize = compare_parity._normalize_token
    assert normalize("buy") == normalize("BUY") == "BUY"
    assert normalize("sell") == normalize("SELL") == "SELL"
    assert normalize("buy") != normalize("sell")
    assert normalize("market") != normalize("LIMIT")


def test_derived_legacy_orders_are_in_execution_order():
    """The entry order must come first: sorting by order_id put an exit ahead
    of the entry that created it."""
    for case_id in ("case_b_perturbed_last_close", "case_c_falling_equity"):
        legacy = json.loads(
            (RESULTS / f"{case_id}.legacy_engine.json").read_text(encoding="utf-8")
        )
        orders = legacy["derived"]["orders_from_fills"]
        assert [order["side"] for order in orders] == ["BUY", "SELL"], case_id
        sequences = [order["first_fill_sequence"] for order in orders]
        assert sequences == sorted(sequences), case_id


# --- 2. cross-case identity collision -------------------------------------


def test_cross_case_identity_collision_is_detected_and_classified(comparison):
    findings = comparison["cross_case_findings"]
    assert findings, "the case A / case B run-identity collision must be reported"
    finding = next(
        row for row in findings if row["difference_id"] == "D17-run-identity-ignores-dataset"
    )
    assert set(finding["colliding_cases"]) >= {"case_a_buy_and_hold", "case_b_perturbed_last_close"}
    assert len(set(finding["distinct_historical_bar_dataset_checksums"])) >= 2
    assert finding["classification"] == "OLD_ENGINE_DEFECT"


def test_the_collision_is_real_in_the_raw_documents():
    """Proved from the two result documents directly, independently of the
    comparator that reports it."""
    a = json.loads((RESULTS / "case_a_buy_and_hold.legacy_engine.json").read_text())
    b = json.loads((RESULTS / "case_b_perturbed_last_close.legacy_engine.json").read_text())
    assert a["historical_bar_dataset_checksum"] != b["historical_bar_dataset_checksum"]
    assert a["final_equity"] != b["final_equity"]
    assert a["backtest_run_id"] == b["backtest_run_id"]
    assert a["configuration_hash"] == b["configuration_hash"]


def test_collision_detector_stays_silent_when_datasets_match(compare_parity):
    """The detector must fire on differing bars, not merely on a shared id."""
    same_bars = [
        {
            "case_id": "one", "legacy_backtest_run_id": "r", "legacy_configuration_hash": "c",
            "historical_bar_dataset_checksum": "x", "final_equity_legacy": 1.0,
        },
        {
            "case_id": "two", "legacy_backtest_run_id": "r", "legacy_configuration_hash": "c",
            "historical_bar_dataset_checksum": "x", "final_equity_legacy": 1.0,
        },
    ]
    assert compare_parity.find_cross_case_identity_collisions(same_bars) == []
    different_bars = [dict(same_bars[0]), dict(same_bars[1], historical_bar_dataset_checksum="y")]
    assert len(compare_parity.find_cross_case_identity_collisions(different_bars)) == 1


# --- 3. exact-case parity --------------------------------------------------


def test_exactly_one_case_claims_exact_entry_parity(manifest):
    exact = [case for case in manifest["cases"] if case["expects_exact_entry_parity"]]
    assert len(exact) == 1
    assert exact[0]["case_id"] == "case_f_exact_entry_parity"
    assert exact[0]["backtest_runtime_entry_after_session"] == "2024-01-03"


def test_exact_parity_case_passes_every_promised_dimension(comparison):
    case = next(
        row for row in comparison["cases"] if row["case_id"] == "case_f_exact_entry_parity"
    )
    exact = case["exact_parity"]
    assert exact is not None
    failed = [name for name, passed in exact["checks"].items() if not passed]
    assert failed == [], f"exact-parity dimensions failed: {failed}"
    assert exact["all_passed"] is True


def test_exact_parity_case_agrees_in_the_raw_documents():
    """Independent of the comparator: read both result documents and check the
    headline numbers agree."""
    runtime = json.loads((RESULTS / "case_f_exact_entry_parity.backtest_runtime.json").read_text())
    legacy = json.loads((RESULTS / "case_f_exact_entry_parity.legacy_engine.json").read_text())

    assert runtime["historical_bar_dataset_checksum"] == legacy["historical_bar_dataset_checksum"]
    assert len(runtime["fills"]) == len(legacy["fills"]) == 1
    assert Decimal(str(runtime["fills"][0]["fill_price"])) == Decimal(
        legacy["fills"][0]["fill_price"]
    )
    assert Decimal(str(runtime["fills"][0]["quantity"])) == Decimal(
        legacy["fills"][0]["quantity"]
    )
    for field in ("final_cash", "final_equity", "final_value"):
        assert Decimal(str(runtime[field])) == Decimal(legacy[field]), field
    assert Decimal(str(runtime["max_drawdown_fraction"])) == Decimal(
        legacy["metrics"]["maximum_drawdown"]
    )
    assert len(runtime["positions"]) == len(legacy["derived"]["end_positions_from_fills"]) == 1
    assert Decimal(str(runtime["positions"][0]["average_price"])) == Decimal(
        legacy["derived"]["end_positions_from_fills"][0]["average_price"]
    )


def test_the_only_daily_state_disagreement_is_the_entry_session(comparison):
    case = next(
        row for row in comparison["cases"] if row["case_id"] == "case_f_exact_entry_parity"
    )
    entry_session = case["exact_parity"]["entry_session"]
    for row in case["daily_state_table"]:
        for field, value in row.items():
            if field == "market_date" or value.get("status") != "differs":
                continue
            assert row["market_date"] == entry_session, (
                f"{field} differs on {row['market_date']}, which is not the entry session"
            )


# --- 4. classification-category validity -----------------------------------


def test_every_classification_is_one_of_the_four_categories(comparison):
    valid = set(comparison["classification_labels"])
    assert valid == {
        "OLD_ENGINE_DEFECT", "ADAPTER_DEFECT", "LIBRARY_SEMANTIC", "UNSUPPORTED",
    }
    for row in _differences(comparison) + comparison["cross_case_findings"]:
        assert row["classification"] in valid, row


def test_no_difference_is_left_unclassified(comparison):
    for row in _differences(comparison) + comparison["cross_case_findings"]:
        assert row["classification"] != "UNCLASSIFIED", row
        assert row["rationale"].strip()


def test_unsupported_is_reserved_for_what_neither_side_can_express(comparison, compare_parity):
    """A capability the legacy engine has and backtest_runtime lacks is an
    adapter capability defect, not an unsupported requirement."""
    for difference_id, (classification, _) in compare_parity.CLASSIFICATIONS.items():
        if compare_parity.SUBCATEGORIES.get(difference_id) == "CAPABILITY":
            assert classification == "ADAPTER_DEFECT", difference_id
    for row in _differences(comparison):
        if row["classification"] == "UNSUPPORTED":
            assert row.get("subcategory") != "CAPABILITY", row


def test_fees_slippage_and_realized_pnl_are_adapter_capability_defects(comparison):
    by_id = {row["difference_id"]: row for row in _differences(comparison)}
    for difference_id in ("D7-fees-and-slippage-model", "D8-realized-pnl-and-exit-support"):
        row = by_id[difference_id]
        assert row["classification"] == "ADAPTER_DEFECT", difference_id
        assert row["subcategory"] == "CAPABILITY", difference_id


def test_tally_matches_the_classified_differences(comparison):
    counted: dict[str, int] = {key: 0 for key in comparison["classification_labels"]}
    for row in _differences(comparison) + comparison["cross_case_findings"]:
        counted[row["classification"]] += 1
    assert counted == comparison["classification_tally"]


# --- 5. fixture point-in-time safety ---------------------------------------


def test_no_legacy_signal_parameter_uses_a_future_bar(manifest):
    """The look-ahead the review round found: `limit_price` was the entry
    session's high, a bar that had not happened when the signal was generated."""
    build_fixtures = _load_module("build_fixtures")
    for case in manifest["cases"]:
        document = json.loads((FIXTURES / case["input"]).read_text(encoding="utf-8"))
        bars = [
            (bar["date"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
            for bar in document["bars"]
        ]
        signal = case["legacy_engine"]["signal"]
        signal_index = [bar[0] for bar in bars].index(signal["generated_after_session"])
        build_fixtures.assert_point_in_time_safe(bars, signal, signal_index)


def test_limit_price_is_the_declared_band_over_the_last_visible_close(manifest):
    build_fixtures = _load_module("build_fixtures")
    band = manifest["non_binding_limit_band"]
    assert band == build_fixtures.NON_BINDING_LIMIT_BAND
    for case in manifest["cases"]:
        document = json.loads((FIXTURES / case["input"]).read_text(encoding="utf-8"))
        signal = case["legacy_engine"]["signal"]
        visible = [
            bar for bar in document["bars"] if bar["date"] <= signal["generated_after_session"]
        ]
        assert float(signal["limit_price"]) == round(visible[-1]["close"] * band, 2), case["case_id"]


def test_option_a_signal_shape_is_unchanged(manifest):
    """The narrowest construction: no strategy stop, target, or holding cap."""
    for case in manifest["cases"]:
        signal = case["legacy_engine"]["signal"]
        assert signal["initial_stop_reference"] is None
        assert signal["target_reference"] is None
        assert signal["maximum_holding_sessions"] is None
