"""Versioned, framework-neutral JSON contract for backtest_runtime.

docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 3: input is a
caller-supplied file containing the complete bar set plus deterministic run
parameters; output is a single serialized result document. Both sides are
validated independently here -- neither trusts the other -- and every
unknown field or non-finite value is rejected rather than coerced.

No LumiBot object ever appears in either document: this module has no
dependency on `lumibot` at all.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date

from . import SCHEMA_VERSION_INPUT, SCHEMA_VERSION_RESULT

REFERENCE_STRATEGY_ID = "backtest_runtime.reference_strategy.v1"


class ContractError(ValueError):
    """Raised for any malformed, unknown, or non-finite input/output field."""


_INPUT_FIELDS = {"schema_version", "strategy", "bars"}
_STRATEGY_FIELDS = {"strategy_id", "symbol", "quantity", "budget"}
_BAR_FIELDS = {"date", "open", "high", "low", "close", "volume"}

_RESULT_FIELDS = {
    "schema_version",
    "historical_bar_dataset_checksum",
    "run_configuration_checksum",
    "strategy_identity",
    "lumibot_version",
    "orders",
    "fills",
    "daily_states",
    "positions",
    "final_cash",
    "final_equity",
    "final_value",
    "max_drawdown_fraction",
}
_ORDER_FIELDS = {"order_id", "symbol", "side", "quantity", "order_type", "status"}
_FILL_FIELDS = {"fill_id", "order_id", "symbol", "side", "quantity", "fill_price", "fees", "market_date"}
_DAILY_STATE_FIELDS = {
    "market_date",
    "cash",
    "equity",
    "realized_pnl",
    "unrealized_pnl",
    "drawdown_fraction",
}
_POSITION_FIELDS = {"symbol", "quantity", "average_price"}


def _require_mapping(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError(f"{what} must be a JSON object")
    return value


def _require_no_unknown_fields(mapping: dict, allowed: set[str], what: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ContractError(f"{what} has unknown fields: {sorted(unknown)}")
    missing = allowed - set(mapping)
    if missing:
        raise ContractError(f"{what} is missing fields: {sorted(missing)}")


def _require_finite_number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{what} must be finite")
    return number


def _require_positive_finite(value: object, what: str) -> float:
    number = _require_finite_number(value, what)
    if number <= 0:
        raise ContractError(f"{what} must be positive")
    return number


def _require_non_negative_finite(value: object, what: str) -> float:
    number = _require_finite_number(value, what)
    if number < 0:
        raise ContractError(f"{what} must not be negative")
    return number


def _require_non_positive_finite(value: object, what: str) -> float:
    number = _require_finite_number(value, what)
    if number > 0:
        raise ContractError(f"{what} must not be positive")
    return number


def _require_positive_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{what} must be an integer")
    if value <= 0:
        raise ContractError(f"{what} must be positive")
    return value


def _require_string(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{what} must be a non-empty string")
    return value


# `date.fromisoformat` accepts a strict superset of "YYYY-MM-DD" that varies
# by Python version -- 3.11+ additionally accepts compact form ("20240102")
# and ISO week dates ("2024-W01-2"), which 3.10 rejects. Gating on this
# pattern first, before ever calling `fromisoformat`, keeps accepted syntax
# identical across both supported interpreter versions.
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_date(value: object, what: str) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        raise ContractError(f"{what} must be a string in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{what} is not a valid ISO date: {exc}") from None


# --------------------------------------------------------------------------
# Input document
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    symbol: str
    quantity: int
    budget: float


@dataclass(frozen=True)
class BacktestInput:
    strategy: StrategyConfig
    bars: tuple[Bar, ...]


def _parse_bar(raw: object, index: int) -> Bar:
    mapping = _require_mapping(raw, f"bars[{index}]")
    _require_no_unknown_fields(mapping, _BAR_FIELDS, f"bars[{index}]")
    session_date = _require_date(mapping["date"], f"bars[{index}].date")
    open_ = _require_positive_finite(mapping["open"], f"bars[{index}].open")
    high = _require_positive_finite(mapping["high"], f"bars[{index}].high")
    low = _require_positive_finite(mapping["low"], f"bars[{index}].low")
    close = _require_positive_finite(mapping["close"], f"bars[{index}].close")
    volume = mapping["volume"]
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
        raise ContractError(f"bars[{index}].volume must be a non-negative integer")
    if high < low:
        raise ContractError(f"bars[{index}] has high < low")
    if not (low <= open_ <= high):
        raise ContractError(f"bars[{index}].open is outside [low, high]")
    if not (low <= close <= high):
        raise ContractError(f"bars[{index}].close is outside [low, high]")
    return Bar(session_date=session_date, open=open_, high=high, low=low, close=close, volume=volume)


def _parse_strategy(raw: object) -> StrategyConfig:
    mapping = _require_mapping(raw, "strategy")
    _require_no_unknown_fields(mapping, _STRATEGY_FIELDS, "strategy")
    strategy_id = mapping["strategy_id"]
    if strategy_id != REFERENCE_STRATEGY_ID:
        raise ContractError(
            f"strategy.strategy_id must be {REFERENCE_STRATEGY_ID!r}, got {strategy_id!r}"
        )
    symbol = _require_string(mapping["symbol"], "strategy.symbol")
    quantity = _require_positive_int(mapping["quantity"], "strategy.quantity")
    budget = _require_positive_finite(mapping["budget"], "strategy.budget")
    return StrategyConfig(strategy_id=strategy_id, symbol=symbol, quantity=quantity, budget=budget)


def parse_input_document(document: object) -> BacktestInput:
    mapping = _require_mapping(document, "input document")
    _require_no_unknown_fields(mapping, _INPUT_FIELDS, "input document")
    if mapping["schema_version"] != SCHEMA_VERSION_INPUT:
        raise ContractError(
            f"unsupported schema_version: {mapping['schema_version']!r}, "
            f"expected {SCHEMA_VERSION_INPUT!r}"
        )
    strategy = _parse_strategy(mapping["strategy"])
    raw_bars = mapping["bars"]
    if not isinstance(raw_bars, list) or not raw_bars:
        raise ContractError("bars must be a non-empty JSON array")
    bars = tuple(_parse_bar(raw, i) for i, raw in enumerate(raw_bars))
    for i in range(1, len(bars)):
        if bars[i].session_date <= bars[i - 1].session_date:
            raise ContractError(
                f"bars must be in strictly increasing date order "
                f"(bars[{i}].date {bars[i].session_date} does not follow "
                f"bars[{i - 1}].date {bars[i - 1].session_date})"
            )
    return BacktestInput(strategy=strategy, bars=bars)


# --------------------------------------------------------------------------
# Canonical checksums
# --------------------------------------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_of(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bars_digest(bars: tuple[Bar, ...]) -> str:
    payload = [
        {
            "date": bar.session_date.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    return sha256_of(payload)


def strategy_digest(strategy: StrategyConfig) -> str:
    return sha256_of(
        {
            "strategy_id": strategy.strategy_id,
            "symbol": strategy.symbol,
            "quantity": strategy.quantity,
            "budget": strategy.budget,
        }
    )


# --------------------------------------------------------------------------
# Result document
# --------------------------------------------------------------------------


def _validate_hex_digest(value: object, what: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ContractError(f"{what} must be a 64-character sha256 hex digest")
    try:
        int(value, 16)
    except ValueError:
        raise ContractError(f"{what} must be a 64-character sha256 hex digest") from None


def _validate_order(raw: object, index: int) -> None:
    mapping = _require_mapping(raw, f"orders[{index}]")
    _require_no_unknown_fields(mapping, _ORDER_FIELDS, f"orders[{index}]")
    _require_string(mapping["order_id"], f"orders[{index}].order_id")
    _require_string(mapping["symbol"], f"orders[{index}].symbol")
    _require_string(mapping["side"], f"orders[{index}].side")
    _require_positive_finite(mapping["quantity"], f"orders[{index}].quantity")
    _require_string(mapping["order_type"], f"orders[{index}].order_type")
    _require_string(mapping["status"], f"orders[{index}].status")


def _validate_fill(raw: object, index: int) -> None:
    mapping = _require_mapping(raw, f"fills[{index}]")
    _require_no_unknown_fields(mapping, _FILL_FIELDS, f"fills[{index}]")
    _require_string(mapping["fill_id"], f"fills[{index}].fill_id")
    _require_string(mapping["order_id"], f"fills[{index}].order_id")
    _require_string(mapping["symbol"], f"fills[{index}].symbol")
    _require_string(mapping["side"], f"fills[{index}].side")
    _require_positive_finite(mapping["quantity"], f"fills[{index}].quantity")
    _require_positive_finite(mapping["fill_price"], f"fills[{index}].fill_price")
    _require_non_negative_finite(mapping["fees"], f"fills[{index}].fees")
    _require_date(mapping["market_date"], f"fills[{index}].market_date")


def _validate_daily_state(raw: object, index: int) -> None:
    """`drawdown_fraction` is (equity - running_peak_equity) / running_peak_equity:
    zero at a new peak, otherwise negative. This matches the convention
    already used by the project's own backtest report, not a LumiBot-specific
    convention -- this module has no LumiBot dependency."""
    mapping = _require_mapping(raw, f"daily_states[{index}]")
    _require_no_unknown_fields(mapping, _DAILY_STATE_FIELDS, f"daily_states[{index}]")
    _require_date(mapping["market_date"], f"daily_states[{index}].market_date")
    _require_finite_number(mapping["cash"], f"daily_states[{index}].cash")
    _require_finite_number(mapping["equity"], f"daily_states[{index}].equity")
    _require_finite_number(mapping["realized_pnl"], f"daily_states[{index}].realized_pnl")
    _require_finite_number(mapping["unrealized_pnl"], f"daily_states[{index}].unrealized_pnl")
    _require_non_positive_finite(
        mapping["drawdown_fraction"], f"daily_states[{index}].drawdown_fraction"
    )


def _validate_position(raw: object, index: int) -> None:
    mapping = _require_mapping(raw, f"positions[{index}]")
    _require_no_unknown_fields(mapping, _POSITION_FIELDS, f"positions[{index}]")
    _require_string(mapping["symbol"], f"positions[{index}].symbol")
    _require_positive_finite(mapping["quantity"], f"positions[{index}].quantity")
    _require_positive_finite(mapping["average_price"], f"positions[{index}].average_price")


def validate_result_document(document: object) -> None:
    """Raise `ContractError` for any malformed result document.

    Called by `cli.py` on the document it is about to write, and reusable by
    tests validating the shape of a produced result independently of how it
    was produced -- neither side of the contract trusts the other.
    """
    mapping = _require_mapping(document, "result document")
    _require_no_unknown_fields(mapping, _RESULT_FIELDS, "result document")
    if mapping["schema_version"] != SCHEMA_VERSION_RESULT:
        raise ContractError(
            f"unsupported schema_version: {mapping['schema_version']!r}, "
            f"expected {SCHEMA_VERSION_RESULT!r}"
        )
    _validate_hex_digest(
        mapping["historical_bar_dataset_checksum"], "historical_bar_dataset_checksum"
    )
    _validate_hex_digest(mapping["run_configuration_checksum"], "run_configuration_checksum")
    _require_string(mapping["strategy_identity"], "strategy_identity")
    _require_string(mapping["lumibot_version"], "lumibot_version")

    orders = mapping["orders"]
    if not isinstance(orders, list):
        raise ContractError("orders must be a JSON array")
    for i, raw in enumerate(orders):
        _validate_order(raw, i)

    fills = mapping["fills"]
    if not isinstance(fills, list):
        raise ContractError("fills must be a JSON array")
    for i, raw in enumerate(fills):
        _validate_fill(raw, i)

    daily_states = mapping["daily_states"]
    if not isinstance(daily_states, list):
        raise ContractError("daily_states must be a JSON array")
    for i, raw in enumerate(daily_states):
        _validate_daily_state(raw, i)

    positions = mapping["positions"]
    if not isinstance(positions, list):
        raise ContractError("positions must be a JSON array")
    for i, raw in enumerate(positions):
        _validate_position(raw, i)

    _require_finite_number(mapping["final_cash"], "final_cash")
    _require_finite_number(mapping["final_equity"], "final_equity")
    _require_finite_number(mapping["final_value"], "final_value")
    _require_non_positive_finite(mapping["max_drawdown_fraction"], "max_drawdown_fraction")
