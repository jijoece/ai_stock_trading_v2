"""Fail-closed input/output contract validation. No `importorskip` -- these
are pure-Python checks against `contract.py`, which has no dependency on
`lumibot` at all."""
from __future__ import annotations

import copy

import pytest
from backtest_runtime.contract import ContractError, parse_input_document, validate_result_document

from support.fixtures import valid_input_document


def test_valid_document_parses():
    document = valid_input_document()
    parsed = parse_input_document(document)
    assert parsed.strategy.symbol == "SPKE"
    assert len(parsed.bars) == 5


@pytest.mark.parametrize(
    "mutate,expected_substring",
    [
        (lambda d: d.update(unexpected_field=1), "unknown fields"),
        (lambda d: d.pop("bars"), "missing fields"),
        # A future version, and the superseded v1: reference strategy v2 added
        # a required `strategy.entry_after_session`, so a v1 document must be
        # rejected outright rather than silently defaulted (DECISIONS.md D6).
        (lambda d: d.update(schema_version="backtest_runtime.input.v3"), "unsupported schema_version"),
        (lambda d: d.update(schema_version="backtest_runtime.input.v1"), "unsupported schema_version"),
    ],
)
def test_malformed_top_level_document_fails_closed(mutate, expected_substring):
    document = valid_input_document()
    mutate(document)
    with pytest.raises(ContractError, match=expected_substring):
        parse_input_document(document)


def test_unknown_strategy_field_rejected():
    document = valid_input_document()
    document["strategy"]["extra"] = "nope"
    with pytest.raises(ContractError, match="unknown fields"):
        parse_input_document(document)


def test_wrong_strategy_id_rejected():
    document = valid_input_document()
    document["strategy"]["strategy_id"] = "not-a-real-strategy"
    with pytest.raises(ContractError, match="strategy_id"):
        parse_input_document(document)


def test_non_positive_quantity_rejected():
    document = valid_input_document()
    document["strategy"]["quantity"] = 0
    with pytest.raises(ContractError, match="positive"):
        parse_input_document(document)


def test_non_positive_budget_rejected():
    document = valid_input_document()
    document["strategy"]["budget"] = -1.0
    with pytest.raises(ContractError, match="positive"):
        parse_input_document(document)


def test_empty_bars_rejected():
    document = valid_input_document()
    document["bars"] = []
    with pytest.raises(ContractError, match="non-empty"):
        parse_input_document(document)


def test_unknown_bar_field_rejected():
    document = valid_input_document()
    document["bars"][0]["extra_field"] = 1
    with pytest.raises(ContractError, match="unknown fields"):
        parse_input_document(document)


def test_missing_bar_field_rejected():
    document = valid_input_document()
    del document["bars"][0]["volume"]
    with pytest.raises(ContractError, match="missing fields"):
        parse_input_document(document)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_non_finite_bar_price_rejected(field):
    document = valid_input_document()
    document["bars"][0][field] = float("nan")
    with pytest.raises(ContractError, match="finite"):
        parse_input_document(document)


def test_infinite_bar_price_rejected():
    document = valid_input_document()
    document["bars"][0]["close"] = float("inf")
    with pytest.raises(ContractError, match="finite"):
        parse_input_document(document)


def test_high_below_low_rejected():
    document = valid_input_document()
    document["bars"][0]["high"] = 1.0
    document["bars"][0]["low"] = 2.0
    with pytest.raises(ContractError, match="high < low"):
        parse_input_document(document)


def test_open_outside_range_rejected():
    document = valid_input_document()
    document["bars"][0]["open"] = 999.0
    with pytest.raises(ContractError, match=r"open is outside"):
        parse_input_document(document)


def test_negative_volume_rejected():
    document = valid_input_document()
    document["bars"][0]["volume"] = -1
    with pytest.raises(ContractError, match="volume"):
        parse_input_document(document)


def test_non_increasing_dates_rejected():
    document = valid_input_document()
    document["bars"][1]["date"] = document["bars"][0]["date"]
    with pytest.raises(ContractError, match="increasing date order"):
        parse_input_document(document)


def test_malformed_date_rejected():
    document = valid_input_document()
    document["bars"][0]["date"] = "not-a-date"
    with pytest.raises(ContractError, match="YYYY-MM-DD"):
        parse_input_document(document)


@pytest.mark.parametrize("compact_date", ["20240102", "2024-W01-2"])
def test_non_dashed_iso_date_forms_rejected_for_bars(compact_date):
    """`date.fromisoformat` accepts compact ("20240102") and ISO week
    ("2024-W01-2") forms on Python 3.11+ but not on 3.10 -- both must be
    rejected on every supported interpreter so behavior does not vary by
    Python version."""
    document = valid_input_document()
    document["bars"][0]["date"] = compact_date
    with pytest.raises(ContractError, match="YYYY-MM-DD"):
        parse_input_document(document)


def test_out_of_range_dashed_date_still_rejected():
    """Confirms the regex gate does not weaken the underlying calendar
    validity check -- YYYY-MM-DD syntax with an impossible day still fails."""
    document = valid_input_document()
    document["bars"][0]["date"] = "2024-02-30"
    with pytest.raises(ContractError, match="valid ISO date"):
        parse_input_document(document)


def test_document_must_be_object():
    with pytest.raises(ContractError, match="JSON object"):
        parse_input_document(["not", "an", "object"])


def test_bars_must_be_array():
    document = valid_input_document()
    document["bars"] = {"not": "a list"}
    with pytest.raises(ContractError, match="non-empty"):
        parse_input_document(document)


# --- result-document validation -------------------------------------------

_VALID_RESULT = {
    "schema_version": "backtest_runtime.result.v1",
    "historical_bar_dataset_checksum": "a" * 64,
    "run_configuration_checksum": "b" * 64,
    "strategy_identity": "backtest_runtime.reference_strategy.v1",
    "lumibot_version": "4.5.78",
    "orders": [],
    "fills": [],
    "daily_states": [],
    "positions": [],
    "final_cash": 100_000.0,
    "final_equity": 100_000.0,
    "final_value": 100_000.0,
    "max_drawdown_fraction": 0.0,
}


def test_valid_result_document_passes():
    validate_result_document(copy.deepcopy(_VALID_RESULT))


def test_result_document_rejects_unknown_field():
    document = copy.deepcopy(_VALID_RESULT)
    document["surprise"] = 1
    with pytest.raises(ContractError, match="unknown fields"):
        validate_result_document(document)


def test_result_document_rejects_bad_checksum_shape():
    document = copy.deepcopy(_VALID_RESULT)
    document["historical_bar_dataset_checksum"] = "not-hex"
    with pytest.raises(ContractError, match="sha256 hex digest"):
        validate_result_document(document)


def test_result_document_rejects_non_finite_final_value():
    document = copy.deepcopy(_VALID_RESULT)
    document["final_value"] = float("nan")
    with pytest.raises(ContractError, match="finite"):
        validate_result_document(document)


def test_result_document_accepts_negative_drawdown():
    """`max_drawdown_fraction` follows `backtesting/engine.py`'s convention:
    (equity - peak_equity) / peak_equity, which is zero or negative."""
    document = copy.deepcopy(_VALID_RESULT)
    document["max_drawdown_fraction"] = -0.1
    validate_result_document(document)


def test_result_document_rejects_positive_drawdown():
    document = copy.deepcopy(_VALID_RESULT)
    document["max_drawdown_fraction"] = 0.1
    with pytest.raises(ContractError, match="not be positive"):
        validate_result_document(document)


@pytest.mark.parametrize("compact_date", ["20240102", "2024-W01-2"])
def test_non_dashed_iso_date_forms_rejected_for_result_daily_state_market_date(compact_date):
    document = copy.deepcopy(_VALID_RESULT)
    document["daily_states"] = [
        {
            "market_date": compact_date,
            "cash": 100_000.0,
            "equity": 100_000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown_fraction": 0.0,
        }
    ]
    with pytest.raises(ContractError, match="YYYY-MM-DD"):
        validate_result_document(document)


def test_result_document_rejects_positive_daily_drawdown_fraction():
    document = copy.deepcopy(_VALID_RESULT)
    document["daily_states"] = [
        {
            "market_date": "2024-01-02",
            "cash": 100_000.0,
            "equity": 100_000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown_fraction": 0.1,
        }
    ]
    with pytest.raises(ContractError, match="not be positive"):
        validate_result_document(document)
