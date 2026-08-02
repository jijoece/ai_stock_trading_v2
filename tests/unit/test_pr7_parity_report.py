"""Regression tests for the PR 7 parity artifacts.

These guard the four properties the PR 7 review round found missing, against
the **committed** artifacts under `docs/library-migration/pr7/` rather than
against a freshly computed run — so a regenerated result set that quietly
loses one of them fails here:

1. order alignment pairs BUY with BUY, and no vocabulary rule can conceal a
   BUY/SELL or a market/limit difference;
2. the cross-case legacy run-identity collision is detected and classified;
3. the exact-parity case really is exact on every promised dimension, on every
   co-dated session with none excluded, and every booking session in the report
   comes from an engine's own record rather than from matching a fill price to
   a bar's open;
4. classification categories are valid, and "unsupported requirement" is not
   used for a capability only one side lacks.

Plus three properties of the fixture set and the harness themselves: no signal
parameter may be derived from a bar the signal could not have seen; every
result document must describe the fixture it is compared against, so a
volume-only fixture edit invalidates stale results; and `run_parity.sh` must
resolve both interpreters to absolute paths before it changes directory.

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


def test_no_co_dated_session_disagrees_in_the_exact_case(comparison):
    """No session is exempt -- the entry session included.

    Reference strategy v1 needed the entry session excluded, because the state
    it sampled there pre-dated that session's own fill. v2 reports the booking
    session's fills in the booking session's state, so an excluded session here
    would mean a hidden disagreement rather than a classified defect.
    """
    case = next(
        row for row in comparison["cases"] if row["case_id"] == "case_f_exact_entry_parity"
    )
    assert case["exact_parity"]["excluded_sessions"] == []
    entry_session = case["exact_parity"]["entry_session"]
    assert entry_session in case["exact_parity"]["sessions_compared"]

    differing = [
        (row["market_date"], field)
        for row in case["daily_state_table"]
        for field, value in row.items()
        if field != "market_date" and value.get("status") == "differs"
    ]
    assert differing == [], f"co-dated sessions disagree: {differing}"


def test_entry_session_state_reflects_the_entry_on_both_sides(comparison):
    """The specific row that used to be exempt, checked by value."""
    case = next(
        row for row in comparison["cases"] if row["case_id"] == "case_f_exact_entry_parity"
    )
    entry_session = case["exact_parity"]["entry_session"]
    row = next(r for r in case["daily_state_table"] if r["market_date"] == entry_session)
    for field in ("cash", "equity", "unrealized_pnl", "realized_pnl", "drawdown_fraction"):
        assert row[field]["status"] == "equal", (field, row[field])
    # Both sides must actually have the position on the entry session -- equal
    # would be trivially satisfiable if neither had entered yet.
    assert Decimal(str(row["cash"]["backtest_runtime"])) < Decimal("100000")
    assert Decimal(row["cash"]["legacy_engine"]) < Decimal("100000")


# --- 3b. authoritative fill timing -----------------------------------------


def test_fill_dates_come_from_each_engines_own_booking_record(comparison):
    """No booking session anywhere in the report is inferred from a price.

    Every reported entry `market_date` must be a session whose open equals the
    reported fill price. That is corroboration of a date each engine supplied,
    not the derivation of one -- and the comparator emits a difference when it
    fails, so this assertion pins the observed state of the artifacts.
    """
    for case in comparison["cases"]:
        runtime = json.loads(
            (RESULTS / f"{case['case_id']}.backtest_runtime.json").read_text(encoding="utf-8")
        )
        legacy = json.loads(
            (RESULTS / f"{case['case_id']}.legacy_engine.json").read_text(encoding="utf-8")
        )
        document = json.loads((FIXTURES / case["input"]).read_text(encoding="utf-8"))
        opens = {bar["date"]: Decimal(str(bar["open"])) for bar in document["bars"]}

        entry = runtime["fills"][0]
        assert opens[entry["market_date"]] == Decimal(str(entry["fill_price"])), (
            case["case_id"],
            "backtest_runtime booked a session that does not open at its fill price",
        )
        legacy_entry = next(fill for fill in legacy["fills"] if fill["side"] == "BUY")
        assert opens[legacy_entry["market_date"]] == Decimal(legacy_entry["fill_price"]), (
            case["case_id"],
            "the legacy engine booked a session that does not open at its fill price",
        )


def test_the_observation_lag_defects_are_resolved_and_cannot_return(
    comparison, compare_parity
):
    """D4 and D15 were adapter defects under reference strategy v1.

    They are reclassified as library semantics -- LumiBot dispatches
    `on_filled_order` a session after its broker books the fill, and runs the
    strategy before processing a session's orders -- and no longer appear as
    differences, because v2 reads the broker's own event log. The comparator
    exits non-zero if either is emitted again.
    """
    resolved = compare_parity.RESOLVED_BY_REFERENCE_STRATEGY_V2
    assert set(resolved) == {"D4-fill-observation-lag", "D15-entry-session-state-lag"}
    for difference_id in resolved:
        classification, _ = compare_parity.CLASSIFICATIONS[difference_id]
        assert classification == "LIBRARY_SEMANTIC", difference_id
        # No longer an adapter defect, so no adapter-defect subcategory.
        assert difference_id not in compare_parity.SUBCATEGORIES

    emitted = {row["difference_id"] for row in _differences(comparison)}
    assert emitted.isdisjoint(resolved), sorted(emitted & set(resolved))
    assert comparison["adapter_defect_subcategory_tally"]["BEHAVIOR"] == 0


def test_probe_records_the_booking_clock_apart_from_the_callback_clock():
    """The evidence the reclassification rests on, in the checked-in probe."""
    transcript = (RESULTS / "probe_output.txt").read_text(encoding="utf-8")
    assert "AUTHORITATIVE BROKER TRADE-EVENT LOG" in transcript
    assert "broker booked the fill on" in transcript
    assert "strategy was told on" in transcript
    assert "first iteration with changed cash" in transcript
    # The exact-parity fixture is probed too, so the delayed booking session is
    # measured rather than assumed.
    assert "case_f_exact_entry_parity.input.json" in transcript
    assert transcript.count("AUTHORITATIVE BROKER TRADE-EVENT LOG") == 3


# --- 3c. results are bound to the current fixtures -------------------------


def test_every_result_matches_the_current_fixture_checksum(comparison, compare_parity):
    for case in comparison["cases"]:
        document = json.loads((FIXTURES / case["input"]).read_text(encoding="utf-8"))
        expected = compare_parity.fixture_bars_digest(document["bars"])
        for suffix in ("backtest_runtime", "legacy_engine"):
            result = json.loads(
                (RESULTS / f"{case['case_id']}.{suffix}.json").read_text(encoding="utf-8")
            )
            assert result["historical_bar_dataset_checksum"] == expected, (
                case["case_id"],
                suffix,
            )


def test_a_volume_only_fixture_change_rejects_the_stale_results(tmp_path, compare_parity):
    """Changing nothing but a volume must invalidate every existing result.

    Volume changes no price and therefore no number either engine computes, so
    two stale result documents would still agree with each other perfectly.
    Only comparison against the current fixture catches it -- which is why the
    comparator recomputes the fixture's checksum itself rather than trusting
    the two documents to agree.
    """
    import shutil

    workspace = tmp_path / "pr7"
    workspace.mkdir()
    shutil.copy(PR7 / "compare_parity.py", workspace / "compare_parity.py")
    shutil.copytree(FIXTURES, workspace / "fixtures")
    shutil.copytree(RESULTS, workspace / "results")

    target = workspace / "fixtures" / "case_a_buy_and_hold.input.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    before = compare_parity.fixture_bars_digest(document["bars"])
    document["bars"][0]["volume"] += 1
    after = compare_parity.fixture_bars_digest(document["bars"])
    assert before != after, "volume must participate in the canonical bar checksum"
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "pr7_compare_parity_stale", workspace / "compare_parity.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.main([str(workspace / "results")]) == 6


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


# --- 4b. run_parity.sh interpreter resolution ------------------------------


def test_run_parity_resolves_interpreters_before_changing_directory():
    """The documented invocation passes relative interpreter paths.

    `run_parity.sh <venv>/bin/python .venv/bin/python` is run from the
    repository root, but step 2 runs the isolated interpreter from a scratch
    directory so LumiBot's `logs/` never lands in the repo. A relative path
    would resolve against that scratch directory instead.
    """
    script = (PR7 / "run_parity.sh").read_text(encoding="utf-8")
    resolution = script.index("absolute_interpreter()")
    assignment = script.index('MAIN_PYTHON="$(absolute_interpreter')
    first_cd = script.index('(cd "${SCRATCH}"')
    assert resolution < first_cd, "interpreters must be resolved before any cd"
    assert assignment < first_cd
    # And the resolved paths must be checked, not merely rewritten.
    assert 'if [ ! -x "${interpreter}" ]' in script
    assert script.index('if [ ! -x "${interpreter}" ]') < first_cd


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
